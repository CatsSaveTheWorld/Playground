from uuid import uuid4

import requests
from django.conf import settings


class MusicAssistantClient:

    @staticmethod
    def get_playlists(limit=100):
        """Music Assistant 라이브러리의 재생목록을 조회한다."""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return [], "재생목록 조회 개수가 올바르지 않습니다."

        if not 1 <= limit <= 500:
            return [], "재생목록 조회 개수는 1부터 500 사이여야 합니다."

        success, result = MusicAssistantClient._send_command(
            command="music/playlists/library_items",
            args={
                "limit": limit,
                "offset": 0,
                "order_by": "sort_name",
                "favorite": True,
            },
            action_name="재생목록 조회",
            return_result=True,
        )
        if not success:
            return [], result

        if not isinstance(result, list):
            return [], "Music Assistant 재생목록 응답 형식이 올바르지 않습니다."

        playlists = []
        for item in result:
            if not isinstance(item, dict):
                continue

            item_id = str(item.get("item_id") or "").strip()
            name = str(item.get("name") or "").strip()

            if not item_id or not name:
                continue

            playlists.append(
                {
                    "item_id": item_id,
                    "name": name,
                }
            )

        return playlists, None

    @staticmethod
    def resolve_player_id(player_id=None, player_name=None):
        """DB 식별자 또는 기기 이름으로 Music Assistant Player ID를 찾는다."""
        lookup_error = None

        if player_id:
            success, result = MusicAssistantClient._send_command(
                command="players/get",
                args={"player_id": str(player_id).strip()},
                action_name="플레이어 조회",
                return_result=True,
            )
            if success and isinstance(result, dict):
                resolved_id = str(result.get("player_id") or "").strip()
                if resolved_id:
                    return resolved_id, None
            elif not success:
                lookup_error = result

        if player_name:
            success, result = MusicAssistantClient._send_command(
                command="players/get_by_name",
                args={"name": str(player_name).strip()},
                action_name="플레이어 이름 조회",
                return_result=True,
            )
            if success and isinstance(result, dict):
                resolved_id = str(result.get("player_id") or "").strip()
                if resolved_id:
                    return resolved_id, None
            elif not success:
                lookup_error = result

        return None, (
            lookup_error
            or "Music Assistant에서 일치하는 플레이어를 찾을 수 없습니다."
        )

    @staticmethod
    def play_playlist(device_id, playlist_id):
        """재생목록으로 현재 큐를 교체하고 재생한다."""
        return MusicAssistantClient._play_media(
            device_id=device_id,
            media_id=playlist_id,
            media_type="playlist",
            media_name="재생목록",
        )

    @staticmethod
    def play_music(device_id, music_id):
        """지정한 곡으로 현재 큐를 교체하고 재생한다."""
        return MusicAssistantClient._play_media(
            device_id=device_id,
            media_id=music_id,
            media_type="track",
            media_name="음악",
        )

    @staticmethod
    def pause(device_id):
        """현재 플레이어 큐를 일시정지한다."""
        player_value, error = MusicAssistantClient._validate_id(
            device_id,
            "device_id",
        )
        if error:
            return False, error

        return MusicAssistantClient._send_command(
            command="player_queues/pause",
            args={"queue_id": player_value},
            action_name="일시정지",
        )

    @staticmethod
    def play_previous(device_id):
        """현재 플레이어 큐의 이전 곡을 재생한다."""
        player_value, error = MusicAssistantClient._validate_id(
            device_id,
            "device_id",
        )
        if error:
            return False, error

        return MusicAssistantClient._send_command(
            command="player_queues/previous",
            args={"queue_id": player_value},
            action_name="이전 곡 재생",
        )

    @staticmethod
    def resume(device_id):
        """일시정지된 현재 플레이어 큐의 재생을 재개한다."""
        player_value, error = MusicAssistantClient._validate_id(
            device_id,
            "device_id",
        )
        if error:
            return False, error

        return MusicAssistantClient._send_command(
            command="player_queues/resume",
            args={"queue_id": player_value},
            action_name="재생 재개",
        )

    @staticmethod
    def play_next(device_id):
        """현재 플레이어 큐의 다음 곡을 재생한다."""
        player_value, error = MusicAssistantClient._validate_id(
            device_id,
            "device_id",
        )
        if error:
            return False, error

        return MusicAssistantClient._send_command(
            command="player_queues/next",
            args={"queue_id": player_value},
            action_name="다음 곡 재생",
        )

    @staticmethod
    def set_volume(device_id, volume):
        """플레이어 음량을 0~100 사이의 정수로 설정한다."""
        player_value, error = MusicAssistantClient._validate_id(
            device_id,
            "device_id",
        )
        if error:
            return False, error

        if isinstance(volume, bool):
            return False, "음량은 0부터 100 사이의 정수여야 합니다."

        try:
            volume_number = float(str(volume).strip())
        except (TypeError, ValueError):
            return False, "음량은 0부터 100 사이의 정수여야 합니다."

        if not volume_number.is_integer():
            return False, "음량은 0부터 100 사이의 정수여야 합니다."

        volume_level = int(volume_number)
        if not 0 <= volume_level <= 100:
            return False, "음량은 0부터 100 사이여야 합니다."

        return MusicAssistantClient._send_command(
            command="players/cmd/volume_set",
            args={
                "player_id": player_value,
                "volume_level": volume_level,
            },
            action_name="음량 설정",
        )

    @staticmethod
    def set_shuffle(device_id, enabled):
        """현재 플레이어 큐의 셔플을 활성화하거나 비활성화한다."""
        player_value, error = MusicAssistantClient._validate_id(
            device_id,
            "device_id",
        )
        if error:
            return False, error

        if not isinstance(enabled, bool):
            return False, "셔플 설정값은 True 또는 False여야 합니다."

        return MusicAssistantClient._send_command(
            command="player_queues/shuffle",
            args={
                "queue_id": player_value,
                "shuffle_enabled": enabled,
            },
            action_name="셔플 설정",
        )

    @staticmethod
    def set_repeat(device_id, repeat_mode):
        """현재 플레이어 큐의 반복 재생 모드를 설정한다."""
        player_value, error = MusicAssistantClient._validate_id(
            device_id,
            "device_id",
        )
        if error:
            return False, error

        repeat_value = str(repeat_mode or "").strip().lower()
        if repeat_value not in {"off", "all", "one"}:
            return False, "반복 재생 모드는 off, all, one 중 하나여야 합니다."

        return MusicAssistantClient._send_command(
            command="player_queues/repeat",
            args={
                "queue_id": player_value,
                "repeat_mode": repeat_value,
            },
            action_name="반복 재생 설정",
        )

    @staticmethod
    def _play_media(device_id, media_id, media_type, media_name):
        player_value, error = MusicAssistantClient._validate_id(
            device_id,
            "device_id",
        )
        if error:
            return False, error

        media_value, error = MusicAssistantClient._validate_id(
            media_id,
            f"{media_type}_id",
        )
        if error:
            return False, error

        media_uri = (
            media_value
            if "://" in media_value
            else f"library://{media_type}/{media_value}"
        )

        return MusicAssistantClient._send_command(
            command="player_queues/play_media",
            args={
                "queue_id": player_value,
                "media": media_uri,
            },
            action_name=f"{media_name} 재생",
        )

    @staticmethod
    def _validate_id(value, field_name):
        if value is None:
            return None, f"Music Assistant {field_name} 값이 없습니다."

        value = str(value).strip()
        if not value:
            return None, f"Music Assistant {field_name} 값이 없습니다."

        return value, None

    @staticmethod
    def _send_command(command, args, action_name, return_result=False):
        server_url = str(
            getattr(settings, "MUSIC_ASSISTANT_URL", "")
        ).strip().rstrip("/")
        token = str(
            getattr(settings, "MUSIC_ASSISTANT_TOKEN", "")
        ).strip()
        timeout = getattr(settings, "MUSIC_ASSISTANT_TIMEOUT", 10)

        if not server_url:
            return False, "MUSIC_ASSISTANT_URL 설정이 없습니다."

        if not token:
            return False, "MUSIC_ASSISTANT_TOKEN 설정이 없습니다."

        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            return False, "MUSIC_ASSISTANT_TIMEOUT 설정이 올바르지 않습니다."

        if timeout <= 0:
            return False, "MUSIC_ASSISTANT_TIMEOUT 값은 0보다 커야 합니다."

        try:
            response = requests.post(
                f"{server_url}/api",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "message_id": uuid4().hex,
                    "command": command,
                    "args": args,
                },
                timeout=timeout,
            )
        except requests.exceptions.Timeout:
            return False, "Music Assistant 응답 시간이 초과되었습니다."
        except requests.exceptions.ConnectionError:
            return False, "Music Assistant 서버에 연결할 수 없습니다."
        except requests.exceptions.RequestException as error:
            return False, f"Music Assistant 요청 중 오류가 발생했습니다. ({error})"

        if not response.ok:
            error_detail = MusicAssistantClient._get_error_detail(response)
            return False, (
                f"Music Assistant {action_name} 요청에 실패했습니다. "
                f"(HTTP {response.status_code}: {error_detail})"
            )

        try:
            result = response.json()
        except ValueError:
            return False, "Music Assistant 응답을 JSON으로 해석할 수 없습니다."

        if isinstance(result, dict):
            error_code = result.get("error_code")
            error_message = result.get("error_message") or result.get("error")

            if (
                result.get("success") is False
                or error_code not in (None, 0)
                or error_message
            ):
                detail = (
                    error_message
                    or result.get("message")
                    or result.get("details")
                    or "알 수 없는 오류"
                )
                return False, (
                    f"Music Assistant {action_name}에 실패했습니다. ({detail})"
                )

        return True, result if return_result else None

    @staticmethod
    def _get_error_detail(response):
        try:
            error_data = response.json()
        except ValueError:
            error_data = {}

        if isinstance(error_data, dict):
            return (
                error_data.get("error_message")
                or error_data.get("message")
                or error_data.get("details")
                or error_data.get("error")
                or response.text.strip()
                or "알 수 없는 오류"
            )

        return response.text.strip() or "알 수 없는 오류"
