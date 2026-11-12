"""Runtime guard against accidental heavy operations in unit and E2E tests.

Shipii detected costly requests through profiling markers emitted by its HTTP
middleware and failed the quality gate when ``SLOW REQUEST`` or
``DB HEAVY REQUEST`` appeared. AgentLoop has no HTTP middleware, so the
equivalent control is enforced at the process/network boundary: real provider
CLIs, package managers, container runtimes, remote Git operations and
non-loopback sockets are heavy by definition.

Tests that intentionally exercise such an operation must opt in with the
``heavy_request`` pytest marker (or the ``@heavy`` Behave tag). Everything else
fails with a structured violation instead of silently consuming tokens or
network access.

The control emits an explicit report so the integral runner can distinguish
"guard active and observed work" from "guard never ran". Silence is never
accepted as evidence of safety.
"""

from __future__ import annotations

import functools
import os
import shlex
import sys
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Self
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterator
    from collections.abc import Sequence

HEAVY_EXECUTABLES = frozenset(
    {
        "docker",
        "docker-compose",
        "podman",
        "nerdctl",
        "buildah",
        "ctr",
        "lxc",
        "kubectl",
        "opencode",
        "claude",
        "codex",
        "cursor-agent",
        "aider",
        "gemini",
        "npm",
        "npx",
        "pnpm",
        "yarn",
        "bun",
        "pip",
        "pip3",
        "pipx",
        "poetry",
        "uv",
        "conda",
        "mamba",
        "composer",
        "gem",
        "bundle",
        "apt",
        "apt-get",
        "brew",
        "curl",
        "wget",
        "ssh",
        "scp",
        "rsync",
        "gh",
    },
)
HEAVY_GIT_SUBCOMMANDS = frozenset({"fetch", "pull", "push", "clone", "ls-remote", "submodule"})
HEAVY_GIT_REMOTE_ACTIONS = frozenset({"update", "prune", "show"})
HEAVY_PYTHON_MODULES = frozenset(
    {
        "build",
        "pip",
        "pipx",
        "venv",
        "ensurepip",
        "poetry",
        "uv",
        "opencode",
        "claude",
        "codex",
    },
)
_WRAPPER_EXECUTABLES = frozenset(
    {
        "env",
        "sudo",
        "nice",
        "nohup",
        "timeout",
        "stdbuf",
        "command",
        "ionice",
        "taskset",
        "xargs",
        "setsid",
        "busybox",
        "toybox",
    },
)
_SHELL_EXECUTABLES = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
_WRAPPER_VALUE_OPTIONS = frozenset(
    {"-u", "-g", "-p", "-n", "-c", "-i", "-s", "-a", "-d", "-e", "-I", "-L", "-P", "-x", "-o", "-E", "-S"},
)
_SPAWN_FUNCTIONS = (
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0"})  # noqa: S104 - comparison only
WATCHED_AUDIT_EVENTS = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyname_ex",
        "socket.gethostbyaddr",
        "socket.getnameinfo",
        "socket.sendto",
        "socket.sendmsg",
        "http.client.connect",
        "urllib.Request",
    },
)
GUARD_REPORT_MARKER = "HEAVY REQUEST GUARD:"
VIOLATION_MARKER = "HEAVY REQUEST VIOLATION:"
AUTHORIZED_MARKER = "HEAVY REQUEST ALLOWED:"
ERROR_MARKER = "HEAVY REQUEST ERROR:"
_STATE: dict[str, object] = {
    "guard": None,
    "installed": False,
    "spawn_wrapped": False,
}
_STATS: dict[str, object] = {
    "initialized": False,
    "activated": 0,
    "observed": 0,
    "violations": [],
    "authorized": [],
    "expected": 0,
    "errors": [],
}


class HeavyRequestGuardError(RuntimeError):
    """Raised when the audit hook cannot be installed or trusted."""


@dataclass(frozen=True, slots=True)
class HeavyViolation:
    """One observed heavy operation with the reason it is heavy."""

    operation: str
    detail: str

    def render(self) -> str:
        """Return the stable human-readable form used in failures."""
        return f"{VIOLATION_MARKER} {self.operation} - {self.detail}"


