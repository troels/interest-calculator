"""Shared test fixtures and paths."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CURVE_TXT = REPO_ROOT / "data" / "swap_rate_curve_2026-06-15.txt"


@pytest.fixture
def sample_curve_txt() -> Path:
    assert SAMPLE_CURVE_TXT.exists(), f"missing sample: {SAMPLE_CURVE_TXT}"
    return SAMPLE_CURVE_TXT
