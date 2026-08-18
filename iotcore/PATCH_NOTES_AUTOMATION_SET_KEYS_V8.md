# Automation trigger-set ownership v8

## Problem
The editor previously connected conditions/actions to a TriggerSet with the Django formset index (`trigger_index`). Deleting or renumbering trigger-set cards could leave child forms carrying a stale index, which produced the validation error that a child action had invalid trigger-set connection information.

## Change
- Added a UI-only stable `set_key` to each TriggerSet form.
- Added a UI-only `trigger_key` to condition/action forms.
- Existing saved TriggerSets receive a deterministic key (`trigger-<pk>`).
- New TriggerSets receive a browser-generated unique key.
- Child conditions/actions inherit the stable key of their parent set.
- Server-side validation, grouping, and save logic now prefer stable keys.
- Legacy `trigger_index` / `action_index` support remains as a compatibility fallback for old POST payloads and tests.
- A submit-time ownership sync is retained as an extra guard, but renumbering form indexes no longer changes ownership.

## Database
No model/database migration is required. These keys exist only in the editor forms and POST payload.

## Deployment
Restart Apache/mod_wsgi after applying the Python/template changes. No `migrate` is required for v8.
