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
                code="mode_auto",
                display_name="냉방 모드",
            ),
            DeviceAction(
                code="mode_auto",
                display_name="제습 모드",
            ),
            DeviceAction(
                code="mode_auto",
                display_name="송풍 모드",
            ),
        ],

        "fan": [
            DeviceAction(
                code="power_cycle",
                display_name="전원",
            ),
            DeviceAction(
                code="stop",
                display_name="정지",
            ),
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