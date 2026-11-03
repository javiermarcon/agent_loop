"""Change attribution and Git history evidence for the target workspace."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from typing import TYPE_CHECKING

from agent_loop.autonomy.discovery.git import ChangeAttribution
from agent_loop.autonomy.discovery.git import GitAudit
from agent_loop.autonomy.discovery.git import GitBaseline
from agent_loop.autonomy.discovery.git import GitChange
from agent_loop.autonomy.discovery.git import GitState
from agent_loop.autonomy.discovery.git import _CommitChange
from agent_loop.autonomy.discovery.git import _git
from agent_loop.autonomy.discovery.git import _hash_file
from agent_loop.autonomy.discovery.git import _index_blobs
from agent_loop.autonomy.discovery.git import _reflog_heads

if TYPE_CHECKING:
    from agent_loop.autonomy.security import TargetWorkspace


_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


@dataclass(frozen=True, slots=True)
class _HistoryEvidence:
    head_changed: bool
    branch_changed: bool
    commit_changes: tuple[_CommitChange, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _AttributionContext:
    baseline: GitBaseline
    baseline_index: dict[str, str]
    final_index: dict[str, str]
    final_hashes: dict[str, str]
    final_state: GitState


def audit_git_changes(
    workspace: TargetWorkspace,
    baseline: GitBaseline,
    final_state: GitState,
    *,
    scope_paths: tuple[str, ...] = (),
) -> GitAudit:
    """Compare the baseline with the final state and attribute observed changes."""
    baseline_hashes = dict(baseline.file_hashes)
    baseline_index = dict(baseline.index_hashes)
    final_hashes = {
        path: digest for path in final_state.changed_files if (digest := _hash_file(workspace, path)) is not None
    }
    final_index = _index_blobs(workspace, final_state.changed_files) if final_state.error is None else {}
    baseline_paths = set(baseline.state.changed_files)
    final_paths = set(final_state.changed_files)
    rename_by_new = {new: old for old, new in final_state.renamed_files}
    rename_sources = set(rename_by_new.values())
    changes_by_path: dict[str, GitChange] = {}
    attribution = _AttributionContext(
        baseline=baseline,
        baseline_index=baseline_index,
        final_index=final_index,
        final_hashes=final_hashes,
        final_state=final_state,
    )
    for path in sorted(final_paths):
        previous = rename_by_new.get(path)
        changes_by_path[path] = GitChange(
            path=path,
            status=_change_status(final_state, path, previous),
            attribution=_attribute_change(path, previous, attribution),
            previous_path=previous,
            baseline_hash=baseline_hashes.get(path) or baseline_hashes.get(previous or ""),
            current_hash=final_hashes.get(path),
        )
    for path in sorted(baseline_paths - final_paths - rename_sources):
        changes_by_path[path] = GitChange(
            path=path,
            status="cleared",
            attribution=ChangeAttribution.UNDETERMINED,
            baseline_hash=baseline_hashes.get(path),
            detail="change present at baseline but absent from final status",
        )
    history = _history_evidence(workspace, baseline, final_state)
    _integrate_commit_changes(changes_by_path, history.commit_changes)
    warnings = [
        *history.warnings,
        *_state_warnings(baseline.state, "baseline"),
        *_state_warnings(final_state, "final"),
    ]
    changes = tuple(sorted(changes_by_path.values(), key=lambda change: change.path))
    return GitAudit(
        baseline=baseline,
        final_state=final_state,
        changes=changes,
        unexpected_paths=_unexpected_paths(changes, scope_paths),
        head_changed=history.head_changed,
        branch_changed=history.branch_changed,
        commit_paths=tuple(
            sorted(
                {
                    path
                    for change in history.commit_changes
                    for path in (change.path, change.previous_path)
                    if path is not None
                },
            ),
        ),
        warnings=tuple(warnings),
    )


def _state_warnings(state: GitState, label: str) -> list[str]:
    return [f"{label} Git state error: {state.error}"] if state.error else []


def merge_window_attribution(
    final_audit: GitAudit,
    *,
    agent_audits: tuple[GitAudit, ...] = (),
    validation_audits: tuple[GitAudit, ...] = (),
) -> GitAudit:
    """Reclassify final changes using the per-execution and per-validator windows."""
    agent_paths = {change.path for audit in agent_audits for change in audit.execution_changes}
    validation_paths = {change.path for audit in validation_audits for change in audit.execution_changes}
    merged: list[GitChange] = []
    for change in final_audit.changes:
        if change.attribution is ChangeAttribution.PREEXISTING:
            merged.append(change)
            continue
        if change.path in validation_paths:
            if change.attribution is ChangeAttribution.UNDETERMINED or change.path in agent_paths:
                merged.append(
                    replace(
                        change,
                        attribution=ChangeAttribution.UNDETERMINED,
                        detail=_append_detail(change.detail, "both agent and validator windows changed this path"),
                    ),
                )
            else:
                merged.append(
                    replace(
                        change,
                        attribution=ChangeAttribution.VALIDATION,
                        detail=_append_detail(change.detail, "changed during a validator window"),
                    ),
                )
            continue
        merged.append(change)
    changes = tuple(sorted(merged, key=lambda change: change.path))
    unexpected = tuple(
        path
        for path in final_audit.unexpected_paths
        if any(change.path == path and change.attribution is ChangeAttribution.EXECUTION for change in changes)
    )
    return replace(final_audit, changes=changes, unexpected_paths=unexpected)


def _change_status(final_state: GitState, path: str, previous: str | None) -> str:
    if path in final_state.untracked_files:
        return "untracked"
    if path in final_state.deleted_files:
        return "deleted"
    if previous is not None:
        return "renamed"
    return "modified"


def _attribute_change(
    path: str,
    previous: str | None,
    context: _AttributionContext,
) -> ChangeAttribution:
    baseline_paths = set(context.baseline.state.changed_files)
    current_hash = context.final_hashes.get(path)
    index_changed = context.baseline_index.get(path) != context.final_index.get(path)
    if path not in baseline_paths:
        return _attribution_for_new_path(path, current_hash, context.final_state)
    if previous is not None:
        return _attribution_for_rename(path, previous, context.baseline, current_hash)
    return _attribution_for_existing_path(
        path,
        context.baseline,
        current_hash,
        context.final_state,
        index_changed=index_changed,
    )


def _attribution_for_new_path(
    path: str,
    current_hash: str | None,
    final_state: GitState,
) -> ChangeAttribution:
    if current_hash is None and path not in final_state.deleted_files:
        return ChangeAttribution.UNDETERMINED
    return ChangeAttribution.EXECUTION


def _attribution_for_rename(
    path: str,
    previous: str,
    baseline: GitBaseline,
    current_hash: str | None,
) -> ChangeAttribution:
    if (previous, path) in baseline.state.renamed_files and baseline.hash_for(path) == current_hash:
        return ChangeAttribution.PREEXISTING
    return ChangeAttribution.EXECUTION


def _attribution_for_existing_path(
    path: str,
    baseline: GitBaseline,
    current_hash: str | None,
    final_state: GitState,
    *,
    index_changed: bool,
) -> ChangeAttribution:
    if index_changed:
        # The index moved after the baseline even when the working tree bytes
        # did not. That transition belongs to the observation window.
        return ChangeAttribution.EXECUTION
    baseline_deleted = path in baseline.state.deleted_files
    final_deleted = path in final_state.deleted_files
    if baseline_deleted:
        return ChangeAttribution.PREEXISTING if final_deleted else ChangeAttribution.EXECUTION
    if final_deleted:
        return ChangeAttribution.EXECUTION if baseline.hash_for(path) is not None else ChangeAttribution.UNDETERMINED
    baseline_hash = baseline.hash_for(path)
    if baseline_hash is None or current_hash is None:
        return ChangeAttribution.UNDETERMINED
    if baseline_hash == current_hash:
        return ChangeAttribution.PREEXISTING
    return ChangeAttribution.EXECUTION


def _history_evidence(
    workspace: TargetWorkspace,
    baseline: GitBaseline,
    final_state: GitState,
) -> _HistoryEvidence:
    baseline_head = baseline.state.head
    final_head = final_state.head
    head_changed = baseline_head != final_head
    branch_changed = baseline.state.branch != final_state.branch
    warnings: list[str] = []
    commit_changes: list[_CommitChange] = []
    if branch_changed:
        warnings.append(
            f"Git branch changed from {baseline.state.branch or 'unknown'} to {final_state.branch or 'unknown'}",
        )
    if head_changed:
        direct_changes, direct_warnings = _head_change_evidence(workspace, baseline_head, final_head)
        commit_changes.extend(direct_changes)
        warnings.extend(direct_warnings)
    elif baseline_head is not None and not branch_changed:
        reflog_changed, reflog_changes, reflog_warnings = _reflog_change_evidence(
            workspace,
            baseline,
            final_head,
        )
        head_changed = reflog_changed
        commit_changes.extend(reflog_changes)
        warnings.extend(reflog_warnings)
    return _HistoryEvidence(
        head_changed=head_changed,
        branch_changed=branch_changed,
        commit_changes=tuple(_deduplicate_commit_changes(commit_changes)),
        warnings=tuple(warnings),
    )


def _head_change_evidence(
    workspace: TargetWorkspace,
    baseline_head: str | None,
    final_head: str | None,
) -> tuple[list[_CommitChange], list[str]]:
    warnings = [f"Git HEAD changed from {baseline_head or 'none'} to {final_head or 'none'}"]
    if final_head is None:
        warnings.append("final HEAD could not be resolved; commit paths cannot be inspected")
        return [], warnings
    parsed, error = _commit_diff(workspace, baseline_head or _EMPTY_TREE_SHA, final_head)
    if error is not None:
        warnings.append(error)
        return [], warnings
    return list(parsed), warnings


def _reflog_change_evidence(
    workspace: TargetWorkspace,
    baseline: GitBaseline,
    final_head: str | None,
) -> tuple[bool, list[_CommitChange], list[str]]:
    final_reflog = _reflog_heads(workspace.root)
    if final_reflog == baseline.reflog_heads:
        return False, [], []
    warnings = ["Git HEAD reflog changed although HEAD returned to its original value"]
    changes: list[_CommitChange] = []
    new_heads = _new_reflog_entries(baseline.reflog_heads, final_reflog)
    for candidate in new_heads:
        if candidate == final_head:
            continue
        parsed, error = _commit_diff(workspace, final_head or candidate, candidate)
        if error is not None:
            warnings.append(error)
            continue
        changes.extend(parsed)
    if not changes and not new_heads:
        warnings.append("transient commits cannot be inspected from the current reflog")
    return True, changes, warnings


def _commit_diff(workspace: TargetWorkspace, base: str, head: str) -> tuple[tuple[_CommitChange, ...], str | None]:
    completed = _git(workspace.root, "diff", "--name-status", "-z", "--find-renames", base, head)
    if completed is None:
        return (), "Git commit range inspection could not be executed"
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown Git error"
        return (), f"Git commit range inspection failed: {detail}"
    return _parse_name_status(completed.stdout), None


def _parse_name_status(output: str) -> tuple[_CommitChange, ...]:
    records = output.split("\0")
    changes: list[_CommitChange] = []
    index = 0
    while index < len(records):
        status = records[index]
        index += 1
        if not status:
            continue
        code = status[0]
        if code in {"R", "C"}:
            if index + 1 >= len(records):
                break
            previous = records[index]
            path = records[index + 1]
            index += 2
            changes.append(
                _CommitChange(
                    path=path,
                    status="renamed" if code == "R" else "copied",
                    previous_path=previous,
                ),
            )
            continue
        if index >= len(records):
            break
        path = records[index]
        index += 1
        changes.append(_CommitChange(path=path, status=_commit_status(code)))
    return tuple(changes)


def _commit_status(code: str) -> str:
    return {
        "A": "added",
        "D": "deleted",
        "M": "modified",
        "T": "modified",
    }.get(code, "modified")


def _deduplicate_commit_changes(changes: list[_CommitChange]) -> list[_CommitChange]:
    deduplicated: dict[str, _CommitChange] = {}
    for change in changes:
        deduplicated.setdefault(change.path, change)
    return [deduplicated[path] for path in sorted(deduplicated)]


def _integrate_commit_changes(
    changes: dict[str, GitChange],
    commits: tuple[_CommitChange, ...],
) -> None:
    detail = "observed in commits between baseline and final HEAD; actor cannot be established"
    for commit in commits:
        existing = changes.get(commit.path)
        if existing is None:
            changes[commit.path] = GitChange(
                path=commit.path,
                status=commit.status,
                attribution=ChangeAttribution.UNDETERMINED,
                previous_path=commit.previous_path,
                detail=detail,
            )
            continue
        if existing.attribution is ChangeAttribution.EXECUTION or existing.status == "cleared":
            changes[commit.path] = replace(
                existing,
                attribution=ChangeAttribution.UNDETERMINED,
                detail=_append_detail(existing.detail, detail),
            )


def _new_reflog_entries(baseline_heads: tuple[str, ...], final_heads: tuple[str, ...]) -> tuple[str, ...]:
    baseline_set = set(baseline_heads)
    return tuple(dict.fromkeys(head for head in final_heads if head not in baseline_set))


def _unexpected_paths(changes: tuple[GitChange, ...], scope_paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            change.path
            for change in changes
            if change.attribution is ChangeAttribution.EXECUTION and not _in_scope(change.path, scope_paths)
        ),
    )


def _in_scope(path: str, scope_paths: tuple[str, ...]) -> bool:
    if not scope_paths:
        return True
    for scope in scope_paths:
        normalized = scope.strip("/")
        if not normalized or path == normalized or path.startswith(f"{normalized}/"):
            return True
    return False


def _append_detail(existing: str, extra: str) -> str:
    return f"{existing}; {extra}" if existing else extra
