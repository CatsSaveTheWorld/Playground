from ..repositories.controller_repository import ControllerRepository
from ..repositories.ircode_repository import IRCodeRepository
from ...infrastructure.ir.client import IRClient
from ...device.repositories.device_repository import DeviceRepository
from django.http import JsonResponse
from ...infrastructure.zigbee.client import ZigbeeClient
from ...infrastructure.music_assistant.client import MusicAssistantClient
from ...infrastructure.remote_tasks.client import RemoteTaskClient


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
            return DeviceService.execute_fan(step)
        if device.device_type == "light":
            return DeviceService.execute_light(device.id, step.function)
        if device.device_type == "media_server":
            return DeviceService.execute_media_server(step)
        if device.device_type == "projector":
            return DeviceService.execute_projector(step)
        if device.device_type == "speaker":
            return DeviceService.execute_speaker(
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
    ) -> tuple:

        device = DeviceRepository.get_by_id(device_id)
        # print(f"[DEBUG] control device : {device}")

        if not device:
            return False, "기기를 찾을 수 없습니다."

        success = False
        error_message = f"지원하지 않는 프로토콜입니다. ({device.protocol})"

        if device.device_type == 'aircon':
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

        else:
            return False, f"지원하지 않는 기기 종류입니다. ({device.device_type})"

        if success:
            return True, success_message or f"{device.name} 제어를 완료했습니다."

        return False, error_message
    

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
