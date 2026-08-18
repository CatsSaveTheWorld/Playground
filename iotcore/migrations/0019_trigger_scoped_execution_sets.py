import copy

import django.db.models.deletion
from django.db import migrations, models


def split_legacy_trigger_action_matrix(apps, schema_editor):
    """Convert the v2 global-trigger/action matrix into independent sets.

    Before this migration every trigger of an automation evaluated every
    action rule.  The new model is one trigger -> one action, with the
    action's conditions scoped to that trigger/action set.  To preserve the
    old runtime meaning, existing rows are expanded to the trigger x action
    cross product.
    """
    Automation = apps.get_model("iotcore", "Automation")
    AutomationTrigger = apps.get_model("iotcore", "AutomationTrigger")
    AutomationAction = apps.get_model("iotcore", "AutomationAction")
    AutomationCondition = apps.get_model("iotcore", "AutomationCondition")
    AutomationRun = apps.get_model("iotcore", "AutomationRun")

    def clone_trigger(source):
        return AutomationTrigger.objects.create(
            automation_id=source.automation_id,
            trigger_type=source.trigger_type,
            config=copy.deepcopy(source.config or {}),
            enabled=source.enabled,
            next_run_at=source.next_run_at,
            last_triggered_at=source.last_triggered_at,
        )

    def clone_action(source, trigger_id, order):
        return AutomationAction.objects.create(
            automation_id=source.automation_id,
            trigger_id=trigger_id,
            order=order,
            action_type=source.action_type,
            device_id=source.device_id,
            function=source.function,
            parameter=copy.deepcopy(source.parameter),
            sequence_id=source.sequence_id,
            delay=source.delay,
        )

    def copy_conditions(source_action_id, target_action, global_conditions):
        source_conditions = list(
            AutomationCondition.objects.filter(action_id=source_action_id)
            .order_by("order", "id")
        )
        order = 0
        for condition in source_conditions:
            order += 1
            AutomationCondition.objects.create(
                automation_id=target_action.automation_id,
                action_id=target_action.pk,
                condition_type=condition.condition_type,
                config=copy.deepcopy(condition.config or {}),
                order=order,
            )
        for condition in global_conditions:
            order += 1
            AutomationCondition.objects.create(
                automation_id=target_action.automation_id,
                action_id=target_action.pk,
                condition_type=condition.condition_type,
                config=copy.deepcopy(condition.config or {}),
                order=order,
            )

    for automation in Automation.objects.all().iterator():
        triggers = list(
            AutomationTrigger.objects.filter(automation_id=automation.pk)
            .order_by("id")
        )
        actions = list(
            AutomationAction.objects.filter(automation_id=automation.pk)
            .order_by("order", "id")
        )
        if not triggers or not actions:
            continue

        global_conditions = list(
            AutomationCondition.objects.filter(
                automation_id=automation.pk,
                action_id__isnull=True,
            ).order_by("order", "id")
        )
        max_order = max(action.order for action in actions)
        next_order = max_order + 1

        # Snapshot condition ids before any clones are created so cloned
        # actions never recursively copy conditions from earlier clones.
        original_condition_ids = {
            action.pk: list(
                AutomationCondition.objects.filter(action_id=action.pk)
                .order_by("order", "id")
                .values_list("id", flat=True)
            )
            for action in actions
        }

        def copy_original_conditions(source_action_id, target_action):
            order = 0
            for condition in AutomationCondition.objects.filter(
                id__in=original_condition_ids[source_action_id]
            ).order_by("order", "id"):
                order += 1
                AutomationCondition.objects.create(
                    automation_id=target_action.automation_id,
                    action_id=target_action.pk,
                    condition_type=condition.condition_type,
                    config=copy.deepcopy(condition.config or {}),
                    order=order,
                )
            for condition in global_conditions:
                order += 1
                AutomationCondition.objects.create(
                    automation_id=target_action.automation_id,
                    action_id=target_action.pk,
                    condition_type=condition.condition_type,
                    config=copy.deepcopy(condition.config or {}),
                    order=order,
                )

        # Preserve every original trigger and every original action at least
        # once, then clone only the additional cross-product cells.
        for trigger_index, source_trigger in enumerate(triggers):
            for action_index, source_action in enumerate(actions):
                if trigger_index == 0 and action_index == 0:
                    target_trigger = source_trigger
                    target_action = source_action
                    target_action.trigger_id = target_trigger.pk
                    target_action.save(update_fields=["trigger"])
                elif trigger_index == 0:
                    target_trigger = clone_trigger(source_trigger)
                    target_action = source_action
                    target_action.trigger_id = target_trigger.pk
                    target_action.save(update_fields=["trigger"])
                elif action_index == 0:
                    target_trigger = source_trigger
                    target_action = clone_action(
                        source_action,
                        target_trigger.pk,
                        next_order,
                    )
                    next_order += 1
                    copy_original_conditions(source_action.pk, target_action)
                else:
                    target_trigger = clone_trigger(source_trigger)
                    target_action = clone_action(
                        source_action,
                        target_trigger.pk,
                        next_order,
                    )
                    next_order += 1
                    copy_original_conditions(source_action.pk, target_action)

        # Legacy action=NULL rows are now represented inside every set.
        if global_conditions:
            # Original actions kept their pre-existing action-scoped
            # conditions. Append the legacy globals once to each original.
            for action in actions:
                current_max = (
                    AutomationCondition.objects.filter(action_id=action.pk)
                    .order_by("-order")
                    .values_list("order", flat=True)
                    .first()
                    or 0
                )
                for condition in global_conditions:
                    current_max += 1
                    AutomationCondition.objects.create(
                        automation_id=automation.pk,
                        action_id=action.pk,
                        condition_type=condition.condition_type,
                        config=copy.deepcopy(condition.config or {}),
                        order=current_max,
                    )
            AutomationCondition.objects.filter(
                automation_id=automation.pk,
                action_id__isnull=True,
            ).delete()

    # Existing queued/history rows usually carry trigger_id in their payload.
    # Preserve it when possible so deduplication/history becomes set-scoped.
    valid_trigger_ids = set(
        AutomationTrigger.objects.values_list("id", flat=True)
    )
    for run in AutomationRun.objects.filter(trigger_id__isnull=True).iterator():
        payload = run.trigger_payload or {}
        trigger_id = payload.get("trigger_id")
        try:
            trigger_id = int(trigger_id)
        except (TypeError, ValueError):
            continue
        if trigger_id in valid_trigger_ids:
            run.trigger_id = trigger_id
            run.save(update_fields=["trigger"])


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0018_automation_action_conditions"),
    ]

    operations = [
        migrations.AddField(
            model_name="automationaction",
            name="trigger",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="actions_legacy",
                to="iotcore.automationtrigger",
            ),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="trigger",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="runs",
                to="iotcore.automationtrigger",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="automationrun",
            name="unique_automation_run_time_v2",
        ),
        migrations.RemoveConstraint(
            model_name="automationrun",
            name="unique_automation_source_event_v2",
        ),
        migrations.RunPython(
            split_legacy_trigger_action_matrix,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="automationaction",
            name="trigger",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="action",
                to="iotcore.automationtrigger",
            ),
        ),
        migrations.AddConstraint(
            model_name="automationrun",
            constraint=models.UniqueConstraint(
                fields=("trigger", "scheduled_for"),
                name="unique_automation_trigger_run_time",
            ),
        ),
        migrations.AddConstraint(
            model_name="automationrun",
            constraint=models.UniqueConstraint(
                fields=("trigger", "source_event_id"),
                name="unique_automation_trigger_source_event",
            ),
        ),
    ]