@dataclass
class HeavyRequestGuard:
    """Collect heavy operations observed while this guard is active.

    ``allow`` opts into the operation for the current scope and records the
    event as an authorized exception. ``expected`` is reserved for the guard's
    own negative tests: it keeps the local violation for assertions without
    claiming that the repository performed an undeclared heavy operation.
    """

    violations: list[HeavyViolation] = field(default_factory=list)
    authorized: list[HeavyViolation] = field(default_factory=list)
    allow: bool = False
    reason: str = "explicit opt-in"
    expected: bool = False
    observed: int = 0

    def check(self, event: str, args: tuple[object, ...]) -> None:
        """Inspect one audit event and record a violation when applicable."""
        if event not in WATCHED_AUDIT_EVENTS:
            return
        self.observed += 1
        _increment("observed")
        violation = classify_audit_event(event, args)
        if violation is None:
            return
        if self.allow:
            self.authorized.append(violation)
            _authorized(self.reason, violation)
            return
        self.violations.append(violation)
        if self.expected:
            _increment("expected")

    def __enter__(self) -> Self:
        install_audit_hook()
        self._previous = _STATE["guard"]
        _STATE["guard"] = self
        _increment("activated")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        _STATE["guard"] = getattr(self, "_previous", None)

    def __iter__(self) -> Iterator[HeavyViolation]:
        return iter(self.violations)


def install_audit_hook() -> None:
    """Install the process-wide audit hook exactly once.

    Raises:
        HeavyRequestGuardError: If the interpreter cannot install the hook.
    """
    if _STATE["installed"]:
        _STATS["initialized"] = True
        _install_spawn_wrappers()
        return
    if not hasattr(sys, "addaudithook"):
        message = "sys.addaudithook is unavailable"
        _record_error(message)
        raise HeavyRequestGuardError(message)
    try:
        sys.addaudithook(_audit)
        _install_spawn_wrappers()
    except Exception as error:
        message = f"cannot install heavy-request audit hook: {error}"
        _record_error(message)
        raise HeavyRequestGuardError(message) from error
    _STATE["installed"] = True
    _STATS["initialized"] = True


def _install_spawn_wrappers() -> None:
    """Wrap ``os.spawn*`` because CPython emits no audit event for them."""
    if _STATE["spawn_wrapped"]:
        return
    for name in _SPAWN_FUNCTIONS:
        original = getattr(os, name, None)
        if original is None or not callable(original):
            continue
        setattr(os, name, _spawn_wrapper(name, original))
    _STATE["spawn_wrapped"] = True


def _spawn_wrapper(name: str, original: Callable[..., object]) -> Callable[..., object]:
    @functools.wraps(original)
    def _wrapper(*args: object, **kwargs: object) -> object:
        _audit("os.spawn", (name, _spawn_command(name, args)))
        return original(*args, **kwargs)

    return _wrapper


def _spawn_command(name: str, args: tuple[object, ...]) -> tuple[str, ...]:
    """Normalize one ``os.spawn*`` call to the executable plus arguments.

    Every ``os.spawn*`` signature starts with the ``mode`` argument. The
    ``spawnv*`` family follows with ``path, argv[, env]``; the ``spawnl*``
    family follows with ``path, arg0, ...[, env]``.
    """
    if name in {"spawnv", "spawnve", "spawnvp", "spawnvpe"}:
        if len(args) > 2 and isinstance(args[2], (list, tuple)):
            argv = args[2]
            return (str(args[1]), *(str(part) for part in argv[1:]))
        if len(args) > 1:
            return (str(args[1]),)
        return ()
    if len(args) <= 2:
        return tuple(str(part) for part in args[1:])
    return (str(args[1]), *(str(part) for part in args[3:]))


def reset_statistics() -> None:
    """Clear accumulated evidence before one pytest/Behave session."""
    _STATS["initialized"] = bool(_STATE["installed"])
    _STATS["activated"] = 0
    _STATS["observed"] = 0
    _STATS["violations"] = []
    _STATS["authorized"] = []
    _STATS["expected"] = 0
    _STATS["errors"] = []


def record_violation(violation: HeavyViolation, *, source: str) -> None:
    """Record a violation that will fail the current test or scenario."""
    violations = _STATS["violations"]
    assert isinstance(violations, list)
    violations.append(f"{violation.render()} source={source}")


def record_authorization(reason: str, *, detail: str = "") -> None:
    """Record an explicit test/scenario-level heavy-operation authorization."""
    authorized = _STATS["authorized"]
    assert isinstance(authorized, list)
    suffix = f" ({detail})" if detail else ""
    authorized.append(f"{AUTHORIZED_MARKER} {reason}{suffix}")


