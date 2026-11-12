"""Tests for canonical resume invocation coordination."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import pytest

from agent_loop.application import resume_invocation

_LIST_EXIT = 0
_SELECTION_EXIT = 3
_PREPARATION_EXIT = 7
_INCOMPATIBLE_EXIT = 5
_INTERACTION_EXIT = 8
_POST_LOCK_EXIT = 4


@dataclass
class _InvocationFake:
    """Record observable collaborator order without legacy infrastructure."""

    catalog: str | None = "catalog"
    catalog_exit: int | None = None
    candidate: str | None = "candidate"
    store: str | None = "store"
    preparation_exit: int | None = None
    compatible_candidate: str | None = "prepared-candidate"
    compatible_config: str | None = "compatible-config"
    compatibility_exit: int | None = None
    context_exit: int | None = None
    prelock_candidate: str | None = "lock-ready-candidate"
    prelock_exit: int | None = None
    acquired_exit: int | None = None
    lock: str | None = "lock"
    events: list[str] = field(default_factory=list)

    def resolve_catalog(
        self,
        request: str,
        target: str | None,
    ) -> resume_invocation.ResumeCatalogResult[str]:
        self.events.append(f"catalog:{request}:{target}")
        return resume_invocation.ResumeCatalogResult(self.catalog, self.catalog_exit)

    def list_candidates(self, catalog: str, target: str) -> None:
        self.events.append(f"list:{catalog}:{target}")

    def prepare_candidate(
        self,
        request: str,
        catalog: str,
        target: str,
    ) -> resume_invocation.ResumeCandidateResult[str, str]:
        self.events.append(f"prepare:{request}:{catalog}:{target}")
        return resume_invocation.ResumeCandidateResult(
            self.candidate,
            self.store,
            self.preparation_exit,
        )

    def load_config(self, candidate: str) -> str:
        self.events.append(f"load-config:{candidate}")
        return "persisted-config"

    def ensure_compatible_config(
        self,
        request: str,
        candidate: str,
        store: str,
        config: str,
    ) -> resume_invocation.ResumeConfigResult[str, str]:
        self.events.append(f"compatibility:{request}:{candidate}:{store}:{config}")
        return resume_invocation.ResumeConfigResult(
            self.compatible_candidate,
            self.compatible_config,
            self.compatibility_exit,
        )

    def restore_options(self, request: str, config: str) -> None:
        self.events.append(f"restore-options:{request}:{config}")

    def apply_override(self, request: str, config: str, candidate: str) -> str:
        self.events.append(f"override:{request}:{config}:{candidate}")
        return "effective-config"

    def restore_context(self, request: str, config: str) -> int | None:
        self.events.append(f"restore-context:{request}:{config}")
        return self.context_exit

    def prepare_lock(
        self,
        request: str,
        candidate: str,
        store: str,
        config: str,
    ) -> resume_invocation.ResumePrelockResult[str]:
        self.events.append(f"sensitive-confirmation:{candidate}:{config}")
        return resume_invocation.ResumePrelockResult(
            self.prelock_candidate,
            self.prelock_exit,
        )

    def acquire_lock(
        self,
        request: str,
        candidate: str,
        store: str,
        config: str,
    ) -> resume_invocation.ResumeInvocationConfiguration[str]:
        self.events.append(f"lock:{request}:{candidate}:{store}:{config}")
        if self.acquired_exit is not None:
            return resume_invocation.ResumeInvocationConfiguration.terminal(
                self.acquired_exit,
            )
        if self.lock is None:
            return resume_invocation.ResumeInvocationConfiguration.continue_without_ownership()
        return resume_invocation.ResumeInvocationConfiguration.transferred(self.lock)

    def collaborators(
        self,
    ) -> resume_invocation.ResumeInvocationCollaborators[
        str,
        str,
        str,
        str,
        str,
        str,
        str,
    ]:
        return resume_invocation.ResumeInvocationCollaborators(
            discovery=resume_invocation.ResumeDiscoveryCollaborators(
                resolve_catalog=self.resolve_catalog,
                list_candidates=self.list_candidates,
                prepare_candidate=self.prepare_candidate,
            ),
            configuration=resume_invocation.ResumeConfigurationCollaborators(
                load_config=self.load_config,
                ensure_compatible_config=self.ensure_compatible_config,
                restore_options=self.restore_options,
                apply_override=self.apply_override,
                restore_context=self.restore_context,
            ),
            ownership=resume_invocation.ResumeOwnershipCollaborators(
                prepare_lock=self.prepare_lock,
                acquire_lock=self.acquire_lock,
            ),
        )


@dataclass
class _LockCapability:
    """Record concrete release attempts for ownership contract tests."""

    release_error: OSError | None = None
    release_count: int = 0

    def release(self) -> None:
        """Release once or expose the configured infrastructure failure."""
        self.release_count += 1
        if self.release_error is not None:
            raise self.release_error


def _configure(
    fake: _InvocationFake,
    *,
    request: str = "explicit-resume",
    target: str | None = "target",
    list_requested: bool = False,
) -> resume_invocation.ResumeInvocationConfiguration[str]:
    return resume_invocation.configure_resume_invocation(
        request,
        target,
        list_requested=list_requested,
        missing_target_exit=_INCOMPATIBLE_EXIT,
        collaborators=fake.collaborators(),
    )


def test_resume_configuration_states_are_exclusive() -> None:
    """A resume outcome cannot combine terminal exit and transferred ownership."""
    lock = _LockCapability()

    continuing: resume_invocation.ResumeInvocationConfiguration[_LockCapability] = (
        resume_invocation.ResumeInvocationConfiguration.continue_without_ownership()
    )
    terminal: resume_invocation.ResumeInvocationConfiguration[_LockCapability] = (
        resume_invocation.ResumeInvocationConfiguration.terminal(_POST_LOCK_EXIT)
    )
    transferred = resume_invocation.ResumeInvocationConfiguration.transferred(lock)

    assert continuing.disposition is resume_invocation.ResumeInvocationDisposition.CONTINUE
    assert continuing.exit_code is None
    assert continuing.lock is None
    assert terminal.disposition is resume_invocation.ResumeInvocationDisposition.TERMINAL
    assert terminal.exit_code == _POST_LOCK_EXIT
    assert terminal.lock is None
    assert transferred.disposition is resume_invocation.ResumeInvocationDisposition.TRANSFERRED
    assert transferred.exit_code is None
    assert transferred.lock is lock
    with pytest.raises(ValueError, match=r"terminal exit.*lock"):
        resume_invocation.ResumeInvocationConfiguration(
            resume_invocation.ResumeInvocationDisposition.TERMINAL,
            _POST_LOCK_EXIT,
            lock,
        )


def test_acquired_resume_ownership_transfers_exactly_once() -> None:
    """Transfer ends adapter ownership without releasing the lifecycle capability."""
    lock = _LockCapability()
    ownership = resume_invocation.ResumeLockOwnership.acquired(lock)

    assert ownership.held_lock is lock
    assert ownership.transfer() is lock
    assert ownership.state is resume_invocation.ResumeLockOwnershipState.TRANSFERRED
    assert lock.release_count == 0
    with pytest.raises(RuntimeError, match="already transferred"):
        ownership.transfer()


def test_acquired_resume_ownership_release_is_single_and_failure_safe() -> None:
    """Release transitions only after concrete cleanup succeeds."""
    lock = _LockCapability()
    ownership = resume_invocation.ResumeLockOwnership.acquired(lock)

    ownership.release()

    assert ownership.state is resume_invocation.ResumeLockOwnershipState.RELEASED
    assert lock.release_count == 1
    with pytest.raises(RuntimeError, match="already released"):
        ownership.release()
    assert lock.release_count == 1

    release_error = OSError("release unavailable")
    failing_lock = _LockCapability(release_error=release_error)
    failing_ownership = resume_invocation.ResumeLockOwnership.acquired(failing_lock)
    with pytest.raises(OSError, match="release unavailable"):
        failing_ownership.release()
    assert failing_ownership.state is resume_invocation.ResumeLockOwnershipState.ACQUIRED
    assert failing_lock.release_count == 1


def test_no_resume_stops_without_catalog_store_or_lock() -> None:
    """A catalog no-op prevents every later stage and side effect."""
    fake = _InvocationFake(catalog=None)

    assert _configure(fake) == resume_invocation.ResumeInvocationConfiguration.continue_without_ownership()
    assert fake.events == ["catalog:explicit-resume:target"]


def test_list_resumable_is_terminal_and_read_only() -> None:
    """Listing emits through its adapter and never prepares or locks a run."""
    fake = _InvocationFake()

    assert _configure(fake, list_requested=True) == (
        resume_invocation.ResumeInvocationConfiguration.terminal(_LIST_EXIT)
    )
    assert fake.events == [
        "catalog:explicit-resume:target",
        "list:catalog:target",
    ]


@pytest.mark.parametrize("invocation_kind", ["explicit-resume", "resume-last"])
def test_resume_orders_all_collaborators_and_returns_lifecycle_lock(
    invocation_kind: str,
) -> None:
    """Explicit and latest resume preserve the complete observable phase order."""
    fake = _InvocationFake()

    assert _configure(fake, request=invocation_kind) == (
        resume_invocation.ResumeInvocationConfiguration.transferred("lock")
    )
    assert fake.events == [
        f"catalog:{invocation_kind}:target",
        f"prepare:{invocation_kind}:catalog:target",
        "load-config:candidate",
        f"compatibility:{invocation_kind}:candidate:store:persisted-config",
        f"restore-options:{invocation_kind}:compatible-config",
        f"override:{invocation_kind}:compatible-config:prepared-candidate",
        f"restore-context:{invocation_kind}:effective-config",
        "sensitive-confirmation:prepared-candidate:effective-config",
        f"lock:{invocation_kind}:lock-ready-candidate:store:effective-config",
    ]
    assert (
        fake.events.count(
            f"lock:{invocation_kind}:lock-ready-candidate:store:effective-config",
        )
        == 1
    )


@pytest.mark.parametrize("exit_code", [_SELECTION_EXIT, _PREPARATION_EXIT])
def test_candidate_failure_stops_configuration_and_lock(exit_code: int) -> None:
    """Selection and preparation terminal results cut every later phase short."""
    fake = _InvocationFake(candidate=None, store=None, preparation_exit=exit_code)

    assert _configure(fake).exit_code == exit_code
    assert fake.events == [
        "catalog:explicit-resume:target",
        "prepare:explicit-resume:catalog:target",
    ]


def test_compatibility_failure_stops_before_restoration_override_and_lock() -> None:
    """Config compatibility/migration exits remain terminal before mutations."""
    fake = _InvocationFake(
        compatible_candidate=None,
        compatible_config=None,
        compatibility_exit=_INCOMPATIBLE_EXIT,
    )

    assert _configure(fake).exit_code == _INCOMPATIBLE_EXIT
    assert fake.events[-1] == ("compatibility:explicit-resume:candidate:store:persisted-config")


def test_restoration_uses_prepared_candidate_and_effective_override() -> None:
    """The repaired candidate and explicit override feed context and ownership."""
    fake = _InvocationFake(context_exit=_INCOMPATIBLE_EXIT)

    assert _configure(fake).exit_code == _INCOMPATIBLE_EXIT
    assert "override:explicit-resume:compatible-config:prepared-candidate" in fake.events
    assert fake.events[-1] == "restore-context:explicit-resume:effective-config"
    assert not any(event.startswith("lock:") for event in fake.events)


@pytest.mark.parametrize(
    ("prelock_exit", "acquired_exit", "expected_exit", "expected_lock"),
    [
        (_INTERACTION_EXIT, None, _INTERACTION_EXIT, None),
        (None, None, None, "lock"),
        (None, _POST_LOCK_EXIT, _POST_LOCK_EXIT, None),
    ],
)
def test_sensitive_and_post_lock_terminal_paths_preserve_adapter_ownership(
    prelock_exit: int | None,
    acquired_exit: int | None,
    expected_exit: int | None,
    expected_lock: str | None,
) -> None:
    """Pre-lock rejection avoids acquisition; post-lock cleanup stays in its adapter."""
    fake = _InvocationFake(
        prelock_candidate=None if prelock_exit is not None else "lock-ready-candidate",
        prelock_exit=prelock_exit,
        acquired_exit=acquired_exit,
        lock=expected_lock,
    )

    if expected_exit is not None:
        expected: resume_invocation.ResumeInvocationConfiguration[str] = (
            resume_invocation.ResumeInvocationConfiguration.terminal(expected_exit)
        )
    else:
        assert expected_lock is not None
        expected = resume_invocation.ResumeInvocationConfiguration.transferred(expected_lock)
    assert _configure(fake) == expected
    lock_events = [event for event in fake.events if event.startswith("lock:")]
    assert len(lock_events) == (0 if prelock_exit is not None else 1)


def test_exception_before_lock_does_not_enter_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operational failures propagate without asking any lock adapter to clean up."""
    fake = _InvocationFake()

    def fail_config(_candidate: str) -> str:
        message = "config unavailable"
        raise OSError(message)

    monkeypatch.setattr(fake, "load_config", fail_config)

    with pytest.raises(OSError, match="config unavailable"):
        _configure(fake)
    assert not any(event.startswith("lock:") for event in fake.events)


def test_coordinator_has_no_legacy_dynamic_or_process_argv_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Application coordination uses only explicit typed collaborators."""
    monkeypatch.setattr(sys, "argv", ["agent-loop", "--agent-command", "ignored"])
    fake = _InvocationFake()

    _configure(fake, request="explicit-request")

    source = Path(resume_invocation.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
    roots = {name.split(".", maxsplit=1)[0] for name in imported}
    assert not roots.intersection({"importlib", "scripts", "sys"})
    assert "getattr(" not in source
    assert "Any" not in source
    assert "resume_candidate_selection" not in source
    assert "resume_candidate_repair" not in source
    assert "resume_compatibility" not in source
    assert "resume_config_migration" not in source
