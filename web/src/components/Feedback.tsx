import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { sendFeedback, type RuleResult, type Verdict } from "../api";
import { VERDICT_LABEL } from "../verdicts";

interface Props {
  jobId: string;
  result: RuleResult;
}

/**
 * Accept the verdict, or say what it should have been.
 *
 * There is no thumbs-down. The API requires a corrected verdict because "this is wrong"
 * is not usable as an eval label while "this should have been PASS" is — and the
 * correction is attached to the LangSmith run that produced the verdict, so a
 * disagreement lands on the trace it disputes.
 */
export default function Feedback({ jobId, result }: Props) {
  const [overriding, setOverriding] = useState(false);
  const [comment, setComment] = useState("");

  const submit = useMutation({
    mutationFn: (verdict: Verdict) =>
      sendFeedback(jobId, {
        rule_id: result.rule_id,
        corrected_verdict: verdict,
        comment: comment.trim() || undefined,
      }),
  });

  if (submit.isSuccess) {
    return (
      <p className="notice notice--ok">
        Recorded. This correction is attached to the run that produced the verdict.
      </p>
    );
  }

  return (
    <div className="feedback">
      {submit.isError ? (
        <p className="notice notice--error">{(submit.error as Error).message}</p>
      ) : null}

      {overriding ? (
        <>
          <p className="feedback__prompt">What should this verdict have been?</p>
          <div className="feedback__choices">
            {(["PASS", "FAIL", "NEEDS_REVIEW"] as Verdict[])
              .filter((verdict) => verdict !== result.verdict)
              .map((verdict) => (
                <button
                  key={verdict}
                  type="button"
                  className="button button--quiet"
                  disabled={submit.isPending}
                  onClick={() => submit.mutate(verdict)}
                >
                  {VERDICT_LABEL[verdict]}
                </button>
              ))}
          </div>
          <label className="field">
            <span className="field__label">Why (optional)</span>
            <textarea
              rows={2}
              value={comment}
              maxLength={4000}
              placeholder="MILK is emphasised in the printed artwork."
              onChange={(event) => setComment(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="link-button"
            onClick={() => setOverriding(false)}
            disabled={submit.isPending}
          >
            Cancel
          </button>
        </>
      ) : (
        <div className="feedback__choices">
          <button
            type="button"
            className="button"
            disabled={submit.isPending}
            onClick={() => submit.mutate(result.verdict)}
          >
            Accept this verdict
          </button>
          <button type="button" className="button button--quiet" onClick={() => setOverriding(true)}>
            Override
          </button>
        </div>
      )}
    </div>
  );
}
