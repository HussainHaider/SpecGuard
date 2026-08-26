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
