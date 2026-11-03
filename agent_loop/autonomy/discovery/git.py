"""Read-only Git inspection, baseline capture and change attribution."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from agent_loop.autonomy.errors import SecurityError
from agent_loop.autonomy.security import run_git_command

if TYPE_CHECKING:
    import subprocess
    from pathlib import Path

    from agent_loop.autonomy.security import TargetWorkspace

_STATUS_PREFIX_LENGTH = 4
_STATUS_CODES = frozenset(" MADRCUT?!")
_HASH_CHUNK_SIZE = 65_536
_INDEX_STAGE_POSITION = 2
_INDEX_MIN_FIELDS = 3
_INDEX_STAGE_ZERO = "0"


@dataclass(frozen=True, slots=True)
class GitState:
    """Normalized, read-only snapshot of a target Git work tree."""

    is_repository: bool
    branch: str | None = None
    head: str | None = None
    dirty: bool = False
    changed_files: tuple[str, ...] = ()
    staged_files: tuple[str, ...] = ()
    unstaged_files: tuple[str, ...] = ()
    untracked_files: tuple[str, ...] = ()
    deleted_files: tuple[str, ...] = ()
    renamed_files: tuple[tuple[str, str], ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "is_repository": self.is_repository,
            "branch": self.branch,
            "head": self.head,
            "dirty": self.dirty,
            "changed_files": list(self.changed_files),
            "staged_files": list(self.staged_files),
            "unstaged_files": list(self.unstaged_files),
            "untracked_files": list(self.untracked_files),
            "deleted_files": list(self.deleted_files),
            "renamed_files": [list(item) for item in self.renamed_files],
            "error": self.error,
        }


class ChangeAttribution(StrEnum):
    """Post-hoc attribution of one observed working-tree change."""

    PREEXISTING = "preexisting"
    EXECUTION = "execution"
    VALIDATION = "validation"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True, slots=True)
class GitBaseline:
    """Immutable Git and content-hash snapshot captured before agent execution."""

    state: GitState
    file_hashes: tuple[tuple[str, str], ...]
    reflog_heads: tuple[str, ...] = ()
    index_hashes: tuple[tuple[str, str], ...] = ()

    def hash_for(self, path: str) -> str | None:
        """Return the baseline content hash for a path, if it was captured."""
        return dict(self.file_hashes).get(path)

    def index_hash_for(self, path: str) -> str | None:
        """Return the baseline index blob identifier for a path, if tracked."""
        return dict(self.index_hashes).get(path)

    def to_dict(self) -> dict[str, object]:
        """Return a compact JSON-serializable baseline summary."""
        return {
            "head": self.state.head,
            "branch": self.state.branch,
            "changed_files": list(self.state.changed_files),
            "staged_files": list(self.state.staged_files),
            "unstaged_files": list(self.state.unstaged_files),
            "untracked_files": list(self.state.untracked_files),
            "deleted_files": list(self.state.deleted_files),
            "reflog_entry_count": len(self.reflog_heads),
            "indexed_paths": len(self.index_hashes),
        }


@dataclass(frozen=True, slots=True)
class GitChange:
    """One observed change with its attribution evidence."""

    path: str
    status: str
    attribution: ChangeAttribution
    previous_path: str | None = None
    baseline_hash: str | None = None
    current_hash: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "path": self.path,
            "status": self.status,
            "attribution": self.attribution.value,
            "previous_path": self.previous_path,
            "baseline_hash": self.baseline_hash,
            "current_hash": self.current_hash,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class GitAudit:
    """Comparison between the pre-execution baseline and the final state."""

    baseline: GitBaseline
    final_state: GitState
    changes: tuple[GitChange, ...]
    unexpected_paths: tuple[str, ...]
    head_changed: bool = False
    branch_changed: bool = False
    commit_paths: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def execution_changes(self) -> tuple[GitChange, ...]:
        """Return changes attributable to the agent execution window."""
        return tuple(change for change in self.changes if change.attribution is ChangeAttribution.EXECUTION)

    @property
    def validation_changes(self) -> tuple[GitChange, ...]:
        """Return changes attributable to validator execution."""
        return tuple(change for change in self.changes if change.attribution is ChangeAttribution.VALIDATION)

    @property
    def preexisting_changes(self) -> tuple[GitChange, ...]:
        """Return changes that already existed at baseline and did not change."""
        return tuple(change for change in self.changes if change.attribution is ChangeAttribution.PREEXISTING)

    @property
    def undetermined_changes(self) -> tuple[GitChange, ...]:
        """Return changes whose attribution cannot be established."""
        return tuple(change for change in self.changes if change.attribution is ChangeAttribution.UNDETERMINED)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "baseline": self.baseline.to_dict(),
            "final_state": self.final_state.to_dict(),
            "head_changed": self.head_changed,
            "branch_changed": self.branch_changed,
            "commit_paths": list(self.commit_paths),
            "warnings": list(self.warnings),
            "changes": [change.to_dict() for change in self.changes],
            "execution_changes": [change.to_dict() for change in self.execution_changes],
            "validation_changes": [change.to_dict() for change in self.validation_changes],
            "preexisting_changes": [change.to_dict() for change in self.preexisting_changes],
            "undetermined_changes": [change.to_dict() for change in self.undetermined_changes],
            "unexpected_paths": list(self.unexpected_paths),
        }


@dataclass(frozen=True, slots=True)
class _ParsedStatus:
    staged: tuple[str, ...]
    unstaged: tuple[str, ...]
    untracked: tuple[str, ...]
    deleted: tuple[str, ...]
    renamed: tuple[tuple[str, str], ...]
    changed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _StatusRecord:
    path: str
    index_status: str
    worktree_status: str
    previous: str | None = None


@dataclass(frozen=True, slots=True)
class _CommitChange:
    path: str
    status: str
    previous_path: str | None = None


def discover_git(workspace: TargetWorkspace) -> GitState:
    """Return branch, HEAD and working-tree state without mutating the repo."""
    inside = _git(workspace.root, "rev-parse", "--is-inside-work-tree")
    if inside is None:
        return GitState(is_repository=False, error="git rev-parse could not be executed")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return GitState(is_repository=False, error="target is not a Git work tree")
    branch = _git(workspace.root, "rev-parse", "--abbrev-ref", "HEAD")
    head = _git(workspace.root, "rev-parse", "HEAD")
    status = _git(workspace.root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    branch_text = _clean(branch.stdout) if branch is not None and branch.returncode == 0 else None
    head_text = _clean(head.stdout) if head is not None and head.returncode == 0 else None
    if status is None:
        return GitState(
            is_repository=True,
            branch=branch_text,
            head=head_text,
            error="git status could not be executed",
        )
    if status.returncode != 0:
        detail = status.stderr.strip() or f"exit code {status.returncode}"
        return GitState(
            is_repository=True,
            branch=branch_text,
            head=head_text,
            error=f"git status failed: {detail}",
        )
    parsed, parse_error = _parse_status(status.stdout)
    if parsed is None:
        return GitState(
            is_repository=True,
            branch=branch_text,
            head=head_text,
            error=parse_error or "git status output could not be parsed",
        )
    return GitState(
        is_repository=True,
        branch=branch_text,
        head=head_text,
        dirty=bool(parsed.changed),
        changed_files=parsed.changed,
        staged_files=parsed.staged,
        unstaged_files=parsed.unstaged,
        untracked_files=parsed.untracked,
        deleted_files=parsed.deleted,
        renamed_files=parsed.renamed,
        error=None,
    )


def capture_git_baseline(workspace: TargetWorkspace) -> GitBaseline:
    """Capture the pre-execution Git state and hashes of already-changed files."""
    state = discover_git(workspace)
    hashes: list[tuple[str, str]] = []
    for path in state.changed_files:
        digest = _hash_file(workspace, path)
        if digest is not None:
            hashes.append((path, digest))
    index_hashes = _index_blobs(workspace, state.changed_files) if state.error is None else {}
    return GitBaseline(
        state=state,
        file_hashes=tuple(sorted(hashes)),
        reflog_heads=_reflog_heads(workspace.root),
        index_hashes=tuple(sorted(index_hashes.items())),
    )


def _reflog_heads(root: Path) -> tuple[str, ...]:
    completed = _git(root, "reflog", "show", "--format=%H", "HEAD")
    if completed is None or completed.returncode != 0:
        return ()
    return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())


def _hash_file(workspace: TargetWorkspace, relative_path: str) -> str | None:
    """Hash a workspace entry without following escaping symlinks.

    A symlink is identified by its link text. This keeps unchanged links
    comparable without reading an external target or pretending the target
    content belongs to the workspace.
    """
    try:
        path = workspace.member_path(relative_path)
        if path.is_symlink():
            link = str(path.readlink())
            return f"symlink:{hashlib.sha256(link.encode('utf-8', 'surrogateescape')).hexdigest()}"
        if not path.is_file():
            return None
        resolved = path.resolve(strict=True)
        if not workspace.is_within(resolved):
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
    except (OSError, RuntimeError, ValueError, SecurityError):
        return None
    return f"file:{digest.hexdigest()}"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    return run_git_command(root, *args)


def _index_blobs(workspace: TargetWorkspace, paths: tuple[str, ...]) -> dict[str, str]:
    """Return the index blob identifier for the requested tracked paths."""
    if not paths:
        return {}
    completed = _git(workspace.root, "ls-files", "--stage", "-z")
    if completed is None or completed.returncode != 0:
        return {}
    wanted = set(paths)
    blobs: dict[str, str] = {}
    for record in completed.stdout.split("\0"):
        if not record or "\t" not in record:
            continue
        metadata, path = record.split("\t", 1)
        fields = metadata.split()
        if len(fields) < _INDEX_MIN_FIELDS or fields[_INDEX_STAGE_POSITION] != _INDEX_STAGE_ZERO or path not in wanted:
            continue
        blobs[path] = fields[1]
    return blobs


def _parse_status(output: str) -> tuple[_ParsedStatus | None, str | None]:
    records, error = _status_records(output)
    if error is not None:
        return None, error
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    deleted: list[str] = []
    renamed: list[tuple[str, str]] = []
    changed: list[str] = []
    for record in records:
        if record.index_status == "?" and record.worktree_status == "?":
            untracked.append(record.path)
            changed.append(record.path)
            continue
        if record.index_status == "D" or record.worktree_status == "D":
            deleted.append(record.path)
        if record.index_status != " ":
            staged.append(record.path)
        if record.worktree_status != " ":
            unstaged.append(record.path)
        changed.append(record.path)
        if record.previous is not None:
            renamed.append((record.previous, record.path))
    return (
        _ParsedStatus(
            staged=tuple(sorted(dict.fromkeys(staged))),
            unstaged=tuple(sorted(dict.fromkeys(unstaged))),
            untracked=tuple(sorted(dict.fromkeys(untracked))),
            deleted=tuple(sorted(dict.fromkeys(deleted))),
            renamed=tuple(sorted(dict.fromkeys(renamed))),
            changed=tuple(sorted(dict.fromkeys(changed))),
        ),
        None,
    )


def _status_records(output: str) -> tuple[tuple[_StatusRecord, ...], str | None]:
    raw_records = output.split("\0")
    records: list[_StatusRecord] = []
    index = 0
    while index < len(raw_records):
        raw = raw_records[index]
        index += 1
        if not raw:
            continue
        error = _validate_status_record(raw)
        if error is not None:
            return (), error
        previous: str | None = None
        if _is_rename_record(raw):
            if index >= len(raw_records) or not raw_records[index]:
                return (), "git status rename record is missing its source path"
            previous = raw_records[index]
            index += 1
        records.append(_StatusRecord(path=raw[3:], index_status=raw[0], worktree_status=raw[1], previous=previous))
    return tuple(records), None


def _is_rename_record(record: str) -> bool:
    return record[0] in {"R", "C"} or record[1] in {"R", "C"}


def _validate_status_record(record: str) -> str | None:
    if len(record) < _STATUS_PREFIX_LENGTH or record[2] != " ":
        return f"git status produced a malformed record: {record!r}"
    index_status, worktree_status = record[0], record[1]
    if index_status not in _STATUS_CODES or worktree_status not in _STATUS_CODES:
        return f"git status produced an unknown status code: {record[:2]!r}"
    if (index_status == "?") != (worktree_status == "?"):
        return f"git status produced an inconsistent untracked record: {record!r}"
    if not record[3:]:
        return "git status produced an empty path"
    return None


def _clean(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None
