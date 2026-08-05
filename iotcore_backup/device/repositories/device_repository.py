from iotcore.models import Device

class DeviceRepository:
    
    @staticmethod
    def get_all():
        return Device.objects.all()

    @staticmethod
    def get_by_id(device_id):
        return Device.objects.filter(id=device_id).first()

    @staticmethod
    def get_by_uid(device_uid):
        return Device.objects.filter(device_uid=device_uid).first()