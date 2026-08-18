# Automation Trigger Set v5 — multiple actions per set

## Final editor structure

Each automation contains one or more independent TriggerSets.

- TriggerSet
  - condition operator: AND / OR
  - conditions: 1..N
  - actions: 1..N

When the whole condition expression changes from FALSE to TRUE, every action
owned by that TriggerSet is queued and executed in the configured order.
Actions in another TriggerSet are not executed.

## Why this change

v4 restricted each TriggerSet to exactly one action. That forced users to
copy identical conditions into multiple sets when the same condition should
perform multiple device/sequence operations. v5 keeps the condition set once
and allows multiple actions beneath it.

## Data/model changes

Migration: `0021_trigger_set_multiple_actions.py`

- `AutomationAction.trigger`: OneToOneField -> ForeignKey
- reverse relation: `trigger.actions`
- action order is now unique within each TriggerSet instead of globally within
  the Automation

Existing v4 data is preserved: each old TriggerSet simply starts with its one
existing action.

## Runtime behavior

`AutomationService._enqueue_set_locked()` places all action IDs belonging to
the matched TriggerSet into `matched_action_ids`. `AutomationExecutor` already
supports an ordered list of matched action IDs, so the actions execute
sequentially. Existing failure semantics are unchanged: if an action fails,
the run stops at that failed action.

## UI behavior

A TriggerSet now shows:

1. AND/OR selector
2. one or more condition cards (`+ 실행 조건 추가`)
3. one or more action cards (`+ 실행 동작 추가`)

A newly-added TriggerSet starts with one condition card and one action card.
