import json

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import (
    Automation,
    AutomationCondition,
    AutomationTrigger,
    Controller,
    Device,
    Sequence,
    SequenceStep,
)


class DeviceForm(forms.ModelForm):
    """
    IoT 기기 정보 테이블 (예: 거실 에어컨, 안방 선풍기 등)
    """
    class Meta:
        model = Device                    # 사용할 모델
        fields = ['device_type', 'device_uid', 'name', 'location']     # QuestionForm에서 사용할 Question 모델의 속성


class ControllerForm(forms.ModelForm):
    """
    IoT 컨트롤러 정보 테이블 (ESP32 리모컨)
    - 1대의 컨트롤러는 오직 1대의 기기(Device)만 전담하여 제어합니다. (1:1 관계)
    - 기기가 아직 연결되지 않은 공석 상태를 위해 null=True, blank=True를 유지합니다.
    """
    class Meta:
        model = Controller                    # 사용할 모델
        fields = ['name', 'mac_address', 'ip_address', 'location', 'device']     # QuestionForm에서 사용할 Question 모델의 속성


class SequenceForm(forms.ModelForm):
    class Meta:
        model = Sequence                    # 사용할 모델
        fields = ['name', 'description']     # QuestionForm에서 사용할 Question 모델의 속성


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


class AutomationForm(forms.ModelForm):
    class Meta:
        model = Automation
        fields = [
            "name",
            "sequence",
            "enabled",
            "cooldown_seconds",
        ]
        labels = {
            "name": "이름",
            "sequence": "실행할 시퀀스",
            "enabled": "활성화",
            "cooldown_seconds": "재실행 제한(초)",
        }


