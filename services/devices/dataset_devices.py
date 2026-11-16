"""Dispositivos tipo DataSet: reproducen el dataset público
"Occupancy Detection" (Candanedo & Feldheim, 2016, UCI Machine Learning Repository).

Cada archivo es una oficina distinta medida cada minuto en febrero de 2015.
La reproducción respeta la cadencia original (1 fila / minuto) y la hora del día:
la fila que se publica es la que corresponde al mismo minuto del día del registro.
"""
import csv
import os
from datetime import datetime

from common import Device

DATA = os.path.join(os.path.dirname(__file__), "data")


def load(fname):
    rows = []
    with open(os.path.join(DATA, fname), newline="") as f:
        reader = csv.reader(f)
        next(reader)
        for r in reader:
            ts = datetime.strptime(r[1], "%Y-%m-%d %H:%M:%S")
            rows.append((ts, {"temperature_c": float(r[2]), "humidity_pct": float(r[3]),
                              "light_lux": float(r[4]), "co2_ppm": float(r[5]),
                              "humidity_ratio": float(r[6]), "occupancy": int(r[7])}))
    return rows


class OfficeReplay(Device):
    kind = "dataset"
    protocol = "CSV (UCI) → MQTT (QoS 1)"
    default_period = 60
    variables = {"temperature_c": ("°C", 19, 25), "humidity_pct": ("%", 16, 40),
                 "light_lux": ("lux", 0, 1700), "co2_ppm": ("ppm", 410, 2080),
                 "humidity_ratio": ("kg/kg", 0.0026, 0.0065), "occupancy": ("0/1", 0, 1)}

    def __init__(self, device_id, fname, **cfg):
        super().__init__(device_id, **cfg)
        self.fname = fname
        self.rows = load(fname)
        self.n = len(self.rows)
        t0 = self.rows[0][0]
        self.start_minute = t0.hour * 60 + t0.minute
        self.start_wd = t0.weekday()

    def index_for(self, ts):
        """Fila equivalente: mismo minuto del día y misma posición en la semana grabada."""
        local = datetime.utcfromtimestamp(ts - 5 * 3600)
        minute = local.hour * 60 + local.minute
        day_offset = (local.weekday() - self.start_wd) % 7
        idx = day_offset * 1440 + minute - self.start_minute
        if idx < 0:                      # antes de la hora de inicio del registro: día siguiente
            idx += 1440
        while idx >= self.n:             # registro más corto que una semana: día anterior equivalente
            idx -= 1440
        return idx % self.n

    def read(self, ts):
        return dict(self.rows[self.index_for(ts)][1])
