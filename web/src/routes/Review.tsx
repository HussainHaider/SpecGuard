import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getCheck, submitCheck, type CheckReport } from "../api";
import Detail from "../components/Detail";
import Upload from "../components/Upload";
import VerdictList from "../components/VerdictList";
import { VERDICT_LABEL } from "../verdicts";

export default function Review() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const submit = useMutation({
    mutationFn: ({ file, language }: { file: File; language: string }) =>
      submitCheck(file, language),
    onSuccess: (accepted) => {
      setJobId(accepted.job_id);
      setSelected(null);
    },
  });

  const check = useQuery({
    queryKey: ["check", jobId],
    queryFn: () => getCheck(jobId!),
    enabled: jobId !== null,
    // A check is several model calls and takes tens of seconds, so the API answers
    // immediately with a job id and this polls. Polling stops the moment the job
    // reaches a terminal state.
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "succeeded" || status === "failed" ? false : 1500;
    },
  });

  const report: CheckReport | null = check.data?.report ?? null;
  const active = report?.results.find((result) => result.rule_id === selected) ?? null;

  return (
    <div className="review">
      <Upload
        onSubmit={(file, language) => submit.mutate({ file, language })}
        pending={submit.isPending}
        error={submit.isError ? (submit.error as Error).message : null}
      />

      {jobId && !report ? (
        <section className="progress">
          <p className="progress__status">
            <span className="progress__spinner" aria-hidden="true" />
            {check.data?.status === "running" ? "Checking" : "Queued"} — {check.data?.filename}
          </p>
          <p className="muted">
            Eight rules, four of them retrieval-backed. This normally takes twenty to forty
            seconds.
          </p>
          {check.data?.correlation_id ? (
            <p className="progress__correlation">
              correlation id <code>{check.data.correlation_id}</code>
            </p>
          ) : null}
        </section>
      ) : null}

      {check.data?.status === "failed" ? (
        <p className="notice notice--error">
          This check failed. {check.data.error ?? ""}
        </p>
      ) : null}

      {report?.demo ? (
        <section className="notice notice--demo">
          <strong>Replayed result.</strong>{" "}
          {report.demo_note ??
            "This report was computed in advance and is served from a fixture."}{" "}
          This deployment runs no model and indexes nothing; it exists so the output can be
          read without anyone paying for it.
        </section>
      ) : null}

      {report ? (
        <>
          <section className="summary">
            <div className="summary__verdict">
              <span className={`summary__badge summary__badge--${report.overall_verdict.toLowerCase()}`}>
                {VERDICT_LABEL[report.overall_verdict]}
              </span>
              <span className="muted">
                {report.counts.FAIL} failing · {report.counts.NEEDS_REVIEW} needing review ·{" "}
                {report.counts.PASS} passing
              </span>
            </div>
            <dl className="summary__meta">
              <div>
                <dt>Corpus</dt>
                <dd>{report.corpus_version}</dd>
              </div>
              <div>
                <dt>Graph</dt>
                <dd>{report.graph_version}</dd>
              </div>
              <div>
                <dt>Duration</dt>
                <dd>{(report.duration_ms / 1000).toFixed(1)} s</dd>
              </div>
              <div>
                <dt>Model cost</dt>
                <dd>${report.total_cost_usd.toFixed(4)}</dd>
              </div>
            </dl>
          </section>

          {report.guardrails.injection_suspected ? (
            <section className="notice notice--warn">
              <strong>This document contains text addressed to the model.</strong> It was
              recorded as a finding and treated as data, never followed. Spans:
              <ul className="signals">
                {report.guardrails.injection_signals.map((signal) => (
                  <li key={signal}>“{signal}”</li>
                ))}
              </ul>
            </section>
          ) : null}

          <div className="workspace">
            <VerdictList
              results={report.results}
              selected={selected}
              onSelect={(ruleId) => setSelected(ruleId === selected ? null : ruleId)}
            />
            <Detail jobId={check.data!.job_id} result={active} />
          </div>
        </>
      ) : null}
    </div>
  );
}