class AutomationTriggerForm(forms.ModelForm):
    WEEKDAY_CHOICES = [
        ("0", "월"),
        ("1", "화"),
        ("2", "수"),
        ("3", "목"),
        ("4", "금"),
        ("5", "토"),
        ("6", "일"),
    ]
    INTERVAL_UNIT_CHOICES = [
        ("minutes", "분"),
        ("hours", "시간"),
        ("days", "일"),
    ]
    EVENT_OPERATOR_CHOICES = [
        ("eq", "값이 같음"),
        ("ne", "값이 다름"),
        ("changed", "값이 변경됨"),
        ("changed_to", "지정한 값으로 변경됨"),
    ]

    schedule_type = forms.ChoiceField(
        required=False,
        label="반복 방식",
        choices=AutomationTrigger.ScheduleType.choices,
    )

    run_at = forms.DateTimeField(
        required=False,
        label="실행 시각",
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
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
    interval_every = forms.IntegerField(
        required=False,
        min_value=1,
        label="실행 간격",
    )
    interval_unit = forms.ChoiceField(
        required=False,
        label="간격 단위",
        choices=INTERVAL_UNIT_CHOICES,
    )
    event_topic = forms.CharField(
        required=False,
        label="MQTT 토픽",
        help_text="예: zigbee2mqtt/front_door",
    )
    event_field = forms.CharField(
        required=False,
        label="데이터 필드",
        initial="contact",
        help_text="중첩 필드는 점으로 구분합니다. 예: action.contact",
    )
    event_operator = forms.ChoiceField(
        required=False,
        label="이벤트 조건",
        choices=EVENT_OPERATOR_CHOICES,
        initial="eq",
    )
    event_value = forms.CharField(
        required=False,
        label="비교 값",
        help_text='true, false, 숫자는 JSON 값으로 저장됩니다.',
    )

    class Meta:
        model = AutomationTrigger
        fields = ["trigger_type", "enabled"]
        labels = {
            "trigger_type": "실행 계기",
            "enabled": "이 계기 활성화",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = self.instance.config if self.instance.pk else {}
        trigger_type = self.instance.trigger_type if self.instance.pk else None
        if trigger_type == AutomationTrigger.TriggerType.TIME:
            schedule_type = config.get("schedule_type")
            self.fields["schedule_type"].initial = schedule_type
        else:
            schedule_type = None

        if schedule_type == AutomationTrigger.ScheduleType.ONCE:
            run_at = parse_datetime(str(config.get("run_at", "")))
            if run_at is not None and timezone.is_aware(run_at):
                run_at = timezone.localtime(run_at)
            self.fields["run_at"].initial = run_at
        elif schedule_type in {
            AutomationTrigger.ScheduleType.DAILY,
            AutomationTrigger.ScheduleType.WEEKLY,
        }:
            self.fields["time_of_day"].initial = config.get("time")
            self.fields["weekdays"].initial = [
                str(day) for day in config.get("weekdays", [])
            ]
        elif schedule_type == AutomationTrigger.ScheduleType.INTERVAL:
            self.fields["interval_every"].initial = config.get("every")
            self.fields["interval_unit"].initial = config.get("unit")
        elif trigger_type == AutomationTrigger.TriggerType.MQTT_EVENT:
            self.fields["event_topic"].initial = config.get("topic")
            self.fields["event_field"].initial = config.get("field")
            self.fields["event_operator"].initial = config.get("operator")
            value = config.get("value")
            self.fields["event_value"].initial = (
                value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            )

    def clean(self):
        cleaned = super().clean()
        trigger_type = cleaned.get("trigger_type")

        if trigger_type == AutomationTrigger.TriggerType.TIME:
            schedule_type = cleaned.get("schedule_type")
            if not schedule_type:
                self.add_error("schedule_type", "반복 방식을 선택하세요.")
            elif schedule_type == AutomationTrigger.ScheduleType.ONCE:
                run_at = cleaned.get("run_at")
                if run_at is None:
                    self.add_error("run_at", "실행 시각을 입력하세요.")
                elif cleaned.get("enabled") and run_at <= timezone.now():
                    self.add_error("run_at", "실행 시각은 현재보다 뒤여야 합니다.")
                else:
                    cleaned["config"] = {
                        "schedule_type": schedule_type,
                        "run_at": run_at.isoformat(),
                    }
            elif schedule_type == AutomationTrigger.ScheduleType.DAILY:
                run_time = cleaned.get("time_of_day")
                if run_time is None:
                    self.add_error("time_of_day", "실행 시간을 입력하세요.")
                else:
                    cleaned["config"] = {
                        "schedule_type": schedule_type,
                        "time": run_time.strftime("%H:%M"),
                    }
            elif schedule_type == AutomationTrigger.ScheduleType.WEEKLY:
                run_time = cleaned.get("time_of_day")
                weekdays = cleaned.get("weekdays") or []
                if run_time is None:
                    self.add_error("time_of_day", "실행 시간을 입력하세요.")
                if not weekdays:
                    self.add_error("weekdays", "요일을 하나 이상 선택하세요.")
                if run_time is not None and weekdays:
                    cleaned["config"] = {
                        "schedule_type": schedule_type,
                        "time": run_time.strftime("%H:%M"),
                        "weekdays": [int(day) for day in weekdays],
                    }
            elif schedule_type == AutomationTrigger.ScheduleType.INTERVAL:
                every = cleaned.get("interval_every")
                unit = cleaned.get("interval_unit")
                if every is None:
                    self.add_error("interval_every", "실행 간격을 입력하세요.")
                if not unit:
                    self.add_error("interval_unit", "간격 단위를 선택하세요.")
                if every is not None and unit:
                    cleaned["config"] = {
                        "schedule_type": schedule_type,
                        "every": every,
                        "unit": unit,
                    }
        elif trigger_type == AutomationTrigger.TriggerType.MQTT_EVENT:
            topic = str(cleaned.get("event_topic") or "").strip()
            field = str(cleaned.get("event_field") or "value").strip()
            operator = cleaned.get("event_operator") or "eq"
            raw_value = str(cleaned.get("event_value") or "").strip()
            if not topic:
                self.add_error("event_topic", "MQTT 토픽을 입력하세요.")
            if not field:
                self.add_error("event_field", "데이터 필드를 입력하세요.")
            if operator != "changed" and raw_value == "":
                self.add_error("event_value", "비교 값을 입력하세요.")
            if topic and field and (operator == "changed" or raw_value != ""):
                try:
                    value = json.loads(raw_value) if raw_value != "" else None
                except json.JSONDecodeError:
                    value = raw_value
                cleaned["config"] = {
                    "topic": topic,
                    "field": field,
                    "operator": operator,
                    "value": value,
                }

        return cleaned

    def save(self, commit=True):
        trigger = super().save(commit=False)
        trigger.config = self.cleaned_data["config"]
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
            form
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        if not active_forms:
            raise forms.ValidationError("실행 계기를 하나 이상 등록하세요.")


class AutomationConditionForm(forms.ModelForm):
    OPERATOR_CHOICES = [
        ("eq", "값이 같음"),
        ("ne", "값이 다름"),
    ]
    time_start = forms.TimeField(
        required=False,
        label="시작 시간",
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    time_end = forms.TimeField(
        required=False,
        label="종료 시간",
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    state_topic = forms.CharField(required=False, label="상태 토픽")
    state_key = forms.CharField(required=False, label="상태 필드")
    state_operator = forms.ChoiceField(
        required=False,
        label="비교 방식",
        choices=OPERATOR_CHOICES,
    )
    state_value = forms.CharField(required=False, label="비교 값")

    class Meta:
        model = AutomationCondition
        fields = ["condition_type"]
        labels = {"condition_type": "조건 종류"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = self.instance.config if self.instance.pk else {}
        if self.instance.condition_type == AutomationCondition.ConditionType.TIME_WINDOW:
            self.fields["time_start"].initial = config.get("start")
            self.fields["time_end"].initial = config.get("end")
        elif self.instance.condition_type == AutomationCondition.ConditionType.DEVICE_STATE:
            self.fields["state_topic"].initial = config.get("topic")
            self.fields["state_key"].initial = config.get("key")
            self.fields["state_operator"].initial = config.get("operator")
            value = config.get("value")
            self.fields["state_value"].initial = (
                value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            )

    def clean(self):
        cleaned = super().clean()
        condition_type = cleaned.get("condition_type")
        if condition_type == AutomationCondition.ConditionType.TIME_WINDOW:
            start = cleaned.get("time_start")
            end = cleaned.get("time_end")
            if start is None:
                self.add_error("time_start", "시작 시간을 입력하세요.")
            if end is None:
                self.add_error("time_end", "종료 시간을 입력하세요.")
            if start is not None and end is not None:
                cleaned["config"] = {
                    "start": start.strftime("%H:%M"),
                    "end": end.strftime("%H:%M"),
                }
        elif condition_type == AutomationCondition.ConditionType.DEVICE_STATE:
            topic = str(cleaned.get("state_topic") or "").strip()
            key = str(cleaned.get("state_key") or "").strip()
            raw_value = str(cleaned.get("state_value") or "").strip()
            if not topic:
                self.add_error("state_topic", "상태 토픽을 입력하세요.")
            if not key:
                self.add_error("state_key", "상태 필드를 입력하세요.")
            if raw_value == "":
                self.add_error("state_value", "비교 값을 입력하세요.")
            if topic and key and raw_value != "":
                try:
                    value = json.loads(raw_value)
                except json.JSONDecodeError:
                    value = raw_value
                cleaned["config"] = {
                    "topic": topic,
                    "key": key,
                    "operator": cleaned.get("state_operator") or "eq",
                    "value": value,
                }
        return cleaned

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
    extra=1,
    can_delete=True,
)

AutomationConditionFormSet = inlineformset_factory(
    Automation,
    AutomationCondition,
    form=AutomationConditionForm,
    extra=1,
    can_delete=True,
)
