from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Device


class DeviceControlLayoutTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="device-layout-user",
            password="test-password",
        )
        self.client.force_login(self.user)

        self.pc = Device.objects.create(
            device_type="pc",
            device_role=Device.Role.HYBRID,
            protocol=Device.Protocol.TCPIP,
            device_uid="layout-pc",
            name="Layout PC",
            location="내 방",
        )
        self.fan = Device.objects.create(
            device_type="electric_fan",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.TUYA,
            device_uid="layout-fan",
            name="Layout Fan",
            location="거실",
        )
        self.projector = Device.objects.create(
            device_type="projector",
            device_role=Device.Role.CONTROL,
            protocol=Device.Protocol.IR,
            device_uid="layout-projector",
            name="Layout Projector",
            location="내 방",
        )

    def test_all_location_is_default_and_categories_are_rendered(self):
        response = self.client.get(reverse("iotcore:device_control"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_location"], "all")
        self.assertContains(response, "컴퓨터")
        self.assertContains(response, "환경 제어")
        self.assertContains(response, "미디어")
        self.assertContains(response, "Layout PC")
        self.assertContains(response, "Layout Fan")
        self.assertContains(response, "Layout Projector")
        self.assertContains(response, "?location=%EB%82%B4%20%EB%B0%A9")
        self.assertContains(response, "?location=%EA%B1%B0%EC%8B%A4")

    def test_location_filter_only_renders_devices_in_selected_location(self):
        response = self.client.get(
            reverse("iotcore:device_control"),
            {"location": "내 방"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_location"], "내 방")
        self.assertContains(response, "Layout PC")
        self.assertContains(response, "Layout Projector")
        self.assertNotContains(response, "Layout Fan")

    def test_unknown_location_falls_back_to_all(self):
        response = self.client.get(
            reverse("iotcore:device_control"),
            {"location": "없는 위치"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_location"], "all")
        self.assertContains(response, "Layout PC")
        self.assertContains(response, "Layout Fan")
