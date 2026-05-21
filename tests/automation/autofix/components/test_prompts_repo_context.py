"""Direct unit tests for `<repo_context>` / `REPO CONTEXT` injection in the
solution + coding system prompts.

The cassette test (`test_coding_gemini_e2e.py`) used to be the only thing
exercising end-to-end that the per-repo familiarity notes land in the
system prompt. That test is currently skipped (see its docstring) while
we stabilize the cassette infra, so these tests cover the same
integration surface at the prompt-formatter level.
"""

import pytest

from seer.automation.autofix.components.coding.prompts import CodingPrompts
from seer.automation.autofix.components.solution.prompts import SolutionPrompts


class TestSolutionPromptRepoContext:
    def test_no_notes_omits_block(self):
        msg = SolutionPrompts.format_system_msg(repos_str="r", has_tools=True)
        assert "<repo_context>" not in msg
        # Sanity: the surrounding sections still render.
        assert "diagnosis_taxonomy" in msg
        assert "Remember:" in msg

    def test_notes_present_renders_block(self):
        notes = "Sentinel familiarity notes for tests."
        msg = SolutionPrompts.format_system_msg(
            repos_str="r", has_tools=True, repo_familiarity_notes=notes
        )
        assert "<repo_context>" in msg
        assert notes in msg
        assert "</repo_context>" in msg
        # Block must land BEFORE the trailing `Remember:` checklist.
        assert msg.index("<repo_context>") < msg.index("Remember:")

    def test_empty_string_notes_treated_as_no_block(self):
        msg = SolutionPrompts.format_system_msg(
            repos_str="r", has_tools=True, repo_familiarity_notes=""
        )
        # Empty string is falsy → no block injected.
        assert "<repo_context>" not in msg

    def test_notes_with_curly_braces_do_not_crash_format(self):
        # If a future profile's familiarity notes ever contain `{` or `}` they
        # must not collide with the .format() call inside format_system_msg.
        notes = "rule: use {key}-value syntax; avoid dict literals like {1: 2}"
        msg = SolutionPrompts.format_system_msg(
            repos_str="r", has_tools=True, repo_familiarity_notes=notes
        )
        assert notes in msg


class TestCodingPromptRepoContext:
    def test_no_notes_omits_block(self):
        msg = CodingPrompts.format_system_msg()
        assert "REPO CONTEXT" not in msg
        # Sanity: the surrounding sections still render.
        assert "HALLUCINATION GUARDS" in msg
        assert "HANDLING SEARCH FAILURES" in msg

    def test_notes_present_renders_block(self):
        notes = "Sentinel coding-side notes for tests."
        msg = CodingPrompts.format_system_msg(repo_familiarity_notes=notes)
        assert "REPO CONTEXT" in msg
        assert notes in msg
        # Block must land between HALLUCINATION GUARDS and HANDLING SEARCH FAILURES.
        assert msg.index("HALLUCINATION GUARDS") < msg.index("REPO CONTEXT")
        assert msg.index("REPO CONTEXT") < msg.index("HANDLING SEARCH FAILURES")

    def test_empty_string_notes_treated_as_no_block(self):
        msg = CodingPrompts.format_system_msg(repo_familiarity_notes="")
        # Empty string is falsy → no block injected.
        assert "REPO CONTEXT" not in msg

    def test_notes_with_curly_braces_do_not_crash_format(self):
        notes = "rule: avoid f-string templates like {x}+{y}; spell it out"
        msg = CodingPrompts.format_system_msg(repo_familiarity_notes=notes)
        assert notes in msg


class TestRealOpenUpgradeProfileRendersIntoBothPrompts:
    """Use the actual registered profile (not a synthetic sentinel) so a future
    edit to the profile's familiarity notes that breaks rendering is caught
    here, not at runtime in production.
    """

    @pytest.fixture
    def openupgrade_notes(self):
        from seer.automation.autofix.repo_profiles import get_profile_for_full_name

        profile = get_profile_for_full_name("ledoent/OpenUpgrade")
        assert profile is not None
        return profile.familiarity_notes

    def test_renders_into_solution_system_msg(self, openupgrade_notes):
        msg = SolutionPrompts.format_system_msg(
            repos_str="r", has_tools=True, repo_familiarity_notes=openupgrade_notes
        )
        assert "openupgrade_framework/odoo_patch/" in msg
        assert "monkey patches" in msg

    def test_renders_into_coding_system_msg(self, openupgrade_notes):
        msg = CodingPrompts.format_system_msg(repo_familiarity_notes=openupgrade_notes)
        assert "openupgrade_framework/odoo_patch/" in msg
        assert "monkey patches" in msg
