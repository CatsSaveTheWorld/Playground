from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from ...infrastructure.remote_tasks.client import RemoteTaskClient
from ...models import Device
from .common import parse_request_data


MEDIA_MOODS = (
    "우주",
    "사이버펑크",
    "판타지",
    "자연",
    "차분함",
    "몽환적",
)


def _get_media_server(device_id):
    return get_object_or_404(
        Device,
        id=device_id,
        device_type="media_server",
        device_role__in=[Device.Role.CONTROL, Device.Role.HYBRID],
    )


def _remote_result(device, action, parameters=None, timeout=12):
    if device.protocol != Device.Protocol.MQTT:
        return {
            "success": False,
            "message": (
                "미디어 서버는 MQTT 프로토콜로 등록되어야 합니다. "
                f"(현재 {device.protocol})"
            ),
        }
    return RemoteTaskClient.execute_result(
        action=action,
        parameters=parameters or {},
        agent_id=device.device_uid,
        timeout=timeout,
    )


@login_required(login_url="common:login")
def media_server_control(request, device_id):
    device = _get_media_server(device_id)
    return render(
        request,
        "iotcore/media_server_control.html",
        {
            "device": device,
            "media_moods": MEDIA_MOODS,
        },
    )


@login_required(login_url="common:login")
@require_GET
def media_server_videos(request, device_id):
    device = _get_media_server(device_id)
    result = _remote_result(device, "media.list_videos", timeout=10)

    if not result.get("success"):
        return JsonResponse(
            {
                "success": False,
                "message": result.get("message") or "영상 목록을 불러오지 못했습니다.",
                "videos": [],
            },
            status=502,
        )

    videos = result.get("videos")
    if not isinstance(videos, list):
        videos = []

    return JsonResponse(
        {
            "success": True,
            "message": result.get("message") or "영상 목록을 불러왔습니다.",
            "videos": videos,
            "now_playing": result.get("now_playing"),
        }
    )


@login_required(login_url="common:login")
@require_POST
def media_server_play_video(request, device_id):
    device = _get_media_server(device_id)
    data = parse_request_data(request)
    video_id = str(data.get("video_id") or "").strip()

    if not video_id:
        return JsonResponse(
            {"success": False, "message": "재생할 영상을 선택해주세요."},
            status=400,
        )

    result = _remote_result(
        device,
        "media.play_video",
        {"video_id": video_id},
    )
    return JsonResponse(
        {
            "success": bool(result.get("success")),
            "message": result.get("message") or "영상 재생 요청을 처리했습니다.",
            "now_playing": result.get("now_playing"),
        },
        status=200 if result.get("success") else 502,
    )


@login_required(login_url="common:login")
@require_POST
def media_server_stop(request, device_id):
    device = _get_media_server(device_id)
    result = _remote_result(device, "media.stop", timeout=8)
    return JsonResponse(
        {
            "success": bool(result.get("success")),
            "message": result.get("message") or "영상 정지 요청을 처리했습니다.",
        },
        status=200 if result.get("success") else 502,
    )


@login_required(login_url="common:login")
@require_POST
def media_server_play_mood(request, device_id):
    # 메타데이터(MediaAsset/MediaMood)가 아직 없으므로 UI/API 계약만 먼저 만든다.
    _get_media_server(device_id)
    data = parse_request_data(request)
    mood = str(data.get("mood") or "").strip()

    if mood and mood not in MEDIA_MOODS:
        return JsonResponse(
            {"success": False, "message": "지원하지 않는 분위기 항목입니다."},
            status=400,
        )

    return JsonResponse(
        {
            "success": False,
            "message": "아직 구현되지 않은 기능입니다.",
            "mood": mood,
        },
        status=501,
    )
