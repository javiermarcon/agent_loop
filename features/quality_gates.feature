Feature: Quality gates
  Validation results must distinguish success, failure, skips and unavailable
  tools. A skipped or unavailable mandatory control is never reported as a
  successful validation.

  Scenario: A passing validation gate is reported as evidence
    Given a target Django repository with a fake validation environment
    When I run validate-only for tool "pytest"
    Then the exit code is 0
    And the JSON status is "pass"
    And the validation status is "pass"
    And the validation report count is 1

  Scenario: A failing validation gate is not hidden
    Given a target Django repository with a fake validation environment
    And the target validation exits with code 1
    When I run validate-only for tool "pytest"
    Then the exit code is 4
    And the JSON status is "fail"
    And the validation status is "fail"

  Scenario: A tool that is not configured is skipped, not validated
    Given a target Django repository with a fake validation environment
    When I run validate-only for tool "bandit"
    Then the exit code is 3
    And the JSON status is "skipped"
    And the validation status is "skipped"
