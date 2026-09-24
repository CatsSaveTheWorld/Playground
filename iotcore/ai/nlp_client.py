from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests
from django.conf import settings


@dataclass(frozen=True)
class NLPResult:
    ok: bool
    text: str = ""
    normalized_text: str = ""
    language: str = ""
    location: str | None = None
    device: str | None = None
    device_uid: str | None = None
    function: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    matched_by: str = ""
    error: str = ""
    elapsed_ms: float | None = None
    http_status: int | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": self.ok,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "language": self.language,
            "location": self.location,
            "device": self.device,
            "device_uid": self.device_uid,
            "function": self.function,
            "parameters": self.parameters,
            "confidence": self.confidence,
            "matched_by": self.matched_by,
            "error": self.error,
        }

        if self.elapsed_ms is not None:
            data["elapsed_ms"] = self.elapsed_ms

        if self.http_status is not None:
            data["http_status"] = self.http_status

        return data


class NLPClient:
    def __init__(self) -> None:
        self.base_url = str(
            getattr(settings, "VOICE_NLP_BASE_URL", "")
        ).strip().rstrip("/")

        self.connect_timeout = float(
            getattr(settings, "VOICE_NLP_CONNECT_TIMEOUT", 2.0)
        )

        self.read_timeout = float(
            getattr(settings, "VOICE_NLP_READ_TIMEOUT", 10.0)
        )

    @property
    def enabled(self) -> bool:
        return bool(
            getattr(settings, "VOICE_NLP_ENABLED", False)
        )

    def parse(
        self,
        *,
        text: str,
        request_id: str,
        client_id: str = "",
        language: str = "ko",
    ) -> NLPResult:
        if not self.enabled:
            return NLPResult(
                ok=False,
                text=text,
                error="NLP disabled",
            )

        if not self.base_url:
            return NLPResult(
                ok=False,
                text=text,
                error="VOICE_NLP_BASE_URL is empty",
            )

        endpoint = f"{self.base_url}/v1/nlp/parse"

        try:
            response = requests.post(
                endpoint,
                json={
                    "text": text,
                    "language": language,
                    "request_id": request_id,
                    "client_id": client_id,
                },
                timeout=(
                    self.connect_timeout,
                    self.read_timeout,
                ),
            )

        except requests.RequestException as exc:
            return NLPResult(
                ok=False,
                text=text,
                error=f"{type(exc).__name__}: {exc}",
            )

        try:
            payload = response.json()

        except ValueError:
            return NLPResult(
                ok=False,
                text=text,
                error=(
                    "NLP worker returned non-JSON response: "
                    f"{response.text[:300]}"
                ),
                http_status=response.status_code,
            )

        if not response.ok:
            return NLPResult(
                ok=False,
                text=text,
                error=str(payload),
                http_status=response.status_code,
            )

        return NLPResult(
            ok=bool(payload.get("ok", False)),
            text=str(payload.get("text", text)),
            normalized_text=str(
                payload.get("normalized_text", "")
            ),
            language=str(
                payload.get("language", language)
            ),
            location=payload.get("location"),
            device=payload.get("device"),
            device_uid=payload.get("device_uid"),
            function=payload.get("function"),
            parameters=payload.get("parameters") or {},
            confidence=float(
                payload.get("confidence", 0.0)
            ),
            matched_by=str(
                payload.get("matched_by", "")
            ),
            error=str(payload.get("error", "")),
            elapsed_ms=payload.get("elapsed_ms"),
            http_status=response.status_code,
        )