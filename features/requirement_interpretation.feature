Feature: Requirement interpretation
  AgentLoop must turn a human requirement into a structured, traceable object
  before any agent or validation runs.

  Scenario: Inline requirement produces a structured discovery report
    Given a target Django repository with a fake validation environment
    When I run discovery only for the requirement "Add a health endpoint"
    Then the exit code is 0
    And the JSON status is "pass"
    And the requirement source origin is "inline"
    And no agent was invoked

  Scenario: Requirement file preserves its origin and path
    Given a target Django repository with a fake validation environment
    And a requirement file containing "Document the new endpoint"
    When I run discovery only from the requirement file
    Then the exit code is 0
    And the requirement source origin is "file"
    And the requirement source path points to the requirement file
    And no agent was invoked

  Scenario: An empty inline requirement is rejected before execution
    Given a target Django repository with a fake validation environment
    When I run discovery only for the requirement "   "
    Then the exit code is 2
    And the error output contains "requirement must be non-empty"
    And no agent was invoked
