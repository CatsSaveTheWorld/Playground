import json

from django import forms
from django.db.models import Q
from django.forms import modelformset_factory
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_time

from .models import Action, Automation, AutomationGroup, Controller, Device, Step, Trigger


class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = ["device_type", "device_role", "protocol", "device_uid", "name", "location"]


class ControllerForm(forms.ModelForm):
    class Meta:
        model = Controller
        fields = ["name", "mac_address", "ip_address", "location", "device"]


class AutomationGroupForm(forms.ModelForm):
    class Meta:
        model = AutomationGroup
        fields = ["name", "order"]
        labels = {"name": "그룹 이름", "order": "정렬 순서"}

    def clean_name(self):
        return self.cleaned_data["name"].strip()


class AutomationForm(forms.ModelForm):
    class Meta:
        model = Automation
        fields = [
            "name",
            "description",
            "automation_type",
            "group",
            "is_favorite",
            "enabled",
            "cooldown_seconds",
        ]
        labels = {
            "name": "이름",
            "description": "설명",
            "automation_type": "실행 방식",
            "group": "그룹",
            "is_favorite": "즐겨찾기",
            "enabled": "활성화",
            "cooldown_seconds": "재실행 대기 시간(초)",
        }
        help_texts = {
            "automation_type": (
                "Trigger 구성과 관계없이 사용자가 직접 결정합니다. "
                "즉시 실행은 직접 한 번 실행하고, 예약 실행은 IoTCore가 Trigger를 감시합니다."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["group"].queryset = AutomationGroup.objects.order_by("order", "name", "id")
        self.fields["group"].empty_label = "미분류"


class StepForm(forms.ModelForm):
    set_key = forms.CharField(required=False, widget=forms.HiddenInput())
    # Keep the familiar UI field name while the DB/API concept is trigger_operator.
    condition_operator = forms.ChoiceField(
        label="트리거 충족 방식",
        choices=Step.TriggerOperator.choices,
        required=False,
        initial=Step.TriggerOperator.AND,
    )

    class Meta:
        model = Step
        fields = ["enabled"]
        labels = {"enabled": "이 Step 활성화"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["condition_operator"].initial = self.instance.trigger_operator
            self.fields["set_key"].initial = f"step-{self.instance.pk}"
        elif not self.is_bound:
            self.fields["enabled"].initial = True

    def clean(self):
        cleaned = super().clean()
        cleaned["trigger_operator"] = cleaned.get("condition_operator") or Step.TriggerOperator.AND
        return cleaned


class TriggerForm(forms.ModelForm):
    WEEKDAY_CHOICES = [(str(i), label) for i, label in enumerate(["월", "화", "수", "목", "금", "토", "일"])]
    INTERVAL_UNIT_CHOICES = [("minutes", "분"), ("hours", "시간"), ("days", "일")]
    OPERATOR_CHOICES = [
        ("eq", "값이 같음"), ("ne", "값이 다름"), ("gt", "보다 큼"),
        ("gte", "이상"), ("lt", "보다 작음"), ("lte", "이하"),
        ("changed", "값이 변경됨"), ("changed_to", "지정한 값으로 변경됨"),
    ]
    MQTT_OPERATOR_CHOICES = [("received", "메시지를 수신함")] + OPERATOR_CHOICES
    WEATHER_OPERATOR_CHOICES = OPERATOR_CHOICES[:6]

    trigger_key = forms.CharField(required=False, widget=forms.HiddenInput())
    trigger_index = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput())
    action_index = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput())

    # Template compatibility: user-facing field is still labelled "실행 조건".
    condition_type = forms.ChoiceField(label="실행 트리거", choices=Trigger.Type.choices)
    schedule_type = forms.ChoiceField(
        required=False,
        label="반복 방식",
        choices=[
            (Trigger.ScheduleType.ONCE, "한 번 실행"),
            (Trigger.ScheduleType.WEEKLY, "요일 선택 반복"),
            (Trigger.ScheduleType.INTERVAL, "일정 간격"),
        ],
    )
    run_at = forms.DateTimeField(
        required=False, label="실행 시각",
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    weekdays = forms.MultipleChoiceField(required=False, choices=WEEKDAY_CHOICES, widget=forms.CheckboxSelectMultiple)
    schedule_time_mode = forms.ChoiceField(
        required=False,
        choices=Trigger.ScheduleTimeMode.choices,
        initial=Trigger.ScheduleTimeMode.AT,
        widget=forms.RadioSelect,
    )
    time_of_day = forms.TimeField(required=False, widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}))
    interval_every = forms.IntegerField(required=False, min_value=1)
    interval_unit = forms.ChoiceField(required=False, choices=INTERVAL_UNIT_CHOICES)
    time_start = forms.TimeField(required=False, widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}))
    time_end = forms.TimeField(required=False, widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}))
    time_no_end = forms.BooleanField(required=False, label="종료 시각 없음")

    state_device = forms.ModelChoiceField(queryset=Device.objects.none(), required=False, label="기기")
    state_key = forms.CharField(required=False, label="상태 키")
    state_operator = forms.ChoiceField(required=False, choices=OPERATOR_CHOICES, label="비교")
    state_value = forms.CharField(required=False, label="값")

    mqtt_topic = forms.CharField(required=False, label="MQTT 토픽")
    mqtt_field = forms.CharField(required=False, label="필드", initial="value")
    mqtt_operator = forms.ChoiceField(required=False, choices=MQTT_OPERATOR_CHOICES, label="비교")
    mqtt_value = forms.CharField(required=False, label="값")

    weather_metric = forms.ChoiceField(
        required=False,
        choices=[("temperature", "현재 기온"), ("humidity", "현재 습도"), ("precipitation_probability", "강수 확률")],
        label="날씨 항목",
    )
    weather_operator = forms.ChoiceField(required=False, choices=WEATHER_OPERATOR_CHOICES, label="비교")
    weather_value = forms.FloatField(required=False, label="값")

    class Meta:
        model = Trigger
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["state_device"].queryset = Device.objects.order_by("location", "name", "id")
        if not self.instance.pk or self.is_bound:
            return
        self.fields["condition_type"].initial = self.instance.trigger_type
        config = self.instance.config or {}
        t = self.instance.trigger_type
        if t == Trigger.Type.SCHEDULE:
            self.fields["schedule_type"].initial = config.get("schedule_type")
            if config.get("schedule_type") == Trigger.ScheduleType.ONCE:
                value = parse_datetime(str(config.get("run_at") or ""))
                if value and timezone.is_aware(value):
                    value = timezone.localtime(value)
                self.fields["run_at"].initial = value
            elif config.get("schedule_type") == Trigger.ScheduleType.WEEKLY:
                self.fields["weekdays"].initial = [str(v) for v in config.get("weekdays", [])]
                mode = config.get("time_mode") or Trigger.ScheduleTimeMode.AT
                self.fields["schedule_time_mode"].initial = mode
                if mode == Trigger.ScheduleTimeMode.WINDOW:
                    self.fields["time_start"].initial = config.get("start")
                    self.fields["time_end"].initial = config.get("end")
                    self.fields["time_no_end"].initial = config.get("end") in (None, "")
                else:
                    self.fields["time_of_day"].initial = config.get("time")
            else:
                self.fields["interval_every"].initial = config.get("every")
                self.fields["interval_unit"].initial = config.get("unit")
        elif t == Trigger.Type.DEVICE_STATE:
            self.fields["state_device"].initial = config.get("device_id")
            self.fields["state_key"].initial = config.get("key")
            self.fields["state_operator"].initial = config.get("operator")
            self.fields["state_value"].initial = self._json_text(config.get("value"))
        elif t == Trigger.Type.MQTT_EVENT:
            self.fields["mqtt_topic"].initial = config.get("topic")
            self.fields["mqtt_field"].initial = config.get("field") or "value"
            self.fields["mqtt_operator"].initial = config.get("operator") or "received"
            self.fields["mqtt_value"].initial = self._json_text(config.get("value"))
        elif t == Trigger.Type.WEATHER:
            self.fields["weather_metric"].initial = config.get("metric")
            self.fields["weather_operator"].initial = config.get("operator")
            self.fields["weather_value"].initial = config.get("value")

    @staticmethod
    def _json_text(value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _parse_value(raw):
        raw = str(raw or "").strip()
        if raw == "":
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def clean(self):
        cleaned = super().clean()
        t = cleaned.get("condition_type")
        if not t:
            return cleaned
        config = {}
        if t == Trigger.Type.SCHEDULE:
            schedule_type = cleaned.get("schedule_type")
            if schedule_type == Trigger.ScheduleType.ONCE:
                run_at = cleaned.get("run_at")
                if run_at is None:
                    self.add_error("run_at", "실행 시각을 입력하세요.")
                else:
                    config = {"schedule_type": schedule_type, "run_at": run_at.isoformat()}
            elif schedule_type == Trigger.ScheduleType.WEEKLY:
                weekdays = cleaned.get("weekdays") or []
                if not weekdays:
                    self.add_error("weekdays", "요일을 하나 이상 선택하세요.")
                mode = cleaned.get("schedule_time_mode") or Trigger.ScheduleTimeMode.AT
                config = {"schedule_type": schedule_type, "weekdays": [int(v) for v in weekdays], "time_mode": mode}
                if mode == Trigger.ScheduleTimeMode.WINDOW:
                    start = cleaned.get("time_start")
                    if start is None:
                        self.add_error("time_start", "시작 시간을 입력하세요.")
                    config["start"] = start.strftime("%H:%M") if start else None
                    end = cleaned.get("time_end")
                    config["end"] = None if cleaned.get("time_no_end") else (end.strftime("%H:%M") if end else None)
                    if not cleaned.get("time_no_end") and end is None:
                        self.add_error("time_end", "종료 시간을 입력하거나 종료 시각 없음을 선택하세요.")
                else:
                    value = cleaned.get("time_of_day")
                    if value is None:
                        self.add_error("time_of_day", "실행 시간을 입력하세요.")
                    config["time"] = value.strftime("%H:%M") if value else None
            elif schedule_type == Trigger.ScheduleType.INTERVAL:
                every, unit = cleaned.get("interval_every"), cleaned.get("interval_unit")
                if every is None:
                    self.add_error("interval_every", "실행 간격을 입력하세요.")
                if not unit:
                    self.add_error("interval_unit", "간격 단위를 선택하세요.")
                config = {"schedule_type": schedule_type, "every": every, "unit": unit}
            else:
                self.add_error("schedule_type", "반복 방식을 선택하세요.")
        elif t == Trigger.Type.DEVICE_STATE:
            device = cleaned.get("state_device")
            key = str(cleaned.get("state_key") or "").strip()
            op = cleaned.get("state_operator")
            if device is None:
                self.add_error("state_device", "기기를 선택하세요.")
            if not key:
                self.add_error("state_key", "상태 키를 입력하세요.")
            if not op:
                self.add_error("state_operator", "비교 방식을 선택하세요.")
            config = {
                "device_id": device.pk if device else None,
                "device_uid": device.device_uid if device else None,
                "device_name": device.name if device else None,
                "key": key,
                "operator": op,
                "value": self._parse_value(cleaned.get("state_value")),
            }
        elif t == Trigger.Type.MQTT_EVENT:
            topic = str(cleaned.get("mqtt_topic") or "").strip()
            op = cleaned.get("mqtt_operator") or "received"
            if not topic:
                self.add_error("mqtt_topic", "MQTT 토픽을 입력하세요.")
            config = {
                "topic": topic,
                "field": str(cleaned.get("mqtt_field") or "value").strip() or "value",
                "operator": op,
                "value": self._parse_value(cleaned.get("mqtt_value")),
            }
        elif t == Trigger.Type.WEATHER:
            metric, op, value = cleaned.get("weather_metric"), cleaned.get("weather_operator"), cleaned.get("weather_value")
            if not metric:
                self.add_error("weather_metric", "날씨 항목을 선택하세요.")
            if not op:
                self.add_error("weather_operator", "비교 방식을 선택하세요.")
            if value is None:
                self.add_error("weather_value", "값을 입력하세요.")
            config = {"metric": metric, "operator": op, "value": value}
        cleaned["trigger_type"] = t
        cleaned["config"] = config
        return cleaned


class ActionForm(forms.ModelForm):
    function = forms.ChoiceField(required=False, label="동작", choices=[])
    trigger_key = forms.CharField(required=False, widget=forms.HiddenInput())
    trigger_index = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput())
    parameter_json = forms.CharField(required=False, label="파라미터(JSON)", widget=forms.Textarea(attrs={"rows": 2}))
    # Keep template field name "sequence" but it now points to another Automation.
    sequence = forms.ModelChoiceField(queryset=Automation.objects.none(), required=False, label="실행할 자동화")

    class Meta:
        model = Action
        fields = ["action_type", "device", "function", "delay", "delay_position"]
        labels = {
            "action_type": "실행 종류", "device": "기기", "function": "동작",
            "delay": "지연(초)", "delay_position": "지연 위치",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["device"].queryset = (
            Device.objects.filter(device_role__in=[Device.Role.CONTROL, Device.Role.HYBRID])
            .filter(~Q(device_type="electric_fan") | Q(protocol=Device.Protocol.TUYA))
            .order_by("location", "name", "id")
        )
        self.fields["sequence"].queryset = Automation.objects.order_by("name", "id")
        from .device_actions import DeviceActionRegistry
        action_choices = {}
        for device_type, definitions in DeviceActionRegistry._ACTIONS.items():
            for definition in definitions:
                action_choices[definition.code] = definition.display_name
        current_function = self.data.get(self.add_prefix("function")) if self.is_bound else getattr(self.instance, "function", "")
        if current_function and current_function not in action_choices:
            action_choices[current_function] = current_function
        self.fields["function"].choices = [("", "---------")] + sorted(action_choices.items(), key=lambda item: item[1])
        if self.instance.pk:
            self.fields["parameter_json"].initial = "" if self.instance.parameter is None else json.dumps(self.instance.parameter, ensure_ascii=False)
            self.fields["sequence"].initial = self.instance.target_automation_id

    def clean(self):
        cleaned = super().clean()
        action_type = cleaned.get("action_type")
        if action_type == Action.Type.AUTOMATION:
            target = cleaned.get("sequence")
            if target is None:
                self.add_error("sequence", "실행할 자동화를 선택하세요.")
            cleaned["target_automation"] = target
            cleaned["device"] = None
            cleaned["function"] = ""
            cleaned["parameter"] = None
            return cleaned

        device = cleaned.get("device")
        function = str(cleaned.get("function") or "").strip()
        if device is None:
            self.add_error("device", "기기를 선택하세요.")
        if not function:
            self.add_error("function", "동작을 선택하세요.")
        elif device is not None:
            from .device_actions import DeviceActionRegistry
            supported = {a.code for a in DeviceActionRegistry.get_actions(device.device_type)}
            if function not in supported:
                self.add_error("function", "선택한 기기에서 지원하지 않는 동작입니다.")
        raw = str(cleaned.get("parameter_json") or "").strip()
        if raw:
            try:
                cleaned["parameter"] = json.loads(raw)
            except json.JSONDecodeError:
                self.add_error("parameter_json", "올바른 JSON 형식으로 입력하세요.")
                cleaned["parameter"] = None
        else:
            cleaned["parameter"] = None

        # Keep Action payloads canonical before they reach the executor/AI API.
        # DeviceService remains the final safety boundary at execution time.
        if device is not None and device.device_type == "electric_fan" and function in {"set_speed", "set_horizontal_angle"}:
            from .device.services.device_service import DeviceService
            parameter = cleaned.get("parameter") or {}
            key = "speed" if function == "set_speed" else "horizontal_angle"
            value = parameter.get(key)
            command, error = DeviceService.prepare_electric_fan_command(function, value)
            if error:
                self.add_error("parameter_json", error)
            elif function == "set_speed":
                cleaned["parameter"] = {"speed": command[1]}
            else:
                cleaned["parameter"] = {"horizontal_angle": command[1]}

        if device is not None and device.device_type == "window_pusher" and function == "set_position":
            from .device.services.window_pusher_service import WindowPusherService
            parameter = cleaned.get("parameter") or {}
            try:
                position = WindowPusherService.parse_position(parameter.get("position"))
            except ValueError as exc:
                self.add_error("parameter_json", str(exc))
            else:
                cleaned["parameter"] = {"position": position}

        cleaned["target_automation"] = None
        return cleaned


StepFormSet = modelformset_factory(Step, form=StepForm, extra=0, can_delete=True)
TriggerFormSet = modelformset_factory(Trigger, form=TriggerForm, extra=0, can_delete=True)
ActionFormSet = modelformset_factory(Action, form=ActionForm, extra=0, can_delete=True)

