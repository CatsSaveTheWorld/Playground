from django.db import models

# class Device(models.Model):
#     """
#     IoT 기기 정보 테이블 (예: 거실 에어컨, 안방 선풍기 등)
#     """
#     device_type = models.CharField(max_length=50)  # 예: aircon, electric_fan 등
#     device_uid = models.CharField(max_length=100, unique=True)  # 시스템 내 고유 식별자
#     name = models.CharField(max_length=100)  # 사용자에게 표시될 기기 이름
#     location = models.CharField(max_length=100)  # 설치 위치

#     def __str__(self):
#         return f"{self.name} ({self.device_type})"

class Device(models.Model):

    class Protocol(models.TextChoices):
        IR = "ir", "IR"
        TUYA = "tuya", "Tuya"
        ZIGBEE = "zigbee", "Zigbee"
        TCPIP = "tcpip", "TCP/IP"
        MQTT = "mqtt", "MQTT"

    """
    IoT 기기 정보 테이블
    """

    device_type = models.CharField(max_length=50)
    protocol = models.CharField(
        max_length=20,
        choices=Protocol.choices,
        default=Protocol.IR,
    )

    device_uid = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.name} ({self.device_type})"


class Controller(models.Model):
    """
    IoT 컨트롤러 정보 테이블 (ESP32 리모컨)
    - 1대의 컨트롤러는 오직 1대의 기기(Device)만 전담하여 제어합니다. (1:1 관계)
    - 기기가 아직 연결되지 않은 공석 상태를 위해 null=True, blank=True를 유지합니다.
    """
    name = models.CharField(max_length=100)  # 컨트롤러 이름
    mac_address = models.CharField(max_length=17, unique=True)  # MAC 주소
    ip_address = models.GenericIPAddressField()  # IP 주소
    location = models.CharField(max_length=100, blank=True)  # 물리적 위치
    device = models.OneToOneField(
        Device,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="controller"  # 1:1이므로 복수형(controllers)에서 단수형으로 변경
    )

    class Meta:
        permissions = [
            ("can_control_iot_devices", "Can control IoT devices"),
        ]

    def __str__(self):
        return f"{self.name} (MAC: {self.mac_address})"
    

class Sequence(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
    

class SequenceStep(models.Model):
    BEFORE = "before"
    AFTER = "after"

    DELAY_POSITION_CHOICES = [
        (BEFORE, "동작 전"),
        (AFTER, "동작 후"),
    ]

    sequence = models.ForeignKey(
        Sequence,
        on_delete=models.CASCADE,
        related_name="steps"
    )
    order = models.PositiveIntegerField()
    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
    )
    function = models.CharField(max_length=50)
    parameter = models.JSONField(blank=True, null=True)

    delay = models.PositiveIntegerField(default=0)  #  지연 시간 (초)
    delay_position = models.CharField(
        max_length=10,
        choices=DELAY_POSITION_CHOICES,
        default=AFTER,
    )

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["sequence", "order"],
                name="unique_sequence_order"
            ),
        ]

    def __str__(self):
        return f"{self.sequence} #{self.order}: {self.function}"


class Automation(models.Model):
    name = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    cooldown_seconds = models.PositiveIntegerField(default=0)
    last_triggered_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "예약 실행"
        verbose_name_plural = "예약 실행"

    def __str__(self):
        return self.name




class AutomationAction(models.Model):
    class ActionType(models.TextChoices):
        DEVICE = "device", "개별 기기 동작"
        SEQUENCE = "sequence", "시퀀스 실행"

    automation = models.ForeignKey(
        Automation,
        on_delete=models.CASCADE,
        related_name="actions",
    )
    order = models.PositiveIntegerField(default=1)
    action_type = models.CharField(max_length=20, choices=ActionType.choices)
    device = models.ForeignKey(
        Device,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="automation_actions",
    )
    function = models.CharField(max_length=100, blank=True)
    parameter = models.JSONField(blank=True, null=True)
    sequence = models.ForeignKey(
        Sequence,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="automation_actions",
    )
    delay = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["automation", "order"],
                name="unique_automation_action_order",
            ),
        ]

    def __str__(self):
        return f"{self.automation} 동작 #{self.order}"


class AutomationTrigger(models.Model):
    class TriggerType(models.TextChoices):
        TIME = "time", "예약 시간"
        MQTT_EVENT = "mqtt_event", "센서/MQTT 이벤트"

    class ScheduleType(models.TextChoices):
        ONCE = "once", "한 번"
        DAILY = "daily", "매일"
        WEEKLY = "weekly", "매주"
        INTERVAL = "interval", "일정 간격"

    automation = models.ForeignKey(
        Automation,
        on_delete=models.CASCADE,
        related_name="triggers",
    )
    trigger_type = models.CharField(max_length=20, choices=TriggerType.choices)
    config = models.JSONField(default=dict)
    enabled = models.BooleanField(default=True)
    next_run_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_triggered_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.automation}: {self.get_trigger_type_display()}"


