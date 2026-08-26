#!/usr/bin/env python3
"""Regenerate the synthetic spec sheets and their manifest.

Thin runner. The generator itself lives in ``api/src/specguard/fixtures/`` so it is
covered by mypy --strict and importable from the tests and the M5 golden set.

    uv run --project api python fixtures/specs/generate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "src"))

from specguard.fixtures.generate import main  # noqa: E402

if __name__ == "__main__":
    main(Path(__file__).resolve().parent)
