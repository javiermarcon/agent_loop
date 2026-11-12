Feature: Requirement bundle planning and execution
  A realistic requirement arrives as several related instructions. AgentLoop
  must preserve the requirement, build and validate a dependency plan, execute
  the ready frontier through the control plane and validate the result.

  The deterministic planning provider is a test double for the AI
  interpretation boundary. It receives the requirement through the real
  AgentRuntime port and returns a structured plan; the domain planner and the
  execution orchestrator are the real implementations.

  Scenario: A multi-instruction requirement is planned, executed and validated
    Given a target Django repository with a real pytest environment
    And a requirement bundle with related instructions
    When AgentLoop interprets the requirement bundle with a deterministic provider
    Then the generated DAG has tasks "price-entity, discount-operation, negative-validation, tests, quality-gate"
    And the DAG dependency edges are "price-entity->discount-operation, discount-operation->negative-validation, discount-operation->tests, negative-validation->tests, tests->quality-gate"
    And the first ready frontier is "price-entity"
    And the blocked frontier is "discount-operation, negative-validation, quality-gate, tests"
    When AgentLoop executes the planned tasks in dependency order
    Then the final execution status is "succeeded"
    And the repository contains the generated implementation
    And the target pytest quality gate status is "pass"
    And the final plan marks every task done
    And the final trace contains the requirement, plan, requests, outcomes and validation reports

  Scenario: Independent tasks share the same ready frontier
    Given a target Django repository with a real pytest environment
    And a requirement bundle with two independent tasks
    When AgentLoop interprets the requirement bundle with a deterministic provider
    Then the generated DAG has tasks "read-model, write-model"
    And the first ready frontier is "read-model, write-model"
    When AgentLoop executes the planned tasks in dependency order
    Then the final execution status is "succeeded"
    And the final plan marks every task done

  Scenario: A failed task keeps its dependents blocked
    Given a target Django repository with a real pytest environment
    And a requirement bundle whose base task fails
    When AgentLoop interprets the requirement bundle with a deterministic provider
    Then the generated DAG has tasks "base, dependent, independent"
    And the first ready frontier is "base, independent"
    When AgentLoop executes the ready frontier once
    Then the base task execution status is "human_intervention"
    And the dependent task remains blocked after replanning
    And the dependent task was never requested

  Scenario: An incomplete instruction is rejected before execution
    Given a target Django repository with a real pytest environment
    And a requirement bundle with an incomplete instruction
    When AgentLoop interprets the requirement bundle with a deterministic provider
    Then requirement planning is rejected with "must be a non-empty string"
    And no task was executed
