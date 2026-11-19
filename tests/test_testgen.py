from __future__ import annotations

import json
from pathlib import Path

from agentci_testgen import generate_tests, load_cases
from agentci_testgen.generators import (
    dump_cases,
    generate_from_logs,
    generate_from_openapi,
    generate_from_prompt,
)

ROOT = Path(__file__).resolve().parents[1]


def test_openapi_generation_includes_all_kinds() -> None:
    cases = generate_from_openapi(ROOT / "openapi.yaml", count=60)
    kinds = {case.kind.value for case in cases}
    assert {"happy", "edge", "adversarial"} <= kinds
    assert len(cases) <= 60
    assert all(case.expected.get("json_schema") for case in cases if case.kind.value == "happy")


def test_prompt_generation_extracts_variables(tmp_path: Path) -> None:
    path = tmp_path / "agent_prompt.txt"
    path.write_text("Summarize {{ticket}} for [customer].")
    cases = generate_from_prompt(path, count=50)
    assert any("ticket" in case.input["value"] for case in cases)
    assert any(case.kind.value == "adversarial" for case in cases)


def test_log_generation_uses_output_as_expected(tmp_path: Path) -> None:
    path = tmp_path / "prod_logs.jsonl"
    path.write_text(json.dumps({"input": {"q": "hi"}, "output": {"answer": "hello"}}) + "\n")
    cases = generate_from_logs(path)
    assert cases[0].expected["output"] == {"answer": "hello"}


def test_inference_and_roundtrip(tmp_path: Path) -> None:
    cases = generate_tests(ROOT / "openapi.yaml", count=20)
    path = tmp_path / "cases.yaml"
    path.write_text(dump_cases(cases))
    assert len(load_cases(path)) == len(cases)
