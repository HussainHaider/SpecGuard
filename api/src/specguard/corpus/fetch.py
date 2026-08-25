"""Fetch consolidated EUR-Lex text and normalise it to plain text under ``corpus/raw/``.

The eur-lex.europa.eu web front end sits behind an AWS WAF JavaScript challenge and
cannot be scripted (it answers ``202`` with ``x-amzn-waf-action: challenge`` and an empty
body). The Publications Office's Cellar service is the machine-readable route to the same
documents and is what this module uses, via content negotiation on the CELEX id.

The XHTML it returns carries semantic classes — ``title-article-norm`` for an article
heading, ``no-parag`` for a paragraph number, ``grid-list`` for lettered points — and,
importantly, language-independent element ids (``art_9``, ``anx_II``). Structure is read
from those, so the German text parses exactly like the English. What lands in
``corpus/raw/`` is ordinary plain text of the shape a EUR-Lex "download as text" export
has, because the loader's contract is to ingest plain text, not markup.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path

import httpx
from lxml import html as lxml_html
from lxml.html import HtmlElement

from specguard.corpus.sources import (
    CELLAR_LANGUAGE_CODES,
    SOURCES,
    SourceSpec,
    raw_filename,
)
from specguard.models.common import Language
from specguard.models.corpus import CorpusDocument

CELLAR_URL = "https://publications.europa.eu/resource/celex/{celex}"
REQUEST_TIMEOUT_S = 90.0

_WS = re.compile(r"\s+")

#: Classes whose text is chrome — the consolidation banner, footnotes, amendment
#: markers, the table of contents — and never part of the legal text.
_SKIP_CLASSES = frozenset(
    {
        "disclaimer",
        "reference",
        "hd-modifiers",
        "hd-collapsible",
        "footnote",
        "modref",
        "arrow",
        "separator",
        "separator-annex",
        "toc-1",
        "toc-2",
        "toc-3",
        "toc-4",
        "doc-ti",
    }
)

#: Headings that open a new section. Emitted verbatim, so "Article 9" and "Artikel 9"
#: both survive into the plain text and the loader can key off either.
_TITLE_CLASSES = frozenset(
    {
        "title-article-norm",
        "stitle-article-norm",
        "title-annex-1",
        "title-annex-2",
        "title-gr-seq-level-1",
        "title-gr-seq-level-2",
        "title-gr-seq-level-3",
        "title-division-1",
        "title-division-2",
    }
)


def _clean(text: str) -> str:
    """Collapse whitespace and normalise the non-breaking spaces EUR-Lex is full of."""
    return _WS.sub(" ", text.replace("\xa0", " ")).strip()


def _classes(el: HtmlElement) -> set[str]:
    return set((el.get("class") or "").split())


def _has_class(el: HtmlElement, name: str) -> bool:
    return name in _classes(el)


def _marker_of(el: HtmlElement) -> str:
    """The paragraph number sitting in its own span, e.g. "1." (EN) or "(1)" (DE)."""
    span = el.find('./span[@class="no-parag"]')
    return _clean(span.text_content()) if span is not None else ""


def _prefix(marker: str, lines: list[str]) -> list[str]:
    """Attach a paragraph or point marker to the head of the block it introduces."""
    if not marker:
        return lines
    if not lines:
        return [marker]
    return [f"{marker} {lines[0]}".strip(), *lines[1:]]


def _render_table(el: HtmlElement) -> list[str]:
    """Render a table row-wise.

    Annex XIV — the energy conversion factors NUTRITION_ARITHMETIC is built on — is a
    table, so dropping tables would drop the rule's own legal basis.
    """
    lines: list[str] = []
    for row in el.xpath(".//tr"):
        cells = [_clean(c.text_content()) for c in row.xpath("./td|./th")]
        line = " | ".join(c for c in cells if c)
        if line:
            lines.append(line)
    return lines


def _flatten(el: HtmlElement) -> list[str]:
    """Render an element's subtree as plain-text lines, points on their own lines."""
    if _has_class(el, "grid-list") and _has_class(el, "grid-container"):
        marker = ""
        body: list[str] = []
        for child in el:
            if _has_class(child, "grid-list-column-1"):
                marker = _clean(child.text_content())
            elif _has_class(child, "grid-list-column-2"):
                body = _flatten(child)
        return _prefix(marker, body)

    if el.tag == "table":
        return _render_table(el)

    if el.tag == "p":
        line = _clean(el.text_content())
        return [line] if line else []

    marker = _marker_of(el)
    lines: list[str] = []
    for child in el:
        if child.tag == "span" and _has_class(child, "no-parag"):
            continue
        lines.extend(_flatten(child))
    if not lines:
        line = _clean(el.text_content())
        lines = [line] if line else []
    return _prefix(marker, lines)


