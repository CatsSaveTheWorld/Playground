from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from ...device.repositories.device_repository import DeviceRepository
from ...device.services.reachability_service import DeviceReachabilityService
from ...device.services.device_state_service import DeviceStateService
from ...device.services.window_pusher_service import WindowPusherService
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
    window_pusher_devices = [
        d
        for d in visible_devices
        if d.device_type == WindowPusherService.DEVICE_TYPE
        and d.protocol == Device.Protocol.ZIGBEE
    ]
    for device in window_pusher_devices:
        DeviceStateService.ensure_device_states(device)
    window_pusher_cards = [
        {"device": device, "state": WindowPusherService.snapshot(device)}
        for device in window_pusher_devices
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
        "window_pusher",
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
        "window_pusher_cards": window_pusher_cards,
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


@login_required(login_url="common:login")
def device_status(request):
    """Return fresh LAN reachability for Device Control status badges."""
    raw_ids = (request.GET.get("ids") or "").strip()
    device_ids = []
    if raw_ids:
        for value in raw_ids.split(","):
            try:
                device_ids.append(int(value))
            except (TypeError, ValueError):
                continue

    queryset = (
        DeviceRepository.get_controllable()
        .select_related("controller")
        .order_by("id")
    )
    if device_ids:
        queryset = queryset.filter(id__in=device_ids)

    devices = list(queryset)
    statuses = DeviceReachabilityService.check_many(devices)

    # Zigbee devices do not expose an IP endpoint that can be pinged.  Their
    # online state comes from Zigbee2MQTT's `<friendly_name>/availability`
    # topic and is mirrored into canonical DeviceState by the MQTT listener.
    for device in devices:
        if device.protocol != Device.Protocol.ZIGBEE:
            continue
        if statuses[device.id]["device"] is not None:
            continue
        online = DeviceStateService.get_value(device, "online")
        if isinstance(online, bool):
            statuses[device.id]["device"] = {
                "online": online,
                "host": f"zigbee2mqtt/{device.device_uid}",
                "method": "zigbee",
            }

    return JsonResponse({
        "devices": statuses,
        "poll_interval_ms": 10000,
    })