class AutomationCondition(models.Model):
    class ConditionType(models.TextChoices):
        TIME_WINDOW = "time_window", "시간대"
        DEVICE_STATE = "device_state", "장치 상태"

    automation = models.ForeignKey(
        Automation,
        on_delete=models.CASCADE,
        related_name="conditions",
    )
    condition_type = models.CharField(max_length=20, choices=ConditionType.choices)
    config = models.JSONField(default=dict)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["automation", "order"],
                name="unique_automation_condition_order",
            ),
        ]

    def __str__(self):
        return f"{self.automation} 조건 #{self.order}"


class DeviceState(models.Model):
    topic = models.CharField(max_length=255)
    key = models.CharField(max_length=255)
    value = models.JSONField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "key"],
                name="unique_device_state_key",
            ),
        ]

    def __str__(self):
        return f"{self.topic}: {self.key}={self.value}"


class NodeMetricSample(models.Model):
    """One point-in-time performance sample for a monitored PC/node."""

    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name="metric_samples",
    )
    cpu_percent = models.FloatField()
    cpu_current_ghz = models.FloatField(blank=True, null=True)
    cpu_max_ghz = models.FloatField(blank=True, null=True)
    memory_percent = models.FloatField()
    memory_used_gb = models.FloatField(blank=True, null=True)
    memory_total_gb = models.FloatField(blank=True, null=True)
    download_mbps = models.FloatField()
    upload_mbps = models.FloatField()
    storage_percent = models.FloatField(blank=True, null=True)
    storage_used_gb = models.FloatField(blank=True, null=True)
    storage_total_gb = models.FloatField(blank=True, null=True)
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(
                fields=["device", "-recorded_at"],
                name="iotcore_node_device_time_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.device.device_uid} "
            f"CPU {self.cpu_percent:.1f}% @ {self.recorded_at}"
        )




class AutomationRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "대기"
        RUNNING = "running", "실행 중"
        SUCCESS = "success", "성공"
        FAILED = "failed", "실패"
        CANCELLED = "cancelled", "취소"

    automation = models.ForeignKey(
        Automation,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="automation_runs",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    scheduled_for = models.DateTimeField(blank=True, null=True)
    source_event_id = models.CharField(max_length=100, blank=True, null=True)
    trigger_payload = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["automation", "scheduled_for"],
                name="unique_automation_run_time_v2",
            ),
            models.UniqueConstraint(
                fields=["automation", "source_event_id"],
                name="unique_automation_source_event_v2",
            ),
        ]


class AutomationActionRun(models.Model):
    automation_run = models.ForeignKey(
        AutomationRun,
        on_delete=models.CASCADE,
        related_name="action_runs",
    )
    automation_action = models.ForeignKey(
        AutomationAction,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="runs",
    )
    order = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=AutomationRun.Status.choices)
    sequence_run = models.ForeignKey(
        "SequenceRun",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="automation_action_runs",
    )
    message = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["order", "id"]


class SequenceRun(models.Model):
    class Trigger(models.TextChoices):
        MANUAL = "manual", "수동"
        AUTOMATION = "automation", "예약 실행"

    class Status(models.TextChoices):
        PENDING = "pending", "대기"
        RUNNING = "running", "실행 중"
        SUCCESS = "success", "성공"
        FAILED = "failed", "실패"
        CANCELLED = "cancelled", "취소"

    sequence = models.ForeignKey(
        Sequence,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    sequence_name = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    automation = models.ForeignKey(
        Automation,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="runs",
    )
    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    scheduled_for = models.DateTimeField(blank=True, null=True)
    source_event_id = models.CharField(max_length=100, blank=True, null=True)
    trigger_payload = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["automation", "scheduled_for"],
                name="unique_automation_run_time",
            ),
            models.UniqueConstraint(
                fields=["automation", "source_event_id"],
                name="unique_automation_source_event",
            ),
        ]

    def __str__(self):
        name = self.sequence_name
        if not name and self.sequence_id:
            name = self.sequence.name
        return f"{name or '삭제된 시퀀스'} ({self.get_status_display()})"


class SequenceStepRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "실행 중"
        SUCCESS = "success", "성공"
        FAILED = "failed", "실패"
        SKIPPED = "skipped", "건너뜀"

    sequence_run = models.ForeignKey(
        SequenceRun,
        on_delete=models.CASCADE,
        related_name="step_runs",
    )
    sequence_step = models.ForeignKey(
        SequenceStep,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="runs",
    )
    step_order = models.PositiveIntegerField()
    action_code = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=Status.choices)
    message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["step_order"]
