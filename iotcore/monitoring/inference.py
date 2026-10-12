"""AI readiness from telemetry and independent request timing from Django."""
from datetime import timedelta
from functools import wraps
import logging
import math
from time import perf_counter

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import AIInferenceEvent

logger = logging.getLogger(__name__)
LABELS = {
    "ready": "요청 가능", "starting": "준비 중", "processing": "처리 중",
    "error": "오류", "unreachable": "연결 안 됨", "unknown": "확인 불가",
    "disabled": "비활성", "unconfigured": "확인 주소 미설정",
}


def _text(value, length=100):
    return str(value or "")[:length]


def _number(value, minimum=0, maximum=None):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < minimum or (maximum is not None and number > maximum):
        return None
    return number


def normalize_ai_payload(payload):
    """Convert relative probe age to server time, rather than renewing stale probes."""
    if not isinstance(payload, dict):
        return {}
    now = timezone.now()
    result = {"services": {}}
    services = payload.get("services")
    for name in ("asr", "nlp"):
        service = services.get(name) if isinstance(services, dict) else None
        if not isinstance(service, dict):
            continue
        age = _number(service.get("age_seconds"), maximum=86400)
        status = service.get("status")
        result["services"][name] = {
            "status": status if status in LABELS else "unknown",
            "checked_at": (now - timedelta(seconds=age)).isoformat() if age is not None else None,
            **{key: _text(service.get(key), 300 if key == "error" else 100)
               for key in ("model", "engine", "device", "version", "error")},
        }
    gpu = payload.get("gpu")
    if isinstance(gpu, dict):
        age = _number(gpu.get("age_seconds"), maximum=86400)
        devices = gpu.get("devices")
        cleaned = []
        for device in devices[:8] if isinstance(devices, list) else []:
            if not isinstance(device, dict):
                continue
            cleaned.append({
                "name": _text(device.get("name")),
                "utilization_percent": _number(device.get("utilization_percent"), maximum=100),
                "memory_used_mb": _number(device.get("memory_used_mb")),
                "memory_total_mb": _number(device.get("memory_total_mb")),
                "temperature_c": _number(device.get("temperature_c"), maximum=150),
            })
        result["gpu"] = {
            "status": "available" if gpu.get("status") == "available" and cleaned else "unavailable",
            "checked_at": (now - timedelta(seconds=age)).isoformat() if age is not None else None,
            "devices": cleaned, "error": _text(gpu.get("error"), 300),
        }
    return result


class AIInferenceMonitor:
    @staticmethod
    def _write(callback):
        # A monitoring write must not break voice control, including when its
        # schema has not been migrated yet. The savepoint protects the caller.
        try:
            with transaction.atomic():
                return callback()
        except Exception:
            logger.exception("AI monitoring metadata write failed")
            return None

    @classmethod
    def start(cls, stage, request_id):
        return cls._write(lambda: AIInferenceEvent.objects.create(
            node_uid=getattr(settings, "IOTCORE_AI_NODE_UID", "home-ai-main"),
            request_id=request_id, stage=stage, status=AIInferenceEvent.Status.PROCESSING,
        ))

    @classmethod
    def finish(cls, event, *, ok, elapsed_ms, error="", disabled=False):
        if event is None:
            return
        cls._write(lambda: AIInferenceEvent.objects.filter(pk=event.pk).update(
            status="disabled" if disabled else "success" if ok else "failed",
            elapsed_ms=elapsed_ms, error=_text(error, 300), finished_at=timezone.now(),
        ))

    @classmethod
    def direct_match(cls, request_id):
        cls._write(lambda: AIInferenceEvent.objects.create(
            node_uid=getattr(settings, "IOTCORE_AI_NODE_UID", "home-ai-main"),
            request_id=request_id, stage="nlp", route="automation_match",
            status="success", finished_at=timezone.now(),
        ))

    @staticmethod
    def _event(event):
        status = event.status
        if status == "processing" and event.started_at < timezone.now() - timedelta(minutes=2):
            status = "unknown"
        return {
            "request_id": event.request_id, "stage": event.stage, "route": event.route,
            "status": status, "elapsed_ms": event.elapsed_ms, "error": event.error,
            "started_at": event.started_at.isoformat(),
            "finished_at": event.finished_at.isoformat() if event.finished_at else None,
        }

    @classmethod
    def snapshot(cls, telemetry):
        now = timezone.now()
        ttl = max(5, int(getattr(settings, "AI_MONITOR_STALE_SECONDS", 15)))
        payload = telemetry.get("ai_status") or {}

        def fresh(data):
            try:
                checked = parse_datetime(data.get("checked_at") or "")
                return bool(telemetry.get("online") and checked and timezone.is_aware(checked)
                            and now - timedelta(seconds=ttl) <= checked <= now + timedelta(seconds=1))
            except (ValueError, TypeError):
                return False

        queryset = AIInferenceEvent.objects.filter(node_uid=telemetry["uid"])
        recent = list(queryset.order_by("-started_at", "-id")[:20])
        active = {row["stage"]: row["count"] for row in queryset.filter(
            status="processing", started_at__gte=now - timedelta(minutes=2),
        ).values("stage").annotate(count=Count("id"))}
        services = {}
        raw_services = payload.get("services") or {}
        for name in ("asr", "nlp"):
            raw = raw_services.get(name) or {}
            is_fresh = fresh(raw)
            status = raw.get("status", "unknown") if is_fresh else "unknown"
            if not getattr(settings, f"VOICE_{name.upper()}_ENABLED", False):
                status = "disabled"
            elif active.get(name):
                status = "processing"
            last = next((event for event in recent if event.stage == name), None)
            services[name] = {
                **raw, "status": status, "label": LABELS.get(status, LABELS["unknown"]),
                "stale": not is_fresh, "active_requests": active.get(name, 0),
                "last_result": cls._event(last) if last else None,
                "expires_in_seconds": max(0, (parse_datetime(raw["checked_at"]) + timedelta(seconds=ttl) - now).total_seconds()) if is_fresh else 0,
            }
        gpu = payload.get("gpu") or {}
        if not fresh(gpu):
            gpu = {**gpu, "status": "unavailable", "devices": [], "error": "현재 GPU 데이터 없음"}
        return {"services": services, "gpu": gpu,
                "recent": [cls._event(event) for event in recent]}


def monitor_inference(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            event = AIInferenceMonitor.start(stage, kwargs["request_id"])
            started = perf_counter()
            try:
                result = function(self, *args, **kwargs)
            except Exception as exc:
                AIInferenceMonitor.finish(event, ok=False, elapsed_ms=(perf_counter() - started) * 1000,
                                          error=f"{type(exc).__name__}: {exc}")
                raise
            AIInferenceMonitor.finish(event, ok=result.ok, elapsed_ms=(perf_counter() - started) * 1000,
                                      error=result.error, disabled=not self.enabled)
            return result
        return wrapped
    return decorate
