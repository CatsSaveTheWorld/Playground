import copy

import django.db.models.deletion
from django.db import migrations, models


def attach_existing_conditions_to_actions(apps, schema_editor):
    Automation = apps.get_model("iotcore", "Automation")
    AutomationCondition = apps.get_model("iotcore", "AutomationCondition")

    for automation in Automation.objects.all().iterator():
        actions = list(automation.actions.order_by("order", "id"))
        if not actions:
            # Keep malformed/legacy rows usable.  Runtime compatibility treats
            # action=NULL as an old automation-wide condition.
            continue

        conditions = list(automation.conditions.order_by("order", "id"))
        if not conditions:
            continue

        first_action = actions[0]
        for condition in conditions:
            condition.action_id = first_action.pk
            condition.save(update_fields=["action"])

            # Old semantics were: every condition had to pass before every
            # action ran.  Copying the same condition set to every action keeps
            # behavior identical immediately after migration.
            for action in actions[1:]:
                AutomationCondition.objects.create(
                    automation_id=automation.pk,
                    action_id=action.pk,
                    condition_type=condition.condition_type,
                    config=copy.deepcopy(condition.config or {}),
                    order=condition.order,
                )


def collapse_action_conditions_for_reverse(apps, schema_editor):
    Automation = apps.get_model("iotcore", "Automation")

    for automation in Automation.objects.all().iterator():
        actions = list(automation.actions.order_by("order", "id"))
        if not actions:
            continue

        first_action_id = actions[0].pk
        # Reverting to the old schema cannot represent independent per-action
        # conditions.  Keep the first action's condition set as the closest
        # safe legacy representation and remove the others before restoring
        # the old automation/order unique constraint.
        automation.conditions.exclude(action_id=first_action_id).delete()
        automation.conditions.filter(action_id=first_action_id).update(action=None)


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0017_door_sensor_entry_history"),
    ]

    operations = [
        migrations.AddField(
            model_name="automationcondition",
            name="action",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="conditions",
                to="iotcore.automationaction",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="automationcondition",
            name="unique_automation_condition_order",
        ),
        migrations.RunPython(
            attach_existing_conditions_to_actions,
            collapse_action_conditions_for_reverse,
        ),
        migrations.AddConstraint(
            model_name="automationcondition",
            constraint=models.UniqueConstraint(
                fields=("action", "order"),
                name="unique_automation_action_condition_order",
            ),
        ),
    ]
