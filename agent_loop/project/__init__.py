"""Project-boundary package: host/tooling adapters and declarative Profiles/Skills.

Historically this package owns project-boundary adapters for host processes and
repository tooling. S20 also owns declarative ``ProjectProfile`` / ``Skill``
catalogs and inventory compatibility assessment (ADR-052).

Does not own harness adapters under ``agent_loop.adapters``, MultiRunner, or
``AgentRuntime.execute``. Domain capabilities remain the requirement vocabulary.
"""
