r"""End-to-end replay of CodingComponent against a recorded Gemini cassette.

Tier 2 of the seer dev loop: edit a prompt or tool description, run
`pytest tests/automation/autofix/components/coding/test_coding_gemini_e2e.py
-xvs`, and see in seconds (cassette replay) — or ~30s (cassette miss → real
Gemini call) — how Gemini's tool-call sequence changes. No image rebuild,
no VM redeploy required.

The fixture comes from a real autofix run (group 48, openupgrade-lab issue
that surfaced module_graph.ir_module_module-not-found in OpenUpgrade 19).
The dump lives at tests/data/autofix/run_12_state.json. To redump after a
schema change:

    ssh root@sentry-seer-1 \
      "docker exec sentry-self-hosted-seer-db-1 \
         psql -U root -d seer -tA -c \
         \"SELECT row_to_json(t) FROM run_state t WHERE id = 12\"" \
      | python3 -m json.tool > tests/data/autofix/run_12_state.json
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from seer.automation.agent.models import Message
from seer.automation.autofix.autofix_context import AutofixContext
from seer.automation.autofix.components.coding.component import CodingComponent
from seer.automation.autofix.components.coding.models import CodingRequest
from seer.automation.autofix.models import AutofixContinuation
from seer.automation.models import EventDetails
from seer.automation.state import LocalMemoryState

FIXTURE_PATH = pathlib.Path(__file__).resolve().parents[4] / "data/autofix/run_12_state.json"

# The fixture references files in OpenUpgrade. We hand the agent canned
# stubs for the read-side tools so it doesn't bail with "the repo is empty"
# (which is what happens with a bare _download_repos mock — see the early
# cassette captures in the PR description). Content is synthetic but
# plausible — the test observes *what tools the agent decides to call*,
# not whether the edit would actually apply cleanly.
STUB_FILE_CONTENT = """\
from odoo.modules.module_graph import ModuleGraph


def _update_from_database(self, *args, **kwargs) -> None:
    ModuleGraph._update_from_database._original_method(self, *args, **kwargs)
    if (
        "base" in self._modules
        and self._modules["base"].demo
        and self._modules["base"].installed_version < odoo.release.major_version
    ):
        self._cr.execute("UPDATE ir_module_module SET demo = false")


