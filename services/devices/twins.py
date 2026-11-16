"""Gemelos digitales: modelos físicos simples que evolucionan en el tiempo.

Usan como entrada el clima real (telemetría de api-weather-unab recibida por MQTT).
Si todavía no hay dato real (o durante el pre-cargado histórico), usan un perfil
climático típico de Bucaramanga (19–29 °C) con ruido.
"""
import json
import math
import random
import time
from datetime import datetime

from common import Device, PREFIX

TZ_OFFSET_H = -5  # America/Bogota


def local_hour(ts):
    return ((ts / 3600.0) + TZ_OFFSET_H) % 24


def weekday(ts):
    return datetime.utcfromtimestamp(ts + TZ_OFFSET_H * 3600).weekday()


class WeatherInput:
    """Último clima real recibido + perfil típico de respaldo."""

    def __init__(self, source_device="api-weather-unab", max_age_s=3600):
        self.topic = f"{PREFIX}/api/{source_device}/telemetry"
        self.last, self.last_ts, self.max_age = {}, 0, max_age_s

    def attach(self, client):
        client.message_callback_add(self.topic, self._on_msg)
        client.subscribe(self.topic, qos=1)

    def _on_msg(self, _c, _u, msg):
        try:
            d = json.loads(msg.payload)
            self.last, self.last_ts = d["values"], d["ts"] / 1000
        except (ValueError, KeyError):
            pass

    @staticmethod
    def typical(ts):
        h = local_hour(ts)
        day = math.sin((h - 9) / 24 * 2 * math.pi)            # máx. ~15 h
        rnd = random.Random(int(ts // 3600))                   # nubosidad por hora
        cloud = max(0, min(100, 55 + 30 * math.sin(ts / 86400 * 1.7) + rnd.gauss(0, 15)))
        return {"temperature_c": 24 + 5 * day + random.gauss(0, 0.3),
                "humidity_pct": max(35, min(98, 72 - 15 * day + random.gauss(0, 2))),
                "cloud_cover_pct": cloud}

    def get(self, ts, live):
        if live and self.last and time.time() - self.last_ts < self.max_age:
            base = self.typical(ts)
            base.update({k: v for k, v in self.last.items() if v is not None})
            return base
        return self.typical(ts)


def sun(ts):
    """Elevación solar normalizada 0..1 (amanece 6 h, anochece 18 h)."""
    h = local_hour(ts)
    return max(0.0, math.sin((h - 6) / 12 * math.pi))


class Twin(Device):
    kind = "twin"
    protocol = "MQTT (QoS 1)"
    default_period = 60

    def __init__(self, device_id, weather, **cfg):
        super().__init__(device_id, **cfg)
        self.weather = weather
        self.t_prev = None
        self.live = True

    def read(self, ts):
        dt = 60 if self.t_prev is None else max(1, min(900, ts - self.t_prev))
        self.t_prev = ts
        return self.step(ts, dt, self.weather.get(ts, self.live))

    def step(self, ts, dt, w):
        raise NotImplementedError


class Greenhouse(Twin):
    """Invernadero con riego automático."""
    variables = {"air_temp_c": ("°C", 15, 40), "rel_humidity_pct": ("%", 30, 100),
                 "soil_moisture_pct": ("%", 0, 100), "light_lux": ("lux", 0, 60000),
                 "irrigation_on": ("0/1", 0, 1)}
    default_setpoint = {"soil_min_pct": 35, "soil_max_pct": 60, "irrigation_mode": 0}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.soil, self.irr, self.temp = 50.0, 0, 24.0

    def step(self, ts, dt, w):
        s = sun(ts)
        cloud = w["cloud_cover_pct"] / 100
        light = 55000 * s * (1 - 0.7 * cloud)
        target = w["temperature_c"] + 9 * s * (1 - 0.6 * cloud)          # efecto invernadero
        self.temp += (target - self.temp) * min(1, dt / 1800)
        mode = int(self.setpoint.get("irrigation_mode", 0))
        if mode == 1:
            self.irr = 1
        elif mode == 2:
            self.irr = 0
        elif self.soil < self.setpoint["soil_min_pct"]:
            self.irr = 1
        elif self.soil > self.setpoint["soil_max_pct"]:
            self.irr = 0
        evap = (0.004 + 0.02 * s) * max(0.3, (self.temp - 15) / 15)       # %/min
        self.soil += (-evap + (0.9 if self.irr else 0)) * dt / 60
        self.soil = max(5, min(95, self.soil))
        hum = max(30, min(99, w["humidity_pct"] + 8 * self.irr - 6 * s + random.gauss(0, 1)))
        return {"air_temp_c": self.temp + random.gauss(0, 0.2), "rel_humidity_pct": hum,
                "soil_moisture_pct": self.soil, "light_lux": max(0, light + random.gauss(0, 300)),
                "irrigation_on": self.irr}


class ClassroomHVAC(Twin):
    """Aula universitaria: ocupación, CO2 y climatización."""
    variables = {"room_temp_c": ("°C", 18, 32), "co2_ppm": ("ppm", 400, 2000),
                 "occupancy": ("personas", 0, 40), "hvac_power_kw": ("kW", 0, 5),
                 "setpoint_c": ("°C", 18, 28)}
    default_setpoint = {"setpoint_c": 23, "co2_limit_ppm": 1000}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.temp, self.co2 = 25.0, 450.0

    def occupancy(self, ts):
        h, wd = local_hour(ts), weekday(ts)
        if wd >= 6:
            return 0
        classes = (7 <= h < 12) or (14 <= h < 18) or (wd < 5 and 18 <= h < 21)
        if not classes:
            return 0
        rnd = random.Random(int(ts // 7200))                               # cambia cada 2 h
        return max(0, int(rnd.gauss(28 if wd < 5 else 12, 6)))

    def step(self, ts, dt, w):
        occ = self.occupancy(ts)
        sp = self.setpoint["setpoint_c"]
        drift = (w["temperature_c"] - self.temp) * 0.3 + occ * 0.06        # °C/h
        hvac_on = occ > 0 and self.temp > sp
        cooling = min(3.5, (self.temp - sp) * 1.8) if hvac_on else 0       # °C/h
        self.temp += (drift - cooling) * dt / 3600
        vent = 0.8 if self.co2 > self.setpoint["co2_limit_ppm"] else 0.25  # renovaciones/h
        self.co2 += (occ * 22 - vent * (self.co2 - 420)) * dt / 3600 * 3
        self.co2 = max(410, self.co2)
        kw = (0.6 + cooling * 1.1) if hvac_on else 0.05
        return {"room_temp_c": self.temp + random.gauss(0, 0.1), "co2_ppm": self.co2 + random.gauss(0, 8),
                "occupancy": occ, "hvac_power_kw": kw, "setpoint_c": sp}


class SolarBattery(Twin):
    """Panel solar de 6 m² con batería de 5 kWh y carga de una caseta."""
    variables = {"irradiance_wm2": ("W/m²", 0, 1100), "pv_power_w": ("W", 0, 1200),
                 "load_w": ("W", 0, 900), "battery_soc_pct": ("%", 0, 100),
                 "load_shed": ("0/1", 0, 1)}
    default_setpoint = {"min_soc_pct": 20, "base_load_w": 220}
    CAP_WH = 5000

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.soc = 70.0

    def step(self, ts, dt, w):
        cloud = w["cloud_cover_pct"] / 100
        irr = 1000 * sun(ts) * (1 - 0.75 * cloud) * random.uniform(0.95, 1.05)
        pv = irr * 6 * 0.18 * (1 - 0.004 * max(0, w["temperature_c"] - 25))
        h = local_hour(ts)
        load = self.setpoint["base_load_w"] + (380 if 18 <= h < 22 else 0) + random.gauss(0, 15)
        shed = 1 if self.soc < self.setpoint["min_soc_pct"] else 0
        if shed:
            load *= 0.4
        self.soc += (pv - load) * dt / 3600 / self.CAP_WH * 100
        self.soc = max(0, min(100, self.soc))
        return {"irradiance_wm2": irr, "pv_power_w": pv, "load_w": load,
                "battery_soc_pct": self.soc, "load_shed": shed}


class WaterTank(Twin):
    """Tanque de agua de 2000 L con bomba y control por histéresis."""
    variables = {"level_pct": ("%", 0, 100), "inflow_lpm": ("L/min", 0, 60),
                 "outflow_lpm": ("L/min", 0, 40), "pump_on": ("0/1", 0, 1)}
    default_setpoint = {"low_pct": 30, "high_pct": 90, "pump_mode": 0}
    CAP_L = 2000

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.level, self.pump = 60.0, 0

    def step(self, ts, dt, w):
        h = local_hour(ts)
        peak = 14 if (6 <= h < 8 or 12 <= h < 13 or 19 <= h < 21) else 3 if 6 <= h < 23 else 0.5
        out = max(0, peak + random.gauss(0, 1.2))
        mode = int(self.setpoint.get("pump_mode", 0))
        if mode == 1:
            self.pump = 1
        elif mode == 2:
            self.pump = 0
        elif self.level < self.setpoint["low_pct"]:
            self.pump = 1
        elif self.level > self.setpoint["high_pct"]:
            self.pump = 0
        inflow = 40 * self.pump
        self.level += (inflow - out) * dt / 60 / self.CAP_L * 100
        self.level = max(0, min(100, self.level))
        return {"level_pct": self.level, "inflow_lpm": inflow, "outflow_lpm": out, "pump_on": self.pump}
