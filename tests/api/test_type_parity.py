"""The TypeScript union and the Python protocol must name the same events.

C-10's client is written against ``api/protocol.py``. Two hand-written definitions
drift, and the first symptom of drift is a shape error in a browser rather than a
failing test. This guard lives in the Python suite so it runs on every pytest, with no
Node required.

It checks event names only. Field-level parity would be a schema generator wearing a
test's clothes, and would need maintaining for five small shapes.
"""

from __future__ import annotations

import re

import pytest

from vericlaim.api.protocol import EVENT_NAMES
from vericlaim.config import PROJECT_ROOT

TYPES_TS = PROJECT_ROOT / "frontend" / "src" / "types.ts"

_EVENT_LITERAL = re.compile(r'event:\s*"([a-z_]+)"')


def _declared() -> set[str]:
    if not TYPES_TS.is_file():
        pytest.fail(f"{TYPES_TS} is missing; the client contract must exist")
    return set(_EVENT_LITERAL.findall(TYPES_TS.read_text(encoding="utf-8")))


def test_the_typescript_union_names_exactly_the_protocol_events() -> None:
    assert _declared() == set(EVENT_NAMES)


def test_the_client_contract_never_names_the_keepalive() -> None:
    """``ping`` is a property of the connection, not of the run. A client that typed it
    would be inviting callers to read the network as if it were run information."""
    assert "ping" not in _declared()
