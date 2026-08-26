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

## 003 — Postgres is the job queue; there is no broker (superseded by 013)

- **Context:** The worker runs check jobs asynchronously, but the compose stack is five
  services and a broker would be a sixth.
- **Options:** (a) Redis or RabbitMQ with a real queue; (b) poll a Postgres jobs table
  with `SELECT ... FOR UPDATE SKIP LOCKED`.
- **Choice:** (b). One datastore already exists for jobs, results and the audit trail,
  and the expected throughput is a handful of documents.
- **Cost:** Polling latency instead of push, no fan-out or retry semantics for free, and
  a rewrite if throughput ever becomes real. None of that is on this project's path.

## 004 — Language rides inside `source_version`

- **Context:** The corpus is indexed in English and German. `chunk_id_for()` derives an
  id from (regulation, article, paragraph, source_version) and has no language
  component, so the two language versions of Article 9 hash to the same Qdrant point id
  and one silently overwrites the other.
- **Options:** (a) add language as a fifth component of the derivation; (b) define
  `source_version` as the document's full identity — consolidated act plus language.
- **Choice:** (b). `source_version` becomes `02011R1169-20180101-en`, because a clause's
  source genuinely *is* one language version of one consolidated act.
- **Cost:** `source_version` now carries two facts in one string and has to be parsed to
  recover either. Accepted, because (a) would break the four-field derivation that
  non-negotiable #3 documents as the guarantee holding the datastore split together.

## 005 — The stack's embedding model does not exist in fastembed

- **Context:** CLAUDE.md specifies `intfloat/multilingual-e5-small`. fastembed 0.8 does
  not ship it; the only e5 available is `multilingual-e5-large`, at 1024 dimensions and
  2.24 GB.
