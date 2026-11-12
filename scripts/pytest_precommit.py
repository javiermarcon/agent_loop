"""Run the pytest modules related to the Python files staged for a commit.

Pre-commit passes the staged paths as arguments. The mapping is deterministic
and limited to concrete ``test_*.py`` modules; a changed production module
without a focused test is reported and skipped here because the complete suite
runs in the full quality gate and in CI.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_PREFIX = "agent_loop/"
TESTS_PREFIX = "tests/"
TEST_FILE_PREFIX = "test_"
MIN_PATH_PARTS = 2
ROOT_TEST_TARGETS = {
    "conftest.py": "tests/agent_loop",
    "run.py": "tests/agent_loop/autonomy/test_cli.py",
}


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


def is_test_module(relative_path: str) -> bool:
    """Return whether the path is a collectable pytest module.

    Args:
        relative_path: Normalized repository-relative path.

    Returns:
        ``True`` for ``test_*.py`` files.
    """
    path = Path(relative_path)
    return path.suffix == ".py" and path.name.startswith(TEST_FILE_PREFIX)


def _existing(relative_path: Path) -> Path | None:
    """Return the path when it exists under the repository root.

    Args:
        relative_path: Candidate path relative to the repository root.

    Returns:
        The candidate when it is a file, otherwise ``None``.
    """
    if relative_path.is_file():
        return relative_path
    return None


def package_test_candidates(relative_path: str) -> tuple[Path, ...]:
    """Return focused test candidates for one ``agent_loop`` module.

    The mirror layout is tried first, then sibling suites sharing the module
    stem, then the suite of the parent package. Results preserve that order and
    may not exist on disk.

    Args:
        relative_path: Normalized repository-relative production path.

    Returns:
        Ordered candidate test paths.
    """
    path = Path(relative_path)
    if not relative_path.startswith(PACKAGE_PREFIX) or len(path.parts) < MIN_PATH_PARTS:
        return ()
    stem = path.stem
    if stem == "__init__":
        return ()

    relative_under_package = Path(*path.parts[1:])
    test_directory = REPO_ROOT / "tests" / "agent_loop" / relative_under_package.parent
    candidates: list[Path] = [test_directory / f"{TEST_FILE_PREFIX}{stem}.py"]
    candidates.extend(sorted(test_directory.glob(f"{TEST_FILE_PREFIX}{stem}_*.py")))

    parent = relative_under_package.parent
    if parent.parts:
        candidates.append(
            REPO_ROOT / "tests" / "agent_loop" / parent.parent / f"{TEST_FILE_PREFIX}{parent.name}.py",
        )
    return tuple(candidates)


def resolve_targets(changed_files: list[str]) -> list[str]:
    """Map changed Python files to concrete pytest modules.

    Args:
        changed_files: Paths supplied by pre-commit.

    Returns:
        Sorted, de-duplicated repository-relative test module paths.
    """
    targets: set[str] = set()
    for raw in changed_files:
        relative = normalize_path(raw)
        full = REPO_ROOT / relative
        if not full.is_file() or full.suffix != ".py":
            continue
        root_target = ROOT_TEST_TARGETS.get(relative)
        if root_target is not None and (REPO_ROOT / root_target).exists():
            targets.add(root_target)
            continue
        if relative.startswith(TESTS_PREFIX):
            if is_test_module(relative):
                targets.add(relative)
            continue
        if not relative.startswith(PACKAGE_PREFIX):
            continue
        for candidate in package_test_candidates(relative):
            file_path = _existing(candidate)
            if file_path is not None and is_test_module(file_path.name):
                targets.add(file_path.relative_to(REPO_ROOT).as_posix())
                break
    return sorted(targets)


def main(argv: list[str] | None = None) -> int:
    """Run the focused pytest modules mapped from the staged files.

    Args:
        argv: Optional path list; defaults to ``sys.argv[1:]``.

    Returns:
        pytest exit code, or ``0`` when no focused test is mapped.
    """
    files = list(sys.argv[1:] if argv is None else argv)
    targets = resolve_targets(files)
    if not targets:
        sys.stderr.write(
            "[pytest-precommit] no focused test mapped; the full suite runs in the quality gate and CI.\n",
        )
        return 0

    sys.stderr.write(f"[pytest-precommit] {len(files)} changed file(s) -> {len(targets)} test module(s)\n")
    command = [sys.executable, "-m", "pytest", "-q", "--tb=short", *targets]
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
