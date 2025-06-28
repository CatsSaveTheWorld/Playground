from django.db import models

# Create your models here.
class Controller(models.Model):
    name = models.CharField(max_length=100)
    mac_address = models.CharField(max_length=17, unique=True)
    ip_address = models.GenericIPAddressField()
    location = models.CharField(max_length=100, blank=True)

    class Meta:
        permissions = [
            ("can_control_iot_devices", "Can control IoT devices"),
        ]

class Device(models.Model):
    controller = models.ForeignKey(Controller, on_delete=models.CASCADE)
    device_type = models.CharField(max_length=50)
    name = models.CharField(max_length=100)
    device_id = models.CharField(max_length=100)