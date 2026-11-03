"""Discover and justify the target repository's Docker Compose configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_loop.autonomy.security import TargetWorkspace

_STANDARD_BASES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
_STANDARD_OVERRIDES: dict[str, tuple[str, ...]] = {
    "compose.yaml": ("compose.override.yaml", "compose.override.yml"),
    "compose.yml": ("compose.override.yml", "compose.override.yaml"),
    "docker-compose.yaml": ("docker-compose.override.yaml", "docker-compose.override.yml"),
    "docker-compose.yml": ("docker-compose.override.yml", "docker-compose.override.yaml"),
}
_VARIANT_RE = re.compile(r"^(?:docker-compose|compose)\.([a-zA-Z0-9][a-zA-Z0-9._-]*)\.(?:ya?ml)$")
_EXPLICIT_RE = re.compile(
    r"(?m)^\s*(?:export\s+)?(?:COMPOSE_FILE|DOCKER_COMPOSE_FILE|CI_COMPOSE_FILE)\s*:?=\s*[\"']?([^\"'\s#]+)",
)
_EXEC_RE = re.compile(r"\b(?:exec|run)(?:\s+--?[a-zA-Z0-9-]+(?:=[^\s]+)?)*\s+(?:-T\s+)?([a-zA-Z0-9_.-]+)")
_DOC_FILE_LIMIT = 40
_DOC_BYTES_LIMIT = 200_000
_STRONG_WEIGHT = 10
_REFERENCE_WEIGHT = 1
_PRODUCTION_NAME = "production"
_DJANGO_HINTS = ("manage.py", "asgi", "wsgi", "gunicorn", "daphne", "uvicorn")
_SERVICE_NAME_HINTS = ("django", "web", "app")
_CONTAINER_SERVICE_PATTERN = re.compile(r"^([a-zA-Z0-9_.-]+):\s*$")
_DOCKERFILE_ARG = re.compile(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)=([^\s#]+)")
_DOCKERFILE_WORKDIR = re.compile(r"^\s*WORKDIR\s+([^\s#]+)")
_DOCKER_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True, slots=True)
class ComposeDiscovery:
    """Compose files, evidence-backed selection and container runtime data."""

    files: tuple[str, ...] = ()
    project_files: tuple[str, ...] = ()
    error: str | None = None
    reason: str | None = None
    evidence: tuple[str, ...] = ()
    service: str | None = None
    service_reason: str | None = None
    runtime_cwd: str | None = None


def discover_compose(workspace: TargetWorkspace) -> ComposeDiscovery:
    """Select a Compose configuration without executing the project."""
    candidates = _compose_candidates(workspace)
    if not candidates:
        return ComposeDiscovery()
    selection, error, reason = _select(workspace, candidates)
    if error is not None:
        return ComposeDiscovery(files=candidates, project_files=(), error=error, reason=reason)
    service, service_reason, runtime_cwd = _container_runtime(workspace, selection)
    scores = _score(workspace, selection)
    evidence = tuple(source for name in selection for source in scores[name][1])
    return ComposeDiscovery(
        files=candidates,
        project_files=selection,
        reason=reason,
        evidence=tuple(dict.fromkeys(evidence)),
        service=service,
        service_reason=service_reason,
        runtime_cwd=runtime_cwd,
    )


def _compose_candidates(workspace: TargetWorkspace) -> tuple[str, ...]:
    found = [name for name in _STANDARD_BASES if _is_file(workspace, name)]
    found.extend(name for base in _STANDARD_BASES for name in _STANDARD_OVERRIDES[base] if _is_file(workspace, name))
    try:
        entries = sorted(workspace.root.iterdir(), key=lambda item: item.name)
    except OSError:
        entries = []
    for entry in entries:
        if not entry.is_file() or entry.name in found:
            continue
        if _VARIANT_RE.match(entry.name) and entry.name not in _STANDARD_BASES:
            found.append(entry.name)
    return tuple(dict.fromkeys(found))


def _select(
    workspace: TargetWorkspace,
    candidates: tuple[str, ...],
) -> tuple[tuple[str, ...], str | None, str | None]:
    bases = tuple(name for name in candidates if name in _STANDARD_BASES)
    overrides = tuple(name for name in candidates if name in _standard_overrides())
    variants = tuple(name for name in candidates if name not in _STANDARD_BASES and name not in _standard_overrides())
    if not bases and not variants:
        names = ", ".join(overrides)
        return (), f"only Docker Compose override file(s) found ({names}); a base compose file is required", None
    pool = (*bases, *variants)
    scored = _score(workspace, (*pool, *overrides))
    ranked = _ranked(scored, pool)
    if len(ranked) != 1:
        names = ", ".join(ranked or pool)
        return (), f"ambiguous Docker Compose configuration candidates: {names}", None
    return _finalize_selection(workspace, ranked[0], overrides, scored)


def _finalize_selection(
    workspace: TargetWorkspace,
    winner: str,
    overrides: tuple[str, ...],
    scored: dict[str, tuple[int, tuple[str, ...]]],
) -> tuple[tuple[str, ...], str | None, str | None]:
    if winner in overrides:
        return (), f"only Docker Compose override file(s) found ({winner}); a base compose file is required", None
    expected_overrides = _STANDARD_OVERRIDES.get(winner, ())
    related = tuple(name for name in overrides if name in expected_overrides)
    if len(related) > 1:
        names = ", ".join(related)
        return (), f"ambiguous Docker Compose override files for {winner}: {names}", None
    paired = _pair_overrides(workspace, winner, overrides)
    if paired is None:
        unrelated = tuple(name for name in overrides if name not in expected_overrides)
        names = ", ".join(unrelated) or ", ".join(overrides)
        return (), f"Docker Compose override file(s) do not match base {winner}: {names}", None
    return (winner, *paired), None, _selection_reason(winner, scored)


def _pair_overrides(
    workspace: TargetWorkspace,
    base: str,
    overrides: tuple[str, ...],
) -> tuple[str, ...] | None:
    expected = _STANDARD_OVERRIDES.get(base, ())
    related = tuple(name for name in overrides if name in expected)
    unrelated = tuple(name for name in overrides if name not in expected)
    if unrelated:
        return None
    if len(related) > 1:
        return None
    if related and not _is_file(workspace, related[0]):
        return None
    return related


def _standard_overrides() -> tuple[str, ...]:
    return tuple(name for base in _STANDARD_BASES for name in _STANDARD_OVERRIDES[base])


def _score(workspace: TargetWorkspace, candidates: tuple[str, ...]) -> dict[str, tuple[int, tuple[str, ...]]]:
    scores: dict[str, tuple[int, tuple[str, ...]]] = dict.fromkeys(candidates, (0, ()))
    for path in evidence_files(workspace):
        text = _read_text(path)
        if not text:
            continue
        for name in candidates:
            if name not in text:
                continue
            weight = _STRONG_WEIGHT if _explicitly_configured(text, name) else _REFERENCE_WEIGHT
            score, sources = scores[name]
            if weight > 0 and path.name not in sources:
                scores[name] = (score + weight, (*sources, path.name))
    return scores


def _explicitly_configured(text: str, name: str) -> bool:
    return any(match.group(1).strip().strip("\"'") == name for match in _EXPLICIT_RE.finditer(text))


def _ranked(scores: dict[str, tuple[int, tuple[str, ...]]], pool: tuple[str, ...]) -> tuple[str, ...]:
    if not pool:
        return ()
    best = max(scores[name][0] for name in pool)
    if best == 0:
        return pool if len(pool) == 1 else ()
    ranked = tuple(name for name in pool if scores[name][0] == best)
    if len(ranked) > 1:
        non_production = tuple(name for name in ranked if _PRODUCTION_NAME not in name)
        if len(non_production) == 1:
            return non_production
    return ranked


def _selection_reason(
    winner: str,
    scores: dict[str, tuple[int, tuple[str, ...]]],
) -> str:
    _score, sources = scores[winner]
    if sources:
        return f"selected {winner} from project documentation/configuration evidence ({', '.join(sources)})"
    return f"selected the only recognized Compose configuration ({winner})"


def evidence_files(workspace: TargetWorkspace) -> tuple[Path, ...]:
    names = ("justfile", "Justfile", "Makefile", "makefile", "README.md", "TESTING.md", "AGENTS.md")
    files = [candidate for name in names if (candidate := _workspace_file(workspace, name)) is not None]
    for directory in ("scripts", ".github/workflows", "docs"):
        base = _workspace_dir(workspace, directory)
        if base is None:
            continue
        for path in sorted(base.rglob("*")):
            if len(files) >= _DOC_FILE_LIMIT:
                return tuple(files)
            if path.is_file() and path.suffix in {".py", ".md", ".yml", ".yaml", ".sh"}:
                parts = path.relative_to(workspace.root).parts
                if not any(part.startswith(".") and part != ".github" for part in parts):
                    files.append(path)
    return tuple(files)


def _container_runtime(
    workspace: TargetWorkspace,
    selection: tuple[str, ...],
) -> tuple[str | None, str | None, str | None]:
    services = _services(workspace, selection)
    if not services:
        return None, "no Compose service could be identified", None
    documented = _documented_service(workspace, services)
    if documented is not None:
        return (
            documented,
            f"service {documented!r} is used by project documentation",
            _service_workdir(
                workspace,
                selection,
                documented,
            ),
        )
    hinted = _service_by_hint(services)
    if len(hinted) == 1:
        return (
            hinted[0],
            f"service {hinted[0]!r} is the only Django-like Compose service",
            _service_workdir(
                workspace,
                selection,
                hinted[0],
            ),
        )
    names = ", ".join(services)
    return None, f"could not determine one Compose service from: {names}", None


def _services(workspace: TargetWorkspace, selection: tuple[str, ...]) -> tuple[str, ...]:
    names: list[str] = []
    for name in selection:
        names.extend(_yaml_services(workspace, name))
        names.extend(_text_services(_read_text(_workspace_file(workspace, name))))
    return tuple(dict.fromkeys(names))


def _yaml_services(workspace: TargetWorkspace, name: str) -> tuple[str, ...]:
    data = _yaml_data(workspace, name)
    services = data.get("services")
    if not isinstance(services, dict):
        return ()
    return tuple(str(key) for key in services)


def _yaml_data(workspace: TargetWorkspace, name: str) -> dict[str, object]:
    try:
        import yaml  # noqa: PLC0415 - optional dependency, isolated to Compose introspection
    except ImportError:
        return {}
    path = _workspace_file(workspace, name)
    if path is None:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _text_services(text: str) -> tuple[str, ...]:
    names: list[str] = []
    in_services = False
    for line in text.splitlines():
        if line.strip() == "services:":
            in_services = True
            continue
        if in_services and line and not line.startswith((" ", "\t")):
            in_services = False
        if not in_services:
            continue
        match = _CONTAINER_SERVICE_PATTERN.match(line)
        if match:
            names.append(match.group(1))
    return tuple(names)


def _documented_service(workspace: TargetWorkspace, services: tuple[str, ...]) -> str | None:
    found: list[str] = []
    for path in evidence_files(workspace):
        text = join_continuations(_read_text(path))
        for match in _EXEC_RE.finditer(text):
            name = match.group(1)
            if name in services and name not in found:
                found.append(name)
    return found[0] if len(found) == 1 else None


def _service_by_hint(services: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in services if any(hint in name.lower() for hint in _SERVICE_NAME_HINTS))


def _service_workdir(workspace: TargetWorkspace, selection: tuple[str, ...], service: str) -> str | None:
    config = _service_config(workspace, selection, service)
    workdir = config.get("working_dir")
    if isinstance(workdir, str) and workdir:
        return _resolve_docker_vars(workdir, _dockerfile_arguments(workspace, selection, service))
    dockerfile = _compose_service_dockerfile(workspace, selection, config)
    if dockerfile is not None:
        return _dockerfile_workdir(workspace, dockerfile)
    return None


def _service_config(workspace: TargetWorkspace, selection: tuple[str, ...], service: str) -> dict[str, object]:
    config: dict[str, object] = {}
    for name in selection:
        data = _yaml_data(workspace, name)
        services = data.get("services")
        if not isinstance(services, dict):
            continue
        entry = services.get(service)
        if isinstance(entry, dict):
            config.update(entry)
    return config


def _compose_service_dockerfile(
    workspace: TargetWorkspace,
    selection: tuple[str, ...],
    config: dict[str, object],
) -> str | None:
    build = config.get("build")
    if not isinstance(build, dict):
        return None
    context = build.get("context", ".")
    dockerfile = build.get("dockerfile", "Dockerfile")
    if not isinstance(context, str) or not isinstance(dockerfile, str):
        return None
    relative = (Path(context) / dockerfile).as_posix().removeprefix("./")
    del selection  # the build context is relative to the project root
    return relative if _workspace_file(workspace, relative) is not None else None


def _dockerfile_arguments(
    workspace: TargetWorkspace,
    selection: tuple[str, ...],
    service: str,
) -> dict[str, str]:
    config = _service_config(workspace, selection, service)
    relative = _compose_service_dockerfile(workspace, selection, config)
    if relative is None:
        return {}
    return _dockerfile_args(workspace, relative)


def _dockerfile_workdir(workspace: TargetWorkspace, relative: str) -> str | None:
    path = _workspace_file(workspace, relative)
    if path is None:
        return None
    workdir: str | None = None
    for line in _read_text(path).splitlines():
        directory = _DOCKERFILE_WORKDIR.match(line)
        if directory:
            workdir = directory.group(1)
    if workdir is None:
        return None
    return _resolve_docker_vars(workdir, _dockerfile_args(workspace, relative))


def _dockerfile_args(workspace: TargetWorkspace, relative: str) -> dict[str, str]:
    path = _workspace_file(workspace, relative)
    if path is None:
        return {}
    arguments: dict[str, str] = {}
    for line in _read_text(path).splitlines():
        arg = _DOCKERFILE_ARG.match(line)
        if arg:
            arguments[arg.group(1)] = arg.group(2)
    return arguments


def _resolve_docker_vars(value: str, arguments: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(3)
        default = match.group(2) or ""
        return arguments.get(name, default)

    return _DOCKER_VAR.sub(_replace, value)


def join_continuations(text: str) -> str:
    return re.sub(r"\\\s*\n\s*", " ", text)


def _is_file(workspace: TargetWorkspace, name: str) -> bool:
    return _workspace_file(workspace, name) is not None


def _workspace_file(workspace: TargetWorkspace, name: str) -> Path | None:
    from agent_loop.autonomy.errors import SecurityError  # noqa: PLC0415 - narrow import keeps module cohesive

    try:
        candidate = workspace.resolve(name)
    except SecurityError:
        return None
    return candidate if candidate.is_file() else None


def _workspace_dir(workspace: TargetWorkspace, name: str) -> Path | None:
    from agent_loop.autonomy.errors import SecurityError  # noqa: PLC0415 - narrow import keeps module cohesive

    try:
        candidate = workspace.resolve(name)
    except SecurityError:
        return None
    return candidate if candidate.is_dir() else None


def _read_text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        if path.stat().st_size > _DOC_BYTES_LIMIT:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
