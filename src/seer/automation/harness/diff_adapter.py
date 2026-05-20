"""Helpers for shipping a captured git diff to seer's autofix-step output.

Phase 2a: emit raw unified-diff text only. The Sentry UI's autofix viewer
renders this directly without needing a structured FilePatch list.

Phase 2b will add a unified-diff -> ``FilePatch`` + ``Hunk`` parser so the
structured viewer also lights up. Deferred because it requires either the
``unidiff`` dep or ~80 lines of hand-rolled regex parsing, and the raw-text
fallback is sufficient for the Phase 2a benchmark.
"""

from typing import Iterable


def count_files_in_diff(diff_str: str) -> int:
    """Return the number of files touched by a unified-diff string.

    Used for telemetry / benchmark scoring — a quick `wc -l` style metric
    without parsing the full hunks.
    """
    return sum(1 for line in diff_str.splitlines() if line.startswith("diff --git "))


def file_paths_in_diff(diff_str: str) -> Iterable[str]:
    """Yield each ``b/<path>`` target file mentioned in the diff header.

    Used for benchmark output ("aider touched these N files") without
    parsing the rest of the diff.
    """
    for line in diff_str.splitlines():
        if not line.startswith("diff --git "):
            continue
        # Format: "diff --git a/<path> b/<path>"
        parts = line.split(" b/", 1)
        if len(parts) == 2:
            yield parts[1].strip()
