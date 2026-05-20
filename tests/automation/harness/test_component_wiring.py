"""Verify root_cause / solution / coding components route through
``select_orchestrator(config.AUTOFIX_HARNESS, ...)`` instead of
hard-constructing AutofixAgent.

Catches the regression where someone removes the harness lookup and goes
back to ``AutofixAgent(...)`` directly — the AUTOFIX_HARNESS flag would
silently stop having any effect.
"""

import inspect

from seer.automation.autofix.components.coding import component as coding_module
from seer.automation.autofix.components.root_cause import component as root_cause_module
from seer.automation.autofix.components.solution import component as solution_module


def _source_calls_select_orchestrator(module) -> bool:
    """Grep the module source for the orchestrator-selection call.

    Source-level grep beats unit-mocking each component's invoke() —
    the components have ~30 injected deps each, so a behaviour test
    would be more boilerplate than this is worth.
    """
    src = inspect.getsource(module)
    return "select_orchestrator(" in src


def test_root_cause_component_uses_select_orchestrator():
    assert _source_calls_select_orchestrator(root_cause_module)


def test_solution_component_uses_select_orchestrator():
    assert _source_calls_select_orchestrator(solution_module)


def test_coding_component_uses_select_orchestrator():
    assert _source_calls_select_orchestrator(coding_module)


def test_components_pass_strict_flag():
    """All three components forward AUTOFIX_HARNESS_STRICT so operators
    who set strict=True actually get the strict behavior. Regression
    guard against dropping the kwarg during a refactor.
    """
    for mod in (root_cause_module, solution_module, coding_module):
        src = inspect.getsource(mod)
        assert "AUTOFIX_HARNESS_STRICT" in src, f"{mod.__name__} lost the strict flag"
