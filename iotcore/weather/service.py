import logging
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


logger = logging.getLogger(__name__)


class KmaApiError(RuntimeError):
    """Raised when the KMA endpoint cannot provide a usable response."""


class KmaNoData(KmaApiError):
    """Raised when a valid KMA response contains no weather rows."""


class KmaWeatherService:
    """Load and cache current outdoor weather from the KMA public API."""

    BASE_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
    CURRENT_ENDPOINT = "getUltraSrtNcst"
    FORECAST_ENDPOINT = "getVilageFcst"
    CACHE_KEY = "iotcore:weather:current:v1"
    STALE_CACHE_KEY = "iotcore:weather:stale:v1"
    UNAVAILABLE = {"available": False}
    KST = ZoneInfo("Asia/Seoul")

    DEFAULT_LOCATIONS = (
        {"name": "송탄", "nx": 62, "ny": 115},
        {"name": "평택", "nx": 62, "ny": 114},
    )

    PRECIPITATION_LABELS = {
        1: "비",
        2: "비/눈",
        3: "눈",
        5: "빗방울",
        6: "빗방울/눈날림",
        7: "눈날림",
    }
    SKY_LABELS = {
        1: "맑음",
        3: "구름많음",
        4: "흐림",
    }

    @classmethod
    def snapshot(cls):
        """Return weather while limiting successful KMA refreshes to every 30 minutes."""
        service_key = str(getattr(settings, "KMA_SERVICE_KEY", "") or "").strip()
        if not service_key:
            return None

        cached = cache.get(cls.CACHE_KEY)
        if cached is not None:
            return None if cached == cls.UNAVAILABLE else cached

        now = timezone.localtime()
        locations = getattr(
            settings,
            "IOTCORE_WEATHER_LOCATIONS",
            cls.DEFAULT_LOCATIONS,
        )

        try:
            weather = cls._load_with_fallback(
                service_key=unquote(service_key),
                locations=locations,
                now=now,
            )
        except KmaApiError as exc:
            logger.warning("기상청 날씨 조회 실패: %s", exc)
            stale = cache.get(cls.STALE_CACHE_KEY)
            if stale is not None:
                weather = dict(stale)
                weather["stale"] = True
                cache.set(cls.CACHE_KEY, weather, cls._cache_seconds())
                return weather
            cache.set(cls.CACHE_KEY, cls.UNAVAILABLE, cls._cache_seconds())
            return None

        cache.set(cls.CACHE_KEY, weather, cls._cache_seconds())
        cache.set(cls.STALE_CACHE_KEY, weather, 60 * 60 * 6)
        return weather

    @classmethod
    def _load_with_fallback(cls, *, service_key, locations, now):
        last_error = None
        for location in locations:
            try:
                return cls._fetch_location(
                    service_key=service_key,
                    location=location,
                    now=now,
                )
            except KmaNoData as exc:
                last_error = exc
                continue
        raise last_error or KmaNoData("설정된 지역에 날씨 자료가 없습니다.")

    @classmethod
    def _fetch_location(cls, *, service_key, location, now):
        observation_base = cls._observation_base_time(now)
        observations = cls._request_items(
            endpoint=cls.CURRENT_ENDPOINT,
            service_key=service_key,
            location=location,
            base_at=observation_base,
        )
        if not observations:
            raise KmaNoData(f"{location['name']} 초단기실황 자료가 없습니다.")

        forecasts = []
        forecast_base = cls._forecast_base_time(now)
        try:
            forecasts = cls._request_items(
                endpoint=cls.FORECAST_ENDPOINT,
                service_key=service_key,
                location=location,
                base_at=forecast_base,
            )
        except KmaApiError as exc:
            logger.warning("%s 단기예보 조회 실패: %s", location["name"], exc)

        daily_forecasts = []
        target_date = now.astimezone(cls.KST).strftime("%Y%m%d")
        if cls._missing_daily_extrema(forecasts, target_date):
            extrema_base = cls._daily_extrema_base_time(now)
            if extrema_base != forecast_base:
                try:
                    daily_forecasts = cls._request_items(
                        endpoint=cls.FORECAST_ENDPOINT,
                        service_key=service_key,
                        location=location,
                        base_at=extrema_base,
                    )
                except KmaApiError as exc:
                    logger.warning(
                        "%s 오늘 최고/최저 보완 조회 실패: %s",
                        location["name"],
                        exc,
                    )
        return cls._build_snapshot(
            location=location,
            observations=observations,
            forecasts=forecasts,
            daily_forecasts=daily_forecasts,
            now=now,
        )

    @classmethod
    def _request_items(cls, *, endpoint, service_key, location, base_at):
        try:
            response = requests.get(
                f"{cls.BASE_URL}/{endpoint}",
                params={
                    "serviceKey": service_key,
                    "pageNo": 1,
                    "numOfRows": 1000,
                    "dataType": "JSON",
                    "base_date": base_at.strftime("%Y%m%d"),
                    "base_time": base_at.strftime("%H%M"),
                    "nx": int(location["nx"]),
                    "ny": int(location["ny"]),
                },
                timeout=float(getattr(settings, "KMA_WEATHER_TIMEOUT", 5)),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            detail = f"HTTP {status_code}" if status_code else exc.__class__.__name__
            raise KmaApiError(f"{endpoint} 요청 오류: {detail}") from exc
        except (ValueError, TypeError) as exc:
            raise KmaApiError(f"{endpoint} JSON 응답을 해석할 수 없습니다.") from exc

        if not isinstance(payload, dict):
            raise KmaApiError(f"{endpoint} JSON 응답 구조가 올바르지 않습니다.")

        api_response = payload.get("response", {})
        if not isinstance(api_response, dict):
            raise KmaApiError(f"{endpoint} 응답 본문 구조가 올바르지 않습니다.")
        header = api_response.get("header", {})
        result_code = str(header.get("resultCode", ""))
        result_message = str(header.get("resultMsg", "알 수 없는 오류"))
        if result_code != "00":
            if result_code in {"03", "NO_DATA"}:
                raise KmaNoData(result_message)
            raise KmaApiError(f"{result_code}: {result_message}")

        body = api_response.get("body", {})
        item_container = body.get("items", {}) if isinstance(body, dict) else {}
        items = (
            item_container.get("item", [])
            if isinstance(item_container, dict)
            else []
        )
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            raise KmaNoData(f"{endpoint} 응답에 자료가 없습니다.")
        return items

    @classmethod
    def _build_snapshot(
        cls,
        *,
        location,
        observations,
        forecasts,
        now,
        daily_forecasts=None,
    ):
        observed = {
            row.get("category"): row.get("obsrValue")
            for row in observations
            if row.get("category")
        }
        temperature = cls._as_float(observed.get("T1H"))
        if temperature is None:
            raise KmaNoData(f"{location['name']} 기온 자료가 없습니다.")

        daily_forecasts = daily_forecasts or []
        forecast_by_time = cls._forecast_by_time(forecasts or daily_forecasts)
        nearest_forecast = cls._nearest_forecast(forecast_by_time, now)
        forecast_values = nearest_forecast[1] if nearest_forecast else {}
        precipitation_type = cls._as_int(observed.get("PTY"))

        if precipitation_type in cls.PRECIPITATION_LABELS:
            condition = cls.PRECIPITATION_LABELS[precipitation_type]
        else:
            condition = cls.SKY_LABELS.get(
                cls._as_int(forecast_values.get("SKY")),
                "강수 없음",
            )

        updated_at = cls._observation_time(observations, now)
        target_date = now.astimezone(cls.KST).strftime("%Y%m%d")

        extrema_forecasts = [*forecasts, *daily_forecasts]
        return {
            "location": str(location["name"]),
            "temperature": temperature,
            "humidity": cls._as_float(observed.get("REH")),
            "condition": condition,
            "precipitation_probability": cls._as_int(
                forecast_values.get("POP")
            ),
            "high": cls._daily_value(extrema_forecasts, target_date, "TMX"),
            "low": cls._daily_value(extrema_forecasts, target_date, "TMN"),
            "updated_at": updated_at,
            "fetched_at": now,
            "stale": False,
            "source": "기상청",
        }

    @classmethod
    def _forecast_by_time(cls, forecasts):
        grouped = {}
        for row in forecasts:
            try:
                target = datetime.strptime(
                    f"{row['fcstDate']}{row['fcstTime']}",
                    "%Y%m%d%H%M",
                ).replace(tzinfo=cls.KST)
                category = row["category"]
            except (KeyError, TypeError, ValueError):
                continue
            grouped.setdefault(target, {})[category] = row.get("fcstValue")
        return grouped

    @classmethod
    def _nearest_forecast(cls, grouped, now):
        if not grouped:
            return None
        local_now = now.astimezone(cls.KST)
        future = [target for target in grouped if target >= local_now]
        target = min(future) if future else max(grouped)
        return target, grouped[target]

    @staticmethod
    def _daily_value(forecasts, target_date, category):
        for row in forecasts:
            if row.get("fcstDate") == target_date and row.get("category") == category:
                value = KmaWeatherService._as_float(row.get("fcstValue"))
                if value is not None:
                    return value
        return None

    @classmethod
    def _missing_daily_extrema(cls, forecasts, target_date):
        return any(
            cls._daily_value(forecasts, target_date, category) is None
            for category in ("TMN", "TMX")
        )

    @classmethod
    def _observation_time(cls, observations, fallback):
        first = observations[0] if observations else {}
        try:
            return datetime.strptime(
                f"{first['baseDate']}{first['baseTime']}",
                "%Y%m%d%H%M",
            ).replace(tzinfo=cls.KST)
        except (KeyError, TypeError, ValueError):
            return fallback

    @classmethod
    def _observation_base_time(cls, now):
        local_now = now.astimezone(cls.KST)
        base_at = local_now.replace(minute=0, second=0, microsecond=0)
        if local_now.minute < 45:
            base_at -= timedelta(hours=1)
        return base_at

    @classmethod
    def _forecast_base_time(cls, now):
        reference = now.astimezone(cls.KST) - timedelta(minutes=10)
        base_hours = (2, 5, 8, 11, 14, 17, 20, 23)
        available_hours = [hour for hour in base_hours if hour <= reference.hour]
        if available_hours:
            return reference.replace(
                hour=max(available_hours),
                minute=0,
                second=0,
                microsecond=0,
            )
        previous_day = reference - timedelta(days=1)
        return previous_day.replace(hour=23, minute=0, second=0, microsecond=0)

    @classmethod
    def _daily_extrema_base_time(cls, now):
        """Return a forecast issue time that contains today's TMN/TMX rows."""
        reference = now.astimezone(cls.KST) - timedelta(minutes=10)
        if reference.hour >= 2:
            return reference.replace(hour=2, minute=0, second=0, microsecond=0)
        previous_day = reference - timedelta(days=1)
        return previous_day.replace(hour=23, minute=0, second=0, microsecond=0)

    @staticmethod
    def _as_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_int(value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _cache_seconds():
        return max(
            60,
            int(getattr(settings, "IOTCORE_WEATHER_CACHE_SECONDS", 30 * 60)),
        )
