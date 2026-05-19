"""Pluggable autofix orchestrators.

The default orchestrator is the in-process ``AutofixAgent`` (a ReAct loop
calling Gemini via the existing ``LlmClient``). Alternate orchestrators
("harnesses") wrap external coding agents like ``aider`` as a subprocess
and let them propose a patch given the same prompt + repo context.

Selection happens at autofix-component construction time via
``select_orchestrator(name, strict=...)``. Harness modules register
themselves at import time with ``register_harness("name", Cls)``.

See ``docs/coding-harnesses.md`` for the rollout plan.
"""

import logging
from typing import Protocol, Type, runtime_checkable

from seer.automation.agent.agent import RunConfig
from seer.automation.autofix.autofix_agent import AutofixAgent

logger = logging.getLogger(__name__)


@runtime_checkable
class AutofixOrchestrator(Protocol):
    """Anything a component can invoke to drive an autofix step.

    Must support ``.run(run_config=RunConfig(...))`` and return whatever
    ``AutofixAgent.run`` returns today (a response object with ``.message``,
    ``.usage``, ``.metadata`` — see ``automation.agent.models``).
    """

    def run(self, run_config: RunConfig): ...


class HarnessNotAvailableError(RuntimeError):
    """Raised when ``AUTOFIX_HARNESS_STRICT=True`` and the requested
    harness is unknown or unimportable. Operators using strict mode want
    a hard failure rather than a silent fallback.
    """


_REGISTRY: dict[str, Type[AutofixOrchestrator]] = {
    "builtin": AutofixAgent,
}


def register_harness(name: str, cls: Type[AutofixOrchestrator]) -> None:
    """Used by harness modules at import time to register themselves.

    Raises ``ValueError`` on duplicate registration so two modules can't
    silently clobber each other.
    """
    if name in _REGISTRY:
        raise ValueError(
            f"Harness '{name}' is already registered as "
            f"{_REGISTRY[name].__name__}; remove the duplicate registration."
        )
    _REGISTRY[name] = cls


def select_orchestrator(harness_name: str, strict: bool = False) -> Type[AutofixOrchestrator]:
    """Resolve ``AppConfig.AUTOFIX_HARNESS`` to an orchestrator class.

    When the requested harness isn't registered:
      * ``strict=False`` (default): log a warning and return the builtin.
      * ``strict=True``: raise ``HarnessNotAvailableError``.

    The fallback prevents a typo or a missing optional dep from disabling
    autofix outright.
    """
    if harness_name in _REGISTRY:
        return _REGISTRY[harness_name]
    msg = f"Unknown AUTOFIX_HARNESS={harness_name!r}; " f"known: {sorted(_REGISTRY)}"
    if strict:
        raise HarnessNotAvailableError(msg)
    logger.warning("%s — falling back to 'builtin'.", msg)
    return _REGISTRY["builtin"]
