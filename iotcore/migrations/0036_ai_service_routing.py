from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('iotcore', '0035_ai_inference_monitor')]
    operations = [migrations.CreateModel(
        name='AIServiceRoute',
        fields=[
            ('service', models.CharField(choices=[('asr', 'ASR'), ('nlp', 'NLP'), ('tts', 'TTS')], max_length=8, primary_key=True, serialize=False)),
            ('configured', models.BooleanField(default=False)),
            ('failover_enabled', models.BooleanField(default=True)),
            ('revision', models.PositiveIntegerField(default=1)),
            ('last_result', models.JSONField(blank=True, default=dict)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('active_device', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='active_ai_routes', to='iotcore.device')),
            ('standby_device', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='standby_ai_routes', to='iotcore.device')),
        ],
        options={'constraints': [models.CheckConstraint(check=models.Q(active_device__isnull=True) | models.Q(standby_device__isnull=True) | ~models.Q(active_device=models.F('standby_device')), name='ai_route_distinct_devices')]},
    ),
        migrations.RemoveConstraint(model_name='aiinferenceevent', name='iotcore_ai_request_stage_unique'),
        migrations.AddConstraint(model_name='aiinferenceevent', constraint=models.UniqueConstraint(fields=['request_id', 'stage', 'node_uid'], name='ai_request_stage_node_unique')),
    ]
