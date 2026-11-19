import { useEffect, useState } from "react";
import type { Gate, ReplayEvent, Report, RunItem } from "./types";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function get<T>(path: string, apiKey: string): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: { "X-API-Key": apiKey },
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="panel p-4">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`mt-2 text-2xl font-semibold ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

function GateList({ gates }: { gates: Gate[] }) {
  return (
    <div className="panel overflow-hidden">
      <div className="border-b border-zinc-800 px-4 py-3 font-semibold">Gates</div>
      {gates.length === 0 ? (
        <p className="p-4 text-sm text-zinc-500">No gates configured.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-zinc-500">
            <tr>
              <th className="px-4 py-2">Metric</th>
              <th className="px-4 py-2">Actual</th>
              <th className="px-4 py-2">Threshold</th>
              <th className="px-4 py-2">Result</th>
            </tr>
          </thead>
          <tbody>
            {gates.map((gate) => (
              <tr key={gate.metric} className="border-t border-zinc-800/70">
                <td className="px-4 py-2 font-mono">{gate.metric}</td>
                <td className="px-4 py-2">{gate.actual.toFixed(4)}</td>
                <td className="px-4 py-2 text-zinc-400">
                  {gate.direction} {gate.threshold.toFixed(4)}
                </td>
                <td className="px-4 py-2">
                  <span className={gate.passed ? "text-emerald-400" : "text-rose-400"}>
                    {gate.passed ? "PASS" : "FAIL"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Timeline({ events }: { events: ReplayEvent[] }) {
  return (
    <div className="panel p-4">
      <div className="mb-3 font-semibold">Failure replay</div>
      <div className="space-y-2">
        {events.map((event, index) => (
          <div key={`${event.name}-${index}`} className="flex items-center gap-3 text-sm">
            <span className="w-16 font-mono text-xs text-zinc-500">{event.at_ms.toFixed(0)}ms</span>
            <span className="w-12 text-xs uppercase text-sky-400">{event.kind}</span>
            <span className="flex-1 truncate font-mono">{event.name}</span>
            <span className="text-zinc-500">{event.duration_ms.toFixed(0)}ms</span>
            <span className="text-zinc-500">{event.tokens} tok</span>
            <span className="text-zinc-500">${event.cost_usd.toFixed(5)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const [apiKey, setApiKey] = useState(() => localStorage.getItem("agentci_api_key") ?? "");
  const [keyDraft, setKeyDraft] = useState("");
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [replay, setReplay] = useState<ReplayEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!apiKey) return;
    setError(null);
    get<RunItem[]>("/runs", apiKey)
      .then((items) => {
        setRuns(items);
        setSelected(items[0]?.run_id ?? null);
      })
      .catch((err: Error) => setError(err.message));
  }, [apiKey]);

  useEffect(() => {
    if (!selected || !apiKey) return;
    get<Report>(`/runs/${selected}`, apiKey)
      .then((item) => {
        setReport(item);
        setReplay([]);
      })
      .catch((err: Error) => setError(err.message));
  }, [selected, apiKey]);

  function signIn() {
    const key = keyDraft.trim();
    if (!key) return;
    localStorage.setItem("agentci_api_key", key);
    setApiKey(key);
    setKeyDraft("");
  }

  function signOut() {
    localStorage.removeItem("agentci_api_key");
    setApiKey("");
    setRuns([]);
    setReport(null);
    setReplay([]);
  }

  async function showReplay(testId: string) {
    if (!selected || !apiKey) return;
    try {
      const data = await get<{ timeline: ReplayEvent[] }>(
        `/runs/${selected}/trace/${testId}`,
        apiKey,
      );
      setReplay(data.timeline);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  if (!apiKey) {
    return (
      <main className="mx-auto max-w-md space-y-4 p-8">
        <h1 className="text-2xl font-bold">AgentCI</h1>
        <p className="text-sm text-zinc-500">
          Enter an AgentCI API key. Keys are created by an admin with{" "}
          <code className="font-mono text-zinc-300">POST /orgs</code>.
        </p>
        <div className="panel space-y-3 p-4">
          <input
            className="w-full rounded bg-zinc-900 px-3 py-2 font-mono text-sm"
            type="password"
            placeholder="ac_live_..."
            value={keyDraft}
            onChange={(event) => setKeyDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") signIn();
            }}
          />
          <button
            className="w-full rounded bg-emerald-500 px-3 py-2 font-semibold text-zinc-950"
            onClick={signIn}
          >
            Continue
          </button>
        </div>
      </main>
    );
  }

  if (error) {
    return (
      <main className="space-y-4 p-8 text-rose-400">
        <p>API error: {error}</p>
        <button className="panel px-3 py-2 text-sm text-zinc-300" onClick={signOut}>
          Use another API key
        </button>
      </main>
    );
  }

  const summary = report?.summary;

  return (
    <main className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold">AgentCI</h1>
          <p className="text-sm text-zinc-500">Eval reports, gate decisions, and replayable failures.</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="panel bg-zinc-900 px-3 py-2 text-sm"
            value={selected ?? ""}
            onChange={(event) => setSelected(event.target.value)}
          >
            {runs.length === 0 && <option value="">No runs yet</option>}
            {runs.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {run.agent} · PR {run.pr ?? "—"} · {(run.pass_rate * 100).toFixed(0)}%
              </option>
            ))}
          </select>
          <button className="panel px-3 py-2 text-xs text-zinc-400" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>

      {!report || !summary ? (
        <div className="panel p-8 text-center text-zinc-500">
          No reports yet. Run <code className="font-mono text-zinc-300">agentci run --pr 123</code>.
        </div>
      ) : (
        <>
          <section className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <Stat
              label="Gate"
              value={report.gate_passed ? "PASS" : "FAIL"}
              tone={report.gate_passed ? "text-emerald-400" : "text-rose-400"}
            />
            <Stat label="Pass rate" value={`${(summary.pass_rate * 100).toFixed(1)}%`} />
            <Stat label="p95 latency" value={`${summary.p95_latency_ms.toFixed(0)}ms`} />
            <Stat label="Cost / run" value={`$${(summary.total_cost_usd / Math.max(summary.total, 1)).toFixed(5)}`} />
            <Stat
              label="Regressions"
              value={String(summary.regressions)}
              tone={summary.regressions ? "text-rose-400" : "text-emerald-400"}
            />
          </section>

          <div className="grid gap-6 lg:grid-cols-2">
            <GateList gates={report.gates} />
            <div className="panel overflow-hidden">
              <div className="border-b border-zinc-800 px-4 py-3 font-semibold">Failed tests</div>
              {summary.failed === 0 ? (
                <p className="p-4 text-sm text-emerald-400">All tests passed.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-left text-xs uppercase text-zinc-500">
                    <tr>
                      <th className="px-4 py-2">Test</th>
                      <th className="px-4 py-2">Reason</th>
                      <th className="px-4 py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {summary.results
                      .filter((result) => !result.passed)
                      .map((result) => (
                        <tr key={result.test_id} className="border-t border-zinc-800/70">
                          <td className="px-4 py-2">{result.test_name}</td>
                          <td className="px-4 py-2 text-zinc-400">{result.failure_reason}</td>
                          <td className="px-4 py-2">
                            <button
                              className="text-sky-400 hover:underline"
                              onClick={() => void showReplay(result.test_id)}
                            >
                              Replay
                            </button>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {replay.length > 0 && <Timeline events={replay} />}
        </>
      )}
    </main>
  );
}
