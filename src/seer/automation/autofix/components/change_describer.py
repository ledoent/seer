import textwrap

import sentry_sdk
from langfuse import observe

from seer.automation.agent.client import GeminiProvider, LlmClient
from seer.automation.autofix.autofix_context import AutofixContext
from seer.automation.autofix.repo_profiles import get_profile_for_full_name
from seer.automation.component import BaseComponent, BaseComponentOutput, BaseComponentRequest
from seer.dependency_injection import inject, injected


class ChangeDescriptionRequest(BaseComponentRequest):
    change_dump: str
    hint: str | None = None
    previous_commits: list[str] | None = None
    repo_full_name: str | None = None
    """`owner/repo` of the codebase the change targets. Used to look up a
    per-repo profile (commit/PR title format + branch prefix). Optional;
    falls back to the generic `seer/<title>` + `fix:` convention.
    """


class ChangeDescriptionOutput(BaseComponentOutput):
    title: str
    description: str
    branch_name: str


class ChangeDescriptionPrompts:
    @staticmethod
    def format_default_msg(
        change_dump: str,
        hint: str | None = None,
        previous_commits: list[str] | None = None,
        title_format_hint: str | None = None,
    ):
        if title_format_hint:
            # Per-repo override wins over conventional-commits + previous-commit
            # examples: when a repo has a strict house style (e.g. OCA's
            # `[<series>][TAG] <scope>: <summary>`), examples from the log
            # only confuse the model.
            formatting_instructions = title_format_hint
        elif previous_commits:
            joined_commits = ", ".join(f'"{commit}"' for commit in previous_commits)
            formatting_instructions = f"Describe the change in a way that is easy to understand, closely following the formatting of previous commit titles in the repo, such as: {joined_commits}"
        else:
            formatting_instructions = "The title should be all lowercase except for symbol/variable names, prefixed with a 'fix:' prefix, and describe the change in a way that is easy to understand."

        return textwrap.dedent(
            """\
            Describe the following changes:

            {change_dump}

            {hint}
            You must output a title and description of the changes that are quickly readable for other engineers. Follow the format of:

            - Title: The most important specific change that is being made. {formatting_instructions}
            - Description: A brief bulleted list of the changes.
            - Branch Name: A short name for the branch that will be created to make the changes"""
        ).format(
            change_dump=change_dump,
            hint=f"In the style of: {hint}\n" if hint else "",
            formatting_instructions=formatting_instructions,
        )


class ChangeDescriptionComponent(BaseComponent[ChangeDescriptionRequest, ChangeDescriptionOutput]):
    context: AutofixContext

    @observe(name="Change Describer")
    @sentry_sdk.trace
    @inject
    def invoke(
        self, request: ChangeDescriptionRequest, llm_client: LlmClient = injected
    ) -> ChangeDescriptionOutput | None:
        profile = get_profile_for_full_name(request.repo_full_name)
        title_format_hint = profile.title_format_hint if profile else None
        # Default seer/<slug>; OCA-style repos override (e.g. `19.0-fix-`)
        branch_prefix = profile.branch_prefix if profile else "seer/"
        # When a profile dictates a strict title format, ignore previous-commit
        # examples — they tend to drift from the house style and dilute the
        # instruction we just gave the model.
        previous_commits = None if title_format_hint else request.previous_commits

        output = llm_client.generate_structured(
            prompt=ChangeDescriptionPrompts.format_default_msg(
                change_dump=request.change_dump,
                hint=request.hint,
                previous_commits=previous_commits,
                title_format_hint=title_format_hint,
            ),
            model=GeminiProvider.model("gemini-3.1-flash-lite"),
            response_format=ChangeDescriptionOutput,
        )
        data = output.parsed

        data.branch_name = f"{branch_prefix}{data.branch_name}"

        with self.context.state.update() as cur:
            cur.usage += output.metadata.usage

        if data is None:  # type: ignore[unreachable]
            return None  # type: ignore[unreachable]

        return data
