"""Exporta las evidencias de la toma de datos desde InfluxDB.

En la VM:
  docker compose run --rm -v "$PWD/evidencias:/app/evidencias" backend python exportar.py --days 6

Genera en evidencias/:
  graficas/<dispositivo>.png    una figura por dispositivo con todas sus variables
                                 (promedio horario y banda mín–máx)
  resumen.json / resumen.md      promedio, máximo, mínimo, último, nº de muestras,
                                 inicio y fin de la toma por dispositivo y variable
  retroceso.csv                  historial de comandos aplicados (cambios de estado)
  alertas.csv                    alertas disparadas por Grafana
"""
import argparse
import csv
import io
import json
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests

URL = f"{os.getenv('INFLUX_URL', 'http://influxdb:8086')}/api/v2/query?org={os.environ['INFLUX_ORG']}"
HDR = {"Authorization": f"Token {os.environ['INFLUX_TOKEN']}", "Content-Type": "application/vnd.flux",
       "Accept": "application/csv"}
BOG = timezone(timedelta(hours=-5))
OUT = os.path.join(os.path.dirname(__file__), "evidencias")
COLORS = {"real": "#2f6fdf", "api": "#c26a12", "twin": "#7a3bb8", "dataset": "#0f7c78"}


def flux(q):
    r = requests.post(URL, data=q.encode(), headers=HDR, timeout=120)
    r.raise_for_status()
    rows = []
    for row in csv.DictReader(io.StringIO(r.text)):
        if row.get("_time") and row.get("_value") not in (None, ""):
            rows.append(row)
    return rows


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(BOG)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=6)
    a = ap.parse_args()
    os.makedirs(os.path.join(OUT, "graficas"), exist_ok=True)
    rng = f"range(start: -{int(a.days * 24)}h)"

    raw = flux(f'from(bucket: "iot") |> {rng} |> filter(fn: (r) => r._measurement == "telemetry")'
               ' |> keep(columns: ["_time", "_value", "_field", "device", "kind"])')
    series = defaultdict(lambda: defaultdict(list))
    kinds = {}
    for r in raw:
        series[r["device"]][r["_field"]].append((ts(r["_time"]), float(r["_value"])))
        kinds[r["device"]] = r["kind"]

    resumen = {}
    for dev in sorted(series, key=lambda d: (kinds[d], d)):
        fields = series[dev]
        resumen[dev] = {"tipo": kinds[dev], "variables": {}}
        n = len(fields)
        fig, axes = plt.subplots(n, 1, figsize=(11, 2.1 * n + 0.6), sharex=True, squeeze=False)
        for ax, (field, pts) in zip(axes[:, 0], sorted(fields.items())):
            pts.sort()
            vals = [v for _, v in pts]
            resumen[dev]["variables"][field] = {
                "promedio": round(sum(vals) / len(vals), 3), "maximo": round(max(vals), 3),
                "minimo": round(min(vals), 3), "ultimo": round(vals[-1], 3), "muestras": len(vals),
                "inicio": pts[0][0].isoformat(timespec="minutes"), "fin": pts[-1][0].isoformat(timespec="minutes"),
                "momento_max": pts[vals.index(max(vals))][0].isoformat(timespec="minutes"),
                "momento_min": pts[vals.index(min(vals))][0].isoformat(timespec="minutes")}
            c = COLORS.get(kinds[dev], "#333")
            if pts[-1][0] - pts[0][0] < timedelta(hours=6):      # serie corta: muestras crudas
                ax.plot([t for t, _ in pts], vals, color=c, lw=1.2, marker="o", ms=2.5)
            else:
                hourly = defaultdict(list)
                for t, v in pts:
                    hourly[t.replace(minute=0, second=0, microsecond=0)].append(v)
                hx = sorted(hourly)
                ax.fill_between(hx, [min(hourly[h]) for h in hx], [max(hourly[h]) for h in hx], color=c, alpha=.18, lw=0)
                ax.plot(hx, [sum(hourly[h]) / len(hourly[h]) for h in hx], color=c, lw=1.4)
            ax.set_ylabel(field, fontsize=8)
            ax.grid(alpha=.3)
            ax.tick_params(labelsize=8)
        axes[-1, 0].xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M", tz=BOG))
        fig.suptitle(f"{dev} ({kinds[dev]}) — promedio horario y rango mín–máx", fontsize=11)
        fig.tight_layout()
        fig.savefig(os.path.join(OUT, "graficas", f"{dev}.png"), dpi=110)
        plt.close(fig)

    with open(os.path.join(OUT, "resumen.json"), "w", encoding="utf-8") as f:
        json.dump(resumen, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "resumen.md"), "w", encoding="utf-8") as f:
        for dev, d in resumen.items():
            f.write(f"\n### {dev} ({d['tipo']})\n\n| Variable | Promedio | Máx | Mín | Muestras | Inicio | Fin |\n|---|---|---|---|---|---|---|\n")
            for k, v in d["variables"].items():
                f.write(f"| {k} | {v['promedio']} | {v['maximo']} | {v['minimo']} | {v['muestras']} | {v['inicio']} | {v['fin']} |\n")

    st = flux(f'from(bucket: "iot") |> {rng} |> filter(fn: (r) => r._measurement == "device_state")'
              ' |> keep(columns: ["_time", "_value", "_field", "device"])')
    last, changes = {}, []
    for r in sorted(st, key=lambda r: r["_time"]):
        key = (r["device"], r["_field"])
        if key in last and last[key] != r["_value"]:
            changes.append([ts(r["_time"]).isoformat(timespec="seconds"), r["device"], r["_field"], last[key], r["_value"]])
        last[key] = r["_value"]
    with open(os.path.join(OUT, "retroceso.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["hora", "dispositivo", "parametro", "antes", "despues"])
        w.writerows(changes)

    al = flux(f'from(bucket: "iot") |> {rng} |> filter(fn: (r) => r._measurement == "alerts" and r._field == "firing")')
    with open(os.path.join(OUT, "alertas.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["hora", "alerta", "dispositivo", "estado"])
        for r in sorted(al, key=lambda r: r["_time"]):
            w.writerow([ts(r["_time"]).isoformat(timespec="seconds"), r.get("alertname"), r.get("device"),
                        "activa" if float(r["_value"]) else "resuelta"])
    print(f"{len(resumen)} dispositivos, {len(raw)} puntos, {len(changes)} cambios de estado, {len(al)} alertas")


if __name__ == "__main__":
    main()
