"""Model signals for derived DeviceState rows."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .device.services.device_state_service import DeviceStateService
from .models import Controller, Device


@receiver(post_save, sender=Device)
def ensure_device_state_rows(sender, instance, raw=False, **kwargs):
    # ``raw`` is used while loading fixtures; defer to normal runtime/migrations.
    if raw:
        return
    DeviceStateService.ensure_device_states(instance)


@receiver(post_save, sender=Controller)
def ensure_controller_state_rows(sender, instance, raw=False, **kwargs):
    if raw or not instance.device_id:
        return
    # A Controller can be assigned after the Device was created, so ensure the
    # controller-specific keys at Controller save time as well.
    DeviceStateService.ensure_device_states(instance.device)
