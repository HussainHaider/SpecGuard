"""Specification sheets whose labels come from EU law rather than from this repository.

    uv run python -m specguard.fixtures.external

Every record in the main golden set is labelled by `generate.py`, which is also what
builds the documents. That circularity is the one weakness the set cannot test its way
out of: if the generator and a rule share a misreading of an article, the test passes and
both are wrong.

These fourteen are the outside check. The claim wording, and whether it may lawfully be
made, come from `evals/golden/sources/eu_register.jsonl` — the Commission's own
authorising and refusing regulations. Seven carry a claim from the authorised list; seven
carry one that was explicitly refused. Nothing here decides the label; EU law did.

**Only `HEALTH_CLAIM_AUTHORISED` is labelled for these documents.** An authorised claim is
lawful only where the food meets that claim's conditions of use, so the authorised seven
also carry the matching "Source of …" statement that satisfies the condition the register
records. Every other rule still runs — the report is complete — but none of their verdicts
becomes ground truth, because inventing a label for them is exactly the failure this file
exists to avoid.

Generation is deliberately separate from `generate.py` and touches none of its state. That
module seeds a PRNG once and walks a fixed catalogue; adding a template to it would shift
every subsequent document, change every sha256, and silently invalidate all eighty
existing golden records.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from specguard.fixtures.catalogue import CATALOGUE, ProductTemplate
from specguard.fixtures.generate import (
    SpecFixture,
    _set_invariant,
    _sheet_from,
    render_pdf,
)
from specguard.models.common import Language
from specguard.models.rule import RuleId, Verdict

REPO_ROOT = Path(__file__).resolve().parents[3].parent
#: parents[3] is api/, where evals/ sits beside src/.
REGISTER = (
    Path(__file__).resolve().parents[3] / "evals" / "golden" / "sources" / "eu_register.jsonl"
)
SPEC_DIR = REPO_ROOT / "fixtures" / "specs"
MANIFEST = SPEC_DIR / "external.jsonl"
#: spec_id -> the register row that settles its label. Written rather than inferred: the
#: manifest carries no claim text, and guessing the source back out of it was how the
#: first version of this ended up recording None.
SOURCES = SPEC_DIR / "external_sources.json"


@dataclass(frozen=True)
class Pick:
    """One register claim, and the product it is put on."""

    nutrient: str
    template_slug: str
    source_of: str | None


#: Seven authorised claims, each on a product that is stated to be a source of the
#: nutrient the register requires. Nutrients are distinct so the seven are not seven
#: rewordings of one judgement.
AUTHORISED: tuple[Pick, ...] = (
    Pick("Vitamin C", "orange-juice", "Source of vitamin C"),
    Pick("Calcium", "strawberry-yogurt", "Source of calcium"),
    Pick("Iron", "oat-granola", "Source of iron"),
    Pick("Magnesium", "dark-chocolate", "Source of magnesium"),
    Pick("Zinc", "mixed-nuts", "Source of zinc"),
    Pick("Biotin", "wholemeal-bread", "Source of biotin"),
    Pick("Vitamin D", "cheddar-cheese", "Source of vitamin D"),
)

#: Seven refused claims. No conditions to satisfy: a refused claim may not be made at all
#: (Reg. 1924/2006 Art. 10(1)), so the label does not depend on the product.
REFUSED_SLUGS: tuple[str, ...] = (
    "apple-juice",
    "tomato-basil-soup",
    "hummus",
    "salted-crisps",
    "digestive-biscuits",
    "chicken-soup",
    "pasta-sauce",
)


def _templates() -> dict[str, ProductTemplate]:
    return {t.slug: t for t in CATALOGUE if t.language is Language.EN}


def _register() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rows = [json.loads(line) for line in REGISTER.read_text(encoding="utf-8").splitlines() if line]
    authorised = [r for r in rows if r["status"] == "authorised"]
    refused = [r for r in rows if r["status"] == "refused"]
    return authorised, refused


def _claim_for(authorised: list[dict[str, str]], nutrient: str) -> dict[str, str]:
    """The first authorised claim for this nutrient, in the register's own order.

    First rather than chosen: picking the claim that suits us would be a thumb on the
    scale, and the register is already sorted deterministically.
    """
    for row in authorised:
        if row["nutrient"].casefold().startswith(nutrient.casefold()):
            return row
    raise LookupError(f"no authorised claim for {nutrient!r} in the register")


def build() -> list[SpecFixture]:
    """Render the fourteen documents and return their manifest entries."""
    _set_invariant()
    authorised, refused = _register()
    templates = _templates()
    generated = SPEC_DIR / "generated"
    generated.mkdir(parents=True, exist_ok=True)

    entries: list[SpecFixture] = []
    sources: dict[str, dict[str, str]] = {}
    index = 0

    def emit(template: ProductTemplate, claim: dict[str, str], verdict: Verdict) -> None:
        nonlocal index
        index += 1
        spec_id = f"EXT-{index:03d}"
        sheet = _sheet_from(template)
        sheet.health_claim = claim["claim"]
        # The register's condition of use, satisfied on the face of the document. Absent
        # this, an authorised wording on a food that is not a source of the nutrient is
        # not compliant, and PASS would be the wrong label.
        sheet.nutrition_claim = claim.get("source_of") or None

        sources[spec_id] = {
            "claim": claim["claim"],
            "status": claim["status"],
            "regulation": claim["regulation"],
            "celex": claim["celex"],
        }
        filename = f"{spec_id}-{template.slug}.pdf"
        payload = render_pdf(sheet, generated / filename)
        entries.append(
            SpecFixture(
                spec_id=spec_id,
                filename=filename,
                sha256=hashlib.sha256(payload).hexdigest(),
                product_name=template.product_name,
                language=template.language,
                compliant=verdict is Verdict.PASS,
                adversarial=False,
                seeded_defects=[],
                # Only the rule whose answer EU law actually settles.
                expected_verdicts={RuleId.HEALTH_CLAIM_AUTHORISED: verdict},
                injected_instruction=None,
            )
        )

    for pick in AUTHORISED:
        claim = dict(_claim_for(authorised, pick.nutrient))
        claim["source_of"] = pick.source_of or ""
        emit(templates[pick.template_slug], claim, Verdict.PASS)

    for slug, row in zip(REFUSED_SLUGS, refused, strict=False):
        emit(templates[slug], dict(row), Verdict.FAIL)

    SOURCES.write_text(json.dumps(sources, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return entries


def main() -> None:
    entries = build()
    MANIFEST.write_text(
        "\n".join(e.model_dump_json() for e in entries) + "\n",
        encoding="utf-8",
    )
    passes = sum(
        e.expected_verdicts[RuleId.HEALTH_CLAIM_AUTHORISED] is Verdict.PASS for e in entries
    )
    print(
        f"wrote {len(entries)} external specs "
        f"({passes} authorised, {len(entries) - passes} refused)"
    )
    for e in entries:
        verdict = e.expected_verdicts[RuleId.HEALTH_CLAIM_AUTHORISED].value
        print(f"  {e.spec_id}  {verdict:5s}  {e.filename}")


if __name__ == "__main__":
    main()
