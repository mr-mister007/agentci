from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Literal, cast

import httpx
from agentci_core.models import TestCase
from pydantic import BaseModel, ValidationError

_SYSTEM_PROMPT = (
    "Generate AgentCI test cases. Return only a JSON array; no markdown and no commentary. "
    "Each item must be an object with exactly these keys: name (string), kind (one of "
    "'happy', 'edge', 'adversarial'), input (object), expected (object), tags (array of strings). "
    "Names must be unique and descriptive. Never include credentials or API keys."
)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class LLMConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    timeout_seconds: float = 30.0
    temperature: float = 0.2
    max_tokens: int = 4000

    @classmethod
    def from_env(cls) -> LLMConfig:
        return cls(
            base_url=os.environ.get("AGENTCI_LLM_BASE_URL", "https://api.openai.com/v1"),
            model=os.environ.get("AGENTCI_LLM_MODEL", "gpt-4o-mini"),
            api_key_env=os.environ.get("AGENTCI_LLM_API_KEY_ENV", "OPENAI_API_KEY"),
            timeout_seconds=float(os.environ.get("AGENTCI_LLM_TIMEOUT_SECONDS", "30.0")),
        )


class LLMGenerationError(RuntimeError):
    pass


def _extract_json(text: str) -> list[Any]:
    candidates = [text.strip()]
    candidates.extend(match.group(1).strip() for match in _FENCE.finditer(text))
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1].strip())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and isinstance(value.get("cases"), list):
            return cast("list[Any]", value["cases"])
    raise LLMGenerationError("LLM response did not contain a JSON array")


def _stable_id(source_text: str, kind: str, name: str, input_value: dict[str, Any]) -> str:
    payload = json.dumps(input_value, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(f"{source_text}|{kind}|{name}|{payload}".encode()).hexdigest()
    return f"llm-{digest[:16]}"


class LLMTestGenerator:
    def __init__(self, config: LLMConfig | None = None, client: Any = None) -> None:
        self.config = config if config is not None else LLMConfig.from_env()
        self._client = client

    async def generate(
        self,
        source_text: str,
        source_kind: Literal["prompt", "openapi", "logs"],
        count: int = 50,
    ) -> list[TestCase]:
        if count <= 0:
            return []
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise LLMGenerationError(f"Missing API key in environment {self.config.api_key_env}")
        user_prompt = json.dumps(
            {"source_kind": source_kind, "count": count, "source": source_text}
        )
        owns_client = self._client is None
        client = self._client if self._client is not None else httpx.AsyncClient()
        response: Any = None
        try:
            response = await client.post(
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.model,
                    "temperature": self.config.temperature,
                    "max_tokens": self.config.max_tokens,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
        except Exception as exc:
            status = getattr(response, "status_code", "unknown")
            raise LLMGenerationError(f"LLM request failed with status {status}") from exc
        finally:
            if owns_client:
                await client.aclose()

        try:
            payload = response.json()
        except Exception as exc:
            raise LLMGenerationError("LLM response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise LLMGenerationError("LLM response payload was not an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMGenerationError("LLM response contained no choices")
        first = choices[0]
        if not isinstance(first, dict) or not isinstance(first.get("message"), dict):
            raise LLMGenerationError("LLM response contained no message")
        content = first["message"].get("content")
        if not isinstance(content, str):
            raise LLMGenerationError("LLM response contained no text content")

        cases: list[TestCase] = []
        for item in _extract_json(content):
            try:
                case = TestCase.model_validate(item)
            except ValidationError as exc:
                raise LLMGenerationError("LLM returned an invalid test case") from exc
            case.id = _stable_id(source_text, case.kind.value, case.name, case.input)
            case.source = f"llm:{source_kind}"
            cases.append(case)
            if len(cases) >= count:
                break
        return cases


async def generate_with_fallback(
    source_text: str,
    source_kind: Literal["prompt", "openapi", "logs"],
    count: int = 50,
    config: LLMConfig | None = None,
    fallback_cases: list[TestCase] | None = None,
) -> list[TestCase]:
    try:
        return await LLMTestGenerator(config).generate(source_text, source_kind, count)
    except Exception:
        return list(fallback_cases or [])[: max(0, count)]
