"""Stable exceptions raised by agent-loop domain policies."""


class MultiRequirementError(RuntimeError):
    """Report an invalid composite plan, state, or unit selection."""


AgentLoopError = MultiRequirementError


class ResumeArgumentError(MultiRequirementError):
    """Report contradictory CLI controls before any resume side effect."""


class UnitRollbackConflictError(RuntimeError):
    """Report that rollback would overwrite a post-agent human edit."""
