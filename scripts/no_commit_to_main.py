"""Pre-commit guard: refuse to commit directly on main (or master).

Every change - an issue, a fix, a feature - goes on its own branch and reaches
main through a pull request, and only once CI has passed (enforced on GitHub by
main's branch protection; this hook is the local half, so the mistake is caught
before a commit exists rather than at push time).

Exits 1 with the exact command to run. Bypass for a genuine one-off with
`SKIP=no-commit-to-main git commit ...` (pre-commit's own mechanism).
"""

from __future__ import annotations

import subprocess
import sys

PROTECTED = {"main", "master"}


def current_branch() -> str:
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()  # empty on a detached HEAD (e.g. mid-rebase)


def main() -> int:
    branch = current_branch()
    if branch in PROTECTED:
        print(
            f"Refusing to commit directly on '{branch}'.\n"
            "Start a branch for this change first:\n"
            "    git switch -c <short-name-for-the-change>\n"
            "then open a pull request; it merges to main once CI passes.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
