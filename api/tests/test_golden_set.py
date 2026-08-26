"""The golden set is ground truth. These tests hold it to that standard.

A golden file is only worth what its provenance is worth. Most of what follows checks
not that the labels are *good* — no test can establish that — but that they still
describe the artefacts they claim to, that no label came from this system's own output,
and that the split does what it says.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest
from evals.build_golden import ANCHORS, split_map
from evals.golden import Split, load_retrieval, load_rules

from specguard.config import Settings
from specguard.corpus.seed import load_clauses
from specguard.fixtures.generate import load_manifest
from specguard.models.rule import RULE_KINDS, RuleId, RuleKind, Verdict

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "fixtures" / "specs"


@pytest.fixture(scope="module")
def manifest():
    path = SPEC_DIR / "manifest.jsonl"
    if not path.exists():
        pytest.skip("fixtures not generated")
    return {entry.spec_id: entry for entry in load_manifest(path)}


@pytest.fixture(scope="module")
def rules():
    return load_rules()


@pytest.fixture(scope="module")
def retrieval():
    return load_retrieval()


class TestVerdictRecords:
    def test_the_set_is_the_size_it_claims(self, rules):
        assert len(rules) == 80

    def test_ids_are_unique(self, rules):
        assert len({record.golden_id for record in rules}) == len(rules)

    def test_every_label_matches_the_manifest(self, rules, manifest):
        """The label is derived from the manifest, so it must still agree with it."""
        for record in rules:
            expected = manifest[record.spec_id].expected_verdicts[record.rule_id]
            assert record.expected_verdict is expected, record.golden_id

    def test_every_record_names_the_document_it_describes(self, rules, manifest):
        """A regenerated PDF must invalidate the record rather than silently mislabel it."""
        for record in rules:
            assert record.provenance.spec_sha256 == manifest[record.spec_id].sha256

    def test_no_label_came_from_this_system(self, rules):
        """A verdict the pipeline produced, fed back as truth, measures only self-consistency."""
        assert {record.provenance.source for record in rules} == {"generator"}

    def test_every_seeded_failure_is_present(self, rules, manifest):
        """The sample may drop a pass. It may never drop a known defect."""
        seeded = {
            (entry.spec_id, defect.rule_id)
            for entry in manifest.values()
            for defect in entry.seeded_defects
        }
        kept = {
            (record.spec_id, record.rule_id)
            for record in rules
            if record.expected_verdict is Verdict.FAIL
        }
        assert seeded == kept

    def test_a_failure_record_says_what_was_wrong(self, rules):
        for record in rules:
            if record.expected_verdict is Verdict.FAIL:
                assert record.defect is not None, record.golden_id

    def test_every_rule_is_represented(self, rules):
        assert {record.rule_id for record in rules} == set(RuleId)

    def test_both_languages_are_represented(self, rules):
        languages = collections.Counter(record.language for record in rules)
        assert len(languages) == 2
        assert min(languages.values()) >= 10


class TestSplit:
    def test_no_specification_straddles_the_split(self, rules):
        """Two rules over one spec share a document and an extraction.

        Splitting between them would put the same evidence on both sides of the line and
        turn the held-out number into a second reading of the dev one.
        """
        splits = collections.defaultdict(set)
        for record in rules:
            splits[record.spec_id].add(record.split)
        assert all(len(seen) == 1 for seen in splits.values())

    def test_both_splits_are_substantial(self, rules):
        counts = collections.Counter(record.split for record in rules)
        assert min(counts.values()) / len(rules) > 0.25

    def test_every_failing_rule_fails_in_both_splits(self, rules):
        """Otherwise a rule's held-out false-negative rate is undefined rather than small."""
        failures = collections.defaultdict(set)
        for record in rules:
            if record.expected_verdict is Verdict.FAIL:
                failures[record.rule_id].add(record.split)
        assert failures
        for rule_id, splits in failures.items():
            assert splits == {Split.DEV, Split.HELD_OUT}, rule_id

    def test_the_split_is_reproducible(self, rules):
        assert {record.spec_id: record.split for record in rules}.items() <= split_map().items()


class TestRetrievalRecords:
    def test_only_rag_rules_retrieve(self, retrieval):
        for record in retrieval:
            assert RULE_KINDS[record.rule_id] is RuleKind.RAG

    def test_every_rag_rule_has_anchors_defined(self):
        rag = {rule_id for rule_id, kind in RULE_KINDS.items() if kind is RuleKind.RAG}
        assert set(ANCHORS) == rag

    def test_every_anchor_resolves_against_the_corpus(self, retrieval):
        """An anchor that is not in the index would look like a retrieval defect.

        It would be a labelling defect, and it would quietly depress recall@5 for as long
        as nobody checked.
        """
        corpus = REPO_ROOT / "corpus"
        if not (corpus / "sources.json").exists():
            pytest.skip("corpus not fetched")
        known = {clause.chunk_id for clause in load_clauses(Settings().corpus_dir)}
        for record in retrieval:
            for chunk_id, reference in zip(
                record.relevant_chunk_ids, record.relevant_references, strict=True
            ):
                assert chunk_id in known, f"{record.golden_id}: {reference}"

    def test_labels_are_readable(self, retrieval):
        """A file of opaque UUIDs is a file nobody will ever check."""
        for record in retrieval:
            assert len(record.relevant_references) == len(record.relevant_chunk_ids)
            assert all("Regulation" in reference for reference in record.relevant_references)

    def test_a_query_is_held_out_only_if_every_spec_that_asks_it_is(self, retrieval):
        splits = split_map()
        for record in retrieval:
            if record.split is Split.HELD_OUT:
                assert all(splits[spec] is Split.HELD_OUT for spec in record.spec_ids)

    def test_search_keys_are_unique(self, retrieval):
        assert len({record.search_key for record in retrieval}) == len(retrieval)
