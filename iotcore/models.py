import uuid

from django.db import models
from django.utils import timezone

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

    class Role(models.TextChoices):
        CONTROL = "control", "제어 기기"
        SENSOR = "sensor", "센서"
        HYBRID = "hybrid", "제어 + 센서"

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
    device_role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.CONTROL,
        db_index=True,
    )
    protocol = models.CharField(
        max_length=20,
        choices=Protocol.choices,
        default=Protocol.IR,
    )

    device_uid = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=100)
    # Protocol-specific control metadata lives with the Device so higher layers
    # (Automation, AI, web UI) only need a device_id and a canonical action.
    # Examples for a PC: mac_address, ip_address, wol_port, agent_port/path.
    control_config = models.JSONField(default=dict, blank=True)

    @property
    def is_controllable(self):
        return self.device_role in {self.Role.CONTROL, self.Role.HYBRID}

    @property
    def is_state_source(self):
        return self.device_role in {self.Role.SENSOR, self.Role.HYBRID}

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
    

class AutomationGroup(models.Model):
    """Optional UI grouping shared by immediate and scheduled automations."""

    name = models.CharField(max_length=100, unique=True)
    order = models.PositiveIntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "name", "id"]
        verbose_name = "자동화 그룹"
        verbose_name_plural = "자동화 그룹"

    def __str__(self):
        return self.name


class Automation(models.Model):
    """A user-facing routine.

    Immediate and scheduled execution share one user-facing automation model.
    The graph runtime uses AutomationNode/AutomationEdge while legacy
    Step/Trigger/Action rows remain available during the compatibility rollout.
    ``automation_type`` is an explicit user choice; condition composition never
    changes that choice automatically.
    """

    class Type(models.TextChoices):
        IMMEDIATE = "immediate", "즉시 실행"
        SCHEDULED = "scheduled", "예약 실행"

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    voice_aliases = models.JSONField(default=list, blank=True)
    automation_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        default=Type.SCHEDULED,
        db_index=True,
    )
    group = models.ForeignKey(
        AutomationGroup,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="automations",
    )
    is_favorite = models.BooleanField(default=False, db_index=True)
    enabled = models.BooleanField(default=True)
    cooldown_seconds = models.PositiveIntegerField(default=0)
    last_triggered_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Graph editor/runtime version. 0 keeps the legacy Step engine available as
    # a rollback path; version 1 uses AutomationNode/AutomationEdge.
    graph_version = models.PositiveSmallIntegerField(default=0, db_index=True)
    graph_revision = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["name", "id"]
        verbose_name = "자동화"
        verbose_name_plural = "자동화"

    def __str__(self):
        return self.name


class Step(models.Model):
    """One ordered unit inside an Automation.

    A Step owns 0..N Triggers and 1..N Actions.  No Trigger means an
    unconditional Step when the Automation is explicitly executed.  Scheduled
    automations with no wake-up Trigger simply have nothing that starts them
    automatically.
    """

    class TriggerOperator(models.TextChoices):
        AND = "and", "모든 트리거 만족 (AND)"
        OR = "or", "하나 이상 만족 (OR)"

    automation = models.ForeignKey(
        Automation,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    order = models.PositiveIntegerField()
    trigger_operator = models.CharField(
        max_length=3,
        choices=TriggerOperator.choices,
        default=TriggerOperator.AND,
    )
    enabled = models.BooleanField(default=True)

    # Scheduler runtime state belongs to the Step because the Step is the
    # independently evaluated execution set.
    last_result = models.BooleanField(default=False)
    next_run_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_triggered_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["automation", "order"],
                name="unique_automation_step_order",
            ),
        ]

    def __str__(self):
        return f"{self.automation} Step #{self.order}"


