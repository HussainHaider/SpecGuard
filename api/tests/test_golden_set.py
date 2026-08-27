"""The golden set is ground truth. These tests hold it to that standard.

A golden file is only worth what its provenance is worth. Most of what follows checks
not that the labels are *good* — no test can establish that — but that they still
describe the artefacts they claim to, that no label came from this system's own output,
and that the split does what it says.
"""

from __future__ import annotations

import collections
import json
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
    """The generator-labelled records.

    Scoped to the internal set on purpose: everything below asserts a property of how the
    generator labels its own documents — that the label matches the manifest, that the
    sha256 still describes the file, that every seeded defect survived sampling. The
    externally-labelled records have none of those properties by construction, and are
    checked separately in TestExternalRecords.
    """
    return [record for record in load_rules() if record.split is not Split.EXTERNAL]


@pytest.fixture(scope="module")
def external():
    return [record for record in load_rules() if record.split is Split.EXTERNAL]


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


class TestExternalRecords:
    """The records EU law labels, not this repository.

    These exist because every other record is labelled by the same generator that writes
    the document. A misreading shared between the generator and a rule passes unnoticed
    in the internal set; it cannot in this one.
    """

    def test_there_is_an_external_split_at_all(self, external):
        assert len(external) == 14

    def test_none_of_them_were_labelled_by_this_system(self, external):
        assert {record.provenance.source for record in external} == {"eu_register"}

    def test_each_one_cites_the_act_that_settles_it(self, external):
        """A reviewer has to be able to check the label against the law itself."""
        for record in external:
            assert record.provenance.external_source, record.golden_id
            assert "Regulation" in record.provenance.external_source

    def test_both_answers_are_represented(self, external):
        """All-refused would be a set a system passes by always saying FAIL."""
        verdicts = collections.Counter(r.expected_verdict for r in external)
        assert verdicts[Verdict.PASS] == 7
        assert verdicts[Verdict.FAIL] == 7

    def test_only_the_rule_eu_law_settles_is_labelled(self, external):
        """An authorised claim is lawful only where the food meets its conditions of use.

        Labelling the other rules on these documents would be inventing ground truth,
        which is the circularity this split exists to remove.
        """
        assert {record.rule_id for record in external} == {RuleId.HEALTH_CLAIM_AUTHORISED}

    def test_every_record_still_names_the_document_it_describes(self, external):
        manifest = SPEC_DIR / "external.jsonl"
        if not manifest.exists():
            pytest.skip("external fixtures not generated")
        hashes = {
            json.loads(line)["spec_id"]: json.loads(line)["sha256"]
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if line
        }
        for record in external:
            assert record.provenance.spec_sha256 == hashes[record.spec_id]

    def test_they_do_not_disturb_the_internal_split(self, rules):
        """The internal set is still exactly the 80 records the baseline was measured on."""
        assert len(rules) == 80
