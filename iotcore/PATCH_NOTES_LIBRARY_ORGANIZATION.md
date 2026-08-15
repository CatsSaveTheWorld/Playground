# Sequence / Automation library organization

## Added
- Separate `SequenceGroup` and `AutomationGroup` models.
- Nullable group relation and `is_favorite` flag on `Sequence` and `Automation`.
- Group management pages; deleting a group moves its members to `미분류` via `SET_NULL`.
- Favorite toggle buttons on list cards.
- Sequence search across name, description, group, device, location, action code and action display name.
- Automation search across name, group, active state, trigger summary, condition summary and action summary.
- Automation filters for active state, trigger type and action type.
- Sort controls and group/favorite scope tabs.
- Responsive list toolbar and group management UI.

## Migration
Apply `0016_sequence_automation_groups_favorites`:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

The migration does not move or delete existing Sequence/Automation rows. Existing rows start as `미분류` and not favorited.