class Trigger(models.Model):
    """A predicate/event definition that decides whether a Step may run."""

    class Type(models.TextChoices):
        SCHEDULE = "schedule", "날짜 / 시간"
        DEVICE_STATE = "device_state", "기기 상태 / 변화"
        MQTT_EVENT = "mqtt_event", "MQTT 이벤트"
        WEATHER = "weather", "현재 날씨"

    class ScheduleType(models.TextChoices):
        ONCE = "once", "한 번"
        WEEKLY = "weekly", "매주"
        INTERVAL = "interval", "일정 간격"

    class ScheduleTimeMode(models.TextChoices):
        AT = "at", "지정 시각"
        WINDOW = "window", "시간대"

    step = models.ForeignKey(
        Step,
        on_delete=models.CASCADE,
        related_name="triggers",
    )
    trigger_type = models.CharField(max_length=20, choices=Type.choices)
    config = models.JSONField(default=dict)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["step", "order"],
                name="unique_step_trigger_order",
            ),
        ]

    def __str__(self):
        return f"{self.step} 트리거 #{self.order}"


class Action(models.Model):
    """One ordered action owned by a Step."""

    class Type(models.TextChoices):
        DEVICE = "device", "개별 기기 동작"
        AUTOMATION = "automation", "다른 자동화 실행"

    class DelayPosition(models.TextChoices):
        BEFORE = "before", "동작 전"
        AFTER = "after", "동작 후"

    step = models.ForeignKey(
        Step,
        on_delete=models.CASCADE,
        related_name="actions",
    )
    order = models.PositiveIntegerField(default=1)
    action_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        default=Type.DEVICE,
    )
    device = models.ForeignKey(
        Device,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="automation_actions",
    )
    function = models.CharField(max_length=100, blank=True)
    parameter = models.JSONField(blank=True, null=True)
    target_automation = models.ForeignKey(
        Automation,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="referenced_by_actions",
    )
    delay = models.PositiveIntegerField(default=0)
    delay_position = models.CharField(
        max_length=10,
        choices=DelayPosition.choices,
        default=DelayPosition.AFTER,
    )

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["step", "order"],
                name="unique_step_action_order",
            ),
        ]

    def __str__(self):
        return f"{self.step} 동작 #{self.order}"

class AutomationNode(models.Model):
    """One visual/control-flow node in the Automation graph.

    The legacy Step model remains during the compatibility period. New graph
    execution is represented independently so the old schema can be rolled
    back without data loss.
    """

    class Type(models.TextChoices):
        START = "start", "시작"
        CONDITION = "condition", "조건"
        ACTION = "action", "동작"
        DELAY = "delay", "지연"
        END = "end", "종료"

    automation = models.ForeignKey(
        Automation,
        on_delete=models.CASCADE,
        related_name="graph_nodes",
    )
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    node_type = models.CharField(max_length=20, choices=Type.choices)
    title = models.CharField(max_length=100, blank=True)
    position_x = models.IntegerField(default=0)
    position_y = models.IntegerField(default=0)
    enabled = models.BooleanField(default=True)
    delay_seconds = models.PositiveIntegerField(default=0)
    legacy_step = models.ForeignKey(
        Step,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="graph_nodes",
    )

    # Entry-condition runtime state. Keeping it on the node lets the graph
    # scheduler replace Step.next_run_at/last_result without overloading the
    # legacy model.
    last_result = models.BooleanField(default=False)
    next_run_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_triggered_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position_y", "position_x", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["automation", "key"],
                name="unique_automation_node_key",
            ),
        ]

    def __str__(self):
        return f"{self.automation} · {self.get_node_type_display()}"


class AutomationConditionItem(models.Model):
    """One predicate inside a CONDITION node. Multiple items are ANDed."""

    node = models.ForeignKey(
        AutomationNode,
        on_delete=models.CASCADE,
        related_name="conditions",
    )
    order = models.PositiveIntegerField(default=1)
    condition_type = models.CharField(max_length=20, choices=Trigger.Type.choices)
    config = models.JSONField(default=dict)
    legacy_trigger = models.ForeignKey(
        Trigger,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="graph_condition_items",
    )

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "order"],
                name="unique_graph_condition_order",
            ),
        ]

    def __str__(self):
        return f"{self.node} 조건 #{self.order}"


