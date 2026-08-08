from __future__ import annotations

import math
import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from ..models import Device, NodeMetricSample


class NodeTelemetryError(ValueError):
    pass


class NodeTelemetryService:
    """Parse, persist, and query high-frequency system telemetry."""

    TOPIC_PATTERN = re.compile(
        r"^iotcore/nodes/(?P<device_uid>[A-Za-z0-9_.-]{1,100})/telemetry$"
    )

    @classmethod
    def device_uid_from_topic(cls, topic: str) -> str | None:
        match = cls.TOPIC_PATTERN.fullmatch(str(topic or ""))
        return match.group("device_uid") if match else None

    @classmethod
    def is_telemetry_topic(cls, topic: str) -> bool:
        return cls.device_uid_from_topic(topic) is not None

    @staticmethod
    def _number(value, *, label: str, minimum: float | None = None, maximum: float | None = None, nullable: bool = False):
        if value is None and nullable:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise NodeTelemetryError(f"{label} 값이 숫자가 아닙니다.") from exc
        if not math.isfinite(number):
            raise NodeTelemetryError(f"{label} 값이 유효한 숫자가 아닙니다.")
        if minimum is not None and number < minimum:
            raise NodeTelemetryError(f"{label} 값이 허용 범위보다 작습니다.")
        if maximum is not None and number > maximum:
            raise NodeTelemetryError(f"{label} 값이 허용 범위보다 큽니다.")
        return number

    @classmethod
    def record_sample(cls, topic: str, payload: dict) -> NodeMetricSample:
        device_uid = cls.device_uid_from_topic(topic)
        if device_uid is None:
            raise NodeTelemetryError("시스템 telemetry 토픽 형식이 아닙니다.")
        if not isinstance(payload, dict):
            raise NodeTelemetryError("telemetry payload는 JSON object여야 합니다.")

        try:
            device = Device.objects.get(device_uid=device_uid)
        except Device.DoesNotExist as exc:
            raise NodeTelemetryError(
                f"등록되지 않은 시스템 노드입니다: {device_uid}"
            ) from exc

        network = payload.get("network") or {}
        storage = payload.get("storage") or {}
        if not isinstance(network, dict) or not isinstance(storage, dict):
            raise NodeTelemetryError("network/storage 값은 object여야 합니다.")

        return NodeMetricSample.objects.create(
            device=device,
            cpu_percent=cls._number(
                payload.get("cpu_percent"),
                label="CPU",
                minimum=0,
                maximum=100,
            ),
            memory_percent=cls._number(
                payload.get("memory_percent"),
                label="RAM",
                minimum=0,
                maximum=100,
            ),
            download_mbps=cls._number(
                network.get("download_mbps"),
                label="다운로드",
                minimum=0,
            ),
            upload_mbps=cls._number(
                network.get("upload_mbps"),
                label="업로드",
                minimum=0,
            ),
            storage_percent=cls._number(
                storage.get("used_percent"),
                label="스토리지 사용률",
                minimum=0,
                maximum=100,
                nullable=True,
            ),
            storage_used_gb=cls._number(
                storage.get("used_gb"),
                label="스토리지 사용량",
                minimum=0,
                nullable=True,
            ),
            storage_total_gb=cls._number(
                storage.get("total_gb"),
                label="스토리지 전체 용량",
                minimum=0,
                nullable=True,
            ),
        )

    @classmethod
    def snapshot(cls, device_uid: str, *, history_seconds: int | None = None) -> dict:
        device = Device.objects.filter(device_uid=device_uid).first()
        if device is None:
            return {
                "uid": device_uid,
                "name": device_uid,
                "online": False,
                "last_seen": None,
                "current": None,
                "history": [],
            }

        latest = (
            NodeMetricSample.objects.filter(device=device)
            .order_by("-recorded_at")
            .first()
        )
        if latest is None:
            return {
                "uid": device.device_uid,
                "name": device.name,
                "online": False,
                "last_seen": None,
                "current": None,
                "history": [],
            }

        now = timezone.now()
        offline_seconds = int(
            getattr(settings, "NODE_TELEMETRY_OFFLINE_SECONDS", 5)
        )
        online = latest.recorded_at >= now - timedelta(seconds=offline_seconds)

        current = {
            "cpu_percent": latest.cpu_percent,
            "memory_percent": latest.memory_percent,
            "download_mbps": latest.download_mbps,
            "upload_mbps": latest.upload_mbps,
            "storage_percent": latest.storage_percent,
            "storage_used_gb": latest.storage_used_gb,
            "storage_total_gb": latest.storage_total_gb,
        }

        history = []
        if history_seconds:
            since = now - timedelta(seconds=max(1, int(history_seconds)))
            samples = (
                NodeMetricSample.objects.filter(
                    device=device,
                    recorded_at__gte=since,
                )
                .order_by("recorded_at")
            )
            history = [
                {
                    "timestamp": sample.recorded_at.isoformat(),
                    "download_mbps": sample.download_mbps,
                    "upload_mbps": sample.upload_mbps,
                }
                for sample in samples
            ]

        return {
            "uid": device.device_uid,
            "name": device.name,
            "online": online,
            "last_seen": latest.recorded_at.isoformat(),
            "current": current,
            "history": history,
        }
