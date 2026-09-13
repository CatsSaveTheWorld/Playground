import requests
from django.conf import settings

from ..repositories.controller_repository import ControllerRepository
from ..repositories.ircode_repository import IRCodeRepository
from ...infrastructure.ir.client import IRClient
from ...device.repositories.device_repository import DeviceRepository
from django.http import JsonResponse
from ...infrastructure.zigbee.client import ZigbeeClient
from ...infrastructure.music_assistant.client import MusicAssistantClient
from ...infrastructure.remote_tasks.client import RemoteTaskClient
from ...infrastructure.tuya.client import TuyaClient
from ...infrastructure.wol.client import WOLClient


class DeviceService:

    @staticmethod
    def execute_step(step):
        """
        SequenceStep 하나를 실행한다.
        모든 실행의 공통 진입점.
        """
        device = step.device
        if device is None:
            return False, "시퀀스에 연결된 장치가 없습니다."

        parameters = step.parameter or {}
        if device.device_type == "aircon":
            return DeviceService.execute_aircon(step)
        if device.device_type == "fan":
            success, message = DeviceService.execute_fan(step)
            if success:
                DeviceService._record_control_state(
                    device,
                    step.function,
                    parameters,
                )
            return success, message
        if device.device_type == "electric_fan":
            fan_value = None
            if step.function == "set_speed":
                fan_value = parameters.get("speed")
            elif step.function == "set_horizontal_angle":
                fan_value = parameters.get("horizontal_angle")
            return DeviceService.control(
                device.id,
                step.function,
                fan_value=fan_value,
            )
        if device.device_type == "pc":
            return DeviceService.control(device.id, step.function)
        if device.device_type == "light":
            return DeviceService.control(device.id, step.function)
        if device.device_type == "media_server":
            return DeviceService.execute_media_server(step)
        if device.device_type == "projector":
            return DeviceService.execute_projector(step)
        if device.device_type == "speaker":
            return DeviceService.control(
                device.id,
                step.function,
                playlist_id=parameters.get("playlist_id"),
                music_id=parameters.get("music_id"),
                volume=parameters.get("volume"),
                repeat_mode=parameters.get("repeat_mode"),
            )

        return False, f"지원하지 않는 장치 타입입니다. ({device.device_type})"

    @staticmethod
    def execute_media_server(step):
        if step.device.protocol != "mqtt":
            return False, (
                "미디어 서버는 MQTT 프로토콜로 등록되어야 합니다. "
                f"(현재 {step.device.protocol})"
            )
        return RemoteTaskClient.execute(
            action=step.function,
            parameters=step.parameter or {},
            agent_id=step.device.device_uid,
        )

    @staticmethod
    def control(
        device_id,
        motion,
        success_message=None,
        bits=None,
        playlist_id=None,
        music_id=None,
        volume=None,
        repeat_mode=None,
        fan_value=None,
    ) -> tuple:

        device = DeviceRepository.get_by_id(device_id)
        # print(f"[DEBUG] control device : {device}")

        if not device:
            return False, "기기를 찾을 수 없습니다."

        success = False
        error_message = f"지원하지 않는 프로토콜입니다. ({device.protocol})"

        if device.device_type == 'pc':
            if device.protocol != 'tcpip':
                return False, (
                    "PC는 TCP/IP 프로토콜로 등록되어야 합니다. "
                    f"(현재 {device.protocol})"
                )
            success, error_message = DeviceService.execute_pc(
                device=device,
                motion=motion,
            )
            if success and not success_message:
                success_message = error_message

        elif device.device_type == 'aircon':
            if device.protocol == 'ir':
                controller = ControllerRepository.get_controller_by_device(device_id)
                # print(f"[DEBUG] control controller : {controller}")

                if not controller:
                    return False, "연결된 컨트롤러를 찾을 수 없습니다."

                success, error_message = DeviceService.execute_ir(
                    controller_id=controller.id,
                    motion=motion,
                    bits=bits,
                )
                # print(f"[DEBUG] control success : {success}")
                # print(f"[DEBUG] control error_message : {error_message}")

        elif device.device_type == 'light':
            if device.protocol == 'zigbee':
                # print(f"[DEBUG] device.protocol : zigbee")
                success, error_message = DeviceService.execute_light(
                    device_id=device_id,
                    motion=motion,
                )

        elif device.device_type == 'projector':
            if device.protocol == 'ir':
                controller = ControllerRepository.get_controller_by_device(device_id)

                if not controller:
                    return False, "연결된 컨트롤러를 찾을 수 없습니다."

                success, error_message = DeviceService.execute_projector_ir(
                    controller_id=controller.id,
                    motion=motion,
                )

        elif device.device_type == 'speaker':
            if device.protocol == 'tcpip':
                # print(f"[DEBUG] device.protocol : tcpip")
                success, error_message = DeviceService.execute_speaker(
                    device_id=device_id,
                    motion=motion,
                    playlist_id=playlist_id,
                    music_id=music_id,
                    volume=volume,
                    repeat_mode=repeat_mode,
                )

        elif device.device_type == 'electric_fan':
            if device.protocol != 'tuya':
                return False, (
                    "선풍기는 Tuya 프로토콜로 등록되어야 합니다. "
                    f"(현재 {device.protocol})"
                )
            success, error_message = DeviceService.execute_electric_fan(
                device=device,
                motion=motion,
                fan_value=fan_value,
            )

        else:
            return False, f"지원하지 않는 기기 종류입니다. ({device.device_type})"

        if success:
            DeviceService._record_control_state(
                device,
                motion,
                {
                    "bits": bits,
                    "playlist_id": playlist_id,
                    "music_id": music_id,
                    "volume": volume,
                    "repeat_mode": repeat_mode,
                    "fan_value": fan_value,
                },
            )
            return True, success_message or f"{device.name} 제어를 완료했습니다."

        return False, error_message
    


    @staticmethod
    def _record_control_state(device, motion, parameters=None):
        """Store last-known state after a successful control operation.

        For IR Devices, an HTTP 2xx response from the dedicated ESP32 means the
        controller received/handled the request, so keep that command separately
        from the physical Device state.  Physical state remains optimistic: for
        example Zeus L1300's raw ``power`` action is a toggle and therefore does
        not by itself prove ON or OFF.
        """
        patch = DeviceService._infer_state_patch(motion, parameters or {})

        if device.protocol == "ir":
            patch["controller_online"] = True
            patch["controller_last_command"] = str(motion)

        if not patch:
            return

        # Use the Automation state recorder so state-trigger evaluation still
        # receives the same canonical update event as before.
        from ...scheduler.service import AutomationService

        AutomationService.record_device_state(
            device,
            patch,
            source="iotcore_control",
        )

    @staticmethod
    def _infer_state_patch(motion, parameters):
        if motion == "power_on":
            return {"power": True}
        if motion in {"power_off", "stop"}:
            return {"power": False}

        if str(motion).startswith("set_temp_"):
            try:
                return {"target_temperature": int(str(motion).rsplit("_", 1)[1])}
            except (TypeError, ValueError):
                return {}

        if motion == "set_temp" and parameters.get("bits") not in (None, ""):
            try:
                return {"target_temperature": int(parameters["bits"])}
            except (TypeError, ValueError):
                return {"target_temperature": parameters["bits"]}

        mode_mapping = {
            "mode_auto": "auto",
            "mode_cool": "cool",
            "mode_dehumidification": "dehumidification",
            "mode_fan": "fan",
        }
        if motion in mode_mapping:
            return {"mode": mode_mapping[motion]}

        if motion in {"play_playlist", "play_music", "resume", "play_next", "play_previous"}:
            return {"playback_state": "playing"}
        if motion == "pause":
            return {"playback_state": "paused"}
        if motion == "adjust_music_volume" and parameters.get("volume") not in (None, ""):
            try:
                return {"volume": int(parameters["volume"])}
            except (TypeError, ValueError):
                return {"volume": parameters["volume"]}
        if motion == "activate_shuffle":
            return {"shuffle": True}
        if motion == "deactivate_shuffle":
            return {"shuffle": False}
        if motion == "set_repeat" and parameters.get("repeat_mode"):
            return {"repeat_mode": parameters["repeat_mode"]}
        if motion == "set_speed" and parameters.get("fan_value") is not None:
            try:
                return {"speed": int(parameters["fan_value"])}
            except (TypeError, ValueError):
                return {}
        if motion == "vertical_swing_on":
            return {"vertical_swing": True}
        if motion == "vertical_swing_off":
            return {"vertical_swing": False}
        if motion == "horizontal_swing_on":
            return {"horizontal_swing": True}
        if motion == "horizontal_swing_off":
            return {"horizontal_swing": False}
        if motion == "set_horizontal_angle" and parameters.get("fan_value") is not None:
            return {"horizontal_angle": str(parameters["fan_value"])}
        if motion == "beep_on":
            return {"beep": True}
        if motion == "beep_off":
            return {"beep": False}
        return {}

    @staticmethod
    def execute_pc(device, motion):
        """Control a PC using connection metadata stored on ``Device``.

        ``power_on`` sends Wake-on-LAN. ``power_off`` calls the local shutdown
        agent.  The browser never supplies MAC/IP credentials; it only sends the
        Device id and action.
        """
        config = dict(device.control_config or {})

        if motion == "power_on":
            mac = str(config.get("mac_address") or "").strip()
            if not mac:
                return False, f"{device.name}의 MAC 주소가 등록되어 있지 않습니다."
            broadcast_ip = str(
                config.get("wol_broadcast_ip") or "255.255.255.255"
            ).strip()
            try:
                wol_port = int(config.get("wol_port") or 9)
            except (TypeError, ValueError):
                return False, f"{device.name}의 WOL 포트 설정이 올바르지 않습니다."
            try:
                WOLClient.send_wol(mac, ip=broadcast_ip, port=wol_port)
            except Exception as exc:
                return False, f"WOL 전송 실패: {exc}"
            return True, f"{device.name}에 WOL 패킷을 전송했습니다."

        if motion == "power_off":
            ip_address = str(config.get("ip_address") or "").strip()
            if not ip_address:
                return False, f"{device.name}의 IP 주소가 등록되어 있지 않습니다."
            try:
                agent_port = int(
                    config.get("agent_port")
                    or getattr(settings, "PC_AGENT_PORT", 5050)
                )
            except (TypeError, ValueError):
                return False, f"{device.name}의 종료 에이전트 포트 설정이 올바르지 않습니다."
            agent_path = str(
                config.get("agent_path")
                or getattr(settings, "PC_AGENT_SHUTDOWN_PATH", "/shutdown")
            ).strip()
            if not agent_path.startswith("/"):
                agent_path = "/" + agent_path

            token = str(
                config.get("agent_token")
                or getattr(settings, "PC_AGENT_TOKEN", "")
                or ""
            ).strip()
            headers = {"X-Token": token} if token else {}
            url = f"http://{ip_address}:{agent_port}{agent_path}"
            try:
                response = requests.post(url, headers=headers, timeout=2)
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                message = (
                    payload.get("msg")
                    or payload.get("message")
                    or f"{device.name} 종료 요청을 전송했습니다."
                )
                return True, message
            except requests.exceptions.Timeout:
                return False, f"{device.name} 종료 요청 시간 초과(Timeout)"
            except requests.exceptions.ConnectionError:
                return False, (
                    f"{device.name} 에이전트 연결 실패"
                    "(PC가 꺼져있거나 포트/방화벽 확인)"
                )
            except requests.exceptions.HTTPError:
                status = getattr(response, "status_code", "?")
                return False, f"{device.name} 에이전트 오류: HTTP {status}"
            except Exception as exc:
                return False, f"{device.name} 종료 요청 실패: {exc}"

        return False, f"지원하지 않는 PC 동작입니다. ({motion})"

    @staticmethod
    def execute_aircon(step):
        """
        detail.py 의 공통 제어 함수를 그대로 사용.
        """
        print(f"[DEBUG] execute_aircon device_id : {step.device.id}")
        print(f"[DEBUG] execute_aircon motion : {step.function}")
        return DeviceService.control(
            device_id=step.device.id,
            motion=step.function,
            bits=(step.parameter or {}).get("bits")
            or (step.parameter or {}).get("temperature"),
        )

    @staticmethod
    def execute_fan(step):
        controller = ControllerRepository.get_controller_by_device(step.device.id)
        if not controller:
            return False, "연결된 컨트롤러를 찾을 수 없습니다."
        return IRClient.send_ir_request(controller.ip_address, step.function)

    @staticmethod
    def execute_electric_fan(device, motion, fan_value=None):
        """Send one of the confirmed Lumena DPS commands."""
        command, error = DeviceService.prepare_electric_fan_command(
            motion,
            fan_value,
        )
        if error:
            return False, error

        dps_id, value = command
        return TuyaClient.set_value(device.device_uid, dps_id, value)

    @staticmethod
    def prepare_electric_fan_command(motion, fan_value=None):
        """Validate an action and return its typed ``(dps_id, value)``."""
        fixed_commands = {
            "power_on": (1, True),
            "power_off": (1, False),
            "vertical_swing_on": (4, True),
            "vertical_swing_off": (4, False),
            "horizontal_swing_on": (5, True),
            "horizontal_swing_off": (5, False),
            "beep_on": (13, True),
            "beep_off": (13, False),
        }
        command = fixed_commands.get(motion)

        if motion == "set_speed":
            if isinstance(fan_value, bool):
                return None, "풍속은 1부터 100 사이의 정수로 입력하세요."
            if isinstance(fan_value, int):
                speed = fan_value
            elif isinstance(fan_value, str):
                raw_speed = fan_value.strip()
                if not raw_speed.isdecimal():
                    return None, "풍속은 1부터 100 사이의 정수로 입력하세요."
                speed = int(raw_speed)
            else:
                return None, "풍속은 1부터 100 사이의 정수로 입력하세요."
            if not 1 <= speed <= 100:
                return None, "풍속은 1부터 100 사이의 정수로 입력하세요."
            command = (3, speed)

        elif motion == "set_horizontal_angle":
            if fan_value is None or isinstance(fan_value, bool):
                return None, "좌우 회전 각도는 30, 60 또는 90으로 입력하세요."
            angle = str(fan_value).strip()
            if angle not in {"30", "60", "90"}:
                return None, "좌우 회전 각도는 30, 60 또는 90으로 입력하세요."
            command = (7, angle)

        if command is None:
            return None, f"지원하지 않는 선풍기 동작입니다. ({motion})"
        return command, None

    @staticmethod
    def execute_light(device_id, motion):

        device = DeviceRepository.get_by_id(device_id)

        if not device:
            return False, "기기를 찾을 수 없습니다."

        mapping = {
            "power_on": "ON",
            "power_off": "OFF",
        }

        state = mapping.get(motion)
        # print(f"[DEBUG] state : {state}")

        if state is None:
            return False, f"지원하지 않는 동작입니다. ({motion})"

        return ZigbeeClient.send_zigbee_request(
            device.device_uid,
            state
        )
    

    @staticmethod
    def execute_ir(controller_id, motion, bits=None):
        controller = ControllerRepository.get_controller(controller_id)
        print(f"[DEBUG] execute_ir controller : {controller}")

        if not controller:
            return False, "컨트롤러를 찾을 수 없습니다."

        code = IRCodeRepository.get_ir_code(motion, bits)
        print(f"[DEBUG] execute_ir code : {code}")

        if not code:
            return False, f"'{motion}'에 대한 IR 코드가 없습니다."

        success, message = IRClient.send_ir_request(
            controller.ip_address,
            code,
        )
        print(f"[DEBUG] execute_ir success : {(success, message)}")

        if not success:
            return False, message or "ESP32와 통신에 실패했습니다."

        return True, message or "정상적으로 제어되었습니다."


    @staticmethod
    def execute_projector(step):
        """Sequence/Automation에서 프로젝터 IR 동작을 실행한다."""
        return DeviceService.control(
            device_id=step.device.id,
            motion=step.function,
        )

    @staticmethod
    def execute_projector_ir(controller_id, motion):
        controller = ControllerRepository.get_controller(controller_id)

        if not controller:
            return False, "컨트롤러를 찾을 수 없습니다."

        code = IRCodeRepository.get_projector_ir_code(
            motion=motion,
            bits=32,
        )

        if not code:
            return False, f"'{motion}'에 대한 프로젝터 IR 코드가 없습니다."

        success, message = IRClient.send_ir_request(
            controller.ip_address,
            code,
        )

        if not success:
            return False, message or "프로젝터 ESP32와 통신에 실패했습니다."

        return True, message or "프로젝터 제어 신호를 전송했습니다."


    @staticmethod
    def execute_speaker(
        device_id,
        motion,
        playlist_id=None,
        music_id=None,
        volume=None,
        repeat_mode=None,
    ):
        device = DeviceRepository.get_by_id(device_id)

        if not device:
            return False, "기기를 찾을 수 없습니다."

        player_id, error_message = MusicAssistantClient.resolve_player_id(
            player_id=device.device_uid,
            player_name=device.name,
        )
        if not player_id:
            return False, error_message

        if motion == "play_playlist":
            return MusicAssistantClient.play_playlist(
                player_id,
                playlist_id,
            )

        elif motion == "play_music":
            return MusicAssistantClient.play_music(
                player_id,
                music_id,
            )

        elif motion == "play_previous":
            return MusicAssistantClient.play_previous(player_id)

        elif motion == "resume":
            return MusicAssistantClient.resume(player_id)

        elif motion == "pause":
            return MusicAssistantClient.pause(player_id)

        elif motion == "play_next":
            return MusicAssistantClient.play_next(player_id)

        elif motion == "adjust_music_volume":
            return MusicAssistantClient.set_volume(
                player_id,
                volume,
            )

        elif motion == "activate_shuffle":
            return MusicAssistantClient.set_shuffle(
                player_id,
                True,
            )

        elif motion == "deactivate_shuffle":
            return MusicAssistantClient.set_shuffle(
                player_id,
                False,
            )

        elif motion == "set_repeat":
            return MusicAssistantClient.set_repeat(
                player_id,
                repeat_mode,
            )

        return False, f"지원하지 않는 스피커 동작입니다. ({motion})"
