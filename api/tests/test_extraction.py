"""Extraction: one schema-constrained call, replayed from recorded fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from specguard.ingest.extract import PROMPT_NAME, extract_spec
from specguard.ingest.pdf import EmptyDocumentError, ingest_pdf
from specguard.llm.fake import FakeClient, MissingFixtureError
from specguard.models.common import Language
from specguard.models.spec import EmphasisStyle
from specguard.prompts.loader import PromptError, load_prompt
from specguard.rules.deterministic.allergens import allergens_in

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "specs"
LLM_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm"
COMPLIANT = "SPEC-001-digestive-biscuits.pdf"
GERMAN = "SPEC-008-de-zartbitterschokolade.pdf"


@pytest.fixture
def client() -> FakeClient:
    return FakeClient(LLM_FIXTURES, model="fixture-model")


def _extract(filename: str, client: FakeClient, language: Language = Language.EN):
    path = FIXTURE_DIR / "generated" / filename
    if not path.exists():
        pytest.skip("fixtures not generated")
    document = ingest_pdf(path)
    return extract_spec(document, client, language=language)


class TestPrompt:
    def test_extraction_prompt_is_versioned(self) -> None:
        # The literal version is deliberately not asserted: bumping a prompt is a normal
        # act, and a test that has to be edited to allow it teaches people to edit tests.
        prompt = load_prompt(PROMPT_NAME)
        assert prompt.version.startswith(f"{PROMPT_NAME}@v")
        assert prompt.body

    def test_a_prompt_without_a_version_is_rejected(self, tmp_path: Path) -> None:
        # An untraceable prompt makes every trace referencing it untraceable too.
        (tmp_path / "broken.md").write_text("---\ndescription: no version\n---\nbody\n")
        with pytest.raises(PromptError, match="version"):
            load_prompt("broken", tmp_path)


class TestExtraction:
    def test_produces_a_spec_with_provenance(self, client: FakeClient) -> None:
        spec, usage = _extract(COMPLIANT, client)
        assert spec.source.filename == COMPLIANT
        # Provenance must match the prompt that actually ran, whatever version that is.
        expected = load_prompt(PROMPT_NAME).version
        assert spec.extractor_prompt_version == expected
        assert usage.prompt_version == expected
        assert spec.legal_name is not None

    def test_every_field_carries_a_confidence(self, client: FakeClient) -> None:
        spec, _ = _extract(COMPLIANT, client)
        for name in ("legal_name", "net_quantity", "nutrition", "ingredients"):
            field = getattr(spec, name)
            assert field is not None
            assert 0.0 <= field.confidence <= 1.0

    def test_german_documents_extract(self, client: FakeClient) -> None:
        spec, _ = _extract(GERMAN, client, Language.DE)
        assert spec.language is Language.DE
        assert spec.ingredients is not None

    def test_call_is_keyed_by_document_content(self, client: FakeClient) -> None:
        # Keyed by hash rather than filename, so renaming a fixture cannot silently
        # replay a different document's response.
        spec, _ = _extract(COMPLIANT, client)
        assert client.calls == [(PROMPT_NAME, spec.source.sha256[:16])]


class TestEmphasisEnrichment:
    """Typography is measured from the PDF, never taken from the model."""

    def test_emphasis_is_recovered_even_though_the_fixture_has_none(
        self, client: FakeClient
    ) -> None:
        # Every recorded fixture has emphasised=False on every ingredient. If the
        # pipeline trusted the model, this would find nothing.
        spec, _ = _extract(COMPLIANT, client)
        assert spec.ingredients is not None
        allergen_items = [
            item.value
            for item in spec.ingredients.value.items
            if allergens_in(item.value.name, Language.EN)
        ]
        assert allergen_items, "expected the biscuit fixture to contain an allergen"
        assert all(item.emphasised for item in allergen_items)
        assert all(item.emphasis_style is EmphasisStyle.UPPERCASE for item in allergen_items)

    def test_non_allergen_ingredients_are_not_marked_emphasised(self, client: FakeClient) -> None:
        spec, _ = _extract(COMPLIANT, client)
        assert spec.ingredients is not None
        items = spec.ingredients.value.items
        plain = [i.value for i in items if i.value.name.lower() == "sugar"]
        assert plain
        assert not plain[0].emphasised


class TestFailureModes:
    def test_a_missing_fixture_fails_loudly(self, tmp_path: Path) -> None:
        # A fake that invented an answer would turn a missing fixture into a green test.
        empty = FakeClient(tmp_path)
        with pytest.raises(MissingFixtureError, match="no recorded response"):
            _extract(COMPLIANT, empty)

    def test_a_pdf_with_no_text_is_an_error_not_an_empty_spec(self, tmp_path: Path) -> None:
        from reportlab.pdfgen import canvas

        blank = tmp_path / "blank.pdf"
        canvas.Canvas(str(blank)).save()
        with pytest.raises(EmptyDocumentError, match="scanned"):
            ingest_pdf(blank)
