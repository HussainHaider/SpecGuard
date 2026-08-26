import type { RuleResult, Verdict } from "../api";
import { VERDICT_LABEL, groupBySeverity, needsHumanReview, ruleTitle } from "../verdicts";

interface Props {
  results: RuleResult[];
  selected: string | null;
  onSelect: (ruleId: string) => void;
}

const CHIP: Record<Verdict, string> = {
  FAIL: "chip chip--fail",
  NEEDS_REVIEW: "chip chip--review",
  PASS: "chip chip--pass",
};

export default function VerdictList({ results, selected, onSelect }: Props) {
  return (
    <div className="verdicts">
      {groupBySeverity(results).map(([verdict, group]) => (
        <section key={verdict} className="verdicts__group">
          <h3 className="verdicts__heading">
            <span className={CHIP[verdict]}>{VERDICT_LABEL[verdict]}</span>
            <span className="verdicts__count">{group.length}</span>
          </h3>

          <ul className="verdicts__list">
            {group.map((result) => (
              <li key={result.rule_id}>
                <button
                  type="button"
                  className={`verdict${selected === result.rule_id ? " verdict--selected" : ""}`}
                  onClick={() => onSelect(result.rule_id)}
                  aria-current={selected === result.rule_id}
                >
                  <span className={`verdict__bar verdict__bar--${verdict.toLowerCase()}`} />
                  <span className="verdict__body">
                    <span className="verdict__title">
                      {ruleTitle(result.rule_id)}
                      {needsHumanReview(result) ? (
                        <span className="tag tag--escalated">Human review required</span>
                      ) : null}
                      {result.kind === "deterministic" ? (
                        <span className="tag">Deterministic</span>
                      ) : null}
                    </span>
                    <span className="verdict__rationale">{result.rationale}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
