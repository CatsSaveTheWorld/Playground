from django.test import SimpleTestCase

from .device.repositories.ircode_repository import IRCodeRepository
from .device_actions import DeviceActionRegistry


class ProjectorIRCodeTests(SimpleTestCase):
    def test_external_input_code_is_loaded_from_projector_csv(self):
        self.assertEqual(
            IRCodeRepository.get_projector_ir_code("external_input"),
            "0x807FE01F",
        )

    def test_projector_actions_exclude_mouse_mode(self):
        actions = {
            action.code
            for action in DeviceActionRegistry.get_actions("projector")
        }

        self.assertIn("external_input", actions)
        self.assertIn("menu", actions)
        self.assertNotIn("mouse_mode", actions)