- **Options:** (a) take e5-large and keep the family; (b) pin an older fastembed; (c)
  default to `paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensions and 0.22 GB.
- **Choice:** (c) as the default, with e5-large supported and selected by
  `DENSE_EMBEDDING_MODEL`. The e5 `passage:`/`query:` prefixes are a property of the
  model spec, so they apply to e5 and not to models never trained with them.
- **Cost:** a deviation from the documented stack, and MiniLM is a weaker multilingual
  model than e5-large. Ten times the image and CI footprint is not worth that for 734
  clauses, and the switch is one line of config if the eval later says otherwise.

## 006 — Annex sub-headings are chunk locators

- **Context:** Regulation 1924/2006 has one unnumbered `ANNEX` whose conditions of use
  are separated by all-caps headings ("SOURCE OF FIBRE", "LOW FAT"), with no numbering
  for `NUTRITION_CLAIM_CONDITIONS` to cite.
- **Options:** (a) index the annex as one chunk; (b) split on the all-caps headings and
  use the heading text as the paragraph locator.
- **Choice:** (b). Each claim's conditions of use becomes an independently citable
  chunk, which is the granularity the rule actually reasons at.
- **Cost:** those locators are the heading text, so they are language-specific — the
  English and German citations to the same conditions read differently. Numbered
  locators stay language-independent; only sub-headings do this.

## 007 — Temperature 0 is not expressible on current Claude models

- **Context:** Non-negotiable #7 requires "temperature 0, schema-constrained output on
  every model call". The current Claude models removed the sampling parameters
  entirely — `temperature` is rejected with a 400, and the Python SDK no longer accepts
  the keyword at all.
- **Options:** (a) pin an older Claude model that still takes `temperature`; (b) drop
  the requirement for Anthropic and keep it where it is supported.
- **Choice:** (b). OpenAI is the primary provider and still accepts `temperature`, so
  every OpenAI call is made at 0. Anthropic calls instead rely on schema-constrained
  output plus `output_config.effort`, which is what replaced the sampling knob.
- **Cost:** the "temperature 0 everywhere" guarantee is now provider-dependent and
  CLAUDE.md overstates it. Schema constraint — the half that actually bounds the output
  shape — still holds on every call, and the protocol has no free-text method, so no
  caller can bypass it.

## 008 — Extraction fixtures are recorded, not hand-authored

- **Context:** The default test suite may not reach a live API, so FakeClient replays
  fixtures. Those fixtures can either be written by hand or recorded from a real call.
- **Options:** (a) hand-author them from the generator's ground truth, which is exact and
  free; (b) record real responses once and commit them.
- **Choice:** (b), with `specguard.llm.record` doing the recording and hand-authoring
  kept only for a schema that has never been called.
- **Cost:** roughly $0.73 per full re-record, fixtures that pin one model's behaviour,
  and a re-record needed whenever the prompt or schema changes. Worth it: hand-authored
  fixtures contain only what the author already believed the model would say, and the
  first recorded run immediately exposed two bugs invisible to them — a required
  `address` field that forced the model to fabricate a missing operator address, and
  emphasis detection that missed partially-capitalised German compounds.

## 009 — The abstention threshold is uncalibrated

- **Context:** Rules abstain below `MIN_EXTRACTION_CONFIDENCE` (0.60) so a badly-read
  field never becomes a FAIL against the supplier. Across 178 fields from 30 real
  extractions, the model never reported below 0.80.
- **Options:** (a) raise the threshold until it fires; (b) leave it and record that the
  path is untested by real output; (c) drop self-reported confidence for a computed
  signal.
- **Choice:** (b) for now, asserted by a test that xfails with the measured distribution
  rather than passing silently.
- **Cost:** the guardrail is currently exercised only by synthetic specs. A threshold
  tuned to a number the model never emits is decorative, and picking one now would be
  fitting to 30 documents from one generator. Revisit in M5 with the eval set.

## 010 — No cross-encoder reranker

- **Context:** A cross-encoder rerank over the fused candidates is the standard next
  quality lever in a hybrid RAG pipeline, and retrieval quality directly bounds what the
  judge can conclude.
- **Options:** (a) add a cross-encoder over the top ~50 fused candidates; (b) rely on
  dense + sparse RRF alone and spend the effort on verification instead.
- **Choice:** (b), deliberately deferred rather than overlooked. The corpus is 734
  clauses of one legal domain, retrieval already returns the governing clause at rank 1
  for the queries the rules actually issue, and a reranker adds a model download, a
  second inference hop per query and latency to every RAG rule.
- **Cost:** recall@5 is whatever RRF gives us, with no second opinion on ranking. The
  honest reason to skip it here is that a wrong verdict in this system comes from
  *reasoning over* a clause rather than from failing to retrieve it — which is why the
  verification pass got the effort instead. Revisit if M5's eval shows retrieval misses
  rather than judgement errors.

## 011 — PASS verdicts are harder to cite than FAIL verdicts

- **Context:** Across 112 rule evaluations on real model output, verification supported
  30 citations and rejected 40 as `insufficient` — and on inspection the verifier was
  right every time. Support rates split sharply by rule:
  `NUTRITION_CLAIM_CONDITIONS` 4/4, `ORIGIN_DECLARATION` 20/32,
  `LEGAL_NAME_AND_QUID` 5/31.
- **Cause, which is structural rather than a prompt defect:** citing a breach is easy —
  you quote the obligation that was broken. Citing compliance often means establishing
  that *no obligation arose*, and a clause stating a conditional requirement cannot
  prove its condition was absent. The conditions of use in the 1924/2006 Annex verify
  perfectly because each entry states a self-contained threshold; Art. 22 does not,
  because *when* QUID is required (22(1)), *how* it must be given (Annex VIII) and when
  it is exempt (22(2)) are three different clauses.
- **Options:** (a) relax the verifier; (b) let a rule reach its PASS through the
  not-applicable path, citing the governing provision, when the requirement was never
  triggered; (c) split the compound rules.
- **Choice:** not (a) — the verifier is the reason no wrong verdict was produced in 112
  evaluations, and every mismatch was an abstention rather than a false PASS or FAIL.
  Leaning toward (b), with (c) for LEGAL_NAME_AND_QUID, which asks two questions at once.
- **Resolution (measured, verify@v2):** the verifier was being asked whether a clause
  *proves compliance*. It cannot: compliance is a fact about the specification, and a
  clause's job is to establish what the obligation is. v2 asks for legal grounding
  instead, while still rejecting a clause about a different obligation. Correct verdicts
  went from 74/112 to 96/120, and `NUTRITION_CLAIM_CONDITIONS` held at 4/4 → 5/5, which
  is what distinguishes a discriminating change from a blanket-permissive one.
- **Cost:** one wrong verdict appeared where there had been none — a false FAIL on a
  compliant spec whose origins are both declared. The safer direction to err, but still
  an error, and worth stating plainly: v1's clean record came from abstaining on 82% of
  one rule, which suppresses bad judgements by accident rather than catching them.

## 012 — A defect must implement what it claims

- **Context:** Two specs flipped to false PASS under verify@v2, both on
  `brand_name_as_legal_name`. The defect built the name as `f"{product_name} Selection"`
  — which keeps every descriptive word and appends a marketing one.
- **Cause:** Art. 17 accepts a descriptive name, so "Pasta Sauce with Mushrooms
  Selection" is arguably compliant. The fixture asserted a failure a careful reviewer
  would dispute, and the model was right to pass it.
- **Choice:** fix the fixture, not the verifier. The defect now uses names with no
  descriptive content at all ("Bella Selezione", "Chef's Reserve"), which is what "a
  brand name in place of the legal name" actually means. Both false PASSes disappeared.
- **Cost:** ground truth had to be revised after a model disagreed with it, which is one
  step away from fitting the test to the answer. The distinction that makes it
  legitimate: the defect did not implement its own stated description, the same failure
  as seeding an allergen defect on a product with no allergens (decision 011's
  neighbour in M2). Generation is deterministic, so only the two affected PDFs changed.


## 013 — Redis, superseding the Postgres job queue

- **Context:** Decision 003 chose to poll a Postgres jobs table specifically to avoid a
  sixth compose service. M4 specifies arq for background execution, and arq is a Redis
  library — there is no Postgres backend for it.
- **Options:** (a) keep the Postgres queue and drop arq; (b) take arq and add Redis.
- **Choice:** (b). The reasoning in 003 was never "Postgres is the better queue", it was
  "one fewer service"; once a broker is asked for, that argument is spent. arq also
  brings job timeouts, retries and concurrency limits that the polling loop would have
  had to grow anyway.
- **Cost:** a sixth service, and Redis is now on the path for submitting a check. The
  API degrades rather than fails when it is down — reports already stored stay readable
  and /healthz reports the problem — but nothing new can be queued.

## 014 — Uploads are scratch, reports are the record

- **Context:** The worker needs the PDF on disk, and something has to decide how long it
  stays there.
- **Options:** (a) keep every upload for reproducibility; (b) delete it once the check
  succeeds, keeping the sha256 and the report.
- **Choice:** (b). The report is what a person read and is what an audit needs; the
  hash identifies the document that produced it.
- **Cost:** a report cannot be regenerated from scratch after the fact without the
  original file being resubmitted. Retaining third-party specification sheets after the
  work is done is a liability rather than an asset, and the hash still proves which
  document a report describes.

## 015 — The golden set is two files, because the labels are not equally strong

- **Context:** Tier 1 needs both verdict labels and retrieval labels, and the milestone
  brief called for one file as the single source of truth.
- **Options:** (a) one file, with relevant chunk ids hanging off each verdict record;
  (b) two files — `rules.jsonl` for verdicts, `retrieval.jsonl` for queries.
- **Choice:** (b). A verdict label is mechanical: the generator seeded a named defect and
  recorded which rule should catch it, so the label is derived from how the document was
  built. A retrieval anchor is a judgement about which article decides a question, written
  out in `evals/build_golden.py` and checked to exist in the corpus, but a judgement all
  the same. Putting both in one record would give them one `provenance` block and imply
  one provenance.
- **Cost:** two files to keep in step, and "single source of truth" now means one
  directory rather than one file. Worth it: the alternative was a retrieval label quietly
  inheriting the credibility of a verdict label, and a recall number gating a build on the
  strength of somebody's opinion about Art. 22.

## 016 — The split is stratified by defect, and holds out more than it should

- **Context:** 30 specifications carry 20 seeded defects between them, some rules having
  only two failure cases in the entire set. A random 70/30 split puts both of a rule's
  failures on the same side often enough to matter.
- **Options:** (a) a random split by specification; (b) group specifications by their
  defect signature and hold out every third within each group.
- **Choice:** (b), with the split assigned per specification either way — a spec's eight
  outcomes share one document and one extraction, so splitting between them would put the
  same evidence on both sides and make the held-out figure a second reading of the dev one.
- **Cost:** the groups are small, so "every third within a group" holds out 13 of 30 specs
  — 43%, well above the conventional 30%, and the dev split is correspondingly thin. Taken
  deliberately: every rule's failures now appear in both splits, and a held-out
  false-negative rate that is *defined* beats a larger dev set that cannot answer the
  question the metric exists to ask.

## 017 — Latency is reported as absent; cost is reconstructed

- **Context:** The tier 1 eval runs offline against recorded fixtures, and the milestone
  asks for p50/p95 latency and cost per spec. A replay has neither.
- **Options:** (a) report the replay's own wall time; (b) report zero; (c) report nothing
  for latency and reconstruct cost from the recorded token counts.
- **Choice:** (c). Fixtures store real input and output token counts, so pricing them at
  the model that produced them gives a cost that is real money really spent —
  $0.0313 for a complete eight-rule check. Latency cannot be reconstructed that way, so it
  prints as `—` and the fixture format now records `latency_ms` and `cost_usd` for
  everything captured from here on.
- **Cost:** the headline table has two empty rows until the fixtures are re-recorded, and
  a reviewer has to run `--live` to see a latency figure at all. Both alternatives were
  worse in the same direction: (a) and (b) each say this system answers in under a
  millisecond, which is a lie that a replay makes very easy to tell by accident.

## 018 — pgvector is implemented and not yet measured

- **Context:** CLAUDE.md sanctions the `VectorStore` protocol on the grounds that the
  Qdrant/pgvector comparison is a deliverable. `PgVectorStore` and
  `evals/benchmark_retrieval.py` now exist and score both stores on the same golden
  queries and the same relevance labels the tier 1 eval uses.
- **Options:** (a) run the benchmark elsewhere and paste in numbers; (b) estimate from
  the shape of the two implementations; (c) ship the comparison unrun and say so.
- **Choice:** (c). Docker is not available on the machine this was written on, so neither
  store could be brought up. The brief for this work said the honest finding is worth more
  than a fabricated one, and an estimate presented as a measurement is the fabrication it
  was warning about. Run `docker compose up -d db qdrant` then
  `uv run python -m evals.benchmark_retrieval --seed` and paste the table here.
- **What can be said without measuring:** the comparison is not "the same hybrid search on
  different storage". Qdrant's lexical half is a bm25 vector from fastembed; Postgres has
  no bm25, so it is `ts_rank_cd` over a `tsvector` with its own stemming. Fusion is
  Qdrant's own RRF server-side, and hand-written SQL for Postgres — the project forbids
  application-layer fusion, and this is that rule's cost made visible rather than broken.
  Any recall difference is therefore partly a difference between two lexical retrievers,
  which is worth stating before the numbers rather than after them.
- **Cost:** an unrun benchmark in the repository is a claim nobody has checked, and it
  will stay that way until someone with Docker runs one command. At 734 clauses a latency
  difference would be noise in any case; recall is the only column worth reading.
- **Measured** (2026-08-26, both stores seeded from the same 734 clauses and scored on the
  same 58 golden queries, three runs):

  | store | recall@5 | hit rate@5 | p50 | p95 |
  |---|---|---|---|---|
  | Qdrant — dense + bm25, server-side RRF | 57.2% | 75.9% | ~70 ms | ~87 ms |
  | pgvector — dense + tsvector, RRF in SQL | 46.8% | 56.9% | ~47 ms | ~66 ms |

  Recall was byte-identical across all three runs; latency varied by a few milliseconds
  after the first, warm-up run. Qdrant's 57.2% / 75.9% is exactly the figure the tier 1
  eval reports, which is the check that matters: the benchmark and the eval are measuring
  the same thing on the same labels rather than two things that happen to agree.

  **The recall gap is real and it is not about storage.** Qdrant's lexical half is a bm25
  vector from fastembed; Postgres has no bm25, so it is `ts_rank_cd` over a `tsvector`
  with its own stemming. That is a different retriever, and 10 points of recall is what it
  costs here. Anyone reading this as "Qdrant beats Postgres" has read it wrong.

  **On latency, the honest answer is that neither store is the expensive part.** pgvector
  is about 23 ms faster at p50 and that is not a win worth claiming: dense query encoding
  alone is 22.1 ms, which is roughly half of pgvector's entire 47 ms and a third of
  Qdrant's 70 ms. The thing to optimise, if anyone needed to, is the encoder.

  One hypothesis died here, which is the reason for measuring rather than reasoning: the
  gap was expected to be Qdrant paying for a second query encoding. It is not — bm25 query
  encoding measures 0.0 ms, because it is a lexical tokeniser and not a neural model.
- **Standing:** the abstraction stays, and so does Qdrant. Not because it is faster — it is
  not — but because the retrieval it makes available is better on the metric that bounds
  what the judge can conclude, and because fusion stays in the engine rather than in our
  code.

## 019 — The evidence panel needed an endpoint that did not exist

- **Context:** M6 specifies an evidence panel showing "the cited article text with the
  relied-on span highlighted". A `Citation` carries `quoted_span` — the words a rule
  relied on — but not the clause they came from, and nothing in the API could return it.
- **Options:** (a) show only the span, which is what the report already contains;
  (b) put the full clause text in every citation in the report; (c) add
  `GET /clauses/{chunk_id}`.
- **Choice:** (c), served from the corpus loaded once in the API process. (a) is not the
  feature — a span with no surrounding text cannot be judged, and the panel exists so a
  reviewer can see what the verdict rested on *in context*. (b) would put several
  kilobytes of duplicated legal text into every stored report, for text that is already
  pinned and immutable.
- **Cost:** a sixth endpoint, where M4 specified five, and the API now loads 734 clauses
  at startup. The clauses were already being loaded by the worker for exactly this kind of
  resolution check, and the alternative was a report that got larger every time a rule
  cited an extra clause.

## 020 — A span that cannot be located is not highlighted

- **Context:** The quoted span rarely matches the stored clause byte for byte: the text a
  rule was shown had been re-wrapped, so line breaks and runs of spaces differ even when
  the words are identical. A plain `indexOf` finds nothing most of the time.
- **Options:** (a) fuzzy-match the span and highlight the closest region; (b) normalise
  whitespace, search on the normalised copy, and map the result back to real source
  offsets; (c) show the span beside the clause without highlighting anything.
- **Choice:** (b), which is the same rule the backend's verification pass applies, so the
  UI and the verifier agree about what counts as a quote. Where it fails to match, the
  panel says so and falls back to (c) rather than guessing.
- **Cost:** a paraphrase highlights nothing, and a reviewer may read that as a defect in
  the tool. It is the right way round: the highlight is evidence, and marking text the
  rule did not actually quote would misrepresent what the verdict rested on — which is the
  one thing this panel exists to show honestly.

## 021 — n8n refuses to start without an encryption key

- **Context:** n8n encrypts saved credentials with `N8N_ENCRYPTION_KEY`. Left unset it
  generates one into its own volume on first start, and every credential saved under that
  key becomes permanently unreadable after a volume reset or on a fresh clone.
- **Options:** (a) let n8n generate a key and document the risk; (b) default it to a fixed
  string in compose; (c) require it, so compose refuses to start without one.
- **Choice:** (c), via `${N8N_ENCRYPTION_KEY:?}`. (a) is the failure mode itself. (b) is
  worse than (a): a shared default that ships in a public repository is a credential store
  anyone can decrypt, and it looks like it was handled.
- **Cost:** the guard is evaluated when compose parses the file, so an unset key breaks
  *every* compose command, including ones that have nothing to do with n8n. That cost is
  paid once, by `cp .env.example .env`, and the alternative is losing credentials silently
  at the worst possible moment.

## 022 — The watcher compares quoted spans, not chunk ids

- **Context:** The weekly regulation watcher re-indexes and then has to decide which stored
  checks to re-run. Consolidated acts are immutable, so an amendment arrives as a new CELEX
  id, therefore a new `source_version`, therefore a new `chunk_id` for every clause in the
  act.
- **Options:** (a) re-run every stored check after any corpus change; (b) compare stored
  citation ids against the new index; (c) match on clause coordinates — regulation, article,
  paragraph — and then check whether the words the verdict relied on still appear.
- **Choice:** (c). (a) is wasteful and, on a real corpus, expensive. (b) looks precise and
  is useless: after any consolidation *no* stored id matches, so every check is flagged and
  the watcher is (a) with extra steps.
- **Cost:** a clause that is renumbered but textually unchanged is treated as unchanged,
  which is right for the verdict and wrong for the citation a reader clicks — they will be
  sent to an article number that has moved. Accepted: the alternative flags everything, and
  a watcher that always says "re-run all of it" is one nobody will leave switched on.

## 023 — The public deployment cannot call a model at all

- **Context:** The deployed instance is public. A real upload endpoint on it means a
  stranger's PDF spending money on model calls and a queue anyone can fill.
- **Options:** (a) deploy the real pipeline behind basic auth and a rate limit; (b) deploy
  with a spending cap on the provider account; (c) serve pre-computed reports and call
  nothing.
- **Choice:** (c). Reports are matched to uploads by content hash, so an unknown document
  is refused rather than answered with somebody else's verdicts, and every replayed report
  is labelled as replayed in the API response and in the UI.
- **Cost:** a visitor cannot check their own document, which is the thing the tool does.
  Mitigated by shipping the specifications that *do* work, including deliberately defective
  ones. The guarantee is structural rather than a promise — `specguard.demo` imports no LLM
  factory and no vector store, and a test asserts that by reading the module's source, so
  there is no configuration under which the public instance starts spending.

## 024 — Three defects that only existed when the stack ran

- **Context:** Through M4 to M6 the compose stack was never actually started; the tests
  stub the queue and the graph runs in-process. Bringing it up for the first time at M7
  found three separate faults in the same path, none of which any test could have caught.
- **What they were:** arq reads `WorkerSettings.redis_settings` as a value and it was
  declared as a `@staticmethod`, so the worker died on startup; the API wrote uploads to a
  container-local `/tmp` that the worker could not see; and `config` derived a repository
  root by walking up from `__file__`, which lands on `/` inside the image, so the corpus and
  fixture paths were both wrong in Docker.
- **Choice:** fix all three, and add tests that assert the *wiring* rather than the
  behaviour — that the enqueued name matches a registered worker function, that
  `redis_settings` is a value, that the compose command runs arq. Those are the assertions
  that would have failed in CI.
- **Cost:** three tests that look like they are testing configuration rather than code,
  which is exactly what they are. The general lesson is the entry in this file: a path that
  is never executed is not covered by anything, and "the tests pass" said nothing at all
  about whether this system could start.

## 025 — The vector store is disposable, and that is a design property

- **Context:** Pinning the Qdrant image to match the client (the mismatch was warning on
  every seed) broke an existing volume: v1.19 could not read segments written by v1.12,
  and the container restart-looped on `unknown variant \`on_disk\``.
- **Options:** (a) stay on the old image and live with the client warning; (b) migrate the
  volume; (c) drop the volume and re-index.
- **Choice:** (c), in about a minute. There is no migration to write because there is
  nothing in Qdrant that is not derived: the corpus text is committed, chunk ids are
  deterministic, and `corpus.seed` reproduces the collection exactly — including the ids
  that citations stored in Postgres resolve against.
- **Cost:** a Qdrant upgrade is a brief outage rather than a rolling one, and anyone who
  assumed the vector store was durable state will be surprised once. Worth stating plainly
  because it is the same property the weekly re-index depends on: if re-indexing could not
  reproduce ids, neither the upgrade path nor the regulation watcher would be safe.
  Postgres is the durable store; Qdrant is a cache with good manners.
