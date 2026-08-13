"""The candidate arbiter's prompts are inside the domain-free rule and had no guard.

Arbitration writes assertions about SQL and grades candidates against them, so it decides
which query the answer comes from. That is squarely inside the rule design non-negotiable 9
states. Both prompts already complied; neither was proven to, which is the same thing as
being one careless edit away from not complying.
"""

from __future__ import annotations

import re

import pytest

from vericlaim.sql.contexts import load_contexts
from vericlaim.sql.unit_tester import EVALUATE_SYSTEM_PROMPT, GENERATE_SYSTEM_PROMPT

CONTEXT_DIR = "contexts/sql"


@pytest.mark.parametrize(
    "prompt",
    [GENERATE_SYSTEM_PROMPT, EVALUATE_SYSTEM_PROMPT],
    ids=["generate", "evaluate"],
)
def test_the_prompt_names_no_table_or_column_of_the_corpus(prompt: str) -> None:
    """The arbiter reads the tables' conventions from the contexts it is handed. Naming
    one here would be the second copy, and the copy in a prompt is the one nobody
    re-reads when the schema changes."""
    contexts = load_contexts(CONTEXT_DIR)
    identifiers = {context.table for context in contexts.values()} | {
        name for context in contexts.values() for name in context.column_names
    }

    named = sorted(
        identifier
        for identifier in identifiers
        if re.search(rf"\b{re.escape(identifier)}\b", prompt, re.I)
    )

    assert named == []


@pytest.mark.parametrize(
    "prompt",
    [GENERATE_SYSTEM_PROMPT, EVALUATE_SYSTEM_PROMPT],
    ids=["generate", "evaluate"],
)
def test_the_prompt_defers_the_domain_rules_to_the_reviewed_conventions(
    prompt: str,
) -> None:
    """Positively, not only by absence: the arbiter is told to judge under the supplied
    conventions, which is what makes the contexts the single home for the rules rather
    than merely the place they happen not to be duplicated from."""
    assert "convention" in prompt.lower()
