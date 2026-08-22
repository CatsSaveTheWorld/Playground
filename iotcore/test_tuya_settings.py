import os
from pathlib import Path
import subprocess
import sys

from django.test import SimpleTestCase


class TuyaSettingsValidationTests(SimpleTestCase):
    SETTINGS_MODULES = (
        "playground.settings",
        "playground.settings_mysql",
    )

    def _import_settings(self, settings_module, raw_value):
        project_root = Path(__file__).resolve().parent.parent
        env = os.environ.copy()
        env["IOTCORE_TUYA_DEVICES_JSON"] = raw_value
        env["PYTHONPATH"] = os.pathsep.join(
            path for path in sys.path if isinstance(path, str) and path
        )
        code = (
            "import importlib\n"
            "try:\n"
            f"    importlib.import_module({settings_module!r})\n"
            "except Exception as exc:\n"
            "    print(type(exc).__name__)\n"
            "    print(str(exc))\n"
            "else:\n"
            "    print('OK')\n"
        )
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=project_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_malformed_environment_json_fails_without_exposing_value(self):
        secret_marker = "do-not-print-this-secret"
        raw_value = f'{{"lumdena": {{"key": "{secret_marker}"}}'

        for settings_module in self.SETTINGS_MODULES:
            with self.subTest(settings_module=settings_module):
                result = self._import_settings(settings_module, raw_value)
                self.assertIn("ImproperlyConfigured", result.stdout)
                self.assertIn("must contain a valid JSON object", result.stdout)
                self.assertNotIn(secret_marker, result.stdout + result.stderr)

    def test_non_object_environment_json_fails_without_exposing_value(self):
        secret_marker = "do-not-print-this-secret"
        raw_value = f'["{secret_marker}"]'

        for settings_module in self.SETTINGS_MODULES:
            with self.subTest(settings_module=settings_module):
                result = self._import_settings(settings_module, raw_value)
                self.assertIn("ImproperlyConfigured", result.stdout)
                self.assertIn("must contain a JSON object", result.stdout)
                self.assertNotIn(secret_marker, result.stdout + result.stderr)

