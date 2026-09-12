from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from ...device.repositories.device_repository import DeviceRepository
from ...infrastructure.music_assistant.client import MusicAssistantClient
from ...models import Controller, Device


@login_required(login_url="common:login")
def device_control(request):
    """Render controllable devices grouped by location and UI category.

    Device remains the single source of truth.  The view only derives
    presentation groups (computer/environment/lighting/media/etc.); it does
    not introduce a second grouping model or special-case PCs in the DB.
    """
    devices = list(
        DeviceRepository.get_controllable()
        .order_by("location", "name", "id")
    )

    locations = sorted(
        {device.location.strip() for device in devices if device.location.strip()}
    )
    requested_location = (request.GET.get("location") or "all").strip()
    selected_location = (
        requested_location if requested_location in locations else "all"
    )

    if selected_location == "all":
        visible_devices = devices
    else:
        visible_devices = [
            device for device in devices
            if device.location.strip() == selected_location
        ]

    visible_ids = [device.id for device in visible_devices]
    controllers = list(
        Controller.objects.filter(
            device_id__in=visible_ids,
            device__device_role__in=[Device.Role.CONTROL, Device.Role.HYBRID],
        )
        .select_related("device")
        .order_by("device__name", "id")
    )

    pc_devices = [d for d in visible_devices if d.device_type == "pc"]
    aircon_controllers = [
        controller
        for controller in controllers
        if controller.device and controller.device.device_type == "aircon"
    ]
    fan_devices = [
        d
        for d in visible_devices
        if d.device_type == "electric_fan" and d.protocol == Device.Protocol.TUYA
    ]
    light_devices = [d for d in visible_devices if d.device_type == "light"]
    projector_devices = [d for d in visible_devices if d.device_type == "projector"]
    media_server_devices = [
        d for d in visible_devices if d.device_type in {"media_server", "media_node"}
    ]
    speaker_devices = [d for d in visible_devices if d.device_type == "speaker"]

    rendered_types = {
        "pc",
        "aircon",
        "electric_fan",
        "light",
        "projector",
        "media_server",
        "media_node",
        "speaker",
    }
    other_devices = [
        d for d in visible_devices if d.device_type not in rendered_types
    ]

    playlists = []
    playlists_error = None
    if speaker_devices:
        playlists, playlists_error = MusicAssistantClient.get_playlists()

    context = {
        "devices": visible_devices,
        "locations": locations,
        "selected_location": selected_location,
        "pc_devices": pc_devices,
        "aircon_controllers": aircon_controllers,
        "fan_devices": fan_devices,
        "light_devices": light_devices,
        "projector_devices": projector_devices,
        "media_server_devices": media_server_devices,
        "speaker_devices": speaker_devices,
        "other_devices": other_devices,
        "playlists": playlists,
        "playlists_error": playlists_error,
    }
    return render(request, "iotcore/device_control.html", context)


# Legacy import compatibility. New code should use device_control.
detail_list = device_control
