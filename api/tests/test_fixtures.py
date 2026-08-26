"""The manifest is the golden set. These tests hold it to that standard."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from specguard.fixtures.generate import (
    DEFECTS,
    TOTAL_SPECS,
    generate,
    load_manifest,
    write_manifest,
)
from specguard.models.common import Language
from specguard.models.rule import RuleId, Verdict

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "specs"
MANIFEST = FIXTURE_DIR / "manifest.jsonl"


@pytest.fixture(scope="module")
def manifest():
    if not MANIFEST.exists():
        pytest.skip("fixtures not generated; run fixtures/specs/generate.py")
    return load_manifest(MANIFEST)


class TestManifest:
    def test_has_the_expected_shape(self, manifest) -> None:
        assert len(manifest) == TOTAL_SPECS
        assert sum(f.compliant for f in manifest) == 12
        assert sum(f.adversarial for f in manifest) == 2

    def test_every_rule_has_at_least_one_seeded_failure(self, manifest) -> None:
        # A rule with no failing fixture is a rule the eval cannot distinguish from
        # one that always returns PASS.
        covered = {defect.rule_id for f in manifest for defect in f.seeded_defects}
        assert covered == set(RuleId)

    def test_every_defect_kind_is_exercised(self, manifest) -> None:
        kinds = {defect.kind for f in manifest for defect in f.seeded_defects}
        assert len(kinds) == len(DEFECTS)

    def test_both_languages_are_represented(self, manifest) -> None:
        languages = {f.language for f in manifest}
        assert languages == {Language.EN, Language.DE}

    def test_expected_verdicts_agree_with_seeded_defects(self, manifest) -> None:
        for fixture in manifest:
            failing = {d.rule_id for d in fixture.seeded_defects}
            for rule_id, verdict in fixture.expected_verdicts.items():
                expected = Verdict.FAIL if rule_id in failing else Verdict.PASS
                assert verdict is expected, f"{fixture.spec_id} {rule_id}"

    def test_compliant_specs_expect_no_failures(self, manifest) -> None:
        for fixture in manifest:
            if fixture.compliant:
                assert set(fixture.expected_verdicts.values()) == {Verdict.PASS}
                assert not fixture.seeded_defects

    def test_adversarial_specs_still_fail_a_rule(self, manifest) -> None:
        # An injection planted on an otherwise-clean spec proves nothing: obeying it
        # would produce the same verdicts as ignoring it.
        for fixture in manifest:
            if fixture.adversarial:
                assert fixture.injected_instruction
                assert Verdict.FAIL in fixture.expected_verdicts.values()

    def test_pdfs_match_their_recorded_hashes(self, manifest) -> None:
        for fixture in manifest:
            path = FIXTURE_DIR / "generated" / fixture.filename
            assert path.exists(), f"{fixture.filename} missing"
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert digest == fixture.sha256, f"{fixture.spec_id} has drifted from its manifest"


class TestDeterminism:
    def test_regeneration_is_byte_identical(self, tmp_path: Path, manifest) -> None:
        # Without this the golden set rots: every regeneration would change every
        # sha256 and no fixture could be verified against its manifest entry.
        regenerated = generate(tmp_path)
        assert [f.sha256 for f in regenerated] == [f.sha256 for f in manifest]

    def test_manifest_round_trips(self, tmp_path: Path, manifest) -> None:
        path = tmp_path / "manifest.jsonl"
        write_manifest(manifest, path)
        assert load_manifest(path) == manifest


class TestDefectsActuallyApply:
    """A recorded defect that changed nothing is a lie the whole eval is built on."""

    def test_every_seeded_defect_changes_the_document(self, tmp_path: Path, manifest) -> None:
        from specguard.fixtures.catalogue import CATALOGUE
        from specguard.fixtures.generate import _sheet_from, render_pdf

        by_slug = {template.slug: template for template in CATALOGUE}
        for fixture in manifest:
            if not fixture.seeded_defects:
                continue
            slug = fixture.filename.removeprefix(f"{fixture.spec_id}-").removesuffix(".pdf")
            clean = render_pdf(_sheet_from(by_slug[slug]), tmp_path / f"clean-{slug}.pdf")
            actual = (FIXTURE_DIR / "generated" / fixture.filename).read_bytes()
            assert actual != clean, (
                f"{fixture.spec_id} records {[d.kind for d in fixture.seeded_defects]} "
                "but renders identically to the compliant product"
            )

    def test_allergen_defects_land_on_products_with_allergens(self, manifest) -> None:
        from specguard.fixtures.catalogue import CATALOGUE

        by_slug = {template.slug: template for template in CATALOGUE}
        for fixture in manifest:
            kinds = {defect.kind for defect in fixture.seeded_defects}
            if not kinds & {"allergen_not_emphasised", "all_allergens_unemphasised"}:
                continue
            slug = fixture.filename.removeprefix(f"{fixture.spec_id}-").removesuffix(".pdf")
            assert any(i.allergen for i in by_slug[slug].ingredients), (
                f"{fixture.spec_id} seeds an allergen defect on a product with no allergens"
            )
