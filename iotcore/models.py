from django.db import models

class Device(models.Model):
    """
    IoT 기기 정보 테이블 (예: 거실 에어컨, 안방 선풍기 등)
    """
    device_type = models.CharField(max_length=50)  # 예: aircon, electric_fan 등
    device_uid = models.CharField(max_length=100, unique=True)  # 시스템 내 고유 식별자
    name = models.CharField(max_length=100)  # 사용자에게 표시될 기기 이름
    location = models.CharField(max_length=100)  # 설치 위치

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
    sequence = models.ForeignKey(
        Sequence,
        on_delete=models.CASCADE,
        related_name="steps"
    )
    order = models.PositiveIntegerField()
    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE
    )
    function = models.CharField(max_length=50)
    parameter = models.JSONField(blank=True, null=True)
    delay = models.PositiveIntegerField(default=0)   # 다음 명령 전 대기(ms)

    class Meta:
        ordering = ["order"]
        # 아래는 같은 시퀀스 내에서 같은 데이터가 들어가는 걸 DB차원에서 방지.
        constraints = [
            models.UniqueConstraint(
                fields=["sequence", "order"],
                name="unique_sequence_order"
            )
        ]