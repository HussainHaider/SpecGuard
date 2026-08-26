import { useQuery } from "@tanstack/react-query";

import { getClause, type Citation } from "../api";
import { highlight } from "../highlight";

/**
 * One cited clause, with the words the verdict rested on marked inside it.
 *
 * The clause is fetched rather than carried in the report: a citation stores the span a
 * rule relied on, not the article it came from, and a span shown without its surrounding
 * text cannot be judged. Reading it in context is the whole point of the panel.
 */
export default function Evidence({ citation, lead }: { citation: Citation; lead: boolean }) {
  const clause = useQuery({
    queryKey: ["clause", citation.chunk_id],
    queryFn: () => getClause(citation.chunk_id),
    staleTime: Infinity, // The corpus is a pinned consolidated act; it does not change.
  });

  const marked = clause.data ? highlight(clause.data.text, citation.quoted_span) : null;

  return (
    <article className="evidence">
      <header className="evidence__header">
        <h4 className="evidence__reference">
          {clause.data?.reference ?? `${citation.regulation} ${citation.article}`}
          {lead ? <span className="tag tag--lead">Verified</span> : null}
        </h4>
        {clause.data?.heading ? <p className="evidence__heading">{clause.data.heading}</p> : null}
      </header>

      {clause.isPending ? <p className="muted">Loading the cited clause…</p> : null}

      {clause.isError ? (
        <p className="notice notice--error">
          This citation did not resolve against the indexed corpus. That is a defect worth
          reporting: a verdict is only allowed to stand on a clause a reader can open.
        </p>
      ) : null}

      {marked ? (
        <>
          <blockquote className="evidence__text">
            {marked.found ? (
              <>
                {marked.before}
                <mark className="evidence__span">{marked.match}</mark>
                {marked.after}
              </>
            ) : (
              marked.before
            )}
          </blockquote>

          {!marked.found ? (
            <p className="notice notice--warn">
              The quoted span could not be located in this clause, so nothing is highlighted.
              The span is shown below as the rule recorded it.
              <span className="evidence__fallback">“{citation.quoted_span}”</span>
            </p>
          ) : null}
        </>
      ) : null}

      <footer className="evidence__meta">
        <span>{citation.source_version}</span>
        {citation.retrieval_score !== null ? (
          <span>fused score {citation.retrieval_score.toFixed(3)}</span>
        ) : (
          <span>fixed clause, not retrieved</span>
        )}
        {citation.eurlex_url ? (
          <a href={citation.eurlex_url} target="_blank" rel="noreferrer">
            Read on EUR-Lex
          </a>
        ) : null}
      </footer>
    </article>
  );
}