ModuleGraph._update_from_database._original_method = ModuleGraph._update_from_database
ModuleGraph._update_from_database = _update_from_database
"""

STUB_TREE = """\
.
openupgrade_framework/
openupgrade_framework/odoo_patch/
openupgrade_framework/odoo_patch/odoo/
openupgrade_framework/odoo_patch/odoo/modules/
openupgrade_framework/odoo_patch/odoo/modules/module_graph.py
openupgrade_framework/odoo_patch/odoo/modules/loading.py
openupgrade_scripts/
openupgrade_scripts/__manifest__.py
"""

STUB_RIPGREP = """\
openupgrade_framework/odoo_patch/odoo/modules/module_graph.py:42:    cr.execute("SELECT name, state, demo AS dbdemo, latest_version AS installed_version FROM ir_module_module WHERE name = %s", (name,))
openupgrade_framework/odoo_patch/odoo/modules/module_graph.py:51:    row = cr.fetchone()
"""


def _load_continuation() -> AutofixContinuation:
    raw = json.loads(FIXTURE_PATH.read_text())
    return AutofixContinuation.model_validate(raw["value"])


@pytest.fixture(autouse=True)
def setup_app():
    """Overrides tests/conftest.py's autouse `setup_app` fixture for this
    file. The conftest version brings up the full Flask app + Postgres
    schema, which we don't need: this test mocks AutofixContext entirely
    and bypasses DB-touching paths. Skipping the boot saves ~5s per
    invocation and lets the loop run without a test-db container.
    """
    yield


@pytest.fixture
def coding_state() -> LocalMemoryState[AutofixContinuation]:
    return LocalMemoryState(_load_continuation())


@pytest.fixture
def coding_context(coding_state):
    """A MagicMock(spec=AutofixContext) with `state` swapped for a real
    LocalMemoryState. DB-touching methods (store_memory, get_memory,
    get_issue_summary) auto-stub via MagicMock so we don't need Postgres.
    """
    ctx = MagicMock(spec=AutofixContext)
    ctx.state = coding_state
    ctx.repos = coding_state.get().request.repos
    ctx.organization_id = coding_state.get().request.organization_id
    ctx.project_id = coding_state.get().request.project_id

    # Patch read paths used during prefill + by the edit-tool handlers
    ctx.autocorrect_repo_name = MagicMock(side_effect=lambda name: name)
    ctx.autocorrect_file_path = MagicMock(side_effect=lambda path, **kw: path)
    ctx.get_file_contents = MagicMock(return_value=STUB_FILE_CONTENT)
    ctx.does_file_exist = MagicMock(return_value=True)
    ctx.process_event_paths = MagicMock()

    # The agent calls store_memory/get_memory through the context; DB-free
    # spec-mock returns MagicMock by default which is fine for our assertion.
    ctx.get_memory = MagicMock(return_value=[])
    ctx.store_memory = MagicMock()

    ctx.event_manager = MagicMock()
    ctx.event_manager.add_log = MagicMock()
    ctx.event_manager.send_insight = MagicMock()
    return ctx


@pytest.fixture
def coding_request(coding_state) -> CodingRequest:
    """Builds CodingRequest exactly as coding_step.AutofixCodingStep would."""
    state = coding_state.get()
    root_cause, root_cause_extra = state.get_selected_root_cause()
    solution, mode = state.get_selected_solution()
    assert root_cause is not None, "fixture must have a selected root cause"
    assert solution is not None, "fixture must have a selected solution"

    event_details = EventDetails.from_event(
        event=state.request.issue.events[0], issue_title=state.request.issue.title
    )
    return CodingRequest(
        event_details=event_details,
        root_cause=root_cause,
        solution=solution,
        original_instruction=state.request.instruction,
        root_cause_extra_instruction=root_cause_extra,
        summary=state.request.issue_summary,
        profile=state.request.profile,
        mode=mode or "fix",
    )


@pytest.mark.vcr(
    # SA OAuth uses a JWT in the request body (urn:ietf:params:oauth:grant-type:jwt-bearer).
    # Filter the `assertion` field so the signed-JWT (which leaks the SA email
    # and private-key kid) never lands in the recorded cassette.
    filter_post_data_parameters=["client_secret", "refresh_token", "assertion"],
)
def test_coding_step_calls_at_least_one_edit_tool(coding_context, coding_request):
    """Asserts the coding agent eventually invokes one of the Gemini edit
    tools (str_replace, create_file, insert_text). This is the regression
    signal we want green: if it goes red, our prompt or tool descriptions
    aren't nudging Gemini hard enough to *use* the edit surface.
    """
    edit_tool_names = {"str_replace", "create_file", "insert_text"}

    captured_memory: list[Message] = []

    real_store_memory = coding_context.store_memory

    def capture_memory(key: str, memory: list[Message]) -> None:
        if key == "code":
            captured_memory.extend(memory)
        real_store_memory(key, memory)

    coding_context.store_memory = MagicMock(side_effect=capture_memory)

    base_tools_path = "seer.automation.autofix.tools.tools.BaseTools"
    with (
        patch(f"{base_tools_path}._download_repos", MagicMock()),
        patch(f"{base_tools_path}.tree", MagicMock(return_value=STUB_TREE)),
        patch(f"{base_tools_path}.run_ripgrep", MagicMock(return_value=STUB_RIPGREP)),
        patch(f"{base_tools_path}.find_files", MagicMock(return_value=STUB_TREE)),
        patch(
            f"{base_tools_path}.semantic_file_search",
            MagicMock(return_value="openupgrade_framework/odoo_patch/odoo/modules/module_graph.py"),
        ),
        patch(f"{base_tools_path}.expand_document", MagicMock(return_value=STUB_FILE_CONTENT)),
        patch(
            f"{base_tools_path}.explain_file",
            MagicMock(return_value="Recent commits touched ir_module_module SQL access."),
        ),
        # `view_file` and the edit tools all funnel through `handle_claude_tools`
        # which calls `_attempt_fix_path` to resolve the path against the
        # downloaded repo. With `_download_repos` stubbed there's no repo,
        # so we short-circuit path resolution to a pass-through. This is
        # what lets Gemini's view/str_replace/etc. calls reach the canned
        # `context.get_file_contents` / `does_file_exist` mocks.
        patch(
            f"{base_tools_path}._attempt_fix_path",
            MagicMock(side_effect=lambda path, *a, **kw: path),
        ),
    ):
        try:
            CodingComponent(coding_context).invoke(coding_request)
        except Exception:
            # The agent loop can raise late (e.g. Gemini stream timeout,
            # MaxIterations, MALFORMED_FUNCTION_CALL on a huge str_replace
            # diff). That's orthogonal to what this test asserts — we care
            # only that the model decided to *call edit tools at all*.
            # store_memory is invoked on every iteration so captured_memory
            # is already populated up to the point of failure.
            pass

    called_tool_names = {m.tool_call_function for m in captured_memory if m.tool_call_function}
    edit_calls = edit_tool_names & called_tool_names

    assert edit_calls, (
        f"Coding agent never called an edit tool. "
        f"Tools called: {sorted(called_tool_names)}. "
        f"Edit tools expected: {sorted(edit_tool_names)}."
    )
