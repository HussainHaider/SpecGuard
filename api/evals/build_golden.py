"""Build the golden set from the fixture manifest and the recorded searches.

Run deliberately, not from a test:

    uv run python -m evals.build_golden

Deterministic — same inputs, byte-identical output — so re-running it on an unchanged
manifest produces an empty diff, and any diff it does produce is a change to ground
truth that a reviewer is meant to read.

Two labelling routes, and they are not equally strong:

* **Verdicts** are mechanical. The generator seeded a named defect and recorded which
  rule should catch it, so the label is derived from the document's construction rather
  than from anyone's opinion about it, and it cannot drift from what the PDF contains.
* **Retrieval anchors** are a judgement: which clause actually decides this question.
  They are written out below as explicit clause references, checked to exist in the
  indexed corpus, and rendered into each record in human-readable form so the claim can
  be disputed. That is the honest ceiling here — nobody hand-labelled 734 clauses for
  relevance, and a record that implied otherwise would be worse than one that says so.

Neither route may take a label from this system's own output. A verdict the pipeline
produced, fed back as ground truth, measures only that the pipeline agrees with itself.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import evals.pipeline as pipeline
from evals.golden import (
    RETRIEVAL_PATH,
    RULES_PATH,
    GoldenRetrieval,
    GoldenRule,
    Provenance,
    SeededDefect,
    Split,
    write_jsonl,
)
from specguard.config import get_settings
from specguard.corpus.seed import load_clauses
from specguard.corpus.sources import source_version_for
from specguard.fixtures.generate import SpecFixture
from specguard.models.citation import chunk_id_for
from specguard.models.common import Language
from specguard.models.rule import RuleId, Verdict
from specguard.models.spec import ProductSpec
from specguard.retrieval.query import build_query
from specguard.rules.registry import rag_rules
from specguard.vectorstore.fixtures import search_key

MANIFEST = pipeline.SPEC_DIR / "manifest.jsonl"

#: Target size of the verdict file. Every seeded failure is kept; the rest of the budget
#: goes to PASS records, sampled across rule and language rather than taken in file
#: order, which would over-weight whichever products happen to sort first.
TARGET_RULE_RECORDS = 80

_1169 = "Regulation (EU) No 1169/2011"
_1924 = "Regulation (EC) No 1924/2006"

#: The clauses that decide each RAG rule — the ones a compliance officer would cite.
#: Article and paragraph only: a chunk id is derived from them, so this table is
#: readable and the ids in the golden file are not hand-copied.
ANCHORS: dict[RuleId, tuple[tuple[str, str, str], ...]] = {
    RuleId.NUTRITION_CLAIM_CONDITIONS: ((_1924, "8", "1"),),
    RuleId.HEALTH_CLAIM_AUTHORISED: (
        (_1924, "10", "1"),
        (_1924, "13", "1"),
        (_1924, "14", "1"),
    ),
    RuleId.ORIGIN_DECLARATION: ((_1169, "26", "2"), (_1169, "26", "3")),
    RuleId.LEGAL_NAME_AND_QUID: (
        (_1169, "17", "1"),
        (_1169, "22", "1"),
        (_1169, "Annex VIII", "1"),
    ),
}

#: A nutrition claim is judged against its own entry in the 1924/2006 Annex, and those
#: entries are located by their heading (docs/decisions.md 006) — which is language
#: specific. So the anchor for NUTRITION_CLAIM_CONDITIONS depends on the claim made,
#: and this is the mapping from the claims the fixtures actually use.
CLAIM_HEADINGS: dict[str, str] = {
    "source of fibre": "SOURCE OF FIBRE",
    "high in fibre": "HIGH FIBRE",
    "high in protein": "HIGH PROTEIN",
    "ballaststoffquelle": "BALLASTSTOFFQUELLE",
    "hoher ballaststoffgehalt": "HOHER BALLASTSTOFFGEHALT",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _split_for(spec_id: str, group: list[str]) -> Split:
    """Hold out every third specification within its defect group, starting at the second.

    The split is assigned per specification and per defect signature, for two reasons.
    A spec is atomic because its eight rule outcomes share one document and one
    extraction — splitting between them would put the same evidence on both sides and
    make the held-out number a re-run of the dev one. Grouping by defect signature then
    stops a rule's only two failure cases both landing in dev, which would leave the
    held-out false-negative rate for that rule undefined rather than merely small.
    """
    return Split.HELD_OUT if group.index(spec_id) % 3 == 1 else Split.DEV


def split_map() -> dict[str, Split]:
    """Every specification's split, computed once and shared by both files.

    Derived from the manifest rather than from whichever records happened to be sampled,
    so a spec that contributes no verdict record still has a split for its queries.
    """
    entries = {entry.spec_id: entry for entry in pipeline.load_manifest(MANIFEST)}
    groups: dict[frozenset[RuleId], list[str]] = defaultdict(list)
    for spec_id, entry in sorted(entries.items()):
        groups[frozenset(defect.rule_id for defect in entry.seeded_defects)].append(spec_id)
    return {
        spec_id: _split_for(spec_id, members) for members in groups.values() for spec_id in members
    }


def build_rule_records(provenance: Provenance) -> list[GoldenRule]:
    """Every seeded failure, plus a stratified sample of passes."""
    entries = {entry.spec_id: entry for entry in pipeline.load_manifest(MANIFEST)}
    splits = split_map()

    defect_for = {
        (entry.spec_id, defect.rule_id): defect
        for entry in entries.values()
        for defect in entry.seeded_defects
    }

    def record(spec_id: str, rule_id: RuleId, verdict: Verdict) -> GoldenRule:
        entry = entries[spec_id]
        defect = defect_for.get((spec_id, rule_id))
        return GoldenRule(
            golden_id=f"GOLD-{spec_id.removeprefix('SPEC-')}-{rule_id.value}",
            split=splits[spec_id],
            spec_id=spec_id,
            filename=entry.filename,
            rule_id=rule_id,
            language=entry.language,
            expected_verdict=verdict,
            defect=(
                SeededDefect(kind=defect.kind, detail=defect.detail) if defect is not None else None
            ),
            adversarial=entry.adversarial,
            provenance=provenance.model_copy(update={"spec_sha256": entry.sha256}),
        )

    failures = [
        record(entry.spec_id, rule_id, verdict)
        for entry in sorted(entries.values(), key=lambda e: e.spec_id)
        for rule_id, verdict in sorted(entry.expected_verdicts.items(), key=lambda kv: kv[0].value)
        if verdict is Verdict.FAIL
    ]

    # Passes, round-robined across (rule, language) so no rule drops out of the sample,
    # and within a stratum taking specs that failed *something else* first: a pass on a
    # document known to contain a defect is a harder negative than a pass on a clean one.
    strata: dict[tuple[RuleId, Language], list[str]] = defaultdict(list)
    for entry in sorted(entries.values(), key=lambda e: (not e.seeded_defects, e.spec_id)):
        for rule_id, verdict in sorted(entry.expected_verdicts.items(), key=lambda kv: kv[0].value):
            if verdict is Verdict.PASS:
                strata[(rule_id, entry.language)].append(entry.spec_id)

    order = sorted(strata, key=lambda k: (k[0].value, k[1].value))
    passes: list[GoldenRule] = []
    depth = 0
    while len(passes) < TARGET_RULE_RECORDS - len(failures):
        added = False
        for key in order:
            if depth < len(strata[key]) and len(passes) < TARGET_RULE_RECORDS - len(failures):
                passes.append(record(strata[key][depth], key[0], Verdict.PASS))
                added = True
        if not added:
            break
        depth += 1

    return [*failures, *passes]


def _anchor_ids(rule_id: RuleId, spec: ProductSpec, known: set[str]) -> tuple[list[str], list[str]]:
    """The chunk ids and readable references this query should retrieve."""
    clauses = list(ANCHORS[rule_id])

    if rule_id is RuleId.NUTRITION_CLAIM_CONDITIONS:
        for claim in spec.claims:
            heading = CLAIM_HEADINGS.get(claim.value.text.strip().casefold())
            if heading is not None:
                clauses.append((_1924, "Annex", heading))

    ids: list[str] = []
    references: list[str] = []
    for regulation, article, paragraph in clauses:
        source_version = source_version_for(regulation, spec.language)
        chunk_id = chunk_id_for(regulation, article, paragraph, source_version)
        if chunk_id not in known:
            # An anchor that is not in the corpus would silently depress recall and look
            # like a retrieval defect. It is a labelling defect, and it fails here.
            raise ValueError(
                f"anchor {regulation} {article} ({paragraph}) for {rule_id.value} is not "
                f"in the indexed corpus at {source_version}"
            )
        ids.append(chunk_id)
        references.append(f"{regulation} {article}({paragraph})")
    return ids, references


def build_retrieval_records(
    provenance: Provenance, splits: dict[str, Split]
) -> list[GoldenRetrieval]:
    """One record per distinct query the RAG rules issue over the fixture set."""
    settings = get_settings()
    known = {clause.chunk_id for clause in load_clauses(settings.corpus_dir)}

    # Gathered mutably first: several specifications produce the same query, and the
    # record for it is only complete once every one of them has been seen.
    gathered: dict[str, dict[str, object]] = {}
    for entry, spec in pipeline.load_specs():
        assert isinstance(spec, ProductSpec)
        for rule_id in sorted(rag_rules(), key=lambda r: r.value):
            query = build_query(rule_id, spec)
            if not query:
                continue

            key = search_key(query, spec.language, settings.retrieval_top_k, None)
            found = gathered.get(key)
            if found is not None:
                found["spec_ids"].append(entry.spec_id)  # type: ignore[union-attr]
                continue

            ids, references = _anchor_ids(rule_id, spec, known)
            gathered[key] = {
                "rule_id": rule_id,
                "language": spec.language,
                "query": query,
                "search_key": key,
                "relevant_chunk_ids": ids,
                "relevant_references": references,
                "spec_ids": [entry.spec_id],
                "corpus_version": source_version_for(_1169, spec.language),
            }

    records: list[GoldenRetrieval] = []
    for key, payload in gathered.items():
        spec_ids = sorted(payload["spec_ids"])  # type: ignore[call-overload]
        rule_id = payload["rule_id"]
        records.append(
            GoldenRetrieval(
                golden_id=f"RET-{rule_id.value}-{key[:8]}",  # type: ignore[union-attr]
                # Held out only when every specification that issues this query is. A
                # query the dev set also asks is not held-out evidence in any useful sense.
                split=(
                    Split.HELD_OUT
                    if all(splits[s] is Split.HELD_OUT for s in spec_ids)
                    else Split.DEV
                ),
                rule_id=rule_id,  # type: ignore[arg-type]
                language=payload["language"],  # type: ignore[arg-type]
                query=payload["query"],  # type: ignore[arg-type]
                search_key=key,
                relevant_chunk_ids=payload["relevant_chunk_ids"],  # type: ignore[arg-type]
                relevant_references=payload["relevant_references"],  # type: ignore[arg-type]
                spec_ids=spec_ids,
                provenance=provenance.model_copy(
                    update={
                        "source": "recorded-search",
                        "labelled_by": "clause anchors from docs/plan.md, verified present "
                        "in the indexed corpus",
                        "corpus_version": payload["corpus_version"],
                        "spec_sha256": None,
                    }
                ),
            )
        )
    return records


def build_external_records(today: str) -> list[GoldenRule]:
    """The records EU law labels, not this repository.

    Every other record in the set is labelled by the same generator that writes the
    document, so a misreading shared between the generator and a rule passes unnoticed.
    These fourteen close that hole: the claim wording and its authorised-or-refused status
    come from the Commission's own regulations, read by `evals.fetch_register`.

    Only HEALTH_CLAIM_AUTHORISED is labelled. An authorised claim is lawful only where the
    food meets its conditions of use, and the authorised seven carry the matching
    "Source of …" statement that satisfies the condition the register records — but for
    every other rule these documents say nothing EU law settles, so inventing a label for
    them would reintroduce exactly the circularity this file is here to remove.
    """
    manifest = pipeline.SPEC_DIR / "external.jsonl"
    if not manifest.exists():
        return []

    sources = json.loads((pipeline.SPEC_DIR / "external_sources.json").read_text(encoding="utf-8"))

    records: list[GoldenRule] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        entry = SpecFixture.model_validate_json(line)
        rule_id, verdict = next(iter(entry.expected_verdicts.items()))
        # Recover which register row settled this label, so the record cites its source.
        origin = sources.get(entry.spec_id, {})
        records.append(
            GoldenRule(
                golden_id=f"GOLD-{entry.spec_id}-{rule_id.value}",
                split=Split.EXTERNAL,
                spec_id=entry.spec_id,
                filename=entry.filename,
                rule_id=rule_id,
                language=entry.language,
                expected_verdict=verdict,
                defect=None,
                adversarial=False,
                provenance=Provenance(
                    source="eu_register",
                    labelled_by=(
                        "the claim appears in the Union list of permitted health claims"
                        if verdict is Verdict.PASS
                        else "authorisation of this claim was refused by the Commission"
                    )
                    + f": {origin.get('claim', '')[:90]}",
                    created_at=today,
                    spec_sha256=entry.sha256,
                    external_source=origin.get("regulation"),
                ),
            )
        )
    return records


def main() -> None:
    """Rebuild both files."""
    today = dt.date.today().isoformat()
    provenance = Provenance(
        source="generator",
        labelled_by="seeded-defect derivation from fixtures/specs/manifest.jsonl",
        created_at=today,
        manifest_sha256=_sha256(MANIFEST),
    )

    rules = [*build_rule_records(provenance), *build_external_records(today)]
    write_jsonl(RULES_PATH, rules)

    retrieval = build_retrieval_records(provenance, split_map())
    write_jsonl(RETRIEVAL_PATH, retrieval)

    for name, records in (("rules", rules), ("retrieval", retrieval)):
        counts = {split.value: sum(r.split is split for r in records) for split in Split}
        shown = "  ".join(f"{k} {v}" for k, v in counts.items() if v)
        print(f"{name:10s} {len(records):>3} records  {shown}")


if __name__ == "__main__":
    main()
