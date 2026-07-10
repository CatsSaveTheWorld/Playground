from django import forms
from .models import Device, Controller, Sequence, SequenceStep


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