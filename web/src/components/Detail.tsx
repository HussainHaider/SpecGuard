import type { RuleResult } from "../api";
import { ABSTENTION_REASON, VERDICT_LABEL, needsHumanReview, ruleTitle } from "../verdicts";
import Evidence from "./Evidence";
import Feedback from "./Feedback";

interface Props {
  jobId: string;
  result: RuleResult | null;
}

export default function Detail({ jobId, result }: Props) {
  if (!result) {
    return (
      <aside className="detail detail--empty">
        <p className="muted">Select a finding to see the clause it rests on.</p>
      </aside>
    );
  }

  const metrics = Object.entries(result.metrics);

  return (
    <aside className="detail">
      <header className="detail__header">
        <h2 className="detail__title">{ruleTitle(result.rule_id)}</h2>
        <p className={`detail__verdict detail__verdict--${result.verdict.toLowerCase()}`}>
          {VERDICT_LABEL[result.verdict]}
          <span className="muted"> · confidence {result.confidence.toFixed(2)}</span>
        </p>
        {needsHumanReview(result) ? (
          <p className="notice notice--escalated">
            Allergen-related failure. This is reported as a failure <em>and</em> routed to a
            person: an undeclared allergen is the finding nobody should act on unread.
          </p>
        ) : null}
      </header>

      <section className="detail__section">
        <h3>Reasoning</h3>
        <p>{result.rationale}</p>
      </section>

      {result.abstention_reason ? (
        <section className="detail__section">
          <h3>Why no decision</h3>
          <p>
            This rule declined to decide because{" "}
            {ABSTENTION_REASON[result.abstention_reason] ?? result.abstention_reason}. Abstention
            is a designed outcome, not a defect.
          </p>
        </section>
      ) : null}

      {result.suggested_fix ? (
        <section className="detail__section">
          <h3>Suggested fix</h3>
          <p className="detail__fix">{result.suggested_fix}</p>
        </section>
      ) : null}

      {metrics.length > 0 ? (
        <section className="detail__section">
          <h3>Evidence</h3>
          <table className="metrics">
            <tbody>
              {metrics.map(([name, value]) => (
                <tr key={name}>
                  <th scope="row">{name.replace(/_/g, " ")}</th>
                  <td>{Number.isInteger(value) ? value : value.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {result.citations.length > 0 ? (
        <section className="detail__section">
          <h3>Cited law</h3>
          {result.citations.map((citation, index) => (
            <Evidence key={citation.chunk_id} citation={citation} lead={index === 0} />
          ))}
        </section>
      ) : (
        <section className="detail__section">
          <h3>Cited law</h3>
          <p className="muted">
            No citation, which is why this is an abstention: a verdict is only allowed to stand
            on a clause a reader can open.
          </p>
        </section>
      )}

      <section className="detail__section">
        <h3>Your decision</h3>
        <Feedback jobId={jobId} result={result} />
      </section>

      {result.llm_usage.length > 0 ? (
        <footer className="detail__trace">
          {result.llm_usage.length} model call{result.llm_usage.length === 1 ? "" : "s"} ·{" "}
          {result.llm_usage.map((usage) => usage.prompt_version).join(", ")} ·{" "}
          {result.duration_ms} ms
        </footer>
      ) : (
        <footer className="detail__trace">
          Deterministic rule · no model call · {result.duration_ms} ms
        </footer>
      )}
    </aside>
  );
}
