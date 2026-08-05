import django.db.models.deletion
from django.db import migrations, models


def migrate_system_steps_to_media_server(apps, schema_editor):
    Device = apps.get_model('iotcore', 'Device')
    SequenceStep = apps.get_model('iotcore', 'SequenceStep')

    media_server, _ = Device.objects.update_or_create(
        device_uid='pi5',
        defaults={
            'name': 'Pi5 미디어 서버',
            'device_type': 'media_server',
            'protocol': 'mqtt',
            'location': '거실',
        },
    )
    SequenceStep.objects.filter(device__isnull=True).update(
        device_id=media_server.pk,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('iotcore', '0004_schedule_sequencerun_sequencesteprun_and_more'),
    ]

    operations = [
        migrations.RunPython(
            migrate_system_steps_to_media_server,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name='sequencestep',
            name='sequence_step_target_matches_device',
        ),
        migrations.RemoveField(
            model_name='sequencestep',
            name='target_type',
        ),
        migrations.AlterField(
            model_name='sequencestep',
            name='device',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to='iotcore.device',
            ),
        ),
        migrations.AlterField(
            model_name='sequencerun',
            name='trigger',
            field=models.CharField(
                choices=[
                    ('manual', '수동'),
                    ('schedule', '예약 실행'),
                    ('mqtt', 'MQTT'),
                ],
                max_length=20,
            ),
        ),
    ]
