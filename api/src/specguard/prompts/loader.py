"""Load versioned prompts from ``prompts/*.md``.

Prompts are files, not string literals, because the version is part of every trace and a
prompt that lives in Python cannot be diffed, reviewed or rolled back on its own.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from specguard.models.common import SpecGuardModel

PROMPT_DIR = Path(__file__).resolve().parent
_FRONTMATTER = re.compile(r"^---\n(?P<meta>.*?)\n---\n(?P<body>.*)$", re.DOTALL)


class PromptError(ValueError):
    """A prompt file is missing or has no version in its frontmatter."""


class Prompt(SpecGuardModel):
    """One versioned prompt."""

    name: str
    version: str
    body: str

    def render(self, **values: str) -> str:
        """Fill ``{placeholders}`` in the body."""
        rendered = self.body
        for key, value in values.items():
            rendered = rendered.replace(f"{{{key}}}", value)
        return rendered


@lru_cache(maxsize=32)
def load_prompt(name: str, directory: Path = PROMPT_DIR) -> Prompt:
    """Read ``<name>.md`` and its frontmatter version."""
    path = directory / f"{name}.md"
    if not path.exists():
        raise PromptError(f"no prompt at {path}")

    match = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
    if match is None:
        raise PromptError(f"{path.name} has no frontmatter block")

    meta = dict(line.split(":", 1) for line in match.group("meta").splitlines() if ":" in line)
    version = meta.get("version", "").strip()
    if not version:
        # An untraceable prompt makes every trace that references it untraceable too.
        raise PromptError(f"{path.name} has no version in its frontmatter")

    return Prompt(name=name, version=version, body=match.group("body").strip())
