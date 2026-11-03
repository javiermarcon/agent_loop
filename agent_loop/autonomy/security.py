"""Workspace and environment security policies for autonomous execution."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent_loop.autonomy.errors import SecurityError

if TYPE_CHECKING:
    from collections.abc import Collection
    from collections.abc import Iterator
    from collections.abc import Mapping

_GIT_TIMEOUT_SECONDS = 30
_GIT_MIN_REV_PARSE_LINES = 2
_GIT_ENV_PREFIX = "GIT_"

_FORBIDDEN_ENV_NAMES = frozenset(
    {
        "AGENT_LOOP_RUN_ID",
        "DJANGO_SETTINGS_MODULE",
        "PIP_INDEX_URL",
        "PIP_TRUSTED_HOST",
        "PYTHONHOME",
        "PYTHONPATH",
        "SHIPII_SETTINGS_MODULE",
        "VIRTUAL_ENV",
    },
)
_FORBIDDEN_ENV_PREFIXES = ("AGENT_LOOP_", "SHIPII_")
_SAFE_ENV_NAMES = frozenset(
    {
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USER",
    },
)


@dataclass(frozen=True, slots=True)
class TargetWorkspace:
    """A normalized target repository that bounds every filesystem operation."""

    root: Path

    @classmethod
    def open(cls, path: str | Path, *, require_git: bool = True) -> TargetWorkspace:
        """Resolve and validate a target repository root.

        The path is normalized with ``resolve`` so symlinked roots are checked
        against their real location. A non-directory or a directory outside a
        Git work tree is rejected before any discovery or execution.
        """
        candidate = Path(path).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            message = f"target repository does not exist or cannot be resolved: {path}"
            raise SecurityError(message) from exc
        if not resolved.is_dir():
            message = f"target repository is not a directory: {resolved}"
            raise SecurityError(message)
        if require_git and not _is_git_work_tree(resolved):
            message = f"target repository is not a Git work tree: {resolved}"
            raise SecurityError(message)
        return cls(root=resolved)

    def resolve(self, candidate: str | Path) -> Path:
        """Resolve a candidate path and reject any escape from the workspace."""
        raw = Path(candidate)
        absolute = raw if raw.is_absolute() else self.root / raw
        try:
            resolved = absolute.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            message = f"path cannot be resolved safely: {candidate}"
            raise SecurityError(message) from exc
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            message = f"path escapes the authorized repository: {candidate}"
            raise SecurityError(message) from exc
        return resolved

    def relative(self, candidate: str | Path) -> str:
        """Return a POSIX-style path relative to the workspace root."""
        return self.resolve(candidate).relative_to(self.root).as_posix()

    def member_path(self, candidate: str | Path) -> Path:
        """Normalize a workspace member without following the final component.

        This lexical view is only an intermediate step for callers that must
        inspect a symlink itself. It never authorizes execution; use
        :func:`resolve_workspace_executable` for that decision.
        """
        raw = Path(candidate)
        absolute = raw if raw.is_absolute() else self.root / raw
        normalized = Path(os.path.normpath(absolute))
        try:
            normalized.relative_to(self.root)
        except ValueError as exc:
            message = f"path escapes the authorized repository: {candidate}"
            raise SecurityError(message) from exc
        return normalized

    def is_within(self, candidate: str | Path) -> bool:
        """Return whether a candidate resolves inside the workspace."""
        try:
            self.resolve(candidate)
        except SecurityError:
            return False
        return True

    def iter_files(self, *, skip_directories: Collection[str] = frozenset()) -> Iterator[Path]:
        """Yield files inside the workspace without following escaping symlinks.

        Symlinks are resolved before traversal. Broken links, cycles and links
        that leave the workspace are skipped. Internal symlinks remain valid.
        """
        yield from _walk_workspace_files(self.root, skip_directories)


def _walk_workspace_files(root: Path, skip_directories: Collection[str]) -> Iterator[Path]:
    stack: list[Path] = [root]
    seen_directories: set[Path] = set()
    while stack:
        current = stack.pop()
        resolved_current = _resolved_within(root, current)
        if resolved_current is None or resolved_current in seen_directories:
            continue
        seen_directories.add(resolved_current)
        try:
            entries = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for entry in entries:
            resolved_entry = _resolved_within(root, entry)
            if resolved_entry is None:
                continue
            if resolved_entry.is_dir():
                if entry.name in skip_directories or entry.name.startswith("."):
                    continue
                stack.append(entry)
            elif resolved_entry.is_file():
                yield entry


def _resolved_within(root: Path, path: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if _contains(root, resolved) else None


def _contains(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_workspace_executable(
    workspace: TargetWorkspace,
    candidate: str | Path,
    *,
    allow_venv_interpreter: bool = False,
) -> Path | None:
    """Return a workspace executable only when its resolved target is authorized.

    A tool binary must resolve inside the workspace. The one documented
    exception is a virtualenv interpreter: ``.venv/bin/python`` is normally a
    symlink to the base interpreter outside the repository. That case is
    accepted only when the virtualenv owns a ``pyvenv.cfg`` inside the
    workspace, which is what makes the interpreter report the target prefix.

    Broken links, symlink chains escaping the workspace and externally
    installed binaries are rejected instead of being executed through a
    lexical ``.venv/bin`` path.
    """
    try:
        lexical = workspace.member_path(candidate)
    except SecurityError:
        return None
    if not _is_executable_file(lexical):
        return None
    try:
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if _contains(workspace.root, resolved):
        return lexical
    if allow_venv_interpreter and _is_venv_interpreter(workspace, lexical, resolved):
        return lexical
    return None


def resolve_system_executable(candidate: str | Path) -> Path | None:
    """Validate an explicitly discovered system executable.

    Callers must pass the absolute path returned by ``shutil.which``. A bare
    command name is rejected because resolving it through a mutable ``PATH``
    would let the target repository intercept privileged tools.
    """
    path = Path(candidate)
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if _is_executable_file(resolved) else None


def find_system_executable(name: str, *, workspace: TargetWorkspace | None = None) -> Path | None:
    """Locate a system executable without trusting workspace-controlled PATH entries.

    Relative PATH entries and entries whose real target lives inside the
    authorized workspace are skipped. This prevents a repository-local
    ``.venv/bin`` shim from intercepting Git or Docker discovery when the
    caller happens to have that directory on its PATH.
    """
    extensions = _executable_extensions()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        directory = Path(entry)
        if not entry or not directory.is_absolute():
            continue
        for extension in extensions:
            candidate = directory / f"{name}{extension}"
            resolved = resolve_system_executable(candidate)
            if resolved is None:
                continue
            if workspace is not None and _contains(workspace.root, resolved):
                continue
            return resolved
    return None


def _executable_extensions() -> tuple[str, ...]:
    if os.name != "nt":
        return ("",)
    raw = os.environ.get("PATHEXT", ".EXE;.BAT;.CMD")
    return tuple(extension.lower() for extension in raw.split(";") if extension) or ("",)


def _is_executable_file(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except (OSError, ValueError):
        return False


def _is_venv_interpreter(workspace: TargetWorkspace, executable: Path, resolved: Path) -> bool:
    if not _is_executable_file(resolved) or executable.parent.name not in {"bin", "Scripts"}:
        return False
    config = executable.parent.parent / "pyvenv.cfg"
    try:
        resolved_config = config.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return resolved_config.is_file() and _contains(workspace.root, resolved_config)


def git_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build an environment that cannot redirect Git to another repository.

    Git honours ``GIT_DIR``, ``GIT_WORK_TREE``, index/object overrides and
    configuration injection variables. Those variables are removed so every
    internal invocation is bound to the discovered workspace.
    """
    base = os.environ if source is None else source
    return {name: value for name, value in base.items() if not _is_git_redirect_variable(name)}


