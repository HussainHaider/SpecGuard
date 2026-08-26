"""Redact personal data from document text before it reaches a model.

The hard part here is what *not* to redact. Art. 9(1)(h) requires the name and address of
the food business operator to appear on the label — that is a mandatory particular, and
MANDATORY_FIELDS exists to check it is present. A scrubber that strips company names and
postal addresses as "PII" would delete the exact field the rule looks for, and the rule
would then report a compliant specification as missing its operator.

So the scope is deliberately narrow: individuals, not businesses. A named contact, their
direct line, their personal email. The operator block stays, because it is required to be
public and a compliance check is meaningless without it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Redaction markers are readable rather than opaque: a reviewer looking at a report
#: should be able to tell what was removed and why, without going back to the source.
EMAIL_MARK = "[redacted-email]"
PHONE_MARK = "[redacted-phone]"
CONTACT_MARK = "[redacted-contact]"

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

#: International and national forms, long enough not to swallow a net quantity or a
#: nutrition figure. Requires a leading + or at least nine digits with separators.
_PHONE = re.compile(r"(?:\+\d{1,3}[\s./-]?)?(?:\(?\d{2,5}\)?[\s./-]?){2,4}\d{2,5}")

#: A labelled contact person. Matching the label rather than guessing at names avoids
#: redacting an ingredient or a brand that happens to look like a personal name.
_CONTACT_LINE = re.compile(
    r"(?i)\b(?:contact|kontakt|ansprechpartner|attn|attention|responsible person|"
    r"quality manager|technical contact)\b\s*[:\-]?\s*(?P<value>[^\n]{1,80})"
)

#: Digit runs that are legitimately part of a specification and must survive.
_KEEP = re.compile(r"(?i)\b(?:\d+[.,]?\d*\s*(?:g|kg|ml|cl|l|kj|kcal|%|mg|µg)|e\d{3}|ean|gtin)\b")


@dataclass(frozen=True)
class Redaction:
    """What was removed, so it can be reported rather than silently disappearing."""

    emails: int = 0
    phones: int = 0
    contacts: int = 0

    @property
    def total(self) -> int:
        return self.emails + self.phones + self.contacts


def scrub(text: str) -> tuple[str, Redaction]:
    """Redact personal data, leaving the mandatory operator particulars intact."""
    emails = len(_EMAIL.findall(text))
    scrubbed = _EMAIL.sub(EMAIL_MARK, text)

    contacts = 0

    def _contact(match: re.Match[str]) -> str:
        nonlocal contacts
        contacts += 1
        return f"{match.group(0)[: match.start('value') - match.start()]}{CONTACT_MARK}"

    scrubbed = _CONTACT_LINE.sub(_contact, scrubbed)

    phones = 0

    def _phone(match: re.Match[str]) -> str:
        nonlocal phones
        candidate = match.group(0)
        digits = sum(character.isdigit() for character in candidate)
        # Too few digits to be a phone number, and quite likely a net quantity, a date
        # or a nutrition value — all of which rules depend on.
        if digits < 9 or _KEEP.search(candidate):
            return candidate
        phones += 1
        return PHONE_MARK

    scrubbed = _PHONE.sub(_phone, scrubbed)
    return scrubbed, Redaction(emails=emails, phones=phones, contacts=contacts)
