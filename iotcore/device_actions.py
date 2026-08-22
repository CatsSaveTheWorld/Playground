from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceAction:
    """
    장치가 수행할 수 있는 하나의 동작을 정의한다.
    """

    code: str                  # 내부 코드 (power_on)
    display_name: str          # 화면 표시용 이름 (전원 켜기)
    parameter_key: str | None = None   # 필요한 파라미터 이름 (temperature 등)

class DeviceActionRegistry:

    _ACTIONS = {
        "aircon": [
            DeviceAction(
                code="power_on",
                display_name="전원 켜기",
            ),
            DeviceAction(
                code="power_off",
                display_name="전원 끄기",
            ),
            DeviceAction(
                code="set_temp",
                display_name="온도 설정",
                parameter_key="temperature",
            ),
            DeviceAction(
                code="mode_auto",
                display_name="자동 모드",
            ),
            DeviceAction(
                code="mode_cool",
                display_name="냉방 모드",
            ),
            DeviceAction(
                code="mode_dehumidification",
                display_name="제습 모드",
            ),
            DeviceAction(
                code="mode_fan",
                display_name="송풍 모드",
            ),
        ],

        "fan": [
            DeviceAction(
                code="power_on",
                display_name="전원",
            ),
            DeviceAction(
                code="power_off",
                display_name="정지",
            ),
        ],

        "electric_fan": [
            DeviceAction(code="power_on", display_name="전원 켜기"),
            DeviceAction(code="power_off", display_name="전원 끄기"),
            DeviceAction(
                code="set_speed",
                display_name="풍속 설정 (1~100)",
                parameter_key="speed",
            ),
            DeviceAction(code="vertical_swing_on", display_name="상하 회전 켜기"),
            DeviceAction(code="vertical_swing_off", display_name="상하 회전 끄기"),
            DeviceAction(code="horizontal_swing_on", display_name="좌우 회전 켜기"),
            DeviceAction(code="horizontal_swing_off", display_name="좌우 회전 끄기"),
            DeviceAction(
                code="set_horizontal_angle",
                display_name="좌우 회전 각도 (30/60/90)",
                parameter_key="horizontal_angle",
            ),
            DeviceAction(code="beep_on", display_name="안내음 켜기"),
            DeviceAction(code="beep_off", display_name="안내음 끄기"),
        ],

        "pc": [
            DeviceAction(
                code="power_on",
                display_name="전원 켜기",
            ),
            DeviceAction(
                code="power_off",
                display_name="전원 끄기",
            ),
        ],

        "light": [
            DeviceAction(
                code="power_on",
                display_name="켜기",
            ),
            DeviceAction(
                code="power_off",
                display_name="끄기",
            ),
        ],

        "media_server": [
            DeviceAction(
                code="ytmusic.refresh_cookie",
                display_name="YouTube Music 쿠키 갱신",
            ),
        ],

        "projector": [
            DeviceAction(code="power", display_name="전원"),
            DeviceAction(code="external_input", display_name="External Input"),
            DeviceAction(code="home", display_name="홈"),
            DeviceAction(code="menu", display_name="메뉴"),
            DeviceAction(code="back", display_name="뒤로 가기"),
            DeviceAction(code="up", display_name="위"),
            DeviceAction(code="down", display_name="아래"),
            DeviceAction(code="left", display_name="왼쪽"),
            DeviceAction(code="right", display_name="오른쪽"),
            DeviceAction(code="ok", display_name="확인"),
            DeviceAction(code="volume_down", display_name="음량 내리기"),
            DeviceAction(code="mute", display_name="음소거"),
            DeviceAction(code="volume_up", display_name="음량 올리기"),
        ],

        "speaker": [
            DeviceAction(
                code="play_playlist",
                display_name="플레이 리스트 재생",
            ),
            DeviceAction(
                code="pause",
                display_name="현재 곡 일시정지",
            ),
            DeviceAction(
                code="play_next",
                display_name="다음 곡 재생",
            ),
            DeviceAction(
                code="adjust_music_volume",
                display_name="음량 설정",
            ),
            DeviceAction(
                code="activate_shuffle",
                display_name="셔플 활성화",
            ),
            DeviceAction(
                code="deactivate_shuffle",
                display_name="셔플 비활성화",
            ),
        ],
    }

    @classmethod
    def get_actions(cls, device_type: str) -> list[DeviceAction]:
        """
        device_type에 해당하는 Action 목록을 반환한다.
        """
        return cls._ACTIONS.get(device_type, [])
    
    @classmethod
    def get_display_name(cls, device_type, code):
        actions = cls.get_actions(device_type)

        for action in actions:
            if action.code == code:
                return action.display_name

        return code
