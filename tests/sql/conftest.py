"""Shared fixtures for the SQL source's tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from vericlaim.config import get_settings

SMOKE = Path(__file__).parents[2] / "scripts" / "smoke.py"


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def smoke():
    """Import ``scripts/smoke.py`` by path; scripts/ is deliberately not a package.

    Registered in ``sys.modules`` before execution because ``@dataclass(slots=True)``
    rebuilds its class and resolves annotations through the module entry.
    """
    spec = importlib.util.spec_from_file_location("vericlaim_smoke", SMOKE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)
