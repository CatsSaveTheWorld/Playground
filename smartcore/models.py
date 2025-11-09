from django.db import models


class Device(models.Model):
    """
    IoT 기기 정보 테이블
    - 여러 컨트롤러(ESP32)가 하나의 기기를 제어할 수 있음 (1:N 관계)
    - 예: 캐리어 벽걸이 에어컨, 선풍기 등
    """
    device_type = models.CharField(max_length=50)  # 예: aircon, electric_fan 등
    device_uid = models.CharField(max_length=100, unique=True)  # 시스템 내 고유 식별자
    name = models.CharField(max_length=100)  # 사용자에게 표시될 기기 이름
    location = models.CharField(max_length=100)  # 설치 위치 (예: 거실, 방 등)

    def __str__(self):
        return f"{self.name} ({self.device_type})"


class Controller(models.Model):
    """
    IoT 컨트롤러 정보 테이블
    - 각 ESP32 리모컨이 하나의 기기(Device)에 연결됨 (N:1 관계)
    """
    name = models.CharField(max_length=100)  # 컨트롤러 이름
    mac_address = models.CharField(max_length=17, unique=True)  # MAC 주소
    ip_address = models.GenericIPAddressField()  # IP 주소
    location = models.CharField(max_length=100, blank=True)  # 물리적 위치
    device = models.ForeignKey(  # 하나의 Device를 참조
        Device,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="controllers"
    )

    class Meta:
        permissions = [
            ("can_control_iot_devices", "Can control IoT devices"),
        ]

    def __str__(self):
        if self.device:
            return f"{self.name} - {self.device.name} ({self.location})"
        return f"{self.name} ({self.location})"