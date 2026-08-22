from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .device.services.device_service import DeviceService
from .device.services.sequence_executor import SequenceExecutor
from .device_actions import DeviceActionRegistry
from .forms import AutomationActionForm, SequenceStepForm
from .infrastructure.tuya.client import TuyaClient
from .models import (
    Automation,
    AutomationAction,
    AutomationRun,
    Device,
    Sequence,
    SequenceRun,
    SequenceStep,
)
from .scheduler.executor import AutomationExecutor


class ElectricFanActionRegistryTests(TestCase):
    def test_registry_exposes_only_confirmed_actions_and_parameter_keys(self):
        actions = {
            action.code: action.parameter_key
            for action in DeviceActionRegistry.get_actions("electric_fan")
        }

        self.assertEqual(
            actions,
            {
                "power_on": None,
                "power_off": None,
                "set_speed": "speed",
                "vertical_swing_on": None,
                "vertical_swing_off": None,
                "horizontal_swing_on": None,
                "horizontal_swing_off": None,
                "set_horizontal_angle": "horizontal_angle",
                "beep_on": None,
                "beep_off": None,
            },
        )

    def test_registry_does_not_expose_unverified_dps_actions(self):
        action_codes = {
            action.code
            for action in DeviceActionRegistry.get_actions("electric_fan")
        }

        self.assertNotIn("set_mode", action_codes)
        self.assertNotIn("natural_wind", action_codes)
        self.assertNotIn("set_timer", action_codes)
        self.assertNotIn("rotation_360", action_codes)


class ElectricFanDeviceServiceTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            device_uid="lumdena",
            name="LUMENA FAN CLASSIC 3 FURE WHITE",
            device_type="electric_fan",
            protocol=Device.Protocol.TUYA,
            location="거실",
        )

    @patch.object(TuyaClient, "set_value", return_value=(True, "완료"))
    def test_control_maps_fixed_actions_to_typed_dps_values(self, set_value):
        cases = [
            ("power_on", 1, True),
            ("power_off", 1, False),
            ("vertical_swing_on", 4, True),
            ("vertical_swing_off", 4, False),
            ("horizontal_swing_on", 5, True),
            ("horizontal_swing_off", 5, False),
            ("beep_on", 13, True),
            ("beep_off", 13, False),
        ]

        for action, dps_id, expected_value in cases:
            with self.subTest(action=action):
                set_value.reset_mock()

                success, message = DeviceService.control(self.device.id, action)

                self.assertTrue(success, message)
                set_value.assert_called_once_with(
                    self.device.device_uid,
                    dps_id,
                    expected_value,
                )
                sent_value = set_value.call_args.args[2]
                self.assertIs(type(sent_value), bool)

    @patch.object(TuyaClient, "set_value", return_value=(True, "완료"))
    def test_control_coerces_speed_to_an_int_for_dps_3(self, set_value):
        success, message = DeviceService.control(
            self.device.id,
            "set_speed",
            fan_value="51",
        )

        self.assertTrue(success, message)
        set_value.assert_called_once_with(self.device.device_uid, 3, 51)
        self.assertIs(type(set_value.call_args.args[2]), int)

    @patch.object(TuyaClient, "set_value", return_value=(True, "완료"))
    def test_control_accepts_speed_range_boundaries(self, set_value):
        for fan_value in (1, 100):
            with self.subTest(fan_value=fan_value):
                set_value.reset_mock()

                success, message = DeviceService.control(
                    self.device.id,
                    "set_speed",
                    fan_value=fan_value,
                )

                self.assertTrue(success, message)
                set_value.assert_called_once_with(
                    self.device.device_uid,
                    3,
                    fan_value,
                )

    @patch.object(TuyaClient, "set_value", return_value=(True, "완료"))
    def test_control_coerces_horizontal_angle_to_a_string_for_dps_7(
        self,
        set_value,
    ):
        for angle in (30, 60, 90):
            with self.subTest(angle=angle):
                set_value.reset_mock()
                success, message = DeviceService.control(
                    self.device.id,
                    "set_horizontal_angle",
                    fan_value=angle,
                )

                self.assertTrue(success, message)
                set_value.assert_called_once_with(
                    self.device.device_uid,
                    7,
                    str(angle),
                )
                self.assertIs(type(set_value.call_args.args[2]), str)

    @patch.object(TuyaClient, "set_value", return_value=(True, "완료"))
    def test_execute_step_reads_parameter_keys_used_by_sequences(self, set_value):
        cases = [
            ("set_speed", {"speed": "37"}, 3, 37),
            (
                "set_horizontal_angle",
                {"horizontal_angle": 60},
                7,
                "60",
            ),
        ]

        for action, parameter, dps_id, expected_value in cases:
            with self.subTest(action=action):
                set_value.reset_mock()
                step = SimpleNamespace(
                    device=self.device,
                    function=action,
                    parameter=parameter,
                )

                success, message = DeviceService.execute_step(step)

                self.assertTrue(success, message)
                set_value.assert_called_once_with(
                    self.device.device_uid,
                    dps_id,
                    expected_value,
                )

    @patch.object(TuyaClient, "set_value", return_value=(True, "완료"))
    def test_rejects_speed_outside_1_to_100(self, set_value):
        for invalid_value in (0, 101, 50.5, "not-a-number", True, None):
            with self.subTest(value=invalid_value):
                success, message = DeviceService.control(
                    self.device.id,
                    "set_speed",
                    fan_value=invalid_value,
                )

                self.assertFalse(success)
                self.assertTrue(message)

        set_value.assert_not_called()

    @patch.object(TuyaClient, "set_value", return_value=(True, "완료"))
    def test_rejects_unconfirmed_horizontal_angles(self, set_value):
        invalid_values = (
            0,
            "0",
            120,
            "120",
            150,
            "150",
            180,
            "180",
            None,
            True,
        )
        for invalid_value in invalid_values:
            with self.subTest(value=invalid_value):
                success, message = DeviceService.control(
                    self.device.id,
                    "set_horizontal_angle",
                    fan_value=invalid_value,
                )

                self.assertFalse(success)
                self.assertTrue(message)

        set_value.assert_not_called()

    @patch.object(TuyaClient, "set_value", return_value=(True, "완료"))
    def test_rejects_unconfirmed_action_without_contacting_tuya(self, set_value):
        success, message = DeviceService.control(
            self.device.id,
            "set_timer",
            fan_value=30,
        )

        self.assertFalse(success)
        self.assertTrue(message)
        set_value.assert_not_called()

    @patch.object(TuyaClient, "set_value", return_value=(True, "완료"))
    def test_requires_tuya_protocol(self, set_value):
        self.device.protocol = Device.Protocol.IR
        self.device.save(update_fields=["protocol"])

        success, message = DeviceService.control(self.device.id, "power_on")

        self.assertFalse(success)
        self.assertTrue(message)
        set_value.assert_not_called()


class ElectricFanExecutionPathTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            device_uid="lumdena",
            name="LUMENA FAN CLASSIC 3 FURE WHITE",
            device_type="electric_fan",
            protocol=Device.Protocol.TUYA,
            location="거실",
        )

    @patch.object(TuyaClient, "set_value", return_value=(True, "완료"))
    def test_sequence_worker_executes_electric_fan_step(self, set_value):
        sequence = Sequence.objects.create(name="선풍기 풍속 설정")
        SequenceStep.objects.create(
            sequence=sequence,
            order=1,
            device=self.device,
            function="set_speed",
            parameter={"speed": 51},
        )
        sequence_run = SequenceRun.objects.create(
            sequence=sequence,
            trigger=SequenceRun.Trigger.MANUAL,
        )

        result = SequenceExecutor.run_next_pending()

        self.assertEqual(result.pk, sequence_run.pk)
        sequence_run.refresh_from_db()
        self.assertEqual(sequence_run.status, SequenceRun.Status.SUCCESS)
        set_value.assert_called_once_with(self.device.device_uid, 3, 51)

    @patch.object(TuyaClient, "set_value", return_value=(True, "완료"))
    def test_automation_worker_executes_electric_fan_action(self, set_value):
        automation = Automation.objects.create(name="선풍기 자동 종료")
        AutomationAction.objects.create(
            automation=automation,
            order=1,
            action_type=AutomationAction.ActionType.DEVICE,
            device=self.device,
            function="power_off",
        )
        automation_run = AutomationRun.objects.create(automation=automation)

        result = AutomationExecutor.run_next_pending()

        self.assertEqual(result.pk, automation_run.pk)
        automation_run.refresh_from_db()
        self.assertEqual(automation_run.status, AutomationRun.Status.SUCCESS)
        self.assertEqual(automation_run.action_runs.count(), 1)
        set_value.assert_called_once_with(self.device.device_uid, 1, False)


class ElectricFanEditorTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            device_uid="lumdena-editor",
            name="LUMENA FAN CLASSIC 3",
            device_type="electric_fan",
            protocol=Device.Protocol.TUYA,
            location="거실",
        )

    def test_automation_form_normalizes_fan_parameter_json(self):
        cases = (
            ("set_speed", '{"speed": "51"}', {"speed": 51}),
            (
                "set_horizontal_angle",
                '{"horizontal_angle": 90}',
                {"horizontal_angle": "90"},
            ),
        )

        for function, parameter_json, expected in cases:
            with self.subTest(function=function):
                form = AutomationActionForm(data={
                    "action_type": AutomationAction.ActionType.DEVICE,
                    "device": self.device.pk,
                    "function": function,
                    "parameter_json": parameter_json,
                    "delay": 0,
                })

                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["parameter"], expected)

    def test_automation_form_rejects_missing_or_unconfirmed_fan_values(self):
        for function, parameter_json in (
            ("set_speed", ""),
            ("set_speed", '{"speed": 101}'),
            ("set_horizontal_angle", '{"horizontal_angle": "120"}'),
        ):
            with self.subTest(function=function, parameter_json=parameter_json):
                form = AutomationActionForm(data={
                    "action_type": AutomationAction.ActionType.DEVICE,
                    "device": self.device.pk,
                    "function": function,
                    "parameter_json": parameter_json,
                    "delay": 0,
                })

                self.assertFalse(form.is_valid())
                self.assertIn("parameter_json", form.errors)

    def test_editors_exclude_non_tuya_electric_fan(self):
        ir_fan = Device.objects.create(
            device_uid="legacy-ir-fan",
            name="기존 IR 선풍기",
            device_type="electric_fan",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            location="거실",
        )

        step_form = SequenceStepForm()
        self.assertNotIn(ir_fan, step_form.fields["device"].queryset)

        action_form = AutomationActionForm(data={
            "action_type": AutomationAction.ActionType.DEVICE,
            "device": ir_fan.pk,
            "function": "power_on",
            "delay": 0,
        })
        self.assertFalse(action_form.is_valid())
        self.assertIn("device", action_form.errors)

        user = get_user_model().objects.create_user(
            username="non-tuya-fan-editor",
            password="test-password",
        )
        self.client.force_login(user)
        sequence = Sequence.objects.create(name="비 Tuya 선풍기 차단")

        response = self.client.get(
            reverse("iotcore:sequence_edit", args=[sequence.pk])
        )
        self.assertNotIn(ir_fan, response.context["devices"])

        response = self.client.post(
            reverse("iotcore:sequence_step_create", args=[sequence.pk]),
            {
                "device": ir_fan.pk,
                "function": "power_on",
                "hour": 0,
                "minute": 0,
                "second": 0,
                "delay_position": SequenceStep.AFTER,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(sequence.steps.exists())

    def test_sequence_editor_saves_typed_fan_value(self):
        user = get_user_model().objects.create_user(
            username="fan-sequence-editor",
            password="test-password",
        )
        self.client.force_login(user)
        sequence = Sequence.objects.create(name="선풍기 설정")

        response = self.client.post(
            reverse("iotcore:sequence_step_create", args=[sequence.pk]),
            {
                "device": self.device.pk,
                "function": "set_speed",
                "parameter_value": "37",
                "hour": 0,
                "minute": 0,
                "second": 0,
                "delay_position": SequenceStep.AFTER,
            },
        )

        self.assertEqual(response.status_code, 302)
        step = sequence.steps.get()
        self.assertEqual(step.function, "set_speed")
        self.assertEqual(step.parameter, {"speed": 37})

    def test_sequence_editor_saves_confirmed_horizontal_angle(self):
        user = get_user_model().objects.create_user(
            username="fan-sequence-angle-editor",
            password="test-password",
        )
        self.client.force_login(user)
        sequence = Sequence.objects.create(name="선풍기 좌우 회전 각도 설정")

        response = self.client.post(
            reverse("iotcore:sequence_step_create", args=[sequence.pk]),
            {
                "device": self.device.pk,
                "function": "set_horizontal_angle",
                "parameter_value": "90",
                "hour": 0,
                "minute": 0,
                "second": 0,
                "delay_position": SequenceStep.AFTER,
            },
        )

        self.assertEqual(response.status_code, 302)
        step = sequence.steps.get()
        self.assertEqual(step.function, "set_horizontal_angle")
        self.assertEqual(step.parameter, {"horizontal_angle": "90"})

    def test_sequence_editor_rejects_invalid_fan_value(self):
        user = get_user_model().objects.create_user(
            username="fan-sequence-invalid",
            password="test-password",
        )
        self.client.force_login(user)
        sequence = Sequence.objects.create(name="잘못된 선풍기 설정")

        response = self.client.post(
            reverse("iotcore:sequence_step_create", args=[sequence.pk]),
            {
                "device": self.device.pk,
                "function": "set_horizontal_angle",
                "parameter_value": "120",
                "hour": 0,
                "minute": 0,
                "second": 0,
                "delay_position": SequenceStep.AFTER,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(sequence.steps.exists())
