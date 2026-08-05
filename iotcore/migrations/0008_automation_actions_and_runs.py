from django.db import migrations, models
import django.db.models.deletion


def migrate_sequences_to_actions(apps, schema_editor):
    Automation = apps.get_model("iotcore", "Automation")
    AutomationAction = apps.get_model("iotcore", "AutomationAction")
    for automation in Automation.objects.exclude(sequence_id=None).iterator():
        AutomationAction.objects.create(
            automation_id=automation.pk,
            order=1,
            action_type="sequence",
            sequence_id=automation.sequence_id,
            function="",
            delay=0,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0007_unique_automation_source_event"),
    ]

    operations = [
        migrations.CreateModel(
            name="AutomationAction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.PositiveIntegerField(default=1)),
                ("action_type", models.CharField(choices=[("device", "개별 기기 동작"), ("sequence", "시퀀스 실행")], max_length=20)),
                ("function", models.CharField(blank=True, max_length=100)),
                ("parameter", models.JSONField(blank=True, null=True)),
                ("delay", models.PositiveIntegerField(default=0)),
                ("automation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="actions", to="iotcore.automation")),
                ("device", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="automation_actions", to="iotcore.device")),
                ("sequence", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="automation_actions", to="iotcore.sequence")),
            ],
            options={"ordering": ["order", "id"]},
        ),
        migrations.CreateModel(
            name="AutomationRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "대기"), ("running", "실행 중"), ("success", "성공"), ("failed", "실패"), ("cancelled", "취소")], db_index=True, default="pending", max_length=20)),
                ("scheduled_for", models.DateTimeField(blank=True, null=True)),
                ("source_event_id", models.CharField(blank=True, max_length=100, null=True)),
                ("trigger_payload", models.JSONField(blank=True, default=dict)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("automation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="automation_runs", to="iotcore.automation")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="AutomationActionRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("pending", "대기"), ("running", "실행 중"), ("success", "성공"), ("failed", "실패"), ("cancelled", "취소")], max_length=20)),
                ("message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("automation_action", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="runs", to="iotcore.automationaction")),
                ("automation_run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="action_runs", to="iotcore.automationrun")),
                ("sequence_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="automation_action_runs", to="iotcore.sequencerun")),
            ],
            options={"ordering": ["order", "id"]},
        ),
        migrations.AddConstraint(
            model_name="automationaction",
            constraint=models.UniqueConstraint(fields=("automation", "order"), name="unique_automation_action_order"),
        ),
        migrations.AddConstraint(
            model_name="automationrun",
            constraint=models.UniqueConstraint(fields=("automation", "scheduled_for"), name="unique_automation_run_time_v2"),
        ),
        migrations.AddConstraint(
            model_name="automationrun",
            constraint=models.UniqueConstraint(fields=("automation", "source_event_id"), name="unique_automation_source_event_v2"),
        ),
        migrations.RunPython(migrate_sequences_to_actions, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="automation",
            name="sequence",
        ),
    ]
