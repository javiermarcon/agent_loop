"""Requirement provenance for autonomous invocations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agent_loop.autonomy.errors import ConfigurationError


class RequirementOrigin(StrEnum):
    """Where the requirement text came from."""

    INLINE = "inline"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class RequirementSource:
    """Requirement text together with its traceable origin."""

    origin: RequirementOrigin
    text: str
    path: str | None = None
    absolute_path: str | None = None

    @classmethod
    def inline(cls, text: str) -> RequirementSource:
        """Build an inline requirement source."""
        return cls(origin=RequirementOrigin.INLINE, text=text)

    @classmethod
    def from_file(cls, path: str | Path) -> RequirementSource:
        """Read a UTF-8 requirement file and preserve its origin path."""
        candidate = Path(path).expanduser()
        try:
            text = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            message = f"requirement file is not valid UTF-8: {candidate}"
            raise ConfigurationError(message) from exc
        except OSError as exc:
            message = f"cannot read requirement file: {candidate}"
            raise ConfigurationError(message) from exc
        if not text.strip():
            message = f"requirement file is empty: {candidate}"
            raise ConfigurationError(message)
        return cls(
            origin=RequirementOrigin.FILE,
            text=text,
            path=str(candidate),
            absolute_path=str(candidate.resolve(strict=False)),
        )

    @property
    def label(self) -> str:
        """Return a short human-readable origin label."""
        if self.origin is RequirementOrigin.FILE:
            return f"file: {self.path}"
        return "inline --requirement"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return {
            "origin": self.origin.value,
            "path": self.path,
            "absolute_path": self.absolute_path,
            "label": self.label,
        }
