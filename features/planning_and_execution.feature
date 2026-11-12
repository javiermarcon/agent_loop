Feature: Planning and execution
  A failing validation is retried only when the failure is an implementation
  defect. Agent failures and non-retryable validation errors must never be
  reported as completed work.

  Scenario: A recoverable failure is corrected on the next iteration
    Given a target Django repository with a fake validation environment
    And the target validation fails once before passing
    When I run AgentLoop for the requirement "Add an add function"
    Then the exit code is 0
    And the JSON status is "pass"
    And the agent run count is 2
    And the validation report count is 2
    And the final message contains "validation passed in 2 iteration(s)"

  Scenario: A non-retryable validation error stops immediately
    Given a target Django repository with a fake validation environment
    And the target validation exits with code 4
    When I run AgentLoop for the requirement "Add an add function"
    Then the exit code is 4
    And the JSON status is "fail"
    And the agent run count is 1
    And the final message contains "stop_non_retryable"

  Scenario: An agent failure never produces a completed validation
    Given a target Django repository with a fake validation environment
    And the target agent fails
    When I run AgentLoop for the requirement "Add an add function"
    Then the exit code is 4
    And the JSON status is "fail"
    And the agent run count is 1
    And the validation report count is 0
    And the final message contains "agent invocation failed"
