"""Detect instructions aimed at the model inside supplier document text.

This is a reporting control, not a filter. The text is never obeyed regardless — it
reaches every model call wrapped and labelled as third-party data — so the job here is to
tell a reviewer that someone tried, and to mark the report for human attention.

Detection is deliberately keyword-shaped rather than a model call. A model asked whether
text contains an injection is itself reading the injection, which is the thing we are
trying to avoid needing to trust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Phrases that only appear when text is addressing an automated reader. A supplier
#: describing a food has no reason to write any of them.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("instruction override", re.compile(r"(?i)\bignore\s+(?:all\s+)?(?:previous|prior|above)\b")),
    (
        "instruction override",
        re.compile(r"(?i)\bdisregard\s+(?:all\s+|any\s+)?(?:previous|prior|the)\b"),
    ),
    (
        "verdict steering",
        re.compile(
            r"(?i)\b(?:return|report|mark|output)\s+(?:it\s+)?(?:as\s+)?(?:pass|compliant)\b"
        ),
    ),
    ("verdict steering", re.compile(r"(?i)\bdo not (?:recalculate|check|verify|flag|report)\b")),
    (
        "false authority",
        re.compile(r"(?i)\b(?:system|admin|developer)\s*(?:note|message|instruction|prompt)\b"),
    ),
    ("false authority", re.compile(r"(?i)\balready been (?:approved|verified|checked|reviewed)\b")),
    ("role assertion", re.compile(r"(?i)\byou are (?:now )?(?:a|an|the)\b")),
    (
        "prompt exfiltration",
        re.compile(
            r"(?i)\b(?:reveal|print|repeat)\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)\b"
        ),
    ),
)

#: Enough context to be recognisable in a report, short enough not to reproduce the
#: whole payload back to the reviewer.
_SPAN_CHARS = 160


@dataclass(frozen=True)
class InjectionFinding:
    """One suspicious span, quoted so a human can judge it."""

    category: str
    span: str


@dataclass(frozen=True)
class InjectionScan:
    """What the scan found."""

    findings: list[InjectionFinding] = field(default_factory=list)

    @property
    def suspected(self) -> bool:
        return bool(self.findings)

    @property
    def categories(self) -> set[str]:
        return {finding.category for finding in self.findings}

    def signals(self) -> list[str]:
        """Verbatim spans, for GuardrailFlags."""
        return [finding.span for finding in self.findings]


def scan(text: str) -> InjectionScan:
    """Look for text addressing the model rather than describing the food."""
    findings: list[InjectionFinding] = []
    seen: set[str] = set()

    for category, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            start = max(0, match.start() - 40)
            span = " ".join(text[start : start + _SPAN_CHARS].split())
            if span in seen:
                continue
            seen.add(span)
            findings.append(InjectionFinding(category=category, span=span))

    return InjectionScan(findings=findings)
