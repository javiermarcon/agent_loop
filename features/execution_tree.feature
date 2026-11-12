Feature: Execution tree construction
  The WorkItem DAG is the structured representation of a requirement. It must
  represent dependencies and ordering without inventing or omitting tasks.

  Scenario: Dependencies determine readiness and blocking
    Given a standard work item DAG
    When I plan the work items
    Then the ready work items are "design"
    And the blocked work items are "docs, implement"
    And the done work items are "audit"
    And the plan reports a pending dependency "design" for "implement"

  Scenario: Completing a dependency unlocks its dependent task
    Given a standard work item DAG
    When I complete the work item design
    Then the ready work items are "implement"
    And the blocked work items are "docs"

  Scenario: Only ready work items become execution requests
    Given a standard work item DAG
    When I build execution requests for the ready work items
    Then the execution requests contain 1 task
    And the execution request task is "design the module"
    And the execution request working directory is "/tmp/target"

  Scenario: Cycles are rejected instead of invented
    Given a cyclic work item DAG
    When I plan the work items
    Then planning fails with "cycle detected"

  Scenario: Missing dependencies are rejected instead of omitted
    Given a work item DAG with a missing dependency "missing"
    When I plan the work items
    Then planning fails with "missing dependency"
