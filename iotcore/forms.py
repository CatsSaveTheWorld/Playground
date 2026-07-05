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
    class Meta:
        model = SequenceStep                    # 사용할 모델
        fields = ['device', 'delay']     # QuestionForm에서 사용할 Question 모델의 속성
    widgets = {
        "delay": forms.NumberInput(
            attrs={
                "min": 0,
                "placeholder": "0 (분)"
            }
        ),
    }