class AutomationActionItem(models.Model):
    """One ordered action inside an ACTION node."""

    node = models.ForeignKey(
        AutomationNode,
        on_delete=models.CASCADE,
        related_name="actions",
    )
    order = models.PositiveIntegerField(default=1)
    action_type = models.CharField(
        max_length=20,
        choices=Action.Type.choices,
        default=Action.Type.DEVICE,
    )
    device = models.ForeignKey(
        Device,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="graph_action_items",
    )
    function = models.CharField(max_length=100, blank=True)
    parameter = models.JSONField(blank=True, null=True)
    target_automation = models.ForeignKey(
        Automation,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="graph_referenced_by_actions",
    )
    # Legacy per-action delay remains representable for lossless migration.
    # New UI should prefer a dedicated DELAY node.
    delay = models.PositiveIntegerField(default=0)
    delay_position = models.CharField(
        max_length=10,
        choices=Action.DelayPosition.choices,
        default=Action.DelayPosition.AFTER,
    )
    legacy_action = models.ForeignKey(
        Action,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="graph_action_items",
    )

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "order"],
                name="unique_graph_action_order",
            ),
        ]

    def __str__(self):
        return f"{self.node} 동작 #{self.order}"


class AutomationEdge(models.Model):
    """Directed control-flow edge. AND/OR is compiled into TRUE/FALSE flow."""

    class Outcome(models.TextChoices):
        ALWAYS = "always", "항상"
        TRUE = "true", "참"
        FALSE = "false", "거짓"

    automation = models.ForeignKey(
        Automation,
        on_delete=models.CASCADE,
        related_name="graph_edges",
    )
    source = models.ForeignKey(
        AutomationNode,
        on_delete=models.CASCADE,
        related_name="outgoing_edges",
    )
    target = models.ForeignKey(
        AutomationNode,
        on_delete=models.CASCADE,
        related_name="incoming_edges",
    )
    outcome = models.CharField(
        max_length=10,
        choices=Outcome.choices,
        default=Outcome.ALWAYS,
    )
    # UI rewrites this from top-to-bottom target position. Runtime only trusts
    # this stable integer, so moving a node without saving cannot reorder work.
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["source_id", "outcome", "order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "outcome", "order"],
                name="unique_graph_edge_order",
            ),
        ]

    def __str__(self):
        return f"{self.source_id} -{self.outcome}-> {self.target_id}"


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


class DoorEvent(models.Model):
    """A confirmed open/close transition reported by a door contact sensor."""

    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name="door_events",
    )
    is_open = models.BooleanField(db_index=True)
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        indexes = [
            models.Index(
                fields=["device", "-recorded_at"],
                name="iotcore_door_device_time_idx",
            ),
        ]

    def __str__(self):
        state = "열림" if self.is_open else "닫힘"
        return f"{self.device.name}: {state} @ {self.recorded_at}"


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
    ai_status = models.JSONField(default=dict, blank=True)
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




class AIInferenceEvent(models.Model):
    """Timing/outcome metadata only; audio and recognized text are not stored."""

    class Status(models.TextChoices):
        PROCESSING = "processing", "처리 중"
        SUCCESS = "success", "성공"
        FAILED = "failed", "실패"
        DISABLED = "disabled", "비활성"

    node_uid = models.CharField(max_length=100, db_index=True)
    request_id = models.CharField(max_length=32)
    stage = models.CharField(max_length=10, choices=[("asr", "ASR"), ("nlp", "NLP")])
    route = models.CharField(max_length=20, default="worker")
    status = models.CharField(max_length=12, choices=Status.choices)
    elapsed_ms = models.FloatField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["request_id", "stage"], name="iotcore_ai_request_stage_unique")]
        indexes = [models.Index(fields=["node_uid", "-started_at"], name="iotcore_ai_node_time_idx")]


