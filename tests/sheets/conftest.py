"""Shared fixtures for the spreadsheet source's tests."""

from __future__ import annotations

import pytest

from vericlaim.config import get_settings


@pytest.fixture(scope="session")
def settings():
    return get_settings()
