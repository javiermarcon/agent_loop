"""Run Pylint over the Python files supplied by pre-commit.

Pylint classifies modules differently when it receives individual files instead
of the whole ``tests`` tree. The test-only disables below reproduce the
repository-wide result for the common test idioms without weakening Pylint for
production code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_PREFIX = "agent_loop/"
TESTS_PREFIX = "tests/"
SCRIPTS_PREFIX = "scripts/"
FEATURES_PREFIX = "features/"
ROOT_SOURCE_FILES = frozenset({"run.py"})
ROOT_TEST_FILES = frozenset({"conftest.py"})
TEST_SCOPE_PREFIXES = (TESTS_PREFIX, FEATURES_PREFIX)
TEST_DISABLES = (
    "abstract-class-instantiated",
    "assignment-from-none",
    "protected-access",
    "redefined-outer-name",
    "superfluous-parens",
    "too-many-positional-arguments",
    "unused-variable",
    "use-implicit-booleaness-not-comparison",
)


def normalize_path(path: str) -> str:
    """Return a repository-relative POSIX path.

    Args:
        path: Relative or absolute path supplied by pre-commit.

    Returns:
        Path relative to the repository root with forward slashes.
    """
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


def is_target(relative_path: str) -> bool:
    """Return whether a staged path is part of the Pylint scope.

    Args:
        relative_path: Normalized repository-relative path.

    Returns:
        ``True`` for repository Python files in the package, tests, tooling,
        features or root entry points.
    """
    if not relative_path.endswith(".py"):
        return False
    if "/" not in relative_path:
        return True
    return relative_path.startswith((PACKAGE_PREFIX, TESTS_PREFIX, SCRIPTS_PREFIX, FEATURES_PREFIX))


def _run(files: list[str], *, test_scope: bool) -> int:
    """Run one Pylint invocation.

    Args:
        files: Repository-relative Python files.
        test_scope: Whether the files are tests that need the test-only disables.

    Returns:
        Pylint exit code.
    """
    command = [sys.executable, "-m", "pylint"]
    if test_scope:
        command.append(f"--disable={','.join(TEST_DISABLES)}")
    command.extend(files)
    completed = subprocess.run(command, cwd=REPO_ROOT, check=False)  # noqa: S603 - fixed interpreter and argv
    return int(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    """Run Pylint over the staged source and test files.

    Args:
        argv: Optional path list; defaults to ``sys.argv[1:]``.

    Returns:
        The highest Pylint exit code across the scopes, or ``0`` without targets.
    """
    raw_files = list(sys.argv[1:] if argv is None else argv)
    normalized = [normalize_path(path) for path in raw_files]
    targets = [path for path in normalized if is_target(path) and (REPO_ROOT / path).is_file()]
    source_files = [
        path
        for path in targets
        if path in ROOT_SOURCE_FILES
        or (path not in ROOT_TEST_FILES and ("/" not in path or path.startswith((PACKAGE_PREFIX, SCRIPTS_PREFIX))))
    ]
    test_files = [path for path in targets if path in ROOT_TEST_FILES or path.startswith(TEST_SCOPE_PREFIXES)]

    exit_code = 0
    if source_files:
        exit_code = max(exit_code, _run(source_files, test_scope=False))
    if test_files and exit_code == 0:
        exit_code = _run(test_files, test_scope=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
