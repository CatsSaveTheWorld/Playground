from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from .weather.service import KmaApiError, KmaNoData, KmaWeatherService


KST = ZoneInfo("Asia/Seoul")
LOCATIONS = (
    {"name": "송탄", "nx": 62, "ny": 115},
    {"name": "평택", "nx": 62, "ny": 114},
)


@override_settings(
    KMA_SERVICE_KEY="encoded%2Ftest%3D",
    IOTCORE_WEATHER_CACHE_SECONDS=1800,
    IOTCORE_WEATHER_LOCATIONS=LOCATIONS,
)
class KmaWeatherServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch.object(KmaWeatherService, "_fetch_location")
    def test_snapshot_is_cached_for_thirty_minutes(self, fetch_location):
        expected = {
            "location": "송탄",
            "temperature": 25.5,
            "updated_at": datetime(2026, 8, 22, 19, 0, tzinfo=KST),
        }
        fetch_location.return_value = expected

        self.assertEqual(KmaWeatherService.snapshot(), expected)
        self.assertEqual(KmaWeatherService.snapshot(), expected)

        fetch_location.assert_called_once()
        self.assertEqual(fetch_location.call_args.kwargs["service_key"], "encoded/test=")

    @patch.object(KmaWeatherService, "_fetch_location")
    def test_songtan_no_data_falls_back_to_pyeongtaek(self, fetch_location):
        expected = {"location": "평택", "temperature": 24.0}
        fetch_location.side_effect = [KmaNoData("송탄 자료 없음"), expected]

        self.assertEqual(KmaWeatherService.snapshot(), expected)
        self.assertEqual(fetch_location.call_count, 2)
        self.assertEqual(
            fetch_location.call_args_list[1].kwargs["location"]["name"],
            "평택",
        )

    @override_settings(KMA_SERVICE_KEY="")
    @patch.object(KmaWeatherService, "_fetch_location")
    def test_missing_service_key_skips_request(self, fetch_location):
        self.assertIsNone(KmaWeatherService.snapshot())
        fetch_location.assert_not_called()

    def test_observation_base_time_uses_previous_day_before_publish_window(self):
        now = datetime(2026, 8, 22, 0, 20, tzinfo=KST)
        self.assertEqual(
            KmaWeatherService._observation_base_time(now),
            datetime(2026, 8, 21, 23, 0, tzinfo=KST),
        )

    def test_build_snapshot_combines_observation_and_forecast(self):
        now = datetime(2026, 8, 22, 19, 30, tzinfo=KST)
        observations = [
            {
                "baseDate": "20260822",
                "baseTime": "1900",
                "category": "T1H",
                "obsrValue": "27.4",
            },
            {"category": "REH", "obsrValue": "71"},
            {"category": "PTY", "obsrValue": "0"},
        ]
        forecasts = [
            {
                "fcstDate": "20260822",
                "fcstTime": "2000",
                "category": "SKY",
                "fcstValue": "3",
            },
            {
                "fcstDate": "20260822",
                "fcstTime": "2000",
                "category": "POP",
                "fcstValue": "20",
            },
            {
                "fcstDate": "20260822",
                "fcstTime": "1500",
                "category": "TMX",
                "fcstValue": "31.0",
            },
            {
                "fcstDate": "20260822",
                "fcstTime": "0600",
                "category": "TMN",
                "fcstValue": "23.0",
            },
        ]

        weather = KmaWeatherService._build_snapshot(
            location=LOCATIONS[0],
            observations=observations,
            forecasts=forecasts,
            now=now,
        )

        self.assertEqual(weather["temperature"], 27.4)
        self.assertEqual(weather["humidity"], 71.0)
        self.assertEqual(weather["condition"], "구름많음")
        self.assertEqual(weather["precipitation_probability"], 20)
        self.assertEqual(weather["high"], 31.0)
        self.assertEqual(weather["low"], 23.0)

    def test_daily_value_skips_invalid_duplicate_before_valid_fallback(self):
        forecasts = [
            {
                "fcstDate": "20260822",
                "category": "TMX",
                "fcstValue": "invalid",
            },
            {
                "fcstDate": "20260822",
                "category": "TMX",
                "fcstValue": "31.5",
            },
        ]

        self.assertEqual(
            KmaWeatherService._daily_value(forecasts, "20260822", "TMX"),
            31.5,
        )

    @patch.object(KmaWeatherService, "_request_items")
    def test_fetch_location_supplements_missing_extrema_from_02_forecast(
        self,
        request_items,
    ):
        now = datetime(2026, 8, 22, 23, 30, tzinfo=KST)
        observations = [
            {
                "baseDate": "20260822",
                "baseTime": "2300",
                "category": "T1H",
                "obsrValue": "23.1",
            },
            {"category": "PTY", "obsrValue": "0"},
        ]
        latest_forecasts = [
            {
                "fcstDate": "20260823",
                "fcstTime": "0000",
                "category": "POP",
                "fcstValue": "20",
            },
            {
                "fcstDate": "20260823",
                "fcstTime": "0000",
                "category": "SKY",
                "fcstValue": "3",
            },
        ]
        daily_forecasts = [
            {
                "fcstDate": "20260823",
                "fcstTime": "0000",
                "category": "POP",
                "fcstValue": "80",
            },
            {
                "fcstDate": "20260823",
                "fcstTime": "0000",
                "category": "SKY",
                "fcstValue": "4",
            },
            {
                "fcstDate": "20260822",
                "fcstTime": "0600",
                "category": "TMN",
                "fcstValue": "21.0",
            },
            {
                "fcstDate": "20260822",
                "fcstTime": "1500",
                "category": "TMX",
                "fcstValue": "29.0",
            },
        ]
        request_items.side_effect = [
            observations,
            latest_forecasts,
            daily_forecasts,
        ]

        weather = KmaWeatherService._fetch_location(
            service_key="test-key",
            location=LOCATIONS[0],
            now=now,
        )

        self.assertEqual(weather["precipitation_probability"], 20)
        self.assertEqual(weather["condition"], "구름많음")
        self.assertEqual(weather["high"], 29.0)
        self.assertEqual(weather["low"], 21.0)
        self.assertEqual(request_items.call_count, 3)
        self.assertEqual(
            request_items.call_args_list[1].kwargs["base_at"],
            datetime(2026, 8, 22, 23, 0, tzinfo=KST),
        )
        self.assertEqual(
            request_items.call_args_list[2].kwargs["base_at"],
            datetime(2026, 8, 22, 2, 0, tzinfo=KST),
        )

    @patch.object(KmaWeatherService, "_request_items")
    def test_fetch_location_before_02_uses_previous_23_forecast_without_retry(
        self,
        request_items,
    ):
        now = datetime(2026, 8, 22, 1, 30, tzinfo=KST)
        observations = [
            {
                "baseDate": "20260822",
                "baseTime": "0100",
                "category": "T1H",
                "obsrValue": "22.0",
            },
            {"category": "PTY", "obsrValue": "0"},
        ]
        forecasts = [
            {
                "fcstDate": "20260822",
                "fcstTime": "0600",
                "category": "TMN",
                "fcstValue": "20.0",
            },
            {
                "fcstDate": "20260822",
                "fcstTime": "1500",
                "category": "TMX",
                "fcstValue": "28.0",
            },
        ]
        request_items.side_effect = [observations, forecasts]

        weather = KmaWeatherService._fetch_location(
            service_key="test-key",
            location=LOCATIONS[0],
            now=now,
        )

        self.assertEqual(weather["high"], 28.0)
        self.assertEqual(weather["low"], 20.0)
        self.assertEqual(request_items.call_count, 2)
        self.assertEqual(
            request_items.call_args_list[1].kwargs["base_at"],
            datetime(2026, 8, 21, 23, 0, tzinfo=KST),
        )

    def test_daily_extrema_base_time_waits_for_02_forecast_publication(self):
        self.assertEqual(
            KmaWeatherService._daily_extrema_base_time(
                datetime(2026, 8, 22, 2, 5, tzinfo=KST)
            ),
            datetime(2026, 8, 21, 23, 0, tzinfo=KST),
        )
        self.assertEqual(
            KmaWeatherService._daily_extrema_base_time(
                datetime(2026, 8, 22, 2, 15, tzinfo=KST)
            ),
            datetime(2026, 8, 22, 2, 0, tzinfo=KST),
        )

    @patch("iotcore.weather.service.requests.get")
    def test_http_error_does_not_expose_service_key(self, get):
        response = get.return_value
        response.raise_for_status.side_effect = requests.HTTPError(
            "request failed: https://example.test/?ServiceKey=super-secret"
        )

        with self.assertRaisesRegex(KmaApiError, "HTTPError") as raised:
            KmaWeatherService._request_items(
                endpoint=KmaWeatherService.CURRENT_ENDPOINT,
                service_key="super-secret",
                location=LOCATIONS[0],
                base_at=datetime(2026, 8, 22, 19, 0, tzinfo=KST),
            )

        self.assertNotIn("super-secret", str(raised.exception))
