from ...manual_control_history import record_manual_control
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.cache import never_cache
from .common import parse_request_data
from ...device.repositories.device_repository import DeviceRepository
from ...device.services.device_service import DeviceService
from ...infrastructure.music_assistant.client import MusicAssistantClient


REPEAT_MODES = {"off", "all", "one"}


@login_required(login_url="common:login")
@require_GET
@never_cache
def speaker_snapshot(request, device_id):
    """Read-only data for the global controller; never dispatch a device action."""
    device = DeviceRepository.get_by_id(device_id)
    if not device:
        return JsonResponse({'success': False, 'message': '스피커를 찾을 수 없습니다.'}, status=404)
    if device.device_type != 'speaker' or not device.is_controllable:
        return JsonResponse({'success': False, 'message': '제어 가능한 스피커가 아닙니다.'}, status=400)
    if request.GET.get('section') == 'playlists':
        playlists, error = MusicAssistantClient.get_playlists()
        return JsonResponse({'success': not bool(error), 'playlists': playlists, 'message': error or ''},
                            status=502 if error else 200)

    player_id, error = MusicAssistantClient.resolve_player_id(player_id=device.device_uid, player_name=device.name)
    if not player_id:
        return JsonResponse({'success': False, 'message': error or '플레이어를 찾을 수 없습니다.'}, status=502)
    player, error = MusicAssistantClient.get_player_state(player_id)
    if error:
        return JsonResponse({'success': False, 'message': error}, status=502)
    queue, queue_error = MusicAssistantClient.get_queue_state(player_id)
    queue = queue or {}
    item = queue.get('current_item') or {}
    media = item.get('media_item') or {}
    return JsonResponse({'success': True, 'state': {
        'available': player.get('available'),
        'playback': queue.get('state') or player.get('state') or 'unknown',
        'track': media.get('name') or item.get('name') or '',
        'volume': player.get('volume_level'),
        'shuffle': queue.get('shuffle_enabled'),
        'repeat': queue.get('repeat_mode'),
    }, 'message': queue_error or ''})

motion_messages = {
    "play_playlist": "플레이 리스트를 재생합니다!", 
    "play_music": "현재 곡을 재생합니다!", 
    "play_previous": "이전 곡을 재생합니다!",
    "resume": "현재 곡 재생을 재개합니다!",
    "pause": "현재 곡을 일시정지합니다!",
    "play_next": "다음 곡을 재생합니다!",
    "adjust_music_volume": "음량이 설정되었습니다!",
    "activate_shuffle": "셔플이 활성화되었습니다!",
    "deactivate_shuffle": "셔플이 비활성화되었습니다!",
    "set_repeat": "반복 재생 모드가 설정되었습니다!",
}


# ────────────────────────────────
#  통합 제어 엔트리 (Form + JSON)
# ────────────────────────────────
def speaker_entry(request, **overrides):
    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "message": "POST 요청만 허용됩니다.",
            },
            status=400,
        )

    data = parse_request_data(request)
    data.update(
        {
            key: value
            for key, value in overrides.items()
            if value is not None
        }
    )
    # print(f"[DEBUG] data : {data}")

    device_id = data.get("device_id")
    motion = data.get("motion") or data.get("function")
    playlist_id = data.get("playlist_id")
    music_id = data.get("music_id")
    volume = data.get("volume")
    repeat_mode = data.get("repeat_mode")

    # print(f"[DEBUG] device_id : {device_id}")
    # print(f"[DEBUG] motion : {motion}")

    if not device_id:
        return JsonResponse(
            {
                "success": False,
                "message": "device_id 누락",
            },
            status=400,
        )

    if not motion:
        return JsonResponse(
            {
                "success": False,
                "message": "motion/function 값이 없습니다.",
            },
            status=400,
        )

    if motion == "play_playlist" and not playlist_id:
        return JsonResponse(
            {
                "success": False,
                "message": "playlist_id 값이 없습니다.",
            },
            status=400,
        )

    if motion == "play_music" and not music_id:
        return JsonResponse(
            {
                "success": False,
                "message": "music_id 값이 없습니다.",
            },
            status=400,
        )

    if motion == "adjust_music_volume" and volume in (None, ""):
        return JsonResponse(
            {
                "success": False,
                "message": "volume 값이 없습니다.",
            },
            status=400,
        )

    if motion == "set_repeat" and repeat_mode not in REPEAT_MODES:
        return JsonResponse(
            {
                "success": False,
                "message": "repeat_mode는 off, all, one 중 하나여야 합니다.",
            },
            status=400,
        )

    device = DeviceRepository.get_by_id(device_id)
    # print(f"[DEBUG] device : {device}")

    if not device:
        return JsonResponse(
            {
                "success": False,
                "message": f"존재하지 않는 device_id: {device_id}",
            },
            status=404,
        )

    if device.device_type != "speaker":
        return JsonResponse(
            {
                "success": False,
                "message": "스피커 기기가 아닙니다.",
            },
            status=400,
        )

    success_message = motion_messages.get(
        motion,
        "요청된 동작을 수행했습니다.",
    )
    # print(f"[DEBUG] success_message : {success_message}")

    success, message = record_manual_control(DeviceService.control,
        device_id=device.id,
        motion=motion,
        success_message=success_message,
        playlist_id=playlist_id,
        music_id=music_id,
        volume=volume,
        repeat_mode=repeat_mode,
    )

    return JsonResponse(
        {
            "success": success,
            "message": message,
        },
        status=200 if success else 400,
    )


# ────────────────────────────────
#  스피커 호환용 뷰 (기존 웹 요청 URL 유지)
# ────────────────────────────────
@login_required(login_url="common:login")
@require_POST
def speaker_play_playlist(request, device_id, playlist_id):
    return speaker_entry(
        request,
        device_id=device_id,
        playlist_id=playlist_id,
        motion="play_playlist",
    )


@login_required(login_url="common:login")
@require_POST
def speaker_play_music(request, device_id, music_id):
    return speaker_entry(
        request,
        device_id=device_id,
        music_id=music_id,
        motion="play_music",
    )


@login_required(login_url="common:login")
@require_POST
def speaker_play_previous(request, device_id):
    return speaker_entry(
        request,
        device_id=device_id,
        motion="play_previous",
    )


@login_required(login_url="common:login")
@require_POST
def speaker_resume(request, device_id):
    return speaker_entry(
        request,
        device_id=device_id,
        motion="resume",
    )


@login_required(login_url="common:login")
@require_POST
def speaker_pause(request, device_id):
    return speaker_entry(
        request,
        device_id=device_id,
        motion="pause",
    )


@login_required(login_url="common:login")
@require_POST
def speaker_play_next(request, device_id):
    return speaker_entry(
        request,
        device_id=device_id,
        motion="play_next",
    )


@login_required(login_url="common:login")
@require_POST
def speaker_adjust_music_volume(request, device_id):
    return speaker_entry(
        request,
        device_id=device_id,
        motion="adjust_music_volume",
    )


@login_required(login_url="common:login")
@require_POST
def speaker_shuffle_activate(request, device_id):
    return speaker_entry(
        request,
        device_id=device_id,
        motion="activate_shuffle",
    )

@login_required(login_url="common:login")
@require_POST
def speaker_shuffle_deactivate(request, device_id):
    return speaker_entry(
        request,
        device_id=device_id,
        motion="deactivate_shuffle",
    )


@login_required(login_url="common:login")
@require_POST
def speaker_set_repeat(request, device_id, repeat_mode):
    return speaker_entry(
        request,
        device_id=device_id,
        repeat_mode=repeat_mode,
        motion="set_repeat",
    )
