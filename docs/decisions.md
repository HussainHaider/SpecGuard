# Decisions

Trade-offs taken during the build, newest last. One entry per decision, five lines:
**context** (what forced a choice), **options** (what was actually on the table),
**choice**, **cost** (what the choice gives up — every real decision has one).

An entry is written when a choice could reasonably have gone the other way. Routine
calls with an obvious default do not belong here; this file is for the ones a reviewer
would otherwise have to reverse-engineer from the code.

---

## 001 — chunk_id is a UUIDv5, not a hash string

- **Context:** Citations are stored in Postgres and must still resolve against a
  re-indexed Qdrant, and Qdrant point ids may only be an unsigned integer or a UUID.
- **Options:** (a) a truncated sha256 string plus a side table mapping it to a Qdrant
  point id; (b) a UUIDv5 derived from the same canonicalised tuple.
- **Choice:** UUIDv5 over `regulation|article|paragraph|source_version`, so the chunk id
  *is* the point id.
- **Cost:** The namespace UUID is now frozen for the life of the project — changing it
  invalidates every citation ever stored. The id is also opaque where a hash prefix
  would have been greppable against the corpus.

## 002 — Nutrition tolerance is configuration, not law

- **Context:** NUTRITION_ARITHMETIC must decide when a declared energy value is "close
  enough" to the value recomputed from the Annex XIV factors, but EU tolerance guidance
  for nutrition declarations is guidance, not binding regulation text.
- **Options:** (a) hard-code the guidance tolerances and cite them as if they were law;
  (b) treat the tolerance as a configured threshold and cite only the method.
- **Choice:** (b). The rule cites Annex XIV and Art. 31(3) for the *method*, and reports
  the computed delta and the threshold it applied in `RuleResult.metrics`.
- **Cost:** The verdict is only as defensible as the configured number, and a reviewer
  can move the threshold without any citation changing. Accepted, because the
  alternative is a citation that overstates what the regulation actually says.

## 003 — Postgres is the job queue; there is no broker

- **Context:** The worker runs check jobs asynchronously, but the compose stack is five
  services and a broker would be a sixth.
- **Options:** (a) Redis or RabbitMQ with a real queue; (b) poll a Postgres jobs table
  with `SELECT ... FOR UPDATE SKIP LOCKED`.
- **Choice:** (b). One datastore already exists for jobs, results and the audit trail,
  and the expected throughput is a handful of documents.
- **Cost:** Polling latency instead of push, no fan-out or retry semantics for free, and
  a rewrite if throughput ever becomes real. None of that is on this project's path.
