from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("iotcore", "0022_merge_time_window_into_schedule"),
    ]

    operations = [
        migrations.AlterField(
            model_name="automationcondition",
            name="condition_type",
            field=models.CharField(
                choices=[
                    ("schedule", "예약 시간"),
                    ("time_window", "시간대 (기존)"),
                    ("device_state", "기기 상태"),
                    ("mqtt_event", "MQTT 이벤트"),
                    ("weather", "현재 날씨"),
                    ("event_value", "트리거 데이터 (기존)"),
                ],
                max_length=20,
            ),
        ),
    ]
