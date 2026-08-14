from django.db import migrations, models


def _device_for_topic(Device, topic):
    topic = str(topic or "")
    if topic.startswith("zigbee2mqtt/"):
        device_uid = topic[len("zigbee2mqtt/"):]
        if device_uid and "/" not in device_uid:
            return Device.objects.filter(device_uid=device_uid).first()
    prefix = "iotcore/devices/"
    suffix = "/state"
    if topic.startswith(prefix) and topic.endswith(suffix):
        device_uid = topic[len(prefix):-len(suffix)]
        if device_uid and "/" not in device_uid:
            return Device.objects.filter(device_uid=device_uid).first()
    return None


def forward_refactor(apps, schema_editor):
    AutomationCondition = apps.get_model("iotcore", "AutomationCondition")
    Device = apps.get_model("iotcore", "Device")

    # 기존 MQTT 트리거의 field/operator/value는 그대로 보존한다.
    # 여러 MQTT 트리거는 OR 관계이므로 이를 공통 조건(AND)으로 자동 변환하면
    # 기존 예약 실행의 의미가 달라질 수 있다. 새 예약 실행부터 트리거와 조건을
    # 분리하고, 기존 인라인 필터는 호환 레이어에서 계속 평가한다.

    # 기존 raw topic 기반 기기 상태 조건은 식별 가능한 경우에만 IoTCore 기기
    # 정보를 보강한다. topic 자체도 남겨 두어 롤백/호환 동작을 보장한다.
    for condition in AutomationCondition.objects.filter(condition_type="device_state"):
        config = dict(condition.config or {})
        if config.get("device_id") or config.get("device_uid"):
            continue
        device = _device_for_topic(Device, config.get("topic"))
        if device is None:
            continue
        config.update({
            "device_id": device.pk,
            "device_uid": device.device_uid,
            "device_name": device.name,
        })
        condition.config = config
        condition.save(update_fields=["config"])


def reverse_refactor(apps, schema_editor):
    # forward_refactor는 기존 의미를 바꾸지 않는 식별 정보 보강만 수행한다.
    # 롤백 시에도 이 추가 메타데이터는 구버전 코드에서 무시되므로 그대로 둔다.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("iotcore", "0013_node_metric_sample_clock_memory_capacity"),
    ]

    operations = [
        migrations.AlterField(
            model_name="automationtrigger",
            name="trigger_type",
            field=models.CharField(
                choices=[
                    ("time", "예약 시간"),
                    ("mqtt_event", "MQTT 이벤트"),
                    ("device_state", "기기 상태 변화"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="automationcondition",
            name="condition_type",
            field=models.CharField(
                choices=[
                    ("time_window", "시간대"),
                    ("device_state", "기기 상태"),
                    ("event_value", "트리거 데이터"),
                ],
                max_length=20,
            ),
        ),
        migrations.RunPython(forward_refactor, reverse_refactor),
    ]
