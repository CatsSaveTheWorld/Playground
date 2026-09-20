"""Canonical DeviceState definitions used by IoTCore.

DeviceState is intentionally a key/value table.  This module describes which
keys a known device type should have *before* the first real observation is
received.  ``default=None`` means "unknown", not OFF/False.

The metadata is kept outside ``models.py`` because these entries are data rows,
not database columns.  It can also be reused later by forms/API code to render
human-friendly state-key/value choices without asking users to type raw keys.
"""

from __future__ import annotations


# ``default`` is the only field required by the initializer.  ``label`` and
# ``value_type`` are deliberately included now so the Automation editor can use
# this same source of truth when its free-text state-key input is replaced by
# selects.
DEVICE_STATE_SCHEMA = {
    "aircon": {
        "power": {"default": None, "label": "전원 상태", "value_type": "boolean"},
        "mode": {"default": None, "label": "운전 모드", "value_type": "string"},
        "target_temperature": {"default": None, "label": "설정 온도", "value_type": "number"},
    },
    # Legacy IR fan type kept for compatibility with existing data/code.
    "fan": {
        "power": {"default": None, "label": "전원 상태", "value_type": "boolean"},
    },
    "electric_fan": {
        "power": {"default": None, "label": "전원 상태", "value_type": "boolean"},
        "speed": {"default": None, "label": "풍속", "value_type": "number"},
        "vertical_swing": {"default": None, "label": "상하 회전", "value_type": "boolean"},
        "horizontal_swing": {"default": None, "label": "좌우 회전", "value_type": "boolean"},
        "horizontal_angle": {"default": None, "label": "좌우 회전 각도", "value_type": "string"},
        "beep": {"default": None, "label": "안내음", "value_type": "boolean"},
    },
    "light": {
        "power": {"default": None, "label": "전원 상태", "value_type": "boolean"},
    },
    "projector": {
        # Zeus L1300 currently exposes one raw POWER toggle code, so the initial
        # value must stay unknown until IoTCore can infer/confirm ON or OFF.
        "power": {"default": None, "label": "전원 상태", "value_type": "boolean"},
    },
    "pc": {
        "power": {"default": None, "label": "전원 상태", "value_type": "boolean"},
        "online": {"default": None, "label": "기기 연결 상태", "value_type": "boolean"},
    },
    "speaker": {
        "playback_state": {"default": None, "label": "재생 상태", "value_type": "string"},
        "volume": {"default": None, "label": "음량", "value_type": "number"},
        "shuffle": {"default": None, "label": "셔플", "value_type": "boolean"},
        "repeat_mode": {"default": None, "label": "반복 모드", "value_type": "string"},
    },
    "media_server": {
        "online": {"default": None, "label": "기기 연결 상태", "value_type": "boolean"},
    },
    "media_node": {
        "online": {"default": None, "label": "기기 연결 상태", "value_type": "boolean"},
    },
    "window_pusher": {
        "online": {"default": None, "label": "기기 연결 상태", "value_type": "boolean"},
        "state": {"default": None, "label": "Zigbee 커버 상태", "value_type": "string"},
        "position": {"default": None, "label": "창문 위치", "value_type": "number"},
        "window_state": {"default": None, "label": "창문 개폐 상태", "value_type": "string"},
        "motion_direction": {"default": None, "label": "이동 방향", "value_type": "string"},
        "target_position": {"default": None, "label": "목표 위치", "value_type": "number"},
        "battery": {"default": None, "label": "배터리", "value_type": "number"},
        "charging": {"default": None, "label": "충전 상태", "value_type": "boolean"},
        "linkquality": {"default": None, "label": "링크 품질", "value_type": "number"},
        "automatic_mode": {"default": None, "label": "자동 보조 모드", "value_type": "string"},
        "slow_stop": {"default": None, "label": "저속 정지", "value_type": "string"},
        "button_position": {"default": None, "label": "물리 버튼 방향", "value_type": "string"},
        "last_command": {"default": None, "label": "마지막 제어 명령", "value_type": "string"},
    },
    "door_sensor": {
        "contact": {"default": None, "label": "문 접촉 상태", "value_type": "boolean"},
        "battery": {"default": None, "label": "배터리", "value_type": "number"},
        "linkquality": {"default": None, "label": "링크 품질", "value_type": "number"},
    },
    "motion_sensor": {
        "occupancy": {"default": None, "label": "움직임 감지", "value_type": "boolean"},
        "illuminance": {"default": None, "label": "조도", "value_type": "number"},
        "battery": {"default": None, "label": "배터리", "value_type": "number"},
        "linkquality": {"default": None, "label": "링크 품질", "value_type": "number"},
        "zone1": {"default": None, "label": "존 1", "value_type": "boolean"},
        "zone2": {"default": None, "label": "존 2", "value_type": "boolean"},
        "zone3": {"default": None, "label": "존 3", "value_type": "boolean"},
        "zone4": {"default": None, "label": "존 4", "value_type": "boolean"},
        "zone5": {"default": None, "label": "존 5", "value_type": "boolean"},
        "zone6": {"default": None, "label": "존 6", "value_type": "boolean"},
        "zone7": {"default": None, "label": "존 7", "value_type": "boolean"},
    },
    # Existing code/tests use more than one historical name for this sensor.
    "temp_humidity": {
        "temperature": {"default": None, "label": "온도", "value_type": "number"},
        "humidity": {"default": None, "label": "습도", "value_type": "number"},
        "pressure": {"default": None, "label": "기압", "value_type": "number"},
        "battery": {"default": None, "label": "배터리", "value_type": "number"},
        "linkquality": {"default": None, "label": "링크 품질", "value_type": "number"},
    },
    "temperature_humidity": {
        "temperature": {"default": None, "label": "온도", "value_type": "number"},
        "humidity": {"default": None, "label": "습도", "value_type": "number"},
        "pressure": {"default": None, "label": "기압", "value_type": "number"},
        "battery": {"default": None, "label": "배터리", "value_type": "number"},
        "linkquality": {"default": None, "label": "링크 품질", "value_type": "number"},
    },
    "temperature_humidity_sensor": {
        "temperature": {"default": None, "label": "온도", "value_type": "number"},
        "humidity": {"default": None, "label": "습도", "value_type": "number"},
        "pressure": {"default": None, "label": "기압", "value_type": "number"},
        "battery": {"default": None, "label": "배터리", "value_type": "number"},
        "linkquality": {"default": None, "label": "링크 품질", "value_type": "number"},
    },
}


# These belong to the dedicated Controller, not to the physical Device itself.
# They are merged only when a Controller is actually assigned to the Device.
CONTROLLER_STATE_SCHEMA = {
    "controller_online": {
        "default": None,
        "label": "컨트롤러 연결 상태",
        "value_type": "boolean",
    },
    "controller_last_command": {
        "default": None,
        "label": "컨트롤러 마지막 수신 명령",
        "value_type": "string",
    },
}


# If a Device has an explicit LAN endpoint in control_config, reachability.py can
# observe it even when the device type is not listed above.
NETWORK_ENDPOINT_KEYS = ("status_ip", "ip_address", "host")
NETWORK_STATE_SCHEMA = {
    "online": {
        "default": None,
        "label": "기기 연결 상태",
        "value_type": "boolean",
    },
}
