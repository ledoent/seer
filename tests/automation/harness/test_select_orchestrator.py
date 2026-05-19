"""Unit tests for the harness selection factory.

Aider-specific tests live in test_aider.py (added in the next commit).
This file covers only the registry + selector behaviour.
"""

import pytest

from seer.automation.autofix.autofix_agent import AutofixAgent
from seer.automation.harness import (
    _REGISTRY,
    HarnessNotAvailableError,
    register_harness,
    select_orchestrator,
)


def test_builtin_is_registered_by_default():
    assert "builtin" in _REGISTRY
    assert _REGISTRY["builtin"] is AutofixAgent


def test_select_builtin_returns_autofix_agent():
    assert select_orchestrator("builtin") is AutofixAgent


def test_select_unknown_falls_back_to_builtin():
    # Not strict — we don't want a typo in AUTOFIX_HARNESS to kill autofix entirely.
    assert select_orchestrator("nope-does-not-exist") is AutofixAgent


def test_select_unknown_strict_raises():
    with pytest.raises(HarnessNotAvailableError) as exc_info:
        select_orchestrator("nope-does-not-exist", strict=True)
    assert "Unknown AUTOFIX_HARNESS='nope-does-not-exist'" in str(exc_info.value)
    assert "builtin" in str(exc_info.value)


def test_register_harness_then_select():
    class _Fake:
        def run(self, run_config):
            pass

    register_harness("__test_fake__", _Fake)
    try:
        assert select_orchestrator("__test_fake__") is _Fake
    finally:
        _REGISTRY.pop("__test_fake__", None)


def test_duplicate_registration_raises():
    class _A:
        def run(self, run_config):
            pass

    class _B:
        def run(self, run_config):
            pass

    register_harness("__test_dup__", _A)
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_harness("__test_dup__", _B)
    finally:
        _REGISTRY.pop("__test_dup__", None)
