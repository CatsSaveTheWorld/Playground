import json

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import (
    Automation,
    AutomationAction,
    AutomationGroup,
    AutomationCondition,
    AutomationTrigger,
    Controller,
    Device,
    Sequence,
    SequenceGroup,
    SequenceStep,
)


class DeviceForm(forms.ModelForm):
    """
    IoT 기기 정보 테이블 (예: 거실 에어컨, 안방 선풍기 등)
    """
    class Meta:
        model = Device                    # 사용할 모델
        fields = ['device_type', 'device_role', 'protocol', 'device_uid', 'name', 'location']     # QuestionForm에서 사용할 Question 모델의 속성


class ControllerForm(forms.ModelForm):
    """
    IoT 컨트롤러 정보 테이블 (ESP32 리모컨)
    - 1대의 컨트롤러는 오직 1대의 기기(Device)만 전담하여 제어합니다. (1:1 관계)
    - 기기가 아직 연결되지 않은 공석 상태를 위해 null=True, blank=True를 유지합니다.
    """
    class Meta:
        model = Controller                    # 사용할 모델
        fields = ['name', 'mac_address', 'ip_address', 'location', 'device']     # QuestionForm에서 사용할 Question 모델의 속성


class SequenceGroupForm(forms.ModelForm):
    class Meta:
        model = SequenceGroup
        fields = ["name", "order"]
        labels = {
            "name": "그룹 이름",
            "order": "정렬 순서",
        }

    def clean_name(self):
        return self.cleaned_data["name"].strip()


