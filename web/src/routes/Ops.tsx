import { useQuery } from "@tanstack/react-query";

import { LANGSMITH_URL } from "../api";

/**
 * The latest tier 1 eval, read from a build-time artefact.
 *
 * `evals/run_eval.py --json` writes it and it is committed. Running the eval at request
 * time would mean loading the corpus and replaying every fixture while someone waits for
 * a page, to produce a number that only changes when the code does.
 */

interface SplitMetrics {
  split: string;
  records: number;
  specs: number;
  accuracy: number;
  abstention_rate: number;
  allergen_fnr: number | null;
  allergen_cases: number;
  false_passes: number;
  wrong_verdicts: number;
  citation_resolution_rate: number | null;
  recall_at_5: number | null;
  hit_rate_at_5: number | null;
  retrieval_queries: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  cost_per_spec_usd: number;
  per_rule: Record<string, [number, number]>;
}

interface EvalReport {
  generated_at: string;
  mode: string;
  baseline: Record<string, number>;
  gates: Record<string, number>;
  splits: Record<string, SplitMetrics>;
}

const percent = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
const usd = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `$${value.toFixed(4)}`;
const plain = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : String(value);

const ROWS: [string, keyof SplitMetrics, (value: never) => string][] = [
  ["accuracy", "accuracy", percent as never],
  ["abstention rate", "abstention_rate", percent as never],
  ["allergen FNR", "allergen_fnr", percent as never],
  ["false passes", "false_passes", plain as never],
  ["wrong verdicts", "wrong_verdicts", plain as never],
  ["citation resolution", "citation_resolution_rate", percent as never],
  ["recall@5", "recall_at_5", percent as never],
  ["hit rate@5", "hit_rate_at_5", percent as never],
  ["p50 latency (ms)", "p50_latency_ms", plain as never],
  ["p95 latency (ms)", "p95_latency_ms", plain as never],
  ["cost per spec", "cost_per_spec_usd", usd as never],
];

const BASELINE_KEY: Record<string, string> = {
  accuracy: "accuracy",
  abstention_rate: "abstention_rate",
  allergen_fnr: "allergen_fnr",
  false_passes: "false_passes",
  wrong_verdicts: "wrong_verdicts",
  citation_resolution_rate: "citation_resolution_rate",
  recall_at_5: "recall_at_5",
  hit_rate_at_5: "hit_rate_at_5",
  cost_per_spec_usd: "cost_per_spec_usd",
};

export default function Ops() {
  const report = useQuery({
    queryKey: ["evals"],
    queryFn: async (): Promise<EvalReport> => {
      const response = await fetch("/evals.json");
      if (!response.ok) throw new Error("No eval report has been generated yet.");
      return (await response.json()) as EvalReport;
    },
    staleTime: Infinity,
  });

  if (report.isPending) return <p className="muted">Loading the eval report…</p>;

  if (report.isError) {
    return (
      <p className="notice notice--warn">
        No eval report found. Generate one with{" "}
        <code>uv run python -m evals.run_eval --json</code>.
      </p>
    );
  }

  const data = report.data;
  const splits = ["all", "dev", "held_out"].filter((name) => data.splits[name]);

  return (
    <div className="ops">
      <header className="ops__header">
        <h1>Evaluation</h1>
        <p className="muted">
          Tier 1 only — deterministic ground-truth scoring over the golden set, which is what
          gates CI. Tier 2 is judged by a model and reported nightly; it never blocks a merge.
        </p>
        <p className="ops__provenance">
          {data.mode === "offline"
            ? "Replayed from recorded fixtures — no network, no API key."
            : "Measured live against real retrieval and a real provider."}{" "}
          Generated {new Date(data.generated_at).toLocaleString()}.
        </p>
      </header>

      <section>
        <h2>Metrics</h2>
        <div className="table-scroll">
          <table className="ops__table">
            <thead>
              <tr>
                <th scope="col">metric</th>
                <th scope="col">baseline</th>
                {splits.map((name) => (
                  <th key={name} scope="col">
                    {name === "held_out" ? "held-out" : name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map(([label, key, format]) => {
                const baselineKey = BASELINE_KEY[key as string];
                const baseline =
                  baselineKey !== undefined ? data.baseline[baselineKey] : undefined;
                return (
                  <tr key={label}>
                    <th scope="row">{label}</th>
                    <td className="muted">
                      {baseline === undefined ? "—" : (format as (v: unknown) => string)(baseline)}
                    </td>
                    {splits.map((name) => (
                      <td key={name}>
                        {(format as (v: unknown) => string)(data.splits[name]![key])}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="ops__note">
          Accuracy counts an abstention as wrong, which is why the abstention rate sits beside
          it: a system can reach a high score by declining everything hard. Allergen FNR is
          strict — an abstention counts as a miss — and rests on{" "}
          {data.splits.all?.allergen_cases ?? 0} cases, so read it as a gate rather than a rate.
          Latency is blank on a replayed run because a replay has none of its own.
        </p>
      </section>

      <section>
        <h2>Per rule</h2>
        <div className="table-scroll">
          <table className="ops__table">
            <thead>
              <tr>
                <th scope="col">rule</th>
                {splits.map((name) => (
                  <th key={name} scope="col">
                    {name === "held_out" ? "held-out" : name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.keys(data.splits.all?.per_rule ?? {}).map((ruleId) => (
                <tr key={ruleId}>
                  <th scope="row">
                    <code>{ruleId}</code>
                  </th>
                  {splits.map((name) => {
                    const score = data.splits[name]?.per_rule[ruleId];
                    return <td key={name}>{score ? `${score[0]}/${score[1]}` : "—"}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2>Traces</h2>
        <p>
          Every model call is traced with its prompt version, rule id, tokens, cost and latency.
          A reviewer's override is attached to the run that produced the verdict.
        </p>
        <a className="button button--quiet" href={LANGSMITH_URL} target="_blank" rel="noreferrer">
          Open the LangSmith project
        </a>
      </section>
    </div>
  );
}
