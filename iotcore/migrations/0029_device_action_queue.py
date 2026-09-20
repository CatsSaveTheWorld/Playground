import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def snapshot_existing_action_runs(apps, schema_editor):
    ActionRun = apps.get_model("iotcore", "ActionRun")
    AutomationRun = apps.get_model("iotcore", "AutomationRun")

    for action_run in ActionRun.objects.select_related("action").iterator():
        action_run.root_run_id = action_run.automation_run_id
        action = action_run.action
        if action is not None:
            action_run.device_id = action.device_id
            action_run.target_automation_id = action.target_automation_id
            action_run.action_type = action.action_type
            action_run.function = action.function
            action_run.parameter = action.parameter
            action_run.delay = action.delay
            action_run.delay_position = action.delay_position
        action_run.available_at = (
            action_run.started_at
            or action_run.finished_at
            or django.utils.timezone.now()
        )
        action_run.save()

    AutomationRun.objects.exclude(finished_at=None).update(
        planned_at=models.F("finished_at")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0028_initialize_device_state_rows"),
    ]

    operations = [
        migrations.AddField(
            model_name="automationrun",
            name="parent_action_run",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="child_automation_run",
                to="iotcore.actionrun",
            ),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="planned_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="root_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="descendant_runs",
                to="iotcore.automationrun",
            ),
        ),
        migrations.AlterField(
            model_name="automationrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "대기"),
                    ("waiting", "지연 대기"),
                    ("running", "실행 중"),
                    ("success", "성공"),
                    ("failed", "실패"),
                    ("cancelled", "취소"),
                    ("skipped", "건너뜀"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="action_type",
            field=models.CharField(
                choices=[
                    ("device", "개별 기기 동작"),
                    ("automation", "다른 자동화 실행"),
                ],
                default="device",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="available_at",
            field=models.DateTimeField(
                db_index=True,
                default=django.utils.timezone.now,
            ),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="delay",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="delay_applied",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="delay_position",
            field=models.CharField(
                choices=[("before", "동작 전"), ("after", "동작 후")],
                default="after",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="device",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="queued_action_runs",
                to="iotcore.device",
            ),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="function",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="parameter",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="root_run",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="queued_action_runs",
                to="iotcore.automationrun",
            ),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="target_automation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="queued_parent_actions",
                to="iotcore.automation",
            ),
        ),
        migrations.AddField(
            model_name="actionrun",
            name="target_automation_name",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AlterField(
            model_name="actionrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "대기"),
                    ("waiting", "지연 대기"),
                    ("running", "실행 중"),
                    ("success", "성공"),
                    ("failed", "실패"),
                    ("cancelled", "취소"),
                    ("skipped", "건너뜀"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.RunPython(
            snapshot_existing_action_runs,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="actionrun",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AlterField(
            model_name="actionrun",
            name="root_run",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="queued_action_runs",
                to="iotcore.automationrun",
            ),
        ),
        migrations.AddIndex(
            model_name="actionrun",
            index=models.Index(
                fields=["device", "status", "available_at"],
                name="iotcore_device_queue_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="actionrun",
            index=models.Index(
                fields=["root_run", "status"],
                name="iotcore_root_queue_idx",
            ),
        ),
    ]
