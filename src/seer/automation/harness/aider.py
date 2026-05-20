"""External coding-harness orchestrator backed by the ``aider`` CLI.

Drop-in replacement for ``AutofixAgent``: matches the constructor signature
and the ``.run(run_config)`` -> ``str | None`` return contract, so the
existing autofix components can swap orchestrators behind a feature flag
without further code changes. Tools / memory / agent-config kwargs are
accepted for compatibility but ignored — aider has its own internal tool
loop and runs fresh per invocation.

Phase 2a behaviour (see ``docs/coding-harnesses.md``):
  * Hardcoded ``ledoent/seer`` repo URL on the ``feature/explorer-endpoints``
    branch since the benchmark issues are all seer-side bugs. Dynamic
    repo resolution from Sentry code-mappings is Phase 2b.
  * ``--ask`` mode for diagnostic steps (root cause, solution); full
    auto-commit mode for the coding step. Step is inferred from
    ``run_config.memory_storage_key``.
  * Captured ``git diff`` lands in ``diff_str`` on the autofix-state step
    when running coding mode; ``list[FilePatch]`` parsing is deferred.

Sandboxing:
  * Each invocation runs in a fresh ``/tmp/aider-<run_id>/`` workdir that
    is shallow-cloned + cleaned up in a ``finally:`` block.
  * Walltime is capped via ``subprocess.run(timeout=...)``.
  * If anything fails (clone, aider non-zero, timeout, etc.), the harness
    raises ``HarnessRunError`` — callers handle via the
    ``AUTOFIX_HARNESS_STRICT`` flag (fall back to ``builtin`` or propagate).
"""

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any, Optional

import sentry_sdk

from seer.automation.agent.agent import RunConfig
from seer.automation.harness import register_harness

logger = logging.getLogger(__name__)

# Phase 2a hardcoded resolution. Phase 2b switches to Sentry code-mapping
# RPC lookup; see docs/coding-harnesses.md §5.
_PHASE_2A_REPO_URL = "https://github.com/ledoent/seer.git"
_PHASE_2A_DEFAULT_BRANCH = "feature/explorer-endpoints"

# Aider command-line constants.
_AIDER_BIN = "aider"
_AIDER_TIMEOUT_SECONDS = 600  # 10 min hard cap per invocation
_GIT_CLONE_TIMEOUT_SECONDS = 120

# Step-name -> aider mode mapping. The diagnostic steps (root_cause,
# solution) use `--ask` so aider doesn't try to commit anything;
# only the coding step gets the full auto-commit flow.
_ASK_MODE_STEPS = {"root_cause_analysis", "solution"}


class HarnessRunError(RuntimeError):
    """Raised when the aider subprocess fails (non-zero exit, timeout,
    clone failure, missing binary, etc.). Includes captured stdout/stderr
    in the message for log-grepping.
    """


