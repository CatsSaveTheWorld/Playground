from django.db import migrations, models


def classify_existing_devices(apps, schema_editor):
    Device = apps.get_model("iotcore", "Device")

    # 이 마이그레이션 전에 사용자가 등록한 센서도 자동으로 센서 역할로 보정한다.
    # 기존 제어 기기는 기본값(control)을 유지한다.
    Device.objects.filter(device_type__icontains="sensor").update(
        device_role="sensor"
    )
    # 현재 IoTCore에서 사용 중인 Aqara T1은 device_type 명명과 무관하게
    # 센서 역할로 보정한다.
    Device.objects.filter(
        device_uid="leedowon_room_temp_humidity"
    ).update(device_role="sensor")


def reverse_classification(apps, schema_editor):
    # 필드 제거 직전에 별도 복구할 데이터는 없다.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0014_automation_state_trigger_refactor"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="device_role",
            field=models.CharField(
                choices=[
                    ("control", "제어 기기"),
                    ("sensor", "센서"),
                    ("hybrid", "제어 + 센서"),
                ],
                db_index=True,
                default="control",
                max_length=20,
            ),
        ),
        migrations.RunPython(classify_existing_devices, reverse_classification),
    ]
