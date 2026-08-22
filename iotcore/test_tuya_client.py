import builtins
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .infrastructure.tuya.client import TuyaClient


VALID_LOCAL_KEY = "do-not-log-key!!"

TUYA_CONFIG = {
    "lumdena": {
        "device_id": "test-device-id",
        "address": "192.0.2.10",
        "local_key": VALID_LOCAL_KEY,
        "dev_type": "device22",
        "version": 3.3,
    },
}


class TuyaClientTests(SimpleTestCase):
    @staticmethod
    def _raise_for_tinytuya(exc):
        original_import = builtins.__import__

        def import_with_failure(name, *args, **kwargs):
            if name == "tinytuya":
                raise exc
            return original_import(name, *args, **kwargs)

        return patch("builtins.__import__", side_effect=import_with_failure)

    @override_settings(IOTCORE_TUYA_DEVICES=TUYA_CONFIG)
    def test_set_value_uses_configured_alias_and_typed_value(self):
        device = Mock()
        device.set_value.return_value = {"dps": {"3": 51}}
        module = SimpleNamespace(Device=Mock(return_value=device))

        with patch.dict(sys.modules, {"tinytuya": module}):
            success, message = TuyaClient.set_value("lumdena", 3, 51)

        self.assertTrue(success, message)
        module.Device.assert_called_once_with(
            "test-device-id",
            "192.0.2.10",
            VALID_LOCAL_KEY,
            dev_type="device22",
            version=3.3,
            connection_timeout=3,
            connection_retry_limit=1,
            connection_retry_delay=1,
        )
        device.set_value.assert_called_once_with(3, 51)

    def test_missing_dev_type_uses_default(self):
        config = {
            "lumdena": {
                "device_id": "test-device-id",
                "address": "192.0.2.10",
                "local_key": VALID_LOCAL_KEY,
                "version": 3.3,
            },
        }
        device = Mock()
        device.set_value.return_value = {"dps": {"1": True}}
        module = SimpleNamespace(Device=Mock(return_value=device))

        with override_settings(IOTCORE_TUYA_DEVICES=config):
            with patch.dict(sys.modules, {"tinytuya": module}):
                success, message = TuyaClient.set_value("lumdena", 1, True)

        self.assertTrue(success, message)
        self.assertEqual("default", module.Device.call_args.kwargs["dev_type"])

    def test_invalid_dev_type_is_rejected_before_connecting(self):
        invalid_values = (
            None,
            "",
            "Device22",
            " device22",
            "device22 ",
            22,
            True,
            [],
            {},
        )

        for value in invalid_values:
            with self.subTest(value=value):
                config = {
                    "lumdena": {
                        "device_id": "test-device-id",
                        "address": "192.0.2.10",
                        "local_key": VALID_LOCAL_KEY,
                        "dev_type": value,
                        "version": 3.3,
                    },
                }
                module = SimpleNamespace(Device=Mock())

                with override_settings(IOTCORE_TUYA_DEVICES=config):
                    with patch.dict(sys.modules, {"tinytuya": module}):
                        success, message = TuyaClient.set_value(
                            "lumdena", 1, True
                        )

                self.assertFalse(success)
                self.assertIn("dev_type", message)
                module.Device.assert_not_called()

    def test_invalid_local_key_is_rejected_before_connecting(self):
        invalid_values = (
            ("a" * 15, "정확히 16자"),
            ("a" * 17, "정확히 16자"),
            ("", "device_id/local_key 설정이 필요합니다"),
            ("가" * 16, "ASCII 문자"),
        )

        for value, expected_message in invalid_values:
            with self.subTest(value=value):
                config = {
                    "lumdena": {
                        "device_id": "test-device-id",
                        "address": "192.0.2.10",
                        "local_key": value,
                        "version": 3.5,
                    },
                }

                with override_settings(IOTCORE_TUYA_DEVICES=config):
                    parsed, error = TuyaClient._device_config("lumdena")

                self.assertIsNone(parsed)
                self.assertIn(expected_message, error)
                if value:
                    self.assertNotIn(value, error)

    @override_settings(IOTCORE_TUYA_DEVICES=TUYA_CONFIG)
    def test_error_response_does_not_expose_credentials(self):
        device = Mock()
        device.set_value.return_value = {
            "Error": "connection failed",
            "Err": "901",
            "Payload": "sensitive",
        }
        module = SimpleNamespace(Device=Mock(return_value=device))

        with patch.dict(sys.modules, {"tinytuya": module}):
            success, message = TuyaClient.set_value("lumdena", 1, True)

        self.assertFalse(success)
        self.assertIn("901", message)
        self.assertNotIn(VALID_LOCAL_KEY, message)
        self.assertNotIn("test-device-id", message)
        self.assertNotIn("sensitive", message)

    @override_settings(IOTCORE_TUYA_DEVICES={})
    def test_missing_alias_fails_without_importing_or_contacting_tuya(self):
        success, message = TuyaClient.set_value("lumdena", 1, True)

        self.assertFalse(success)
        self.assertIn("lumdena", message)

    def test_invalid_dps_is_rejected(self):
        for value in (None, True, 0, -1, "bad"):
            with self.subTest(value=value):
                success, message = TuyaClient.set_value("lumdena", value, True)
                self.assertFalse(success)
                self.assertTrue(message)

    @override_settings(IOTCORE_TUYA_DEVICES=TUYA_CONFIG)
    def test_missing_tinytuya_package_is_reported_as_not_installed(self):
        error = ModuleNotFoundError(
            "No module named 'tinytuya'",
            name="tinytuya",
        )

        with self.assertLogs(
            "iotcore.infrastructure.tuya.client", level="WARNING"
        ) as logs:
            with self._raise_for_tinytuya(error):
                success, message = TuyaClient.set_value("lumdena", 1, True)

        self.assertFalse(success)
        self.assertIn("TinyTuya가 설치되어 있지 않습니다", message)
        self.assertNotIn(VALID_LOCAL_KEY, message)
        self.assertNotIn("test-device-id", message)
        self.assertIn("TinyTuya package is not installed", logs.output[0])

    @override_settings(IOTCORE_TUYA_DEVICES=TUYA_CONFIG)
    def test_missing_tinytuya_dependency_reports_dependency_name(self):
        error = ModuleNotFoundError(
            "No module named 'cryptography'",
            name="cryptography",
        )

        with self.assertLogs(
            "iotcore.infrastructure.tuya.client", level="WARNING"
        ) as logs:
            with self._raise_for_tinytuya(error):
                success, message = TuyaClient.set_value("lumdena", 1, True)

        self.assertFalse(success)
        self.assertIn("의존 모듈", message)
        self.assertIn("cryptography", message)
        self.assertNotIn(VALID_LOCAL_KEY, message)
        self.assertNotIn("test-device-id", message)
        self.assertIn("cryptography", logs.output[0])

    @override_settings(IOTCORE_TUYA_DEVICES=TUYA_CONFIG)
    def test_other_import_error_reports_only_exception_type(self):
        secret_marker = "must-not-be-returned"
        error = ImportError(secret_marker)

        with self.assertLogs(
            "iotcore.infrastructure.tuya.client", level="WARNING"
        ) as logs:
            with self._raise_for_tinytuya(error):
                success, message = TuyaClient.set_value("lumdena", 1, True)

        self.assertFalse(success)
        self.assertIn("ImportError", message)
        self.assertNotIn(secret_marker, message)
        self.assertNotIn(secret_marker, logs.output[0])
        self.assertNotIn(VALID_LOCAL_KEY, message)
        self.assertNotIn("test-device-id", message)
