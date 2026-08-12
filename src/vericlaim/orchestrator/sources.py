"""What each source can answer, written down once and reviewed.

Routing decides which of four heterogeneous sources a question needs. That decision has
to come from a description of each source, and there are only two places such a
description can live: in the router's prompt, or in a file. It lives in a file here for
the same reasons the schema contexts do.

* **The prompt stays domain-free.** A prompt that described the corpus would answer that
  corpus and would have to be edited every time a source changed. A test asserts the
  router's prompt names none of these sources.
* **The claim is reviewable.** "What this system believes each source covers" is exactly
  the kind of statement that is wrong quietly. In a file it can be read, corrected, and
  diffed; in a prompt it is buried in instructions nobody re-reads.
* **A fifth source is a file, not a code change.**

Each capability states what its source holds, what it can answer, **what it cannot**, and
what a citation from it looks like. The limits earn their place: a router told only what
each source covers will route every question to whichever description sounds nearest.

The `name` is not free text -- it must be one of the evidence source types. A capability
named outside that vocabulary would route a question to a tool whose evidence the
citation layer cannot place, which is a failure two nodes further on rather than here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from vericlaim.config import PROJECT_ROOT
from vericlaim.evidence import SOURCE_TYPES

SOURCES_FILE = PROJECT_ROOT / "contexts" / "sources.yaml"

_REQUIRED_TEXT = ("tool", "title", "holds", "citation")
_REQUIRED_LISTS = ("answers", "cannot_answer")
_FIELDS = frozenset({"name", *_REQUIRED_TEXT, *_REQUIRED_LISTS})


class SourceError(ValueError):
    """Raised for a capability that cannot be trusted to describe a source.

    Fatal rather than skipped: a capability dropped for being malformed removes its
    source from routing entirely, and the answer that follows is confidently incomplete
    rather than obviously broken.
    """


@dataclass(frozen=True, slots=True)
class SourceCapability:
    """One source, described in the terms a routing decision is made in."""

    name: str
    tool: str
    title: str
    holds: str
    answers: tuple[str, ...]
    cannot_answer: tuple[str, ...]
    citation: str


def load_capabilities(path: Path | str = SOURCES_FILE) -> dict[str, SourceCapability]:
    """Read the reviewed capability file, keyed by source name."""
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"No source capability file at {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SourceError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, list) or not raw:
        raise SourceError(f"{path} must be a non-empty list of source capabilities")

    capabilities: dict[str, SourceCapability] = {}
    for entry in raw:
        capability = _capability(entry, path)
        if capability.name in capabilities:
            raise SourceError(f"{path} describes {capability.name} more than once")
        capabilities[capability.name] = capability
    return capabilities


def capability_detail(capability: SourceCapability) -> dict[str, Any]:
    """The capability as the router sees it.

    The tool is deliberately withheld. The router chooses a source; which callable
    serves that source is the graph's business, and a model shown function names starts
    returning them.
    """
    return {
        "name": capability.name,
        "title": capability.title,
        "holds": capability.holds,
        "answers": list(capability.answers),
        "cannot_answer": list(capability.cannot_answer),
        "citation": capability.citation,
    }


def _capability(entry: Any, path: Path) -> SourceCapability:
    if not isinstance(entry, Mapping):
        raise SourceError(f"{path} contains a capability that is not a mapping")

    unknown = sorted(set(entry) - _FIELDS)
    if unknown:
        raise SourceError(
            f"{path} capability has key(s) nothing reads: {', '.join(unknown)}"
        )

    name = str(entry.get("name") or "").strip()
    if name not in SOURCE_TYPES:
        raise SourceError(
            f"{path} describes a source named {name!r}, which is not one of the "
            f"evidence source types ({', '.join(SOURCE_TYPES)}). Evidence from it "
            "could not be cited."
        )

    values: dict[str, Any] = {"name": name}
    for field in _REQUIRED_TEXT:
        text = str(entry.get(field) or "").strip()
        if not text:
            raise SourceError(f"{path}: {name} declares no {field}")
        values[field] = text
    for field in _REQUIRED_LISTS:
        items = tuple(
            str(item).strip() for item in entry.get(field) or () if str(item).strip()
        )
        if not items:
            raise SourceError(f"{path}: {name} declares no {field}")
        values[field] = items

    return SourceCapability(**values)