def render_report(scope: str) -> str:
    """Return the stable evidence block printed at the end of a test session."""
    violations = _STATS["violations"]
    authorized = _STATS["authorized"]
    errors = _STATS["errors"]
    assert isinstance(violations, list)
    assert isinstance(authorized, list)
    assert isinstance(errors, list)
    initialized = "yes" if _STATS["initialized"] else "no"
    lines = [
        (
            f"{GUARD_REPORT_MARKER} scope={scope} initialized={initialized} "
            f"activated={_STATS['activated']} observed={_STATS['observed']} "
            f"violations={len(violations)} authorized={len(authorized)} "
            f"expected={_STATS['expected']} errors={len(errors)}"
        ),
    ]
    lines.extend(
        f"{VIOLATION_MARKER} {line.removeprefix(VIOLATION_MARKER).strip()}"
        if not line.startswith(VIOLATION_MARKER)
        else line
        for line in violations
    )
    lines.extend(authorized)
    lines.extend(errors)
    return "\n".join(lines)


def statistics() -> dict[str, object]:
    """Return a shallow copy of the accumulated evidence for tests."""
    return {key: (list(value) if isinstance(value, list) else value) for key, value in _STATS.items()}


def _increment(name: str) -> None:
    value = _STATS[name]
    assert isinstance(value, int)
    _STATS[name] = value + 1


def _authorized(reason: str, violation: HeavyViolation) -> None:
    authorized = _STATS["authorized"]
    assert isinstance(authorized, list)
    entry = f"{AUTHORIZED_MARKER} {reason}: {violation.operation} - {violation.detail}"
    if entry not in authorized:
        authorized.append(entry)


def _record_error(message: str) -> None:
    errors = _STATS["errors"]
    assert isinstance(errors, list)
    errors.append(f"{ERROR_MARKER} {message}")


def _audit(event: str, args: tuple[object, ...]) -> None:
    active = _STATE["guard"]
    if isinstance(active, HeavyRequestGuard):
        active.check(event, args)


_PROCESS_AUDIT_EVENTS = frozenset({"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn"})
_ADDRESS_EVENT_ARGUMENTS = {
    "socket.connect": 1,
    "socket.sendto": 1,
    "socket.sendmsg": 4,
    "socket.getnameinfo": 0,
}
_HOST_EVENT_ARGUMENTS = {
    "socket.getaddrinfo": 0,
    "socket.gethostbyname": 0,
    "socket.gethostbyname_ex": 0,
    "socket.gethostbyaddr": 0,
    "http.client.connect": 1,
}


def classify_audit_event(event: str, args: tuple[object, ...]) -> HeavyViolation | None:
    """Return the heavy-request reason for one audit event, if any."""
    if event in _PROCESS_AUDIT_EVENTS:
        return _classify_process_event(event, args)
    if event in _ADDRESS_EVENT_ARGUMENTS or event in _HOST_EVENT_ARGUMENTS:
        return _classify_network_event(event, args)
    if event == "urllib.Request":
        return classify_host(urlsplit(str(args[0] if args else "")).hostname)
    return None


def _classify_process_event(event: str, args: tuple[object, ...]) -> HeavyViolation | None:
    if not args:
        return None
    if event == "subprocess.Popen":
        executable = args[0]
        argv = args[1] if len(args) > 1 else executable
        return _classify_path_and_argv(executable, argv)
    if event == "os.system":
        return classify_command(args[0])
    if event == "os.spawn":
        return classify_command(args[1] if len(args) > 1 else None)
    argv = args[1] if len(args) > 1 and isinstance(args[1], (list, tuple)) else None
    return _classify_path_and_argv(args[0], argv)


def _classify_path_and_argv(path: object, argv: object) -> HeavyViolation | None:
    """Classify argv and detect an ``executable=`` override that differs from it.

    ``subprocess.Popen(args=["safe"], executable="docker")`` emits an audit
    event whose ``argv`` does not name the real executable. Classifying argv
    alone would miss it, while classifying the executable alone would misread a
    legitimate ``docker compose version`` probe as unauthorized.
    """
    violation = classify_command(argv)
    if violation is not None:
        return violation
    if not isinstance(path, str) or not path:
        return None
    argv_parts = _command_parts(argv)
    if argv_parts and Path(argv_parts[0]).name == Path(path).name:
        return None
    remaining = argv_parts[1:] if argv_parts else ()
    return classify_command((path, *remaining))


def _classify_network_event(event: str, args: tuple[object, ...]) -> HeavyViolation | None:
    if not args:
        return None
    if event in _ADDRESS_EVENT_ARGUMENTS:
        index = _ADDRESS_EVENT_ARGUMENTS[event]
        return classify_address(args[index] if len(args) > index else None)
    index = _HOST_EVENT_ARGUMENTS[event]
    return classify_host(args[index] if len(args) > index else None)