class AiderHarness:
    """Orchestrator that delegates to the ``aider`` CLI."""

    def __init__(
        self,
        config: Any = None,
        context: Any = None,
        tools: Any = None,
        memory: Any = None,
        name: str = "AiderHarness",
    ):
        # Stored for compatibility with the AutofixAgent contract; only
        # ``context`` and ``name`` are actually used. Tools/memory are
        # accepted so component code can keep its existing kwargs without
        # an extra branch.
        self.config = config
        self.context = context
        self.name = name
        self._unused_tools = tools
        self._unused_memory = memory
        self.memory: list = []  # required by some downstream code paths

    def should_continue(self, run_config: RunConfig) -> bool:
        """Compatibility no-op. AiderHarness runs in a single subprocess
        invocation, so there's no internal iteration loop to continue.
        """
        return False

    def run(self, run_config: RunConfig) -> Optional[str]:
        """Invoke aider with the prompt from ``run_config`` and return
        its stdout (which contains the model's response text).

        Raises ``HarnessRunError`` on any subprocess / clone failure.
        """
        prompt = (run_config.prompt or "").strip()
        if not prompt:
            raise HarnessRunError("AiderHarness.run requires run_config.prompt to be non-empty")

        ask_mode = self._is_ask_step(run_config)
        run_id = getattr(run_config, "run_name", None) or "anon"
        # Slugify run_id for filesystem use
        run_slug = "".join(c if c.isalnum() else "-" for c in run_id)[:40]

        workdir = tempfile.mkdtemp(prefix=f"aider-{run_slug}-")
        sentry_sdk.set_tag("harness", "aider")
        sentry_sdk.set_tag("harness.ask_mode", ask_mode)

        try:
            self._clone_repo(workdir)
            stdout = self._invoke_aider(workdir, prompt, ask_mode=ask_mode)

            if not ask_mode:
                # Stash the diff for the coding step to surface in the UI.
                diff_str = self._capture_diff(workdir)
                if diff_str:
                    self._record_diff(diff_str)

            return stdout
        finally:
            self._cleanup(workdir)

    # ─── internal helpers ───────────────────────────────────────────────

    def _is_ask_step(self, run_config: RunConfig) -> bool:
        key = getattr(run_config, "memory_storage_key", "") or ""
        return key in _ASK_MODE_STEPS

    def _clone_repo(self, workdir: str) -> None:
        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth=1",
                    "--branch",
                    _PHASE_2A_DEFAULT_BRANCH,
                    _PHASE_2A_REPO_URL,
                    workdir,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=_GIT_CLONE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as e:
            raise HarnessRunError(
                f"git clone of {_PHASE_2A_REPO_URL} timed out after "
                f"{_GIT_CLONE_TIMEOUT_SECONDS}s"
            ) from e
        except subprocess.CalledProcessError as e:
            raise HarnessRunError(f"git clone failed (rc={e.returncode}): {e.stderr[:500]}") from e
        # Set a local git identity so aider's auto-commits don't fail on
        # the global-config-missing case inside the container.
        subprocess.run(
            ["git", "config", "user.email", "seer-aider@ledoweb.com"],
            cwd=workdir,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Seer Aider Harness"],
            cwd=workdir,
            check=True,
        )

    def _invoke_aider(self, workdir: str, prompt: str, ask_mode: bool) -> Optional[str]:
        model_name = self._resolve_model_name()
        env = {
            **os.environ,
            # litellm picks these up for vertex_ai/... model routing.
            "VERTEXAI_PROJECT": os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
            "VERTEXAI_LOCATION": "us-central1",
            "AIDER_NO_PRETTY": "1",
        }
        argv = [
            _AIDER_BIN,
            "--no-pretty",
            "--no-stream",
            "--yes",
            "--no-attribute-author",
            "--model",
            f"vertex_ai/{model_name}",
            "--map-tokens",
            "1024",
            "--message",
            prompt,
        ]
        if ask_mode:
            argv.insert(-2, "--ask")
        else:
            argv.insert(-2, "--auto-commits")

        try:
            result = subprocess.run(
                argv,
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=_AIDER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as e:
            raise HarnessRunError(f"aider exceeded walltime cap {_AIDER_TIMEOUT_SECONDS}s") from e
        except FileNotFoundError as e:
            raise HarnessRunError(
                f"aider binary not found on PATH ({_AIDER_BIN!r}). "
                "Bake `pip install aider-chat` into the seer image."
            ) from e

        if result.returncode != 0:
            raise HarnessRunError(
                f"aider exited rc={result.returncode}: " f"stderr={result.stderr[:500]!r}"
            )
        return result.stdout

    def _capture_diff(self, workdir: str) -> str:
        """Returns the unified diff of HEAD~1..HEAD, or empty string if
        aider produced no commit.
        """
        try:
            result = subprocess.run(
                ["git", "diff", "HEAD~1", "HEAD"],
                cwd=workdir,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            return result.stdout
        except subprocess.CalledProcessError:
            # No HEAD~1 means aider made no commit — valid outcome.
            return ""

    def _record_diff(self, diff_str: str) -> None:
        """Stash the captured diff onto the current autofix step so the
        Sentry UI can render it. Best-effort — the context may be None
        in unit tests or when invoked outside an autofix run.
        """
        if self.context is None:
            logger.debug("No context — skipping diff record (test/standalone mode)")
            return
        try:
            with self.context.state.update() as state:
                if state.steps:
                    # diff_str is the simplest payload that doesn't require
                    # parsing into FilePatch + Hunks; Phase 2b can upgrade.
                    state.steps[-1].aider_diff_str = diff_str
        except Exception as exc:
            logger.warning("Failed to record aider diff onto autofix state: %s", exc)

    def _resolve_model_name(self) -> str:
        """Read AUTOFIX_HARNESS_MODEL from env (set via AppConfig).

        The default 'gemini-2.5-flash' suffices for most autofix issues;
        callers can override per-deploy.
        """
        return os.environ.get("AUTOFIX_HARNESS_MODEL", "gemini-2.5-flash")

    def _cleanup(self, workdir: str) -> None:
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception as exc:
            logger.warning("Failed to clean aider workdir %s: %s", workdir, exc)


# Register at import time so select_orchestrator("aider") finds us.
register_harness("aider", AiderHarness)
