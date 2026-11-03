"""Minimal Behave steps for the project B fixture."""

from behave import given


@given("a running project")
def step_running_project(context: object) -> None:
    """Accept the fixture scenario without external side effects."""
    del context