def classify_command(command: object) -> HeavyViolation | None:
    """Classify one executable plus arguments as heavy or safe."""
    parts = _command_parts(command)
    if not parts or _is_version_probe(parts):
        return None
    unwrapped = _strip_wrapper(parts)
    if unwrapped and unwrapped != parts:
        return classify_command(unwrapped)
    return _classify_executable(parts)


def _classify_executable(parts: Sequence[str]) -> HeavyViolation | None:
    executable = Path(parts[0]).name.lower()
    if executable in HEAVY_EXECUTABLES:
        return HeavyViolation(operation="command", detail=f"heavy executable: {executable}")
    if executable in _SHELL_EXECUTABLES:
        return _classify_shell(parts)
    if executable.startswith("git"):
        return _classify_git(parts)
    if executable.startswith("python"):
        return _classify_python(parts)
    return None


def _classify_shell(parts: Sequence[str]) -> HeavyViolation | None:
    if len(parts) <= 2 or parts[1] not in {"-c", "-lc"}:
        return None
    try:
        nested = shlex.split(parts[2])
    except ValueError:
        nested = parts[2].split()
    return classify_command(nested)


def _classify_git(parts: Sequence[str]) -> HeavyViolation | None:
    subcommands = {part for part in parts[1:] if not part.startswith("-")}
    heavy = subcommands & HEAVY_GIT_SUBCOMMANDS
    if heavy:
        return HeavyViolation(operation="git", detail=f"remote Git operation: {sorted(heavy)[0]}")
    if "remote" in subcommands and (subcommands & HEAVY_GIT_REMOTE_ACTIONS):
        return HeavyViolation(operation="git", detail="remote Git operation: remote")
    return None


def _strip_wrapper(parts: Sequence[str]) -> tuple[str, ...]:
    """Remove transparent ``env``/``timeout``/``sudo`` style prefixes."""
    index = 0
    while index < len(parts) and Path(parts[index]).name.lower() in _WRAPPER_EXECUTABLES:
        wrapper = Path(parts[index]).name.lower()
        index += 1
        while index < len(parts):
            argument = parts[index]
            if wrapper == "env":
                split = _env_split_string(argument, parts, index)
                if split is not None:
                    return split
            if argument == "--":
                index += 1
                break
            if argument.startswith("-") and argument != "-":
                index += 1
                if argument in _WRAPPER_VALUE_OPTIONS and index < len(parts):
                    index += 1
                continue
            if wrapper == "env" and "=" in argument and not argument.startswith("="):
                index += 1
                continue
            break
        if wrapper == "timeout" and index < len(parts):
            index += 1
    return tuple(parts[index:])


def _env_split_string(argument: str, parts: Sequence[str], index: int) -> tuple[str, ...] | None:
    """Expand GNU ``env -S/--split-string`` so its command is still classified."""
    if argument not in {"-S", "--split-string"} and not argument.startswith("--split-string="):
        return None
    inline = argument.partition("=")[2]
    index += 1
    raw = inline or (parts[index] if index < len(parts) else "")
    if not inline:
        index += 1
    try:
        split = shlex.split(raw)
    except ValueError:
        split = raw.split()
    return (*split, *parts[index:])


def _classify_python(parts: Sequence[str]) -> HeavyViolation | None:
    for index, part in enumerate(parts[:-1]):
        if part == "-m" and parts[index + 1] in HEAVY_PYTHON_MODULES:
            return HeavyViolation(operation="python-module", detail=f"heavy module: {parts[index + 1]}")
    return None


def _is_version_probe(parts: Sequence[str]) -> bool:
    """Version probes are read-only and do not consume tokens or mutate state."""
    arguments = tuple(part.lower() for part in parts[1:])
    if any(argument in {"--version", "-version"} for argument in arguments):
        return True
    return arguments in {("version",), ("compose", "version")}


def _command_parts(command: object) -> tuple[str, ...]:
    if isinstance(command, str):
        try:
            return tuple(shlex.split(command))
        except ValueError:
            return (command,)
    if isinstance(command, (list, tuple)) and command:
        return tuple(str(part) for part in command)
    return ()


def classify_host(host: object) -> HeavyViolation | None:
    """Reject non-loopback DNS/HTTP targets."""
    if host is None:
        return None
    text = str(host).strip().lower().rstrip(".")
    if not text or text in LOOPBACK_HOSTS:
        return None
    if text.startswith("127.") or text.endswith(".localhost"):
        return None
    return HeavyViolation(operation="network", detail=f"non-loopback host: {text}")


def classify_address(address: object) -> HeavyViolation | None:
    """Reject non-loopback socket addresses while allowing local servers."""
    if not isinstance(address, tuple) or not address:
        return None
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    return classify_host(host)
