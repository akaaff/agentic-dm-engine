"""Finding an existing GitHub issue the log watcher itself already filed
for a given deterministic signature (see signatures.py), via `gh` -
reused directly rather than adding a new GitHub client library, matching
how every git/gh operation across this whole project already works.

Every issue scripts/log_watcher.py files carries the `auto-detected`
label and a hidden `<!-- watcher-signature: <sig> -->` marker in its own
body, so a future run only ever needs to list issues under that one
label - cheap, and clearly distinguishes these from every hand-filed
issue (#1 onward).

Split into a real I/O call (list_tracked_issues, a genuine subprocess
call to `gh` - no offline stub, tested live only) and a pure lookup
(find_existing, a plain list scan) - the same "extract the pure decision,
keep I/O in one thin wrapper" split this project applies everywhere else,
so the actual matching logic is trivially unit-testable without ever
touching a subprocess."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

WATCHER_LABEL = "auto-detected"
SIGNATURE_MARKER_TEMPLATE = "<!-- watcher-signature: {signature} -->"
_SIGNATURE_MARKER = re.compile(r"<!-- watcher-signature: (.+?) -->")


@dataclass(frozen=True)
class TrackedIssue:
    number: int
    title: str
    signature: str


def extract_signature(body: str | None) -> str | None:
    if not body:
        return None
    match = _SIGNATURE_MARKER.search(body)
    return match.group(1) if match else None


def list_tracked_issues() -> list[TrackedIssue]:
    """Every open-or-closed issue this watcher has ever filed, each with
    its embedded signature. An issue under the `auto-detected` label
    whose body has no parseable marker (hand-edited away, or a malformed
    older format) is skipped rather than raising - a future run simply
    treats its signature as unseen and may file a fresh issue for it,
    which is the same graceful-degradation choice this project always
    makes over letting one bad record break a whole pass."""
    result = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--label",
            WATCHER_LABEL,
            "--state",
            "all",
            "--json",
            "number,title,body",
            "--limit",
            "500",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = []
    for raw in json.loads(result.stdout):
        signature = extract_signature(raw.get("body"))
        if signature is not None:
            tracked.append(
                TrackedIssue(number=raw["number"], title=raw["title"], signature=signature)
            )
    return tracked


def find_existing(signature: str, tracked: list[TrackedIssue]) -> int | None:
    for issue in tracked:
        if issue.signature == signature:
            return issue.number
    return None
