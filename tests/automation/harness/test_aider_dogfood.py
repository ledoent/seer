r"""Live integration test for AiderHarness — gated, manual-only.

This test really shells out to ``aider`` (so the binary must be on PATH)
and really clones the seer repo from GitHub, then asks aider a trivial
question in ``--ask`` mode. It is skipped unless ``SEER_AIDER_DOGFOOD=1``
is set, so CI never picks it up.

Run manually inside the seer container after rebuilding ``:lightweight``::

    docker exec -it sentry-self-hosted-seer-1 \\
      env SEER_AIDER_DOGFOOD=1 \\
      pytest tests/automation/harness/test_aider_dogfood.py -s

Pass criteria:
  * aider exits 0
  * stdout contains a non-empty answer from Gemini
  * tempdir is cleaned up afterwards

Failure modes worth surfacing:
  * Vertex ADC not wired (missing/expired credentials at
    ``/etc/sentry-extra/seer-vertex-key.json``).
  * Network egress blocked from container to GitHub or Vertex.
  * aider version drift breaks the flag set we pass.
"""

import os
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from seer.automation.harness.aider import AiderHarness

DOGFOOD = os.environ.get("SEER_AIDER_DOGFOOD") == "1"

pytestmark = pytest.mark.skipif(
    not DOGFOOD,
    reason="Live dogfood test — set SEER_AIDER_DOGFOOD=1 to run.",
)


def _has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def _ask_run_config(prompt: str) -> MagicMock:
    rc = MagicMock()
    rc.prompt = prompt
    rc.memory_storage_key = "root_cause_analysis"  # triggers --ask mode
    rc.run_name = "dogfood-ask"
    return rc


@pytest.mark.skipif(not _has_binary("aider"), reason="aider not on PATH")
@pytest.mark.skipif(not _has_binary("git"), reason="git not on PATH")
def test_aider_ask_mode_real_clone_real_invocation():
    harness = AiderHarness(
        tools=None,
        config=MagicMock(),
        context=MagicMock(),
        memory=[],
        name="dogfood",
    )

    rc = _ask_run_config("In one sentence: what does src/seer/automation/harness/__init__.py do?")

    response = harness.run(rc)

    assert response is not None, "aider returned no output"
    assert len(response.strip()) > 20, f"suspiciously short aider response: {response!r}"


@pytest.mark.skipif(not _has_binary("aider"), reason="aider not on PATH")
def test_aider_version_callable():
    """Smoke check: the aider binary in the container is invokable."""
    result = subprocess.run(["aider", "--version"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "aider" in result.stdout.lower()
