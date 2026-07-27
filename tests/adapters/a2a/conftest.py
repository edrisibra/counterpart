"""Shared fixtures: the vendored, checksum-pinned spec artifacts.

See tests/data/README.md for provenance. These fixtures let tests verify our models
against the official machine-readable definitions instead of our own assumptions.
"""

import json
from pathlib import Path
from typing import Any

import pytest

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(scope="session")
def vendored_proto() -> str:
    """The normative a2a.proto at tag v1.0.1 (spec section 1.4)."""
    return (DATA_DIR / "a2a_v1.0.1.proto").read_text()


@pytest.fixture(scope="session")
def official_schema() -> dict[str, Any]:
    """The generated (non-normative) JSON Schema bundle published for v1.0.1."""
    data: dict[str, Any] = json.loads((DATA_DIR / "a2a_v1.0.1.schema.json").read_text())
    return data
