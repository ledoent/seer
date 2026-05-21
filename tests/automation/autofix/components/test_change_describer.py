from unittest.mock import MagicMock

import pytest

from seer.automation.agent.client import LlmClient
from seer.automation.agent.models import (
    LlmGenerateStructuredResponse,
    LlmProviderType,
    LlmResponseMetadata,
    Usage,
)
from seer.automation.autofix.autofix_context import AutofixContext
from seer.automation.autofix.components.change_describer import (
    ChangeDescriptionComponent,
    ChangeDescriptionOutput,
    ChangeDescriptionPrompts,
    ChangeDescriptionRequest,
)
from seer.dependency_injection import Module


class TestChangeDescriptionPrompts:
    def test_title_format_hint_overrides_default(self):
        msg = ChangeDescriptionPrompts.format_default_msg(
            change_dump="diff",
            title_format_hint="strict OCA convention: [<series>][TAG] <scope>: <summary>",
        )
        assert "strict OCA convention" in msg
        # When a strict format is dictated, we deliberately drop the default
        # `fix:` template — it would contradict the per-repo format.
        assert "'fix:'" not in msg

    def test_title_format_hint_wins_over_previous_commits(self):
        msg = ChangeDescriptionPrompts.format_default_msg(
            change_dump="diff",
            previous_commits=["feat: add thing", "chore: remove other"],
            title_format_hint="HOUSE STYLE WINS",
        )
        # Per-repo profile beats noisy previous-commit examples.
        assert "HOUSE STYLE WINS" in msg
        assert "feat: add thing" not in msg

    def test_previous_commits_used_when_no_profile(self):
        msg = ChangeDescriptionPrompts.format_default_msg(
            change_dump="diff",
            previous_commits=["fix(api): null check"],
        )
        assert "fix(api): null check" in msg

    def test_falls_back_to_fix_prefix_when_nothing_provided(self):
        msg = ChangeDescriptionPrompts.format_default_msg(change_dump="diff")
        assert "'fix:'" in msg


def _ok_response(title: str, branch: str) -> LlmGenerateStructuredResponse:
    return LlmGenerateStructuredResponse(
        parsed=ChangeDescriptionOutput(
            title=title,
            description="some description",
            branch_name=branch,
        ),
        metadata=LlmResponseMetadata(
            model="gemini-2.5-flash",
            provider_name=LlmProviderType.GEMINI,
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        ),
    )


@pytest.fixture
def component():
    mock_context = MagicMock(spec=AutofixContext)
    mock_context.state = MagicMock()
    # update() must work as a context manager that yields a mutable-ish object.
    mock_context.state.update.return_value.__enter__.return_value = MagicMock()
    return ChangeDescriptionComponent(mock_context)


class TestChangeDescriptionComponentBranchPrefix:
    def test_default_branch_prefix(self, component):
        mock_llm = MagicMock()
        mock_llm.generate_structured.return_value = _ok_response("fix: foo", "fix-foo")

        module = Module()
        module.constant(LlmClient, mock_llm)

        with module:
            output = component.invoke(
                ChangeDescriptionRequest(change_dump="diff", repo_full_name="getsentry/sentry")
            )

        assert output is not None
        assert output.branch_name == "seer/fix-foo"

    def test_openupgrade_branch_prefix(self, component):
        mock_llm = MagicMock()
        mock_llm.generate_structured.return_value = _ok_response(
            "[19.0][OU-FIX] base: rename groups_id", "base-groups-id"
        )

        module = Module()
        module.constant(LlmClient, mock_llm)

        with module:
            output = component.invoke(
                ChangeDescriptionRequest(change_dump="diff", repo_full_name="ledoent/OpenUpgrade")
            )

        assert output is not None
        assert output.branch_name == "19.0-fix-base-groups-id"

    def test_unknown_repo_uses_default_prefix(self, component):
        mock_llm = MagicMock()
        mock_llm.generate_structured.return_value = _ok_response("fix: bar", "bar-fix")

        module = Module()
        module.constant(LlmClient, mock_llm)

        with module:
            output = component.invoke(
                ChangeDescriptionRequest(change_dump="diff", repo_full_name=None)
            )

        assert output is not None
        assert output.branch_name == "seer/bar-fix"


class TestChangeDescriptionComponentPromptInjection:
    def test_openupgrade_profile_injects_title_hint(self, component):
        mock_llm = MagicMock()
        mock_llm.generate_structured.return_value = _ok_response("ignored", "ignored")

        module = Module()
        module.constant(LlmClient, mock_llm)

        with module:
            component.invoke(
                ChangeDescriptionRequest(
                    change_dump="diff",
                    previous_commits=["unrelated previous commit"],
                    repo_full_name="ledoent/OpenUpgrade",
                )
            )

        prompt = mock_llm.generate_structured.call_args[1]["prompt"]
        assert "OCA/openupgrade convention" in prompt
        # Previous commits should be suppressed in favor of the strict format.
        assert "unrelated previous commit" not in prompt

    def test_no_profile_passes_previous_commits(self, component):
        mock_llm = MagicMock()
        mock_llm.generate_structured.return_value = _ok_response("ignored", "ignored")

        module = Module()
        module.constant(LlmClient, mock_llm)

        with module:
            component.invoke(
                ChangeDescriptionRequest(
                    change_dump="diff",
                    previous_commits=["feat: a", "chore: b"],
                    repo_full_name="getsentry/sentry",
                )
            )

        prompt = mock_llm.generate_structured.call_args[1]["prompt"]
        assert "feat: a" in prompt
