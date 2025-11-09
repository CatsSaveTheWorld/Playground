from django.db import models


class Device(models.Model):
    """
    IoT 기기 정보 테이블
    - device_uid: 시스템 내 고유 식별자 (외부 연동 시 사용 가능)
    - device_type: 기기 종류 (예: aircon, fan, light 등)
    - name: 사용자 표시용 기기 이름
    """
    device_type = models.CharField(max_length=50)
    device_uid = models.CharField(max_length=100, unique=True)
    controller = models.OneToOneField(
        "Controller", 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name="controlled_device"
    )
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.name} ({self.device_type})"


class Controller(models.Model):
    """
    IoT 컨트롤러 정보 테이블
    - 하나의 기기(Device)는 여러 컨트롤러와 연결 가능 (1:N)
    - 컨트롤러 삭제 시 기기 정보는 유지되며, 연결만 해제됨
    """
    name = models.CharField(max_length=100)
    mac_address = models.CharField(max_length=17, unique=True)
    ip_address = models.GenericIPAddressField()
    location = models.CharField(max_length=100, blank=True)
    device = models.ForeignKey(
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
