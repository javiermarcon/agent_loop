"""Logical model profiles and runtime-declared associations.

These contracts describe what a control plane may *declare* about models.
They do not select models, route tasks, probe availability, authenticate,
authorize, or infer capabilities from names or provider metadata.
"""

from __future__ import annotations

from dataclasses import dataclass


def _require_nonempty_unpadded(field_name: str, value: str) -> None:
    if not value or not value.strip():
        message = f"{field_name} must be a non-empty string"
        raise ValueError(message)
    if value != value.strip():
        message = f"{field_name} must not include leading or trailing whitespace"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Logical model profile independent of any runtime that exposes it.

    ``profile_id`` is the stable logical identity for catalogs and routing
    slices that consume this contract later. ``provider_id`` and ``model_id``
    are opaque declared identifiers; S12 does not case-fold or otherwise
    normalize them.

    This type intentionally omits tool/runtime ``CapabilityId`` declarations.
    Those ids name effective harness/tool axes (filesystem, work items, …)
    owned by ``RuntimeInventory`` / ``CapabilitySnapshot``. Model-specific
    capability vocabulary, if needed for selection, is a later contract.
    """

    profile_id: str
    provider_id: str
    model_id: str

    def __post_init__(self) -> None:
        _require_nonempty_unpadded("ModelProfile.profile_id", self.profile_id)
        _require_nonempty_unpadded("ModelProfile.provider_id", self.provider_id)
        _require_nonempty_unpadded("ModelProfile.model_id", self.model_id)


@dataclass(frozen=True, slots=True)
class RuntimeModelProfile:
    """Association between one AgentRuntime id and a logical ``ModelProfile``.

    ``runtime_id`` shares the string space of ``RuntimeInventory.runtime_identity``
    and ``RuntimeIdentity.runtime_id``. ``native_model_ref`` is an optional
    harness-native identifier as declared (for example an inventory model id);
    its presence does not mean the model is available, healthy, authenticated,
    or authorized.
    """

    runtime_id: str
    profile: ModelProfile
    native_model_ref: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_unpadded("RuntimeModelProfile.runtime_id", self.runtime_id)
        if self.native_model_ref is not None:
            _require_nonempty_unpadded(
                "RuntimeModelProfile.native_model_ref",
                self.native_model_ref,
            )

    def identity_key(self) -> tuple[str, str]:
        """Return the stable association key ``(runtime_id, profile_id)``."""
        return (self.runtime_id, self.profile.profile_id)
