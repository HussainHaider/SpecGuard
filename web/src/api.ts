/**
 * The API boundary, typed to match the Pydantic models the backend actually returns.
 *
 * These types are hand-written rather than generated. Generating them would be one more
 * build step for a surface of five endpoints, and hand-writing them means a change to a
 * response shape shows up here as a review comment rather than as a regenerated diff
 * nobody reads.
 */

const BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type Verdict = "PASS" | "FAIL" | "NEEDS_REVIEW";
export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface Citation {
  regulation: string;
  article: string;
  paragraph: string | null;
  point: string | null;
  chunk_id: string;
  quoted_span: string;
  source_version: string;
  eurlex_url: string | null;
  retrieval_score: number | null;
}

export interface LlmUsage {
  provider: string;
  model: string;
  prompt_version: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number;
  langsmith_run_id: string | null;
}

export interface RuleResult {
  rule_id: string;
  verdict: Verdict;
  citations: Citation[];
  rationale: string;
  suggested_fix: string | null;
  confidence: number;
  abstention_reason: string | null;
  metrics: Record<string, number>;
  llm_usage: LlmUsage[];
  duration_ms: number;
  kind: "deterministic" | "rag";
  langsmith_run_id: string | null;
}

export interface GuardrailFlags {
  injection_suspected: boolean;
  injection_signals: string[];
  low_confidence_fields: string[];
  unreadable_pages: number[];
}

export interface CheckReport {
  report_id: string;
  job_id: string | null;
  created_at: string;
  results: RuleResult[];
  guardrails: GuardrailFlags;
  demo: boolean;
  demo_note: string | null;
  corpus_version: string;
  graph_version: string;
  duration_ms: number;
  overall_verdict: Verdict;
  counts: Record<Verdict, number>;
  total_cost_usd: number;
}

export interface CheckStatus {
  job_id: string;
  status: JobStatus;
  correlation_id: string;
  filename: string;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  report: CheckReport | null;
}

export interface ClauseText {
  chunk_id: string;
  regulation: string;
  article: string;
  paragraph: string | null;
  heading: string | null;
  language: string;
  source_version: string;
  text: string;
  reference: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    // The API returns {"detail": "..."} for anything it refused on purpose. Anything
    // else is shown as a status, never as a raw body: an unhandled error's message can
    // carry a connection string or a fragment of someone's specification.
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep the status-only message */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export function submitCheck(file: File, language: string): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  return request(`/checks?language=${encodeURIComponent(language)}`, {
    method: "POST",
    body: form,
  });
}

export function getCheck(jobId: string): Promise<CheckStatus> {
  return request(`/checks/${jobId}`);
}

export function getClause(chunkId: string): Promise<ClauseText> {
  return request(`/clauses/${chunkId}`);
}

export function sendFeedback(
  jobId: string,
  body: { rule_id: string; corrected_verdict: Verdict; comment?: string; reviewer?: string },
): Promise<unknown> {
  return request(`/checks/${jobId}/feedback`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const LANGSMITH_URL = import.meta.env.VITE_LANGSMITH_URL ?? "https://smith.langchain.com";
