Feature: Final result and autonomy
  The final result must preserve the chain from requirement to plan, execution
  and validation. Routine operations must be deterministic and must not consume
  an agent or network call.

  Scenario: The final report exposes the complete traceability chain
    Given a target Django repository with a fake validation environment
    When I run AgentLoop for the requirement "Add an add function"
    Then the exit code is 0
    And the report exposes the complete traceability chain

  Scenario: A routine dry run is deterministic and consumes no agent
    Given a target Django repository with a fake validation environment
    When I run the same dry run twice
    Then the exit code is 0
    And both prompts are identical
    And no agent was invoked

  Scenario: A missing provider command is unavailable, never a false success
    Given a target Django repository with a fake validation environment
    When I run AgentLoop with a missing agent command
    Then the exit code is 3
    And the JSON status is "unavailable"
    And no agent run is reported as successful
