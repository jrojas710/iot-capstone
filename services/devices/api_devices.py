"""Dispositivos tipo API: Azure Maps Weather (REST/HTTPS) -> adaptador -> MQTT.

Docs: https://learn.microsoft.com/es-es/rest/api/maps/weather?view=rest-maps-2025-01-01
La llave se lee de la variable de entorno AZURE_MAPS_KEY (nunca va en el repositorio).
"""
import logging
import os
from datetime import datetime

import requests

from common import Device

log = logging.getLogger("devices.api")
BASE = "https://atlas.microsoft.com/weather"


def _get(path, **params):
    params.update({"api-version": "1.1", "subscription-key": os.environ["AZURE_MAPS_KEY"],
                   "language": "es-419"})
    r = requests.get(f"{BASE}/{path}/json", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _v(d, *path):
    for p in path:
        if d is None:
            return None
        d = d.get(p) if isinstance(d, dict) else None
    return d


class WeatherStation(Device):
    """Estación meteorológica virtual: condiciones actuales de Azure Maps."""
    kind = "api"
    protocol = "HTTPS REST (Azure Maps) → MQTT"
    default_period = 600   # 10 min: respeta la cuota de la llave compartida
    variables = {
        "temperature_c": ("°C", 10, 35), "humidity_pct": ("%", 20, 100),
        "pressure_mb": ("mb", 1000, 1025), "wind_kmh": ("km/h", 0, 40),
        "uv_index": ("índice", 0, 14), "cloud_cover_pct": ("%", 0, 100),
        "precip_mm": ("mm/h", 0, 30)}

    def __init__(self, device_id, lat, lon, **cfg):
        super().__init__(device_id, **cfg)
        self.query = f"{lat},{lon}"

    @staticmethod
    def parse(res):
        return {
            "temperature_c": _v(res, "temperature", "value"),
            "humidity_pct": res.get("relativeHumidity"),
            "pressure_mb": _v(res, "pressure", "value"),
            "wind_kmh": _v(res, "wind", "speed", "value"),
            "uv_index": res.get("uvIndex"),
            "cloud_cover_pct": res.get("cloudCover"),
            "precip_mm": _v(res, "precipitationSummary", "pastHour", "value") or 0.0,
        }

    def read(self, ts):
        data = _get("currentConditions", query=self.query, duration=0)
        return self.parse(data["results"][0])

    def backfill_24h(self):
        """Publica las últimas 24 h de observaciones horarias reales (duration=24)."""
        data = _get("currentConditions", query=self.query, duration=24)
        n = 0
        for res in data.get("results", []):
            ts = datetime.fromisoformat(res["dateTime"]).timestamp()
            self.publish_values(self.parse(res), ts)
            n += 1
        log.info("%s: %d observaciones históricas reales publicadas", self.id, n)
        return n


class AirQualityStation(Device):
    """Calidad del aire actual (Azure Maps airQuality)."""
    kind = "api"
    protocol = "HTTPS REST (Azure Maps) → MQTT"
    default_period = 900
    variables = {"aqi": ("índice", 0, 300), "pm2_5": ("µg/m³", 0, 150),
                 "pm10": ("µg/m³", 0, 250), "o3": ("µg/m³", 0, 200),
                 "no2": ("µg/m³", 0, 200), "co": ("µg/m³", 0, 2000)}
    TYPES = {"PM2.5": "pm2_5", "PM10": "pm10", "O3": "o3", "NO2": "no2", "CO": "co"}

    def __init__(self, device_id, lat, lon, **cfg):
        super().__init__(device_id, **cfg)
        self.query = f"{lat},{lon}"

    def read(self, ts):
        res = _get("airQuality/current", query=self.query, pollutants="true")["results"][0]
        out = {"aqi": res.get("globalIndex")}
        for p in res.get("pollutants", []):
            key = self.TYPES.get(p.get("type"))
            if key:
                out[key] = _v(p, "concentration", "value")
        return out