def _blocks(el: HtmlElement) -> Iterator[list[str]]:
    """Walk the document in order, yielding one block of lines per structural unit.

    Document order is the only thing this depends on. The English consolidated text
    wraps each article in ``div.eli-subdivision#art_9`` while the German text of the
    same act is a flat body with no wrappers and no structural ids at all — so any
    approach keyed on nesting or on element ids works for one language and silently
    returns nothing for the other.
    """
    classes = _classes(el)
    if classes & _SKIP_CLASSES:
        return

    if el.tag == "table":
        block = _render_table(el)
        if block:
            yield block
        return

    if el.tag == "p":
        if classes & _TITLE_CLASSES or _has_class(el, "norm") or not classes:
            block = [_clean(el.text_content())]
            if block[0]:
                yield block
        return

    if _has_class(el, "grid-container") and _has_class(el, "grid-list"):
        block = _flatten(el)
        if block:
            yield block
        return

    if el.tag == "div" and _has_class(el, "norm") and _marker_of(el):
        block = _flatten(el)
        if block:
            yield block
        return

    for child in el:
        yield from _blocks(child)


def to_plain_text(document: str | bytes) -> str:
    """Convert a Cellar XHTML act into structured plain text.

    Takes bytes where possible: the payload carries an XML encoding declaration, and
    lxml refuses to parse a str that declares its own encoding.
    """
    payload = document.encode("utf-8") if isinstance(document, str) else document
    root = lxml_html.fromstring(payload)
    body = root.find("body")
    blocks = ["\n".join(block) for block in _blocks(body if body is not None else root)]
    return "\n\n".join(blocks) + "\n"


def fetch_document(
    source: SourceSpec, language: Language, client: httpx.Client
) -> tuple[str, CorpusDocument]:
    """Download one language version of one act and return its text and its record."""
    url = CELLAR_URL.format(celex=source.celex)
    response = client.get(
        url,
        headers={
            "Accept": "application/xhtml+xml",
            "Accept-Language": CELLAR_LANGUAGE_CODES[language],
        },
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    text = to_plain_text(response.content)
    document = CorpusDocument(
        celex=source.celex,
        regulation=source.regulation,
        language=language,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        fetched_at=dt.datetime.now(dt.UTC),
        url=url,
    )
    return text, document


def fetch_all(corpus_dir: Path) -> list[CorpusDocument]:
    """Fetch every source in every indexed language into ``corpus_dir/raw``."""
    raw_dir = corpus_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    documents: list[CorpusDocument] = []

    with httpx.Client() as client:
        for source in SOURCES:
            for language in source.languages:
                text, document = fetch_document(source, language, client)
                path = raw_dir / raw_filename(source.celex, language)
                path.write_text(text, encoding="utf-8")
                documents.append(document)
                print(f"  {path.name}: {len(text):,} chars, sha256 {document.sha256[:12]}")

    manifest = corpus_dir / "sources.json"
    manifest.write_text(
        json.dumps([d.model_dump(mode="json") for d in documents], indent=2) + "\n",
        encoding="utf-8",
    )
    return documents


def main() -> None:
    """CLI: ``python -m specguard.corpus.fetch``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-dir", type=Path, default=Path("../corpus"), help="Directory holding raw/."
    )
    args = parser.parse_args()
    print(f"Fetching {len(SOURCES)} acts from Cellar into {args.corpus_dir}/raw ...")
    documents = fetch_all(args.corpus_dir)
    print(f"Wrote {len(documents)} documents and sources.json")


if __name__ == "__main__":
    main()