class SequenceForm(forms.ModelForm):
    class Meta:
        model = Sequence
        fields = ["name", "description", "group", "is_favorite"]
        labels = {
            "name": "이름",
            "description": "설명",
            "group": "그룹",
            "is_favorite": "즐겨찾기",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["group"].queryset = SequenceGroup.objects.order_by(
            "order", "name", "id"
        )
        self.fields["group"].empty_label = "미분류"


class SequenceStepForm(forms.ModelForm):
    # --------------------------
    # Form 전용 필드
    # 시 / 분 / 초를 UI에서 입력받아 초 단위로 DB에 저장.
    # --------------------------
    hour = forms.IntegerField(
        min_value=0,
        initial=0,
        required=False,
    )

    minute = forms.IntegerField(
        min_value=0,
        initial=0,
        required=False,
    )

    second = forms.IntegerField(
        min_value=0,
        initial=0,
        required=False,
    )

    class Meta:
        model = SequenceStep
        fields = [
            "device",
            "delay_position",
        ]

        widgets = {
            "delay_position": forms.RadioSelect(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["device"].queryset = Device.objects.filter(
            device_role__in=[Device.Role.CONTROL, Device.Role.HYBRID]
        ).order_by("location", "name", "id")

        delay = self.instance.delay if self.instance.pk else 0

        self.fields["hour"].initial = delay // 3600
        self.fields["minute"].initial = (delay % 3600) // 60
        self.fields["second"].initial = delay % 60

    def save(self, commit=True):
        instance = super().save(commit=False)

        hour = self.cleaned_data.get("hour") or 0
        minute = self.cleaned_data.get("minute") or 0
        second = self.cleaned_data.get("second") or 0

        instance.delay = (
            hour * 3600 +
            minute * 60 +
            second
        )

        # print(f"[DEBUG] delay = {instance.delay}")
        # print(f"[DEBUG] delay_position = {instance.delay_position}")

        if commit:
            instance.save()

        return instance


class AutomationGroupForm(forms.ModelForm):
    class Meta:
        model = AutomationGroup
        fields = ["name", "order"]
        labels = {
            "name": "그룹 이름",
            "order": "정렬 순서",
        }

    def clean_name(self):
        return self.cleaned_data["name"].strip()


class AutomationForm(forms.ModelForm):
    class Meta:
        model = Automation
        fields = [
            "name",
            "group",
            "is_favorite",
            "enabled",
            "cooldown_seconds",
        ]
        labels = {
            "name": "이름",
            "group": "그룹",
            "is_favorite": "즐겨찾기",
            "enabled": "활성화",
            "cooldown_seconds": "재실행 제한(초)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["group"].queryset = AutomationGroup.objects.order_by(
            "order", "name", "id"
        )
        self.fields["group"].empty_label = "미분류"


class AutomationActionForm(forms.ModelForm):
    # UI-only owner pointer. Actions are an automation-level formset, so the
    # editor posts the TriggerSet form index that owns each action card.
    trigger_index = forms.IntegerField(
        required=False,
        min_value=0,
        widget=forms.HiddenInput(),
    )
    parameter_json = forms.CharField(
        required=False,
        label="동작 파라미터(JSON)",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text='예: {"temperature": 24} 또는 {"volume": 35}',
    )

    class Meta:
        model = AutomationAction
        fields = [
            "action_type",
            "device",
            "function",
            "sequence",
            "delay",
        ]
        labels = {
            "action_type": "실행 종류",
            "device": "기기",
            "function": "동작",
            "sequence": "시퀀스",
            "delay": "실행 전 지연(초)",
        }
        widgets = {
            "function": forms.Select(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .device_actions import DeviceActionRegistry

        choices = [("", "---------")]
        seen = set()
        for actions in DeviceActionRegistry._ACTIONS.values():
            for action in actions:
                if action.code not in seen:
                    choices.append((action.code, action.display_name))
                    seen.add(action.code)
        self.fields["device"].queryset = Device.objects.filter(
            device_role__in=[Device.Role.CONTROL, Device.Role.HYBRID]
        ).order_by("location", "name", "id")
        self.fields["function"].widget.choices = choices
        if self.instance.pk and self.instance.parameter is not None:
            self.fields["parameter_json"].initial = json.dumps(
                self.instance.parameter,
                ensure_ascii=False,
            )

    def clean(self):
        cleaned = super().clean()
        action_type = cleaned.get("action_type")
        device = cleaned.get("device")
        function = str(cleaned.get("function") or "").strip()
        sequence = cleaned.get("sequence")
        raw_parameter = str(cleaned.get("parameter_json") or "").strip()

        if action_type == AutomationAction.ActionType.DEVICE:
            if device is None:
                self.add_error("device", "기기를 선택하세요.")
            if not function:
                self.add_error("function", "동작을 선택하세요.")
            elif device is not None:
                from .device_actions import DeviceActionRegistry

                supported_codes = {
                    action.code
                    for action in DeviceActionRegistry.get_actions(device.device_type)
                }
                if function not in supported_codes:
                    self.add_error(
                        "function",
                        "선택한 기기에서 지원하지 않는 동작입니다.",
                    )
            cleaned["sequence"] = None
            parameter = None
            if raw_parameter:
                try:
                    parameter = json.loads(raw_parameter)
                except json.JSONDecodeError:
                    self.add_error("parameter_json", "올바른 JSON 형식으로 입력하세요.")
            cleaned["parameter"] = parameter
        elif action_type == AutomationAction.ActionType.SEQUENCE:
            if sequence is None:
                self.add_error("sequence", "시퀀스를 선택하세요.")
            cleaned["device"] = None
            cleaned["function"] = ""
            cleaned["parameter"] = None
        return cleaned

    def save(self, commit=True):
        action = super().save(commit=False)
        action.parameter = self.cleaned_data.get("parameter")
        if action.action_type == AutomationAction.ActionType.SEQUENCE:
            action.device = None
            action.function = ""
        else:
            action.sequence = None
        if commit:
            action.save()
        return action


class BaseAutomationActionFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        remaining = [
            form for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        if not remaining:
            raise forms.ValidationError("실행 동작을 하나 이상 등록하세요.")


class AutomationTriggerForm(forms.ModelForm):
    """Editor form for one trigger set.

    New UI only exposes ``enabled`` and ``condition_operator``.  Legacy
    trigger-specific fields are deliberately retained so pre-0020 tests/data
    and rollback tooling can still parse the old TIME/MQTT/DEVICE trigger
    representation.
    """
    WEEKDAY_CHOICES = [
        ("0", "월"), ("1", "화"), ("2", "수"), ("3", "목"),
        ("4", "금"), ("5", "토"), ("6", "일"),
    ]
    INTERVAL_UNIT_CHOICES = [
        ("minutes", "분"), ("hours", "시간"), ("days", "일"),
    ]
    TIME_SCHEDULE_CHOICES = [
        (AutomationTrigger.ScheduleType.ONCE, "한 번 실행"),
        (AutomationTrigger.ScheduleType.WEEKLY, "요일 선택 반복"),
        (AutomationTrigger.ScheduleType.INTERVAL, "일정 간격"),
    ]

    schedule_type = forms.ChoiceField(required=False, label="반복 방식", choices=TIME_SCHEDULE_CHOICES)
    run_at = forms.DateTimeField(
        required=False,
        label="실행 시각",
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    time_of_day = forms.TimeField(
        required=False,
        label="실행 시간",
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    weekdays = forms.MultipleChoiceField(
        required=False,
        label="요일",
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    interval_every = forms.IntegerField(required=False, min_value=1, label="실행 간격")
    interval_unit = forms.ChoiceField(required=False, label="간격 단위", choices=INTERVAL_UNIT_CHOICES)
    event_topic = forms.CharField(required=False, label="MQTT 토픽")
    legacy_event_config = forms.CharField(required=False, widget=forms.HiddenInput())
    state_device = forms.ModelChoiceField(queryset=Device.objects.none(), required=False, label="감시 기기")
    trigger_type = forms.ChoiceField(required=False, label="기존 실행 계기", choices=AutomationTrigger.TriggerType.choices)
    condition_operator = forms.ChoiceField(
        required=False,
        label="조건 충족 방식",
        choices=AutomationTrigger.ConditionOperator.choices,
        initial=AutomationTrigger.ConditionOperator.AND,
    )

    class Meta:
        model = AutomationTrigger
        fields = ["trigger_type", "enabled", "condition_operator"]
        labels = {
            "enabled": "이 트리거 세트 활성화",
            "condition_operator": "조건 충족 방식",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["state_device"].queryset = Device.objects.all().order_by("location", "name", "id")
        if not self.is_bound and not self.instance.pk:
            self.fields["enabled"].initial = True
            self.fields["condition_operator"].initial = AutomationTrigger.ConditionOperator.AND

        config = self.instance.config if self.instance.pk else {}
        trigger_type = self.instance.trigger_type if self.instance.pk else None
        if trigger_type == AutomationTrigger.TriggerType.TIME:
            schedule_type = config.get("schedule_type")
            if schedule_type == AutomationTrigger.ScheduleType.DAILY:
                schedule_type = AutomationTrigger.ScheduleType.WEEKLY
            self.fields["schedule_type"].initial = schedule_type
            if schedule_type == AutomationTrigger.ScheduleType.ONCE:
                run_at = parse_datetime(str(config.get("run_at", "")))
                if run_at is not None and timezone.is_aware(run_at):
                    run_at = timezone.localtime(run_at)
                self.fields["run_at"].initial = run_at
            elif schedule_type in {AutomationTrigger.ScheduleType.DAILY, AutomationTrigger.ScheduleType.WEEKLY}:
                self.fields["time_of_day"].initial = config.get("time")
                self.fields["weekdays"].initial = [str(day) for day in config.get("weekdays", [])] or [str(day) for day in range(7)]
            elif schedule_type == AutomationTrigger.ScheduleType.INTERVAL:
                self.fields["interval_every"].initial = config.get("every")
                self.fields["interval_unit"].initial = config.get("unit")
        elif trigger_type == AutomationTrigger.TriggerType.MQTT_EVENT:
            self.fields["event_topic"].initial = config.get("topic")
            legacy_config = {key: config.get(key) for key in ("field", "operator", "value") if key in config}
            if legacy_config:
                self.fields["legacy_event_config"].initial = json.dumps(legacy_config, ensure_ascii=False)
        elif trigger_type == AutomationTrigger.TriggerType.DEVICE_STATE:
            device_id = config.get("device_id")
            if device_id:
                self.fields["state_device"].initial = device_id
            elif config.get("device_uid"):
                device = Device.objects.filter(device_uid=config.get("device_uid")).first()
                if device is not None:
                    self.fields["state_device"].initial = device.pk

    def clean(self):
        cleaned = super().clean()
        trigger_type = cleaned.get("trigger_type")

        # Current trigger-set UI does not submit a trigger_type.  The source
        # events are inferred from the conditions owned by the set.
        cleaned["condition_operator"] = (
            cleaned.get("condition_operator")
            or AutomationTrigger.ConditionOperator.AND
        )
        if not trigger_type or trigger_type == AutomationTrigger.TriggerType.SET:
            cleaned["trigger_type"] = AutomationTrigger.TriggerType.SET
            cleaned["config"] = {}
            return cleaned

        # Legacy parser retained for old requests/tests.
        if trigger_type == AutomationTrigger.TriggerType.TIME:
            schedule_type = cleaned.get("schedule_type")
            if not schedule_type:
                self._ignore_incomplete(cleaned)
            elif schedule_type == AutomationTrigger.ScheduleType.ONCE:
                run_at = cleaned.get("run_at")
                if run_at is None:
                    self._ignore_incomplete(cleaned)
                else:
                    cleaned["config"] = {"schedule_type": schedule_type, "run_at": run_at.isoformat()}
            elif schedule_type == AutomationTrigger.ScheduleType.WEEKLY:
                run_time = cleaned.get("time_of_day")
                weekdays = cleaned.get("weekdays") or []
                if run_time is None and not weekdays:
                    self._ignore_incomplete(cleaned)
                else:
                    if run_time is None:
                        self.add_error("time_of_day", "실행 시간을 입력하세요.")
                    if not weekdays:
                        self.add_error("weekdays", "요일을 하나 이상 선택하세요.")
                    if run_time is not None and weekdays:
                        cleaned["config"] = {"schedule_type": schedule_type, "time": run_time.strftime("%H:%M"), "weekdays": [int(day) for day in weekdays]}
            elif schedule_type == AutomationTrigger.ScheduleType.INTERVAL:
                every = cleaned.get("interval_every")
                unit = cleaned.get("interval_unit")
                if every is None and not unit:
                    self._ignore_incomplete(cleaned)
                else:
                    if every is None:
                        self.add_error("interval_every", "실행 간격을 입력하세요.")
                    if not unit:
                        self.add_error("interval_unit", "간격 단위를 선택하세요.")
                    if every is not None and unit:
                        cleaned["config"] = {"schedule_type": schedule_type, "every": every, "unit": unit}
        elif trigger_type == AutomationTrigger.TriggerType.MQTT_EVENT:
            topic = str(cleaned.get("event_topic") or "").strip()
            if not topic:
                self._ignore_incomplete(cleaned)
            else:
                config = {"topic": topic}
                raw_legacy = str(cleaned.get("legacy_event_config") or "").strip()
                if raw_legacy:
                    try:
                        legacy_config = json.loads(raw_legacy)
                    except json.JSONDecodeError:
                        legacy_config = {}
                    if isinstance(legacy_config, dict):
                        for key in ("field", "operator", "value"):
                            if key in legacy_config:
                                config[key] = legacy_config[key]
                cleaned["config"] = config
        elif trigger_type == AutomationTrigger.TriggerType.DEVICE_STATE:
            device = cleaned.get("state_device")
            if device is None:
                self._ignore_incomplete(cleaned)
            else:
                cleaned["config"] = {"device_id": device.pk, "device_uid": device.device_uid, "device_name": device.name}
        return cleaned

    def _post_clean(self):
        if getattr(self, "_skip_model_validation", False):
            return
        super()._post_clean()

    def _ignore_incomplete(self, cleaned):
        self._skip_model_validation = True
        cleaned["trigger_type"] = None
        cleaned["config"] = {}

    def save(self, commit=True):
        trigger = super().save(commit=False)
        trigger.trigger_type = self.cleaned_data.get("trigger_type") or AutomationTrigger.TriggerType.SET
        trigger.config = self.cleaned_data.get("config") or {}
        trigger.next_run_at = None
        if commit:
            trigger.save()
            from .scheduler.service import AutomationService
            AutomationService.recalculate_trigger(trigger)
        return trigger


class BaseAutomationTriggerFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        active_forms = [
            form for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and (
                form.cleaned_data.get("trigger_type")
                or form.cleaned_data.get("condition_operator")
            )
        ]
        if not active_forms:
            raise forms.ValidationError("트리거 세트를 하나 이상 등록하세요.")


class AutomationConditionForm(forms.ModelForm):
    trigger_index = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput())
    # Backward-compatible hidden field; new editor uses trigger_index.
    action_index = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput())

    OPERATOR_CHOICES = [
        ("eq", "값이 같음"), ("ne", "값이 다름"), ("gt", "보다 큼"),
        ("gte", "이상"), ("lt", "보다 작음"), ("lte", "이하"),
        ("changed", "값이 변경됨"), ("changed_to", "지정한 값으로 변경됨"),
    ]
    MQTT_OPERATOR_CHOICES = [("received", "메시지를 수신함")] + OPERATOR_CHOICES
    WEEKDAY_CHOICES = AutomationTriggerForm.WEEKDAY_CHOICES
    INTERVAL_UNIT_CHOICES = AutomationTriggerForm.INTERVAL_UNIT_CHOICES
    TIME_SCHEDULE_CHOICES = AutomationTriggerForm.TIME_SCHEDULE_CHOICES

    schedule_type = forms.ChoiceField(required=False, label="반복 방식", choices=TIME_SCHEDULE_CHOICES)
    run_at = forms.DateTimeField(
        required=False,
        label="실행 시각",
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    time_of_day = forms.TimeField(required=False, label="실행 시간", widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}))
    weekdays = forms.MultipleChoiceField(required=False, label="요일", choices=WEEKDAY_CHOICES, widget=forms.CheckboxSelectMultiple)
    interval_every = forms.IntegerField(required=False, min_value=1, label="실행 간격")
    interval_unit = forms.ChoiceField(required=False, label="간격 단위", choices=INTERVAL_UNIT_CHOICES)

    time_start = forms.TimeField(required=False, label="시작 시간", widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}))
    time_end = forms.TimeField(required=False, label="종료 시간", widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}))

    state_device = forms.ModelChoiceField(queryset=Device.objects.none(), required=False, label="기기")
    state_key = forms.CharField(required=False, label="상태 필드", help_text="예: power, temperature, humidity, state")
    state_operator = forms.ChoiceField(required=False, label="비교 기준", choices=OPERATOR_CHOICES, initial="eq")
    state_value = forms.CharField(required=False, label="비교 값", help_text="true, false, 숫자는 JSON 값으로 해석됩니다.")

    mqtt_topic = forms.CharField(required=False, label="MQTT 토픽")
    mqtt_field = forms.CharField(required=False, label="데이터 필드", initial="value", help_text="중첩 필드는 점으로 구분합니다. 예: action.contact")
    mqtt_operator = forms.ChoiceField(required=False, label="비교 기준", choices=MQTT_OPERATOR_CHOICES, initial="received")
    mqtt_value = forms.CharField(required=False, label="비교 값", help_text="true, false, 숫자는 JSON 값으로 해석됩니다.")

    # Legacy event-value widgets remain parseable but are not offered for new
    # sets.  0020 migrates normal MQTT-backed rows to MQTT_EVENT.
    event_field = forms.CharField(required=False, label="데이터 필드", initial="value")
    event_operator = forms.ChoiceField(required=False, label="비교 기준", choices=OPERATOR_CHOICES, initial="eq")
    event_value = forms.CharField(required=False, label="비교 값")

    MODERN_CONDITION_CHOICES = [
        (AutomationCondition.ConditionType.SCHEDULE, "예약 시간"),
        (AutomationCondition.ConditionType.TIME_WINDOW, "시간대"),
        (AutomationCondition.ConditionType.DEVICE_STATE, "기기 상태"),
        (AutomationCondition.ConditionType.MQTT_EVENT, "MQTT 이벤트"),
    ]
    condition_type = forms.ChoiceField(
        required=False,
        label="조건 종류",
        choices=MODERN_CONDITION_CHOICES,
    )

    class Meta:
        model = AutomationCondition
        fields = ["condition_type"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["state_device"].queryset = Device.objects.all().order_by("location", "name", "id")
        config = self.instance.config if self.instance.pk else {}
        condition_type = self.instance.condition_type if self.instance.pk else None
        posted_condition_type = None
        if self.is_bound:
            posted_condition_type = self.data.get(self.add_prefix("condition_type"))
        if condition_type == AutomationCondition.ConditionType.EVENT_VALUE or posted_condition_type == AutomationCondition.ConditionType.EVENT_VALUE:
            self.fields["condition_type"].choices = [
                *self.MODERN_CONDITION_CHOICES,
                (AutomationCondition.ConditionType.EVENT_VALUE, "트리거 데이터 (기존)"),
            ]
        if condition_type == AutomationCondition.ConditionType.SCHEDULE:
            schedule_type = config.get("schedule_type")
            if schedule_type == AutomationTrigger.ScheduleType.DAILY:
                schedule_type = AutomationTrigger.ScheduleType.WEEKLY
            self.fields["schedule_type"].initial = schedule_type
            if schedule_type == AutomationTrigger.ScheduleType.ONCE:
                run_at = parse_datetime(str(config.get("run_at", "")))
                if run_at is not None and timezone.is_aware(run_at):
                    run_at = timezone.localtime(run_at)
                self.fields["run_at"].initial = run_at
            elif schedule_type in {AutomationTrigger.ScheduleType.DAILY, AutomationTrigger.ScheduleType.WEEKLY}:
                self.fields["time_of_day"].initial = config.get("time")
                self.fields["weekdays"].initial = [str(day) for day in config.get("weekdays", [])] or [str(day) for day in range(7)]
            elif schedule_type == AutomationTrigger.ScheduleType.INTERVAL:
                self.fields["interval_every"].initial = config.get("every")
                self.fields["interval_unit"].initial = config.get("unit")
        elif condition_type == AutomationCondition.ConditionType.TIME_WINDOW:
            self.fields["time_start"].initial = config.get("start")
            self.fields["time_end"].initial = config.get("end")
        elif condition_type == AutomationCondition.ConditionType.DEVICE_STATE:
            device_id = config.get("device_id")
            if not device_id and config.get("device_uid"):
                device = Device.objects.filter(device_uid=config.get("device_uid")).first()
                device_id = device.pk if device is not None else None
            if device_id:
                self.fields["state_device"].initial = device_id
            self.fields["state_key"].initial = config.get("key")
            self.fields["state_operator"].initial = config.get("operator")
            self.fields["state_value"].initial = self._format_initial_value(config.get("value"))
        elif condition_type == AutomationCondition.ConditionType.MQTT_EVENT:
            self.fields["mqtt_topic"].initial = config.get("topic")
            self.fields["mqtt_field"].initial = config.get("field") or "value"
            self.fields["mqtt_operator"].initial = config.get("operator") or "received"
            self.fields["mqtt_value"].initial = self._format_initial_value(config.get("value"))
        elif condition_type == AutomationCondition.ConditionType.EVENT_VALUE:
            self.fields["event_field"].initial = config.get("field")
            self.fields["event_operator"].initial = config.get("operator")
            self.fields["event_value"].initial = self._format_initial_value(config.get("value"))

    @staticmethod
    def _format_initial_value(value):
        if value is None:
            return ""
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _parse_value(raw_value):
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return raw_value

    def clean(self):
        cleaned = super().clean()
        condition_type = cleaned.get("condition_type")

        if condition_type == AutomationCondition.ConditionType.SCHEDULE:
            schedule_type = cleaned.get("schedule_type")
            if not schedule_type:
                self._ignore_incomplete(cleaned)
            elif schedule_type == AutomationTrigger.ScheduleType.ONCE:
                run_at = cleaned.get("run_at")
                if run_at is None:
                    self.add_error("run_at", "실행 시각을 입력하세요.")
                else:
                    cleaned["config"] = {"schedule_type": schedule_type, "run_at": run_at.isoformat()}
            elif schedule_type == AutomationTrigger.ScheduleType.WEEKLY:
                run_time = cleaned.get("time_of_day")
                weekdays = cleaned.get("weekdays") or []
                if run_time is None:
                    self.add_error("time_of_day", "실행 시간을 입력하세요.")
                if not weekdays:
                    self.add_error("weekdays", "요일을 하나 이상 선택하세요.")
                if run_time is not None and weekdays:
                    cleaned["config"] = {"schedule_type": schedule_type, "time": run_time.strftime("%H:%M"), "weekdays": [int(day) for day in weekdays]}
            elif schedule_type == AutomationTrigger.ScheduleType.INTERVAL:
                every = cleaned.get("interval_every")
                unit = cleaned.get("interval_unit")
                if every is None:
                    self.add_error("interval_every", "실행 간격을 입력하세요.")
                if not unit:
                    self.add_error("interval_unit", "간격 단위를 선택하세요.")
                if every is not None and unit:
                    cleaned["config"] = {"schedule_type": schedule_type, "every": every, "unit": unit}

        elif condition_type == AutomationCondition.ConditionType.TIME_WINDOW:
            start = cleaned.get("time_start")
            end = cleaned.get("time_end")
            if start is None and end is None:
                self._ignore_incomplete(cleaned)
            else:
                if start is None:
                    self.add_error("time_start", "시작 시간을 입력하세요.")
                if end is None:
                    self.add_error("time_end", "종료 시간을 입력하세요.")
                if start is not None and end is not None:
                    cleaned["config"] = {"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")}

        elif condition_type == AutomationCondition.ConditionType.DEVICE_STATE:
            device = cleaned.get("state_device")
            key = str(cleaned.get("state_key") or "").strip()
            operator = cleaned.get("state_operator") or "eq"
            raw_value = str(cleaned.get("state_value") or "").strip()
            legacy_topic = str((self.instance.config or {}).get("topic") or "").strip() if self.instance.pk else ""
            if device is None and not legacy_topic and not key and raw_value == "":
                self._ignore_incomplete(cleaned)
            else:
                if device is None and not legacy_topic:
                    self.add_error("state_device", "기기를 선택하세요.")
                if not key:
                    self.add_error("state_key", "상태 필드를 입력하세요.")
                if key == "*" and operator != "changed":
                    self.add_error("state_operator", "상태 필드 * 는 '값이 변경됨' 기준에서만 사용할 수 있습니다.")
                if operator != "changed" and raw_value == "":
                    self.add_error("state_value", "비교 값을 입력하세요.")
            if (device is not None or legacy_topic) and key and (operator == "changed" or raw_value != ""):
                config = {"key": key, "operator": operator, "value": None if operator == "changed" else self._parse_value(raw_value)}
                if device is not None:
                    config.update({"device_id": device.pk, "device_uid": device.device_uid, "device_name": device.name})
                else:
                    config["topic"] = legacy_topic
                cleaned["config"] = config

        elif condition_type == AutomationCondition.ConditionType.MQTT_EVENT:
            topic = str(cleaned.get("mqtt_topic") or "").strip()
            field = str(cleaned.get("mqtt_field") or "value").strip()
            operator = cleaned.get("mqtt_operator") or "received"
            raw_value = str(cleaned.get("mqtt_value") or "").strip()
            if not topic:
                self.add_error("mqtt_topic", "MQTT 토픽을 입력하세요.")
            if operator != "received" and not field:
                self.add_error("mqtt_field", "데이터 필드를 입력하세요.")
            if operator not in {"received", "changed"} and raw_value == "":
                self.add_error("mqtt_value", "비교 값을 입력하세요.")
            if topic and (operator == "received" or field):
                cleaned["config"] = {
                    "topic": topic,
                    "field": field or "value",
                    "operator": operator,
                    "value": None if operator in {"received", "changed"} else self._parse_value(raw_value),
                }

        elif condition_type == AutomationCondition.ConditionType.EVENT_VALUE:
            field = str(cleaned.get("event_field") or "value").strip()
            operator = cleaned.get("event_operator") or "eq"
            raw_value = str(cleaned.get("event_value") or "").strip()
            if not field:
                self.add_error("event_field", "데이터 필드를 입력하세요.")
            if operator != "changed" and raw_value == "":
                self.add_error("event_value", "비교 값을 입력하세요.")
            if field and (operator == "changed" or raw_value != ""):
                cleaned["config"] = {"field": field, "operator": operator, "value": None if operator == "changed" else self._parse_value(raw_value)}
        return cleaned

    def _post_clean(self):
        if getattr(self, "_skip_model_validation", False):
            return
        super()._post_clean()

    def _ignore_incomplete(self, cleaned):
        self._skip_model_validation = True
        cleaned["condition_type"] = None
        cleaned["config"] = {}

    def save(self, commit=True):
        condition = super().save(commit=False)
        condition.config = self.cleaned_data["config"]
        if commit:
            condition.save()
        return condition


AutomationTriggerFormSet = inlineformset_factory(
    Automation,
    AutomationTrigger,
    form=AutomationTriggerForm,
    formset=BaseAutomationTriggerFormSet,
    extra=0,
    can_delete=True,
)

AutomationConditionFormSet = inlineformset_factory(
    Automation,
    AutomationCondition,
    form=AutomationConditionForm,
    extra=0,
    can_delete=True,
)


AutomationActionFormSet = inlineformset_factory(
    Automation,
    AutomationAction,
    form=AutomationActionForm,
    formset=BaseAutomationActionFormSet,
    extra=0,
    can_delete=True,
)
