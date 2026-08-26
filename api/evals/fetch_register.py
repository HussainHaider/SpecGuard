"""Fetch the EU register of authorised and refused health claims.

    uv run python -m evals.fetch_register

Writes ``evals/golden/sources/eu_register.jsonl``, which is committed. Run it again to
refresh; the diff is the change in EU law.

**Why this exists.** Every label in the golden set was derived from the same generator
that produces the specifications — so if `fixtures/specs/generate.py` and a rule shared a
misreading of an article, the test would pass and both would be wrong. Nothing in the set
could catch that, and `build_golden.py` conceded as much in its own docstring.

These records are the outside opinion. The claim wordings and, crucially, their
authorised-or-refused status come from the Commission's own regulations rather than from
anything in this repository. A specification carrying one of these claims has a label this
project did not author and cannot have talked itself into.

The tables are read by column *header*, not position: the authorising regulations and the
refusing ones lay their annexes out differently, and one of the refusing ones carries an
applicant column the others do not.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path

import httpx

CELLAR_URL = "https://publications.europa.eu/resource/celex/{celex}"
OUT = Path(__file__).resolve().parent / "golden" / "sources" / "eu_register.jsonl"


@dataclass(frozen=True)
class Source:
    """One regulation, and what appearing in its annex means."""

    celex: str
    regulation: str
    status: str


#: Authorising regulations list claims that may be made; refusing ones list claims that
#: may not. Art. 10(1) of 1924/2006 is what makes the second list meaningful: a health
#: claim is prohibited unless it is authorised, so a refusal is a durable legal fact and
#: not merely an absence of permission.
SOURCES: tuple[Source, ...] = (
    Source("32012R0432", "Commission Regulation (EU) No 432/2012", "authorised"),
    Source("32013R0536", "Commission Regulation (EU) No 536/2013", "authorised"),
    Source("32014R0040", "Commission Regulation (EU) No 40/2014", "authorised"),
    Source("32015R0007", "Commission Regulation (EU) 2015/7", "authorised"),
    Source("32011R0440", "Commission Regulation (EU) No 440/2011", "refused"),
    Source("32011R0665", "Commission Regulation (EU) No 665/2011", "refused"),
    Source("32011R1170", "Commission Regulation (EU) No 1170/2011", "refused"),
    Source("32012R0378", "Commission Regulation (EU) No 378/2012", "refused"),
    Source("32012R0379", "Commission Regulation (EU) No 379/2012", "refused"),
)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _cells(row: str) -> list[str]:
    return [
        unescape(_WS.sub(" ", _TAG.sub(" ", cell))).strip()
        for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
    ]


def _column_map(cells: list[str]) -> dict[str, int] | None:
    """Map the columns this script needs, or None if this is not a header row."""
    wanted = {"claim": "claim", "nutrient": "nutrient", "conditions of use": "conditions"}
    found: dict[str, int] = {}
    for index, cell in enumerate(cells):
        lowered = cell.casefold()
        for prefix, key in wanted.items():
            if lowered.startswith(prefix) and key not in found:
                found[key] = index
    return found if "claim" in found and "nutrient" in found else None


def parse(html: str, source: Source) -> list[dict[str, object]]:
    """Every claim row in this regulation's annex."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    columns: dict[str, int] | None = None
    records: list[dict[str, object]] = []

    for row in rows:
        cells = _cells(row)
        if not cells:
            continue
        header = _column_map(cells)
        if header is not None:
            columns = header
            continue
        if columns is None or len(cells) <= max(columns.values()):
            continue

        claim = cells[columns["claim"]]
        nutrient = cells[columns["nutrient"]]
        # Header repeats and continuation rows both show up as short or empty cells.
        if len(claim) < 15 or not nutrient:
            continue

        records.append(
            {
                "claim": claim,
                "nutrient": nutrient,
                "conditions": cells[columns["conditions"]] if "conditions" in columns else "",
                "status": source.status,
                "regulation": source.regulation,
                "celex": source.celex,
            }
        )
    return records


def main() -> None:
    fetched_at = dt.datetime.now(dt.UTC).date().isoformat()
    records: list[dict[str, object]] = []

    with httpx.Client(follow_redirects=True, timeout=90.0) as client:
        for source in SOURCES:
            response = client.get(
                CELLAR_URL.format(celex=source.celex),
                headers={"Accept": "application/xhtml+xml", "Accept-Language": "eng"},
            )
            response.raise_for_status()
            found = parse(response.text, source)
            print(f"  {source.celex}  {source.status:10s} {len(found):>4} claims")
            records.extend({**record, "fetched_at": fetched_at} for record in found)

    # Sorted so a refetch of unchanged law produces an empty diff.
    records.sort(key=lambda r: (str(r["status"]), str(r["celex"]), str(r["claim"])))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records) + "\n",
        encoding="utf-8",
    )
    authorised = sum(r["status"] == "authorised" for r in records)
    print(
        f"wrote {len(records)} claims ({authorised} authorised, "
        f"{len(records) - authorised} refused) to {OUT}"
    )


if __name__ == "__main__":
    main()
