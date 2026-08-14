import importlib

from django.apps import apps
from django.test import TestCase

from .device.repositories.device_repository import DeviceRepository
from .forms import (
    AutomationActionForm,
    AutomationConditionForm,
    AutomationTriggerForm,
    DeviceForm,
    SequenceStepForm,
)
from .models import Device


class DeviceRoleTests(TestCase):
    def setUp(self):
        self.control = Device.objects.create(
            device_uid="control-device",
            device_type="light",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.ZIGBEE,
            name="제어 기기",
            location="테스트 방",
        )
        self.sensor = Device.objects.create(
            device_uid="sensor-device",
            device_type="temperature_humidity_sensor",
            device_role=Device.Role.SENSOR,
            protocol=Device.Protocol.ZIGBEE,
            name="온습도 센서",
            location="테스트 방",
        )
        self.hybrid = Device.objects.create(
            device_uid="hybrid-device",
            device_type="hybrid_test",
            device_role=Device.Role.HYBRID,
            protocol=Device.Protocol.MQTT,
            name="복합 기기",
            location="테스트 방",
        )

    def test_controllable_repository_excludes_sensor(self):
        ids = set(DeviceRepository.get_controllable().values_list("id", flat=True))
        self.assertIn(self.control.id, ids)
        self.assertIn(self.hybrid.id, ids)
        self.assertNotIn(self.sensor.id, ids)

    def test_state_source_repository_includes_sensor_and_hybrid(self):
        ids = set(DeviceRepository.get_state_sources().values_list("id", flat=True))
        self.assertNotIn(self.control.id, ids)
        self.assertIn(self.sensor.id, ids)
        self.assertIn(self.hybrid.id, ids)

    def test_trigger_and_condition_can_select_sensor(self):
        trigger_ids = set(
            AutomationTriggerForm().fields["state_device"].queryset.values_list(
                "id", flat=True
            )
        )
        condition_ids = set(
            AutomationConditionForm().fields["state_device"].queryset.values_list(
                "id", flat=True
            )
        )
        self.assertIn(self.sensor.id, trigger_ids)
        self.assertIn(self.sensor.id, condition_ids)

    def test_action_and_sequence_forms_hide_sensor(self):
        action_ids = set(
            AutomationActionForm().fields["device"].queryset.values_list(
                "id", flat=True
            )
        )
        sequence_ids = set(
            SequenceStepForm().fields["device"].queryset.values_list(
                "id", flat=True
            )
        )
        self.assertNotIn(self.sensor.id, action_ids)
        self.assertNotIn(self.sensor.id, sequence_ids)
        self.assertIn(self.control.id, action_ids)
        self.assertIn(self.hybrid.id, action_ids)

    def test_device_form_exposes_role_and_protocol(self):
        form = DeviceForm()
        self.assertIn("device_role", form.fields)
        self.assertIn("protocol", form.fields)

    def test_sensor_type_data_migration_classifies_existing_row(self):
        legacy_sensor = Device.objects.create(
            device_uid="legacy-sensor",
            device_type="door_sensor",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.ZIGBEE,
            name="문열림 센서",
            location="테스트 방",
        )
        migration = importlib.import_module(
            "iotcore.migrations.0015_device_role_sensor_support"
        )
        migration.classify_existing_devices(apps, None)
        legacy_sensor.refresh_from_db()
        self.assertEqual(legacy_sensor.device_role, Device.Role.SENSOR)
    def test_known_aqara_uid_is_classified_even_without_sensor_in_type(self):
        aqara = Device.objects.create(
            device_uid="leedowon_room_temp_humidity",
            device_type="temperature_humidity",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.ZIGBEE,
            name="Aqara T1",
            location="테스트 방",
        )
        migration = importlib.import_module(
            "iotcore.migrations.0015_device_role_sensor_support"
        )
        migration.classify_existing_devices(apps, None)
        aqara.refresh_from_db()
        self.assertEqual(aqara.device_role, Device.Role.SENSOR)

