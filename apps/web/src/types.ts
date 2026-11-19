export interface Metric {
  name: string;
  value: number;
  threshold: number | null;
  direction: "gte" | "lte";
  passed: boolean;
  details: Record<string, unknown>;
}

export interface EvalResult {
  test_id: string;
  test_name: string;
  kind: string;
  passed: boolean;
  metrics: Metric[];
  trace_id: string | null;
  latency_ms: number;
  cost_usd: number;
  failure_reason: string | null;
}

export interface Summary {
  run_id: string;
  agent: string;
  pr: string | null;
  commit: string | null;
  branch: string | null;
  total: number;
  passed: number;
  failed: number;
  pass_rate: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  total_cost_usd: number;
  cost_delta_usd: number;
  tool_accuracy: number;
  safety_score: number;
  policy_score: number;
  regressions: number;
  results: EvalResult[];
}

export interface Gate {
  metric: string;
  actual: number;
  threshold: number;
  direction: string;
  passed: boolean;
  message: string;
}

export interface Report {
  summary: Summary;
  gate_passed: boolean;
  gates: Gate[];
  auto_approve: boolean;
  traces: Array<{ test_id: string; trace_id: string; status: string }>;
  created_at: string;
}

export interface RunItem {
  run_id: string;
  agent: string;
  pr: string | null;
  pass_rate: number;
  gate_passed: boolean;
  created_at: string;
}

export interface ReplayEvent {
  at_ms: number;
  kind: string;
  name: string;
  duration_ms: number;
  tokens: number;
  cost_usd: number;
  error: string | null;
}
