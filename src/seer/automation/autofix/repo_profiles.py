"""Per-repo conventions + familiarity notes for autofix.

A profile bundles together the small things every repo needs the agent to
know but that don't belong in a single global prompt:

  - Commit/PR title format (e.g. `[19.0][OU-FIX] base: rename groups_id`)
  - Branch name format (e.g. `19.0-fix-<scope>` instead of `seer/<title>`)
  - "Familiarity notes": prose injected into the solution + coding system
    prompts as `<repo_context>` so the agent knows what's a monkey-patch,
    where bind-mounts live, what symptoms are operator state, etc.

PR #18 hardcoded an openupgrade-lab `<repo_context>` block directly into
the prompts. This module promotes that to a reusable struct so we can add
more repos (kencove-storefront, ledoent/website, ledoent/seer, etc.)
without growing the prompts.

Match order: registry list. First match by `full_name` substring wins.
`None` is the "no profile" fallback — agent uses generic behavior.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

from seer.automation.models import RepoDefinition


@dataclass(frozen=True)
class RepoProfile:
    """Per-repo conventions + familiarity notes.

    `matches_full_name_contains` is a list of substrings; the first profile
    whose substring appears in the repo's `full_name` wins. Cheap; if we
    ever need regex or globbing, swap this for a `matcher: Callable`.
    """

    matches_full_name_contains: tuple[str, ...]
    title_format_hint: str
    """Free-form sentence describing the title format the agent should
    follow. Injected into the change-describer prompt. Examples:
      - "[<series>][TAG] <scope>: <terse summary>" for OCA repos
      - "feat(<scope>): <summary>" for conventional-commits repos
    Leave empty to let the agent pick its own format.
    """

    branch_prefix: str
    """Branch prefix prepended to the LLM-suggested branch name. The default
    is `seer/`. OCA-style repos use `<series>-fix-` (e.g. `19.0-fix-`).
    """

    familiarity_notes: str
    """Prose injected into the solution + coding system prompts as
    `<repo_context>`. Should cover anything reviewers would need to flag
    on every PR — monkey-patches, bind-mounts, multi-DB heuristics,
    commit conventions, etc. Keep terse; this lands in every Gemini turn.
    """


_OPENUPGRADE_LAB_NOTES = textwrap.dedent(
    """\
    For ledoent/OpenUpgrade and other openupgrade-lab forks of OCA projects:
      - `openupgrade_framework/odoo_patch/` is monkey patches that override Odoo core. Edits there are runtime overrides for ALL Odoo, not module-local. Prefer NOT to edit unless the bug is explicitly in the patched function itself.
      - The lab uses bind-mounted sources (`./openupgrade:/opt/openupgrade`, openupgradelib at `.../site-packages/openupgradelib`); errors can surface from either tree depending on which the running process imports.
      - Multi-DB symptoms (errors referencing `repro_a`, `seed_*`, `openupgrade_test`, etc.) usually reflect operator state (the dev's `make reset` history, a stale CNPG namespace) — not a code bug.
      - Commit/PR convention: title `[<series>][TAG] <scope>: <summary>` where TAG ∈ `[OU-FIX]`, `[OU-ADD]`, `[OU-IMP]`, `[MIG]`; branch `<series>-fix-<scope>` for FIX, `<series>-mig-<scope>` for MIG.
    """
).strip()


# Registry. Add new repos here as we onboard them to autofix.
PROFILES: tuple[RepoProfile, ...] = (
    RepoProfile(
        matches_full_name_contains=(
            "ledoent/OpenUpgrade",
            "openupgrade-lab",
            "OCA/OpenUpgrade",
        ),
        title_format_hint=(
            "OCA/openupgrade convention: title `[<series>][TAG] <scope>: <terse summary>` "
            "where TAG ∈ `[OU-FIX]`, `[OU-ADD]`, `[OU-IMP]`, `[MIG]`. "
            "Example: `[19.0][OU-FIX] base: rename groups_id references in domain/code`. "
            "Use `[MIG]` only for migration scripts under `openupgrade_scripts/`."
        ),
        branch_prefix="19.0-fix-",
        familiarity_notes=_OPENUPGRADE_LAB_NOTES,
    ),
)


def get_repo_profile(repo: RepoDefinition | None) -> RepoProfile | None:
    """First-match-wins lookup against the registry. Returns None when the
    repo has no profile — caller should fall back to the default behavior
    (the `seer/<title>` branch prefix, conventional-commits title, etc.).
    """
    if repo is None:
        return None
    return get_profile_for_full_name(repo.full_name)


def get_profile_for_full_name(full_name: str | None) -> RepoProfile | None:
    """String-based variant of `get_repo_profile`. Useful from sites that
    only have a `owner/name` string at hand (the change-describer pipeline
    passes the full_name through, not the RepoDefinition).
    """
    if not full_name:
        return None
    for profile in PROFILES:
        if any(needle in full_name for needle in profile.matches_full_name_contains):
            return profile
    return None


def get_first_matching_profile(repos: list[RepoDefinition]) -> RepoProfile | None:
    """Pick the first repo in `repos` that has a registered profile. Used
    when an autofix run has multiple repos in scope but the system prompt
    needs a single set of familiarity notes — typically a run targets one
    primary repo and we want that repo's profile to win.
    """
    for repo in repos:
        profile = get_repo_profile(repo)
        if profile is not None:
            return profile
    return None
