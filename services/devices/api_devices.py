"""Dispositivos tipo API: servicio meteorológico REST (HTTPS) -> adaptador -> MQTT.

Proveedor (variable API_PROVIDER):
  openmeteo (por defecto): Open-Meteo, sin llave; incluye historial horario real (past_days).
  azure: Azure Maps Weather (requiere AZURE_MAPS_KEY).
"""
import logging
import os
import time
from datetime import datetime

import requests

from common import Device

log = logging.getLogger("devices.api")
PROVIDER = os.getenv("API_PROVIDER", "openmeteo")
OM = "https://api.open-meteo.com/v1/forecast"
OM_AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"
AZ = "https://atlas.microsoft.com/weather"

W_VARS = {"temperature_c": "temperature_2m", "humidity_pct": "relative_humidity_2m",
          "pressure_mb": "surface_pressure", "wind_kmh": "wind_speed_10m", "uv_index": "uv_index",
          "cloud_cover_pct": "cloud_cover", "precip_mm": "precipitation"}
A_VARS = {"aqi": "us_aqi", "pm2_5": "pm2_5", "pm10": "pm10", "o3": "ozone",
          "no2": "nitrogen_dioxide", "co": "carbon_monoxide"}


def _om(url, lat, lon, mapping, past_days=0):
    keys = ",".join(mapping.values())
    p = {"latitude": lat, "longitude": lon, "current": keys, "timezone": "UTC", "timeformat": "unixtime"}
    if past_days:
        p.update({"hourly": keys, "past_days": int(past_days), "forecast_days": 1})
    r = requests.get(url, params=p, timeout=30)
    r.raise_for_status()
    return r.json()


def _az(path, **params):
    params.update({"api-version": "1.1", "subscription-key": os.environ["AZURE_MAPS_KEY"], "language": "es-419"})
    r = requests.get(f"{AZ}/{path}/json", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


class _ApiDevice(Device):
    kind = "api"
    URL, MAP = OM, W_VARS

    def __init__(self, device_id, lat, lon, **cfg):
        super().__init__(device_id, **cfg)
        self.lat, self.lon = lat, lon

    def read(self, ts):
        cur = _om(self.URL, self.lat, self.lon, self.MAP)["current"]
        return {k: cur.get(v) for k, v in self.MAP.items()}

    def backfill(self, days):
        """Publica el historial horario REAL de los últimos `days` días."""
        h = _om(self.URL, self.lat, self.lon, self.MAP, past_days=days).get("hourly", {})
        now, n = time.time(), 0
        for i, t in enumerate(h.get("time", [])):
            if t > now:
                break
            vals = {k: h.get(v, [None] * (i + 1))[i] for k, v in self.MAP.items()}
            if any(v is not None for v in vals.values()):
                self.publish_values(vals, t)
                n += 1
        log.info("%s: %d observaciones horarias reales publicadas", self.id, n)
        return n


class WeatherStation(_ApiDevice):
    """Estación meteorológica virtual (condiciones actuales)."""
    protocol = "HTTPS REST (Open-Meteo) → MQTT" if PROVIDER == "openmeteo" else "HTTPS REST (Azure Maps) → MQTT"
    default_period = 600
    variables = {"temperature_c": ("°C", 10, 35), "humidity_pct": ("%", 20, 100),
                 "pressure_mb": ("hPa", 880, 1025), "wind_kmh": ("km/h", 0, 40),
                 "uv_index": ("índice", 0, 14), "cloud_cover_pct": ("%", 0, 100), "precip_mm": ("mm/h", 0, 30)}

    def read(self, ts):
        if PROVIDER != "azure":
            return super().read(ts)
        r = _az("currentConditions", query=f"{self.lat},{self.lon}", duration=0)["results"][0]
        g = lambda *p: (lambda d: [d := (d or {}).get(k) for k in p][-1])(r)
        return {"temperature_c": g("temperature", "value"), "humidity_pct": r.get("relativeHumidity"),
                "pressure_mb": g("pressure", "value"), "wind_kmh": g("wind", "speed", "value"),
                "uv_index": r.get("uvIndex"), "cloud_cover_pct": r.get("cloudCover"),
                "precip_mm": g("precipitationSummary", "pastHour", "value") or 0.0}


class AirQualityStation(_ApiDevice):
    """Calidad del aire (modelo CAMS vía Open-Meteo Air Quality)."""
    protocol = "HTTPS REST (Open-Meteo) → MQTT"
    URL, MAP = OM_AIR, A_VARS
    default_period = 900
    variables = {"aqi": ("US AQI", 0, 300), "pm2_5": ("µg/m³", 0, 150), "pm10": ("µg/m³", 0, 250),
                 "o3": ("µg/m³", 0, 200), "no2": ("µg/m³", 0, 200), "co": ("µg/m³", 0, 2000)}
