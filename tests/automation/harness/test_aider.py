"""Unit tests for AiderHarness.

The subprocess invocation is patched throughout — none of these tests
actually exec aider or git. Live verification happens via the integration
test in test_aider_dogfood.py (gated on ``AUTOFIX_HARNESS=aider``).
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from seer.automation.agent.models import Message, Usage
from seer.automation.harness.aider import (
    _AIDER_TIMEOUT_SECONDS,
    _GIT_CLONE_TIMEOUT_SECONDS,
    AiderHarness,
    HarnessRunError,
)


def _mock_run_config(
    prompt: str = "diagnose the bug", memory_storage_key: str = "coding"
) -> MagicMock:
    """Construct a minimal RunConfig-shaped mock — we only read .prompt
    and .memory_storage_key, so a MagicMock with those attrs is enough.
    """
    rc = MagicMock()
    rc.prompt = prompt
    rc.memory_storage_key = memory_storage_key
    rc.run_name = "test-run"
    return rc


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_ask_mode_skips_diff_capture(mock_run, mock_mkdtemp, mock_rmtree):
    """Diagnostic steps (root_cause / solution) use --ask mode and don't
    invoke `git diff` post-run.
    """
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    mock_run.return_value = MagicMock(returncode=0, stdout="reasoning text", stderr="")

    harness = AiderHarness(context=None)
    result = harness.run(_mock_run_config(memory_storage_key="root_cause_analysis"))

    assert result == "reasoning text"
    # 3 subprocess.run calls: git clone, git config user.email, git config user.name,
    # then aider. No 5th call for `git diff HEAD~1 HEAD`.
    argv_lists = [call.args[0] for call in mock_run.call_args_list]
    assert any("clone" in argv for argv in argv_lists), argv_lists
    assert any(argv[0] == "aider" for argv in argv_lists), argv_lists
    assert not any("diff" in argv and "HEAD~1" in argv for argv in argv_lists)
    # Cleanup always fires.
    mock_rmtree.assert_called_once()


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_coding_mode_captures_diff(mock_run, mock_mkdtemp, mock_rmtree):
    """Coding mode invokes git diff after aider to capture the changeset."""
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    # Sequenced return values: clone, config x2, aider, diff
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="", stderr=""),  # git clone
        MagicMock(returncode=0, stdout="", stderr=""),  # git config email
        MagicMock(returncode=0, stdout="", stderr=""),  # git config name
        MagicMock(returncode=0, stdout="patch applied", stderr=""),  # aider
        MagicMock(returncode=0, stdout="diff --git ...", stderr=""),  # git diff
    ]

    harness = AiderHarness(context=None)
    result = harness.run(_mock_run_config(memory_storage_key="coding"))

    assert result == "patch applied"
    argv_lists = [call.args[0] for call in mock_run.call_args_list]
    # Last call should be the diff capture.
    assert argv_lists[-1] == ["git", "diff", "HEAD~1", "HEAD"]


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_propagates_aider_model_flag(mock_run, mock_mkdtemp, mock_rmtree):
    """The model arg should be threaded through with the `vertex_ai/` prefix
    so litellm routes via Vertex AI rather than direct Gemini API.
    """
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

    harness = AiderHarness(context=None)
    harness.run(_mock_run_config(memory_storage_key="root_cause_analysis"))

    argv_lists = [call.args[0] for call in mock_run.call_args_list]
    aider_argv = next(argv for argv in argv_lists if argv[0] == "aider")
    assert "vertex_ai/gemini-2.5-flash" in aider_argv


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_handles_clone_failure(mock_run, mock_mkdtemp, mock_rmtree):
    """Clone failure surfaces as HarnessRunError; workdir still cleaned up."""
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    mock_run.side_effect = subprocess.CalledProcessError(
        returncode=128, cmd=["git", "clone"], stderr="repo not found"
    )

    harness = AiderHarness(context=None)
    with pytest.raises(HarnessRunError, match="git clone failed"):
        harness.run(_mock_run_config())
    mock_rmtree.assert_called_once()


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_handles_clone_timeout(mock_run, mock_mkdtemp, mock_rmtree):
    """Slow clone hits the timeout and produces a clear error message."""
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["git", "clone"], timeout=_GIT_CLONE_TIMEOUT_SECONDS
    )

    harness = AiderHarness(context=None)
    with pytest.raises(HarnessRunError, match="timed out"):
        harness.run(_mock_run_config())
    mock_rmtree.assert_called_once()


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_handles_aider_timeout(mock_run, mock_mkdtemp, mock_rmtree):
    """Aider walltime exceeded surfaces with the cap value in the error."""
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="", stderr=""),  # clone
        MagicMock(returncode=0, stdout="", stderr=""),  # config email
        MagicMock(returncode=0, stdout="", stderr=""),  # config name
        subprocess.TimeoutExpired(cmd=["aider"], timeout=_AIDER_TIMEOUT_SECONDS),
    ]

    harness = AiderHarness(context=None)
    with pytest.raises(HarnessRunError, match=str(_AIDER_TIMEOUT_SECONDS)):
        harness.run(_mock_run_config())
    mock_rmtree.assert_called_once()


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_handles_aider_nonzero_exit(mock_run, mock_mkdtemp, mock_rmtree):
    """Non-zero exit from aider becomes a HarnessRunError with stderr."""
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="", stderr=""),  # clone
        MagicMock(returncode=0, stdout="", stderr=""),  # config email
        MagicMock(returncode=0, stdout="", stderr=""),  # config name
        MagicMock(returncode=2, stdout="", stderr="API quota exceeded"),  # aider
    ]

    harness = AiderHarness(context=None)
    with pytest.raises(HarnessRunError, match="API quota"):
        harness.run(_mock_run_config())
    mock_rmtree.assert_called_once()


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_handles_missing_aider_binary(mock_run, mock_mkdtemp, mock_rmtree):
    """Missing aider binary surfaces with a hint to install aider-chat."""
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="", stderr=""),  # clone
        MagicMock(returncode=0, stdout="", stderr=""),  # config email
        MagicMock(returncode=0, stdout="", stderr=""),  # config name
        FileNotFoundError("aider"),
    ]

    harness = AiderHarness(context=None)
    with pytest.raises(HarnessRunError, match="aider-chat"):
        harness.run(_mock_run_config())


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_empty_prompt_raises(mock_run, mock_mkdtemp, mock_rmtree):
    """No prompt → harness raises before doing any subprocess work."""
    harness = AiderHarness(context=None)
    with pytest.raises(HarnessRunError, match="non-empty"):
        harness.run(_mock_run_config(prompt="   "))
    mock_run.assert_not_called()
    mock_mkdtemp.assert_not_called()


def test_should_continue_is_false():
    """Compatibility no-op — AiderHarness has no internal iteration."""
    harness = AiderHarness(context=None)
    assert harness.should_continue(_mock_run_config()) is False


def test_registry_lookup_finds_aider():
    """Import-time side effect: select_orchestrator('aider') resolves."""
    from seer.automation.harness import select_orchestrator

    assert select_orchestrator("aider") is AiderHarness


# ─── AutofixAgent contract surface ───────────────────────────────────────
#
# The autofix components in root_cause/, solution/, and coding/ call into
# the agent in five ways: constructor kwargs, agent.add_user_message(...),
# agent.tools = [], agent.memory (passed to formatter LLM), and agent.usage
# (summed into step totals). These tests verify AiderHarness matches that
# surface without a runtime AttributeError.


def test_usage_attribute_default_initialized():
    """coding/solution/root_cause all run `cur.usage += agent.usage` after
    agent.run — the harness must default-init Usage() so the += works.
    """
    harness = AiderHarness()
    assert isinstance(harness.usage, Usage)
    # `+= Usage()` must succeed against a fresh Usage() with no AttributeError.
    accumulator = Usage()
    accumulator += harness.usage
    assert accumulator.total_tokens == 0


def test_tools_attribute_is_settable():
    """root_cause/component.py sets `agent.tools = []` mid-flow to disable
    tools before the reasoning pass.
    """
    harness = AiderHarness(tools=["initial-tool-list"])
    assert harness.tools == ["initial-tool-list"]
    harness.tools = []
    assert harness.tools == []


def test_add_user_message_appends_to_memory():
    """Coding + solution components push their prompts via add_user_message
    before calling run() — that message has to land in memory.
    """
    harness = AiderHarness()
    harness.add_user_message("here is the bug")
    assert len(harness.memory) == 1
    assert harness.memory[0].role == "user"
    assert harness.memory[0].content == "here is the bug"


def test_memory_seeded_from_constructor():
    """If the caller passes prior memory (e.g. CodingComponent's prefill),
    the harness uses it instead of starting empty.
    """
    seed = [Message(role="user", content="prior context")]
    harness = AiderHarness(memory=seed)
    assert len(harness.memory) == 1
    # Defensive copy: mutating the constructor list shouldn't affect us.
    seed.append(Message(role="user", content="leak"))
    assert len(harness.memory) == 1


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_falls_back_to_last_user_message_for_prompt(mock_run, mock_mkdtemp, mock_rmtree):
    """When run_config.prompt is empty (the coding/solution flow), the
    harness reaches into memory for the last user message.
    """
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    mock_run.return_value = MagicMock(returncode=0, stdout="reasoning", stderr="")

    harness = AiderHarness()
    harness.add_user_message("the actual prompt from add_user_message")
    result = harness.run(_mock_run_config(prompt="", memory_storage_key="root_cause_analysis"))

    assert result == "reasoning"
    aider_argv = next(
        call.args[0] for call in mock_run.call_args_list if call.args[0][0] == "aider"
    )
    # --message immediately follows in argv
    msg_idx = aider_argv.index("--message")
    assert aider_argv[msg_idx + 1] == "the actual prompt from add_user_message"


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_appends_assistant_message_for_formatter(mock_run, mock_mkdtemp, mock_rmtree):
    """The root_cause + solution formatter LLMs read agent.memory after run.
    The harness must inject aider's stdout as an assistant Message so the
    formatter has something to extract.
    """
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    mock_run.return_value = MagicMock(returncode=0, stdout="The root cause is X.", stderr="")

    harness = AiderHarness()
    harness.run(_mock_run_config(memory_storage_key="root_cause_analysis"))

    assistant_msgs = [m for m in harness.memory if m.role == "assistant"]
    assert len(assistant_msgs) == 1
    assert "root cause is X" in assistant_msgs[0].content


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_passes_chat_mode_ask_flag(mock_run, mock_mkdtemp, mock_rmtree):
    """Aider 0.65.0 takes `--chat-mode ask` (two argv tokens), not `--ask`."""
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

    harness = AiderHarness()
    harness.run(_mock_run_config(memory_storage_key="root_cause_analysis"))

    aider_argv = next(
        call.args[0] for call in mock_run.call_args_list if call.args[0][0] == "aider"
    )
    # The two tokens must appear adjacent in that order.
    chat_idx = aider_argv.index("--chat-mode")
    assert aider_argv[chat_idx + 1] == "ask"
    # And --ask must NOT be present (regression guard for the 5a36248 fix).
    assert "--ask" not in aider_argv


@patch("seer.automation.harness.aider.shutil.rmtree")
@patch("seer.automation.harness.aider.tempfile.mkdtemp")
@patch("seer.automation.harness.aider.subprocess.run")
def test_run_passes_update_suppression_flags(mock_run, mock_mkdtemp, mock_rmtree):
    """`--no-check-update` prevents aider from `pip install --upgrade`-ing
    itself mid-session, which broke seer's pinned tokenizers on the VM.
    """
    mock_mkdtemp.return_value = "/tmp/aider-test-xyz"
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

    harness = AiderHarness()
    harness.run(_mock_run_config(memory_storage_key="root_cause_analysis"))

    aider_argv = next(
        call.args[0] for call in mock_run.call_args_list if call.args[0][0] == "aider"
    )
    assert "--no-check-update" in aider_argv
    assert "--no-show-release-notes" in aider_argv