class AutomationRun(models.Model):
    """Execution history for both immediate and scheduled Automations."""

    class Status(models.TextChoices):
        PENDING = "pending", "대기"
        WAITING = "waiting", "지연 대기"
        RUNNING = "running", "실행 중"
        SUCCESS = "success", "성공"
        FAILED = "failed", "실패"
        CANCELLED = "cancelled", "취소"
        SKIPPED = "skipped", "건너뜀"

    class Source(models.TextChoices):
        MANUAL = "manual", "수동"
        SCHEDULER = "scheduler", "스케줄러"
        MQTT = "mqtt", "MQTT"
        DEVICE = "device", "기기 상태"
        WEATHER = "weather", "날씨"
        AUTOMATION = "automation", "다른 자동화"
        API = "api", "API"
        AI = "ai", "AI"

    automation = models.ForeignKey(
        Automation,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="runs",
    )
    root_run = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="descendant_runs",
    )
    parent_action_run = models.OneToOneField(
        "ActionRun",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="child_automation_run",
    )
    planned_at = models.DateTimeField(blank=True, null=True, db_index=True)
    step = models.ForeignKey(
        Step,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="runs",
    )
    automation_name = models.CharField(max_length=100, blank=True, default="")
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
        db_index=True,
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
    graph_version = models.PositiveSmallIntegerField(default=0)
    graph_snapshot = models.JSONField(default=dict, blank=True)
    entry_node_key = models.UUIDField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["step", "scheduled_for"],
                name="unique_step_run_time",
            ),
            models.UniqueConstraint(
                fields=["step", "source_event_id"],
                name="unique_step_source_event",
            ),
            models.UniqueConstraint(
                fields=["automation", "entry_node_key", "scheduled_for"],
                name="unique_graph_entry_run_time",
            ),
            models.UniqueConstraint(
                fields=["automation", "entry_node_key", "source_event_id"],
                name="unique_graph_entry_event",
            ),
        ]

    def __str__(self):
        name = self.automation_name or (self.automation.name if self.automation_id else "삭제된 자동화")
        return f"{name} ({self.get_status_display()})"


class ActionRun(models.Model):
    automation_run = models.ForeignKey(
        AutomationRun,
        on_delete=models.CASCADE,
        related_name="action_runs",
    )
    action = models.ForeignKey(
        Action,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="runs",
    )
    root_run = models.ForeignKey(
        AutomationRun,
        on_delete=models.CASCADE,
        related_name="queued_action_runs",
    )
    device = models.ForeignKey(
        Device,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="queued_action_runs",
    )
    target_automation = models.ForeignKey(
        Automation,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="queued_parent_actions",
    )
    step_order = models.PositiveIntegerField(default=1)
    order = models.PositiveIntegerField()
    action_type = models.CharField(
        max_length=20,
        choices=Action.Type.choices,
        default=Action.Type.DEVICE,
    )
    function = models.CharField(max_length=100, blank=True)
    parameter = models.JSONField(blank=True, null=True)
    target_automation_name = models.CharField(max_length=100, blank=True)
    graph_node_key = models.UUIDField(blank=True, null=True, db_index=True)
    graph_item_order = models.PositiveIntegerField(blank=True, null=True)
    delay = models.PositiveIntegerField(default=0)
    delay_position = models.CharField(
        max_length=10,
        choices=Action.DelayPosition.choices,
        default=Action.DelayPosition.AFTER,
    )
    delay_applied = models.BooleanField(default=False)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=AutomationRun.Status.choices,
        default=AutomationRun.Status.PENDING,
        db_index=True,
    )
    message = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["step_order", "order", "id"]
        indexes = [
            models.Index(
                fields=["device", "status", "available_at"],
                name="iotcore_device_queue_idx",
            ),
            models.Index(
                fields=["root_run", "status"],
                name="iotcore_root_queue_idx",
            ),
        ]


class AutomationNodeRun(models.Model):
    """Durable queue/history row for a graph node within one AutomationRun."""

    automation_run = models.ForeignKey(
        AutomationRun,
        on_delete=models.CASCADE,
        related_name="node_runs",
    )
    node = models.ForeignKey(
        AutomationNode,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="runs",
    )
    node_key = models.UUIDField()
    node_type = models.CharField(max_length=20, choices=AutomationNode.Type.choices)
    queue_order = models.PositiveBigIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=AutomationRun.Status.choices,
        default=AutomationRun.Status.PENDING,
        db_index=True,
    )
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    result = models.JSONField(default=dict, blank=True)
    message = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["queue_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["automation_run", "node_key"],
                name="unique_run_node_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "available_at", "queue_order"],
                name="iotcore_node_run_queue_idx",
            ),
        ]

    def __str__(self):
        return f"Run {self.automation_run_id} · {self.node_type} · {self.status}"
