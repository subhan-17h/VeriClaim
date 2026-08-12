"""The reviewed descriptions of what each source can and cannot answer.

Routing is a decision made from these descriptions and nothing else. That is the whole
point of keeping them in a reviewed file: the router's prompt stays domain-free, adding a
fifth source is a file rather than a prompt edit, and what the system claims each source
covers is written down somewhere a person signed off on.

The loader is strict for the same reason the schema contexts' loader is: a capability
silently dropped for being malformed removes a source from routing, and the answer that
follows is confidently incomplete rather than obviously broken.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from vericlaim.evidence import SOURCE_TYPES
from vericlaim.orchestrator.sources import (
    SOURCES_FILE,
    SourceCapability,
    SourceError,
    capability_detail,
    load_capabilities,
)


def entry(**overrides: Any) -> dict[str, Any]:
    base = {
        "name": "policy",
        "tool": "search_policy",
        "title": "Policy wordings",
        "holds": "The wordings the insurer issues.",
        "answers": ["what a wording states about a peril"],
        "cannot_answer": ["how many times something happened"],
        "citation": "document > page > clause",
    }
    base.update(overrides)
    return base


def write(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return path


# ------------------------------------------------------------------ loading


def test_a_capability_file_becomes_capabilities_keyed_by_source(tmp_path: Path) -> None:
    capabilities = load_capabilities(write(tmp_path, [entry()]))

    assert list(capabilities) == ["policy"]
    assert capabilities["policy"].tool == "search_policy"
    assert capabilities["policy"].answers == ("what a wording states about a peril",)


def test_a_source_nobody_can_cite_is_not_a_source(tmp_path: Path) -> None:
    """The name is the evidence vocabulary. A capability named outside it routes a
    question to a tool whose evidence the citation layer cannot place."""
    with pytest.raises(SourceError, match="claims_database"):
        load_capabilities(write(tmp_path, [entry(name="claims_database")]))


@pytest.mark.parametrize(
    "field", ["tool", "title", "holds", "citation"]
)
def test_a_blank_description_is_a_broken_capability(
    tmp_path: Path, field: str
) -> None:
    with pytest.raises(SourceError, match=field):
        load_capabilities(write(tmp_path, [entry(**{field: "  "})]))


@pytest.mark.parametrize("field", ["answers", "cannot_answer"])
def test_a_capability_states_both_what_it_covers_and_what_it_does_not(
    tmp_path: Path, field: str
) -> None:
    """The limits are load-bearing. A router told only what a source covers routes
    everything to whichever description sounds closest."""
    with pytest.raises(SourceError, match=field):
        load_capabilities(write(tmp_path, [entry(**{field: []})]))


def test_the_same_source_described_twice_is_a_mistake(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="policy"):
        load_capabilities(write(tmp_path, [entry(), entry()]))


def test_a_key_nobody_reads_is_rejected_rather_than_ignored(tmp_path: Path) -> None:
    """A misspelled `cautions` silently dropped is a reviewed instruction that never
    reaches the model."""
    with pytest.raises(SourceError, match="cauton"):
        load_capabilities(write(tmp_path, [entry(cauton="typo")]))


def test_a_file_that_is_not_a_list_of_capabilities_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text("policy: search_policy\n", encoding="utf-8")

    with pytest.raises(SourceError):
        load_capabilities(path)


def test_a_missing_file_is_named_in_the_error(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="sources.yaml"):
        load_capabilities(tmp_path / "sources.yaml")


# ------------------------------------------------------------------ the payload


def test_a_capability_reaches_the_model_as_data(tmp_path: Path) -> None:
    detail = capability_detail(load_capabilities(write(tmp_path, [entry()]))["policy"])

    assert detail == {
        "name": "policy",
        "title": "Policy wordings",
        "holds": "The wordings the insurer issues.",
        "answers": ["what a wording states about a peril"],
        "cannot_answer": ["how many times something happened"],
        "citation": "document > page > clause",
    }


def test_the_tool_is_not_offered_to_the_model() -> None:
    """The router chooses a source, not a function. Which callable serves a source is
    the graph's business, and a model that saw tool names would start naming them."""
    capability = SourceCapability(
        name="policy",
        tool="search_policy",
        title="t",
        holds="h",
        answers=("a",),
        cannot_answer=("b",),
        citation="c",
    )

    assert "tool" not in capability_detail(capability)


# ------------------------------------------------------------------ the committed file


def test_all_four_sources_are_described() -> None:
    """Every source stays first-class. One missing from this file is one the system
    silently stops consulting."""
    capabilities = load_capabilities(SOURCES_FILE)

    assert sorted(capabilities) == sorted(SOURCE_TYPES)


def test_each_described_source_names_a_tool_that_exists() -> None:
    import importlib

    modules = {
        "policy": "vericlaim.policy.tool",
        "sql": "vericlaim.sql.tool",
        "spreadsheet": "vericlaim.sheets.tool",
        "scanned_pdf": "vericlaim.scanned.tool",
    }
    for name, capability in load_capabilities(SOURCES_FILE).items():
        module = importlib.import_module(modules[name])
        assert hasattr(module, capability.tool), capability.tool
