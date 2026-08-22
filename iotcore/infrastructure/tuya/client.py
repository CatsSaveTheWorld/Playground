import logging

from django.conf import settings


logger = logging.getLogger(__name__)


class TuyaClient:
    """Small, fail-closed adapter around TinyTuya LAN control."""

    SUPPORTED_DEV_TYPES = frozenset({"default", "device22"})

    @staticmethod
    def _device_config(device_uid):
        devices = getattr(settings, "IOTCORE_TUYA_DEVICES", {})
        if not isinstance(devices, dict):
            return None, "Tuya 기기 설정 형식이 올바르지 않습니다."

        config = devices.get(str(device_uid))
        if not isinstance(config, dict):
            return None, f"Tuya 기기 설정이 없습니다. ({device_uid})"

        device_id = str(config.get("device_id") or config.get("id") or "").strip()
        local_key = str(config.get("local_key") or config.get("key") or "")
        address = str(config.get("address") or config.get("ip") or "Auto").strip()
        dev_type = config.get("dev_type", "default")
        try:
            version = float(config.get("version", 3.3))
        except (TypeError, ValueError):
            return None, f"Tuya 프로토콜 버전 설정이 올바르지 않습니다. ({device_uid})"

        if not device_id or not local_key:
            return None, f"Tuya device_id/local_key 설정이 필요합니다. ({device_uid})"
        try:
            local_key_length = len(local_key.encode("ascii"))
        except UnicodeEncodeError:
            local_key_length = -1
        if local_key_length != 16:
            return None, (
                "Tuya local_key는 ASCII 문자 기준 정확히 16자여야 합니다. "
                f"({device_uid})"
            )
        if (
            not isinstance(dev_type, str)
            or dev_type not in TuyaClient.SUPPORTED_DEV_TYPES
        ):
            return None, f"Tuya dev_type 설정이 올바르지 않습니다. ({device_uid})"
        if version not in {3.1, 3.2, 3.3, 3.4, 3.5}:
            return None, f"지원하지 않는 Tuya 프로토콜 버전입니다. ({version:g})"

        return {
            "device_id": device_id,
            "local_key": local_key,
            "address": address or "Auto",
            "dev_type": dev_type,
            "version": version,
        }, None

    @classmethod
    def _connect(cls, device_uid):
        config, error = cls._device_config(device_uid)
        if error:
            return None, error

        try:
            import tinytuya
        except ModuleNotFoundError as exc:
            if exc.name == "tinytuya":
                logger.warning("TinyTuya package is not installed.")
                return None, "TinyTuya가 설치되어 있지 않습니다. requirements.txt를 설치하세요."

            dependency = str(exc.name or "unknown")
            logger.warning(
                "A TinyTuya dependency is not installed: %s.",
                dependency,
            )
            return None, f"TinyTuya 의존 모듈이 설치되어 있지 않습니다. ({dependency})"
        except ImportError as exc:
            error_type = type(exc).__name__
            logger.warning("TinyTuya import failed (%s).", error_type)
            return None, f"TinyTuya를 불러오지 못했습니다. ({error_type})"

        timeout = max(
            1,
            int(getattr(settings, "IOTCORE_TUYA_CONNECTION_TIMEOUT", 3)),
        )
        retry_limit = max(
            1,
            int(getattr(settings, "IOTCORE_TUYA_CONNECTION_RETRY_LIMIT", 1)),
        )
        retry_delay = max(
            0,
            int(getattr(settings, "IOTCORE_TUYA_CONNECTION_RETRY_DELAY", 1)),
        )

        try:
            device = tinytuya.Device(
                config["device_id"],
                config["address"],
                config["local_key"],
                dev_type=config["dev_type"],
                version=config["version"],
                connection_timeout=timeout,
                connection_retry_limit=retry_limit,
                connection_retry_delay=retry_delay,
            )
        except Exception as exc:
            return None, f"Tuya 연결을 준비하지 못했습니다. ({type(exc).__name__})"
        return device, None

    @classmethod
    def set_value(cls, device_uid, dps_id, value):
        """Write one explicitly selected DPS without exposing credentials."""
        if isinstance(dps_id, bool):
            return False, "Tuya DPS 번호가 올바르지 않습니다."
        try:
            dps_id = int(dps_id)
        except (TypeError, ValueError):
            return False, "Tuya DPS 번호가 올바르지 않습니다."
        if dps_id <= 0:
            return False, "Tuya DPS 번호가 올바르지 않습니다."

        device, error = cls._connect(device_uid)
        if error:
            return False, error

        try:
            result = device.set_value(dps_id, value)
        except Exception as exc:
            return False, f"Tuya 기기 통신 중 오류가 발생했습니다. ({type(exc).__name__})"

        if isinstance(result, dict) and result.get("Err") is not None:
            return False, f"Tuya 기기 통신에 실패했습니다. (오류 코드 {result['Err']})"
        return True, "Tuya 제어 명령을 전송했습니다."
