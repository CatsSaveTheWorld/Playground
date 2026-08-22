from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Device


class ElectricFanControlViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="fan-web-user",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.fan = Device.objects.create(
            device_type="electric_fan",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.TUYA,
            device_uid="lumdena-test",
            name="LUMENA FAN CLASSIC 3",
            location="거실",
        )
        self.url = reverse("iotcore:electricfan_control")

    @patch("iotcore.api.views.electric_fan.DeviceService.control")
    def test_power_and_boolean_style_actions_pass_no_fan_value(self, control):
        control.return_value = True, "완료"
        actions = (
            "power_on",
            "power_off",
            "vertical_swing_on",
            "vertical_swing_off",
            "horizontal_swing_on",
            "horizontal_swing_off",
            "beep_on",
            "beep_off",
        )

        for action in actions:
            with self.subTest(action=action):
                control.reset_mock()
                response = self.client.post(
                    self.url,
                    {"device_id": self.fan.id, "action": action},
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"success": True, "message": "완료"})
                control.assert_called_once_with(
                    self.fan.id,
                    action,
                    fan_value=None,
                )

    @patch("iotcore.api.views.electric_fan.DeviceService.control")
    def test_speed_is_normalized_to_bounded_integer(self, control):
        control.return_value = True, "풍속 설정 완료"

        response = self.client.post(
            self.url,
            {
                "device_id": self.fan.id,
                "action": "set_speed",
                "fan_value": "37",
            },
        )

        self.assertEqual(response.status_code, 200)
        control.assert_called_once_with(
            self.fan.id,
            "set_speed",
            fan_value=37,
        )

    @patch("iotcore.api.views.electric_fan.DeviceService.control")
    def test_horizontal_angle_remains_an_allowed_string(self, control):
        control.return_value = True, "각도 설정 완료"

        for angle in ("30", "60", "90"):
            with self.subTest(angle=angle):
                control.reset_mock()
                response = self.client.post(
                    self.url,
                    {
                        "device_id": self.fan.id,
                        "action": "set_horizontal_angle",
                        "fan_value": angle,
                    },
                )

                self.assertEqual(response.status_code, 200)
                control.assert_called_once_with(
                    self.fan.id,
                    "set_horizontal_angle",
                    fan_value=angle,
                )

    @patch("iotcore.api.views.electric_fan.DeviceService.control")
    def test_invalid_action_and_values_never_reach_service(self, control):
        invalid_payloads = (
            {"action": "set_dps", "fan_value": "1"},
            {"action": "set_speed", "fan_value": "0"},
            {"action": "set_speed", "fan_value": "101"},
            {"action": "set_speed", "fan_value": "1.5"},
            {"action": "set_speed", "fan_value": "true"},
            {"action": "set_speed"},
            {"action": "set_horizontal_angle", "fan_value": "120"},
            {"action": "set_horizontal_angle", "fan_value": "150"},
            {"action": "set_horizontal_angle", "fan_value": "360"},
            {"action": "set_horizontal_angle"},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    self.url,
                    {"device_id": self.fan.id, **payload},
                )
                self.assertEqual(response.status_code, 400)
        control.assert_not_called()

    @patch("iotcore.api.views.electric_fan.DeviceService.control")
    def test_unrecognized_dps_fields_are_not_forwarded(self, control):
        control.return_value = True, "완료"

        response = self.client.post(
            self.url,
            {
                "device_id": self.fan.id,
                "action": "power_on",
                "dps": "999",
                "dps_value": "unsafe",
            },
        )

        self.assertEqual(response.status_code, 200)
        control.assert_called_once_with(
            self.fan.id,
            "power_on",
            fan_value=None,
        )

    @patch("iotcore.api.views.electric_fan.DeviceService.control")
    def test_only_tuya_electric_fan_device_is_accepted(self, control):
        light = Device.objects.create(
            device_type="light",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.TUYA,
            device_uid="not-a-fan",
            name="다른 기기",
            location="거실",
        )
        ir_fan = Device.objects.create(
            device_type="electric_fan",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            device_uid="legacy-ir-fan",
            name="기존 IR 선풍기",
            location="거실",
        )

        for device in (light, ir_fan):
            with self.subTest(device=device):
                response = self.client.post(
                    self.url,
                    {"device_id": device.id, "action": "power_on"},
                )
                self.assertEqual(response.status_code, 400)
        control.assert_not_called()

    @patch("iotcore.api.views.electric_fan.DeviceService.control")
    def test_sensor_role_is_rejected_and_hybrid_role_is_controllable(self, control):
        sensor = Device.objects.create(
            device_type="electric_fan",
            device_role=Device.Role.SENSOR,
            protocol=Device.Protocol.TUYA,
            device_uid="sensor-only-fan",
            name="센서 전용 선풍기",
            location="거실",
        )
        hybrid = Device.objects.create(
            device_type="electric_fan",
            device_role=Device.Role.HYBRID,
            protocol=Device.Protocol.TUYA,
            device_uid="hybrid-fan",
            name="하이브리드 선풍기",
            location="거실",
        )

        rejected = self.client.post(
            self.url,
            {"device_id": sensor.id, "action": "power_on"},
        )
        self.assertEqual(rejected.status_code, 400)
        control.assert_not_called()

        control.return_value = True, "완료"
        accepted = self.client.post(
            self.url,
            {"device_id": hybrid.id, "action": "power_on"},
        )
        self.assertEqual(accepted.status_code, 200)
        control.assert_called_once_with(
            hybrid.id,
            "power_on",
            fan_value=None,
        )

    @patch("iotcore.api.views.electric_fan.DeviceService.control")
    def test_missing_device_and_service_failure_have_error_status(self, control):
        malformed = self.client.post(
            self.url,
            {"device_id": "not-a-number", "action": "power_on"},
        )
        self.assertEqual(malformed.status_code, 400)

        missing = self.client.post(
            self.url,
            {"device_id": 999999, "action": "power_on"},
        )
        self.assertEqual(missing.status_code, 404)
        control.assert_not_called()

        control.return_value = False, "Tuya 통신 실패"
        failed = self.client.post(
            self.url,
            {"device_id": self.fan.id, "action": "power_on"},
        )
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(
            failed.json(),
            {"success": False, "message": "Tuya 통신 실패"},
        )

    @patch("iotcore.api.views.electric_fan.DeviceService.control")
    def test_non_object_json_is_rejected(self, control):
        response = self.client.post(
            self.url,
            data="[]",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        control.assert_not_called()

    def test_login_and_post_are_required(self):
        self.client.logout()
        response = self.client.post(
            self.url,
            {"device_id": self.fan.id, "action": "power_on"},
        )
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)


class ElectricFanTemplateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="fan-template-user",
            password="test-password",
        )
        self.client.force_login(self.user)

    def test_controllerless_tuya_fan_has_supported_controls(self):
        fan = Device.objects.create(
            device_type="electric_fan",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.TUYA,
            device_uid="controllerless-lumdena",
            name="LUMENA FAN CLASSIC 3 FURE WHITE",
            location="거실",
        )

        response = self.client.get(reverse("iotcore:device_control"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, fan.name)
        self.assertContains(response, f'data-device-id="{fan.id}"')
        self.assertContains(response, reverse("iotcore:electricfan_control"))
        for action in (
            "power_on",
            "power_off",
            "set_speed",
            "vertical_swing_on",
            "vertical_swing_off",
            "horizontal_swing_on",
            "horizontal_swing_off",
            "set_horizontal_angle",
            "beep_on",
            "beep_off",
        ):
            self.assertContains(response, action)
        for angle in ("30", "60", "90"):
            self.assertContains(
                response,
                f"sendFanAction(event, 'set_horizontal_angle', '{angle}')",
            )
        for obsolete_angle in ("120", "150"):
            self.assertNotContains(
                response,
                f"sendFanAction(event, 'set_horizontal_angle', '{obsolete_angle}')",
            )
        self.assertNotContains(response, "power_cycle")
        self.assertNotContains(response, "timer_add_30m")
        self.assertNotContains(response, "fan_way_toggle")

    def test_legacy_ir_fan_is_not_rendered_as_tuya_control(self):
        Device.objects.create(
            device_type="electric_fan",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            device_uid="hidden-ir-fan",
            name="숨겨야 할 IR 선풍기",
            location="거실",
        )

        response = self.client.get(reverse("iotcore:device_control"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "숨겨야 할 IR 선풍기")
