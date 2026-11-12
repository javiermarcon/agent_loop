"""Run the Behave scenarios implied by changed features or step modules.

Pre-commit passes the changed paths. Feature files are executed directly; a
changed step module resolves the features that reference its patterns. When no
specific feature can be resolved, the complete BDD suite runs so a step change
never becomes an unvalidated no-op.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURES_PREFIX = "features/"
STEPS_PREFIX = "features/steps/"
STEP_PATTERN_RE = re.compile(
    r"""@(?:given|when|then|step)\s*\(\s*(?P<quote>['"])(?P<pattern>.+?)(?P=quote)""",
)


def normalize_path(path: str) -> str:
    """Return a repository-relative POSIX path."""
    normalized = path.replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute():
        try:
            normalized = candidate.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            return normalized
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _step_patterns(path: Path) -> tuple[str, ...]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    return tuple(match.group("pattern") for match in STEP_PATTERN_RE.finditer(source))


def features_for_step_file(relative_path: str) -> set[str]:
    """Resolve features that reference a changed step module."""
    patterns = _step_patterns(REPO_ROOT / relative_path)
    if not patterns:
        return set()
    matched: set[str] = set()
    for feature in sorted((REPO_ROOT / "features").glob("*.feature")):
        try:
            content = feature.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(pattern in content for pattern in patterns):
            matched.add(feature.relative_to(REPO_ROOT).as_posix())
    return matched


def resolve_targets(paths: list[str]) -> list[str]:
    """Map changed paths to unique feature files."""
    targets: set[str] = set()
    for raw in paths:
        relative = normalize_path(raw)
        if relative.startswith(FEATURES_PREFIX) and relative.endswith(".feature"):
            if (REPO_ROOT / relative).is_file():
                targets.add(relative)
        elif relative.startswith(STEPS_PREFIX) and relative.endswith(".py"):
            targets.update(features_for_step_file(relative))
    return sorted(targets)


def main(argv: list[str] | None = None) -> int:
    """Run the resolved features, or the full suite when resolution is empty."""
    paths = list(sys.argv[1:] if argv is None else argv)
    targets = resolve_targets(paths)
    if not targets:
        sys.stderr.write("[behave-precommit] no specific feature resolved; running the complete suite.\n")
        targets = [FEATURES_PREFIX.rstrip("/")]
    command = [sys.executable, "-m", "behave", *targets]
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and argv
        command,
        cwd=REPO_ROOT,
        check=False,
        env=environment,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
