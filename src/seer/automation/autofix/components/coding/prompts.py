import textwrap
from typing import Literal

from seer.automation.autofix.components.coding.models import (
    CodeChangesPromptXml,
    RootCausePlanTaskPromptXml,
)
from seer.automation.autofix.components.root_cause.models import RootCauseAnalysisItem
from seer.automation.autofix.components.solution.models import SolutionTimelineEvent
from seer.automation.autofix.prompts import format_instruction
from seer.automation.models import EventDetails


class CodingPrompts:
    @staticmethod
    def format_system_msg(repo_familiarity_notes: str | None = None):
        repo_context_block = (
            f"REPO CONTEXT — read before any edit:\n{repo_familiarity_notes}\n\n"
            if repo_familiarity_notes
            else ""
        )
        return textwrap.dedent(
            """\
            You are an exceptional principal engineer that is amazing at finding and fixing issues in codebases.

            You have two kinds of tools:
              - Search/inspect tools (grep_search, find_files, semantic_file_search, view_file, expand_document, explain_file, tree, view_diff) — for orienting yourself in the codebase.
              - Edit tools (str_replace, create_file, insert_text, undo_edit) — for **applying the fix**. These are how you actually change the code; describing a change in text accomplishes nothing.

            CRITICAL LOOP RULE: the agent loop terminates the moment you return a response that does not include a tool call. **Never** end a turn with a text-only response unless every required edit has already been applied via the edit tools in this run. If you have just identified what to change, the next turn must be a tool call — not a narration of what you intend to do. If you have just finished applying an edit, either continue with the next tool call or, if every required edit is now applied, you may end the run.

            FOLLOW THE FINAL_SOLUTION_PLAN EXACTLY. The plan in the next user message is your contract. Do not invent additional changes (e.g., don't decide to remove an unrelated `import` you happened to spot). Do not skip the planned change because you "couldn't find" something — if a path or symbol in the plan doesn't appear where you expect, search a little more, but the planned edit at the planned file is what gets applied. Anything outside the plan is scope creep — do not do it.

            DO NOT SUBSTITUTE A SMALLER CHANGE. If the planned change is multi-line, structural, or doesn't map to a single tidy `str_replace`, that is NOT a signal to fall back to a tiny adjacent change (a stray `import`, a comment, a one-line typo fix). It IS a signal to express the planned change as multiple `str_replace` calls, an `insert_text` to introduce a block at a line, or a `create_file` for new modules. The diff you ship MUST be a faithful implementation of the plan — a small unrelated edit is worse than no edit, because it opens a PR that looks like progress but doesn't fix the bug. If after a few honest attempts you genuinely cannot express the planned change with the edit tools, end the run with a one-sentence text response naming the specific obstacle (e.g. "Could not apply the ir_module_module check: the existing method signature in module_graph.py uses a decorator I couldn't pattern-match safely."). That is the correct failure mode; substituting unrelated changes is not.

            HALLUCINATION GUARDS — three checks before any edit lands:
              1. CAUSAL CONNECTION. Before invoking an edit tool, be able to state in ONE concrete sentence how the proposed change prevents the specific error in the stack trace. Vague rationales like "this likely prepares for…" or "this may help by…" are red flags — they signal you're guessing, not fixing. If you can't state the causal link concretely, end the run with a text response naming what you'd need to investigate first.
              2. SYMBOL USAGE. If you add an `import`, the imported symbol(s) MUST appear in the resulting file body. If you remove a usage, the import should come out too. Mentally run `pyflakes` against your changed file before finishing.
              3. FILE TARGETING. The file printing the stack trace is rarely the file that needs the fix when the error is about MISSING STATE — "database X doesn't exist", "table Y missing", "module Z not installed", "config K not set" almost always live in config/state/migration code, not in the source file emitting the error. If the plan's target file feels wrong for the symptom class, surface that doubt in your text outcome instead of editing the wrong file.

            {repo_context_block}HANDLING SEARCH FAILURES: if `grep_search`, `find_files`, or any search tool errors or times out, do not despair and do not give up the run. Switch strategy:
              - `view_file` (or `expand_document`) the exact file path named in the plan to confirm the snippet you need to replace.
              - Then call `str_replace` on that file with `old_str` = an exact, unique block from what you just saw and `new_str` = the replacement.
            The plan tells you the file and the change. You do not need to find anything else.

            EDIT TOOL PARAMETERS: `str_replace` requires `old_str` and `new_str` as non-empty strings. `create_file` requires `file_text`. `insert_text` requires `insert_line` and `insert_text`. Never call an edit tool with empty or missing required arguments — that wastes an iteration and returns a tool error. Form the arguments fully before the tool call.

            READ THE TOOL RESPONSE. A successful edit returns exactly "Change applied successfully." Any response that starts with "Error:" or contains "No changes were made" means **the change was not applied**. The most common cause is that your `old_str` doesn't match the file byte-for-byte — usually a whitespace or indentation difference. When that happens, call `view_file` on the target path to see the exact bytes (note: `view_file` prepends `N: ` line numbers; strip them before copying into `old_str`), pick a smaller unique block from what you viewed, and retry. Do not re-issue the same `old_str` after it failed — the agent will dedup the identical call and you'll burn the iteration. Do not declare success while the most recent edit-tool response was an error.

            FINISHING THE RUN: when every change in the plan has been applied via the edit tools **and the latest edit-tool response was "Change applied successfully."**, end the run with a brief one-sentence text-only response describing what changed (e.g. "Applied the ir_module_module existence check to _update_from_database in module_graph.py."). The text-only response is your "I'm done" signal — but only after a real applied-successfully edit. If your last edit attempt errored, you have not finished; either fix the `old_str` and retry, or back out via `undo_edit` and try a different approach.

            You succeed by calling the edit tools to produce a concrete diff. After you have enough context to make the change, stop searching and apply it. If you are unsure between two approaches, pick one and apply it — you can always `undo_edit` and try again.

            When passing paths into tools, the codebase of each repo is at the root of the repo, there is no "/repo/src", it's just "/src". """
        ).format(repo_context_block=repo_context_block)

    @staticmethod
    def format_extra_root_cause_instruction(instruction: str):
        return f"The user has provided the following instruction for the fix along with the root cause: {format_instruction(instruction)}"

    @staticmethod
    def format_original_instruction(instruction: str):
        return f"Earlier, the user provided context: {format_instruction(instruction)}"

    @staticmethod
    def format_root_cause(root_cause: RootCauseAnalysisItem | str | None):
        if root_cause is None:
            return ""

        if isinstance(root_cause, RootCauseAnalysisItem):
            return f"""The steps to reproduce the root cause of the issue have been identified: {RootCausePlanTaskPromptXml.from_root_cause(
                    root_cause
                ).to_prompt_str()}"""
        else:
            return f"The user has provided the following instruction for the fix: {root_cause}"

    @staticmethod
    def format_custom_solution(custom_solution: str | None):
        if not custom_solution:
            return "No plan provided."

        return custom_solution

    @staticmethod
    def format_solution_item(index: int, solution_item: SolutionTimelineEvent):
        solution_item_content = (
            solution_item.code_snippet_and_analysis + "\n"
            if solution_item.code_snippet_and_analysis
            else ""
        )
        return f"<step_{index+1}>\n{solution_item.title}\n{solution_item_content}</step_{index+1}>"

    @staticmethod
    def format_auto_solution(auto_solution: list[SolutionTimelineEvent] | None):
        if not auto_solution:
            return "No plan provided."

        solution_str = ""
        solution_str = "\n".join(
            CodingPrompts.format_solution_item(i, solution_item)
            for i, solution_item in enumerate(
                [solution_item for solution_item in auto_solution if solution_item.is_active]
            )
        )
        return solution_str

    @staticmethod
    def format_fix_msg(
        *,
        custom_solution: str | None = None,
        auto_solution: list[SolutionTimelineEvent] | None = None,
        mode: Literal["all", "fix", "test"] = "fix",
        has_test: bool = False,
        event_details: EventDetails | None = None,
        root_cause: RootCauseAnalysisItem | str | None = None,
        repos_str: str,
    ):
        return textwrap.dedent(
            """\
            <goal>
            Follow the task of {mode_str} and make the necessary changes to the codebase. {filter_str}
            </goal>

            <guidelines>
            {has_fix_guidelines}
            {has_test_guidelines}
            </guidelines>

            <repositories>
            {repos_str}
            </repositories>

            <final_solution_plan>
            {solution_str}
            </final_solution_plan>

            <root_cause_of_issue>
            {root_cause_str}
            </root_cause_of_issue>

            <raw_issue_details>
            {event_details_str}
            </raw_issue_details>"""
        ).format(
            mode_str=(
                "fixing the issue"
                if mode == "fix"
                else (
                    "writing a unit test to reproduce the issue and assert the planned solution (following test-driven development)"
                    if mode == "test"
                    else "writing a unit test to reproduce the issue and assert the planned solution (following test-driven development) and then fixing the issue"
                )
            ),
            filter_str=(
                "Use the planned solution to inform the test, but do NOT implement the solution. Only write the test."
                if mode == "test"
                else "You should exactly follow the final_solution_plan, do not add any additional steps or changes."
            ),
            has_fix_guidelines=(
                "- Follow the planned solution EXACTLY, but you can add on to it or modify it if necessary to make the solution work as a complete implementation. Do not add any unnecessary changes.\n"
                "- Apply each change by calling one of the edit tools (`str_replace`, `create_file`, `insert_text`). A textual answer that does not invoke an edit tool is a failed run — the only successful outcome of this step is a tool-call sequence that produces the planned diff.\n"
                "- Search no more than a few times to confirm what you need to change. Once the target file and surrounding context are clear, switch to the edit tools immediately."
                if mode == "fix" or mode == "all"
                else ""
            ),
            has_test_guidelines=(
                "- Examine any existing tests to determine existing testing patterns in the codebase and if there is an appropriate test suite to add your test to. If not, create a new test. Make sure that your test case fits in well with the codebase."
                if mode == "test" or mode == "all" or has_test
                else ""
            ),
            steps_example_str=CodeChangesPromptXml.get_example().to_prompt_str(),
            solution_str=(
                CodingPrompts.format_custom_solution(custom_solution)
                if custom_solution
                else CodingPrompts.format_auto_solution(auto_solution)
            ),
            root_cause_str=CodingPrompts.format_root_cause(root_cause),
            event_details_str=(event_details.format_event() if event_details else ""),
            repos_str=repos_str,
        )

    @staticmethod
    def format_missing_msg(
        missing_files: list[str], existing_files: list[str], correct_paths: list[str]
    ):
        text = ""

        if correct_paths:
            text += (
                f"The following code changes are formatted correctly: {', '.join(correct_paths)}\n"
            )
            text += "But..."
        if missing_files:
            text += f"The following files don't exist, yet you are trying to modify them: {', '.join(missing_files)}\n"
        if existing_files:
            text += f"The following files already exist, yet you are trying to create them: {', '.join(existing_files)}\n"

        text += "\nPlease fix the above issues by correcting the file paths or correcting the type (file_create, file_change, or file_delete) and output your answer in the correct format again. Re-write your WHOLE answer, including the already-correct changes."

        return text

    @staticmethod
    def format_xml_format_fix_msg():
        example = CodeChangesPromptXml.get_example().to_prompt_str()
        return textwrap.dedent(
            """\
            Your previous response had invalid XML formatting. Please provide your response again with valid XML tag, fields, and attributes. Again, here is the example of the correct format:\n{example}"""
        ).format(example=example)

    @staticmethod
    def format_is_obvious_msg(
        root_cause: RootCauseAnalysisItem | str,
        original_instruction: str | None,
        root_cause_extra_instruction: str | None,
        custom_solution: str | None,
        auto_solution: list[SolutionTimelineEvent] | None,
        mode: Literal["all", "fix", "test"] = "fix",
    ):
        return (
            textwrap.dedent(
                """\
                Here is an issue in our codebase:

                {original_instruction}
                {root_cause_str}{root_cause_extra_instruction}

                <solution_plan>
                {solution_str}
                </solution_plan>

                Does the code change needed for {mode_str} exist ONLY in files you can already see in your context here or do you need to look at other files?"""
            )
            .format(
                mode_str=(
                    "fixing the issue"
                    if mode == "fix"
                    else (
                        "writing a unit test to reproduce the issue"
                        if mode == "test"
                        else "writing a unit test and fixing the issue"
                    )
                ),
                root_cause_str=CodingPrompts.format_root_cause(root_cause),
                original_instruction=(
                    ("\n" + CodingPrompts.format_original_instruction(original_instruction))
                    if original_instruction
                    else ""
                ),
                root_cause_extra_instruction=(
                    (
                        "\n"
                        + CodingPrompts.format_extra_root_cause_instruction(
                            root_cause_extra_instruction
                        )
                    )
                    if root_cause_extra_instruction
                    else ""
                ),
                solution_str=(
                    CodingPrompts.format_custom_solution(custom_solution)
                    if custom_solution
                    else CodingPrompts.format_auto_solution(auto_solution)
                ),
            )
            .strip()
        )
