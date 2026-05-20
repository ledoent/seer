"""Unit tests for AiderHarness.

The subprocess invocation is patched throughout — none of these tests
actually exec aider or git. Live verification happens via the integration
test in test_aider_dogfood.py (gated on ``AUTOFIX_HARNESS=aider``).
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

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