def run_git_command(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a read-only Git command with the repository-isolated environment."""
    executable = find_system_executable("git")
    if executable is None or _contains(root, executable):
        return None
    try:
        return subprocess.run(  # noqa: S603 - resolved executable, no shell, sanitized environment
            (executable, "-C", str(root), *args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=git_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _is_git_redirect_variable(name: str) -> bool:
    # Every GIT_* variable can alter repository discovery, object lookup,
    # configuration injection or the helpers Git executes. Repository-local
    # configuration remains available through the target workspace itself.
    return name.startswith(_GIT_ENV_PREFIX)


def _is_git_work_tree(root: Path) -> bool:
    completed = run_git_command(root, "rev-parse", "--is-inside-work-tree", "--show-toplevel")
    if completed is None or completed.returncode != 0:
        return False
    lines = completed.stdout.strip().splitlines()
    if len(lines) < _GIT_MIN_REV_PARSE_LINES or lines[0].strip() != "true":
        return False
    try:
        toplevel = Path(lines[1].strip()).resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return toplevel == root


def sanitize_environment(
    source: Mapping[str, str],
    *,
    extra: Mapping[str, str] | None = None,
    path_prepend: str | None = None,
) -> dict[str, str]:
    """Build a target process environment without leaking Agent Loop context.

    Only a small allowlist of neutral variables is copied from the caller.
    Explicit ``extra`` entries are added because the caller authorized them;
    they are still rejected if they use an Agent Loop or Shipii prefix.
    """
    environment: dict[str, str] = {}
    for name in _SAFE_ENV_NAMES:
        value = source.get(name)
        if value is not None:
            environment[name] = value
    if path_prepend:
        existing = environment.get("PATH", "")
        environment["PATH"] = f"{path_prepend}{os.pathsep}{existing}" if existing else path_prepend
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for key, value in (extra or {}).items():
        _validate_env_name(key)
        environment[key] = value
    return environment


def _validate_env_name(name: str) -> None:
    if not name or "\x00" in name or "=" in name:
        message = f"invalid environment variable name: {name!r}"
        raise SecurityError(message)
    if name in _FORBIDDEN_ENV_NAMES or name.startswith(_FORBIDDEN_ENV_PREFIXES):
        message = f"environment variable is not allowed for target execution: {name}"
        raise SecurityError(message)
    if _is_git_redirect_variable(name):
        message = f"Git context variable is not allowed for target execution: {name}"
        raise SecurityError(message)
