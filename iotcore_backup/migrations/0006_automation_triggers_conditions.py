import django.db.models.deletion
from django.db import migrations, models


def migrate_schedules_to_time_triggers(apps, schema_editor):
    Automation = apps.get_model('iotcore', 'Automation')
    AutomationTrigger = apps.get_model('iotcore', 'AutomationTrigger')
    SequenceRun = apps.get_model('iotcore', 'SequenceRun')

    for automation in Automation.objects.all():
        config = {'schedule_type': automation.trigger_type}
        config.update(automation.trigger_config or {})
        AutomationTrigger.objects.create(
            automation=automation,
            trigger_type='time',
            config=config,
            enabled=automation.enabled,
            next_run_at=automation.next_run_at,
            last_triggered_at=automation.last_run_at,
        )
        automation.last_triggered_at = automation.last_run_at
        automation.save(update_fields=['last_triggered_at'])

    SequenceRun.objects.filter(trigger='schedule').update(trigger='automation')


class Migration(migrations.Migration):

    dependencies = [
        ('iotcore', '0005_media_server_device_remove_system_target'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='sequencerun',
            name='unique_schedule_run_time',
        ),
        migrations.RenameModel(
            old_name='Schedule',
            new_name='Automation',
        ),
        migrations.RenameField(
            model_name='sequencerun',
            old_name='schedule',
            new_name='automation',
        ),
        migrations.AddField(
            model_name='automation',
            name='cooldown_seconds',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='automation',
            name='last_triggered_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='AutomationTrigger',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('trigger_type', models.CharField(choices=[('time', '예약 시간'), ('mqtt_event', '센서/MQTT 이벤트')], max_length=20)),
                ('config', models.JSONField(default=dict)),
                ('enabled', models.BooleanField(default=True)),
                ('next_run_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('last_triggered_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('automation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='triggers', to='iotcore.automation')),
            ],
            options={'ordering': ['id']},
        ),
        migrations.CreateModel(
            name='AutomationCondition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('condition_type', models.CharField(choices=[('time_window', '시간대'), ('device_state', '장치 상태')], max_length=20)),
                ('config', models.JSONField(default=dict)),
                ('order', models.PositiveIntegerField(default=1)),
                ('automation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='conditions', to='iotcore.automation')),
            ],
            options={
                'ordering': ['order', 'id'],
                'constraints': [models.UniqueConstraint(fields=('automation', 'order'), name='unique_automation_condition_order')],
            },
        ),
        migrations.CreateModel(
            name='DeviceState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('topic', models.CharField(max_length=255)),
                ('key', models.CharField(max_length=100)),
                ('value', models.JSONField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('topic', 'key'), name='unique_device_state_key')],
            },
        ),
        migrations.RunPython(
            migrate_schedules_to_time_triggers,
            migrations.RunPython.noop,
        ),
        migrations.AlterModelOptions(
            name='automation',
            options={
                'ordering': ['name'],
                'verbose_name': '자동화',
                'verbose_name_plural': '자동화',
            },
        ),
        migrations.RemoveField(
            model_name='automation',
            name='trigger_type',
        ),
        migrations.RemoveField(
            model_name='automation',
            name='trigger_config',
        ),
        migrations.RemoveField(
            model_name='automation',
            name='next_run_at',
        ),
        migrations.RemoveField(
            model_name='automation',
            name='last_run_at',
        ),
        migrations.AlterField(
            model_name='automation',
            name='sequence',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='automations', to='iotcore.sequence'),
        ),
        migrations.AlterField(
            model_name='sequencerun',
            name='trigger',
            field=models.CharField(choices=[('manual', '수동'), ('automation', '자동화')], max_length=20),
        ),
        migrations.AddField(
            model_name='sequencerun',
            name='source_event_id',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='sequencerun',
            name='trigger_payload',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddConstraint(
            model_name='sequencerun',
            constraint=models.UniqueConstraint(fields=('automation', 'scheduled_for'), name='unique_automation_run_time'),
        ),
    ]
