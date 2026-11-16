"""Ejecuta en la nube los 10 dispositivos virtuales (API, gemelos digitales y dataset).
Los 2 dispositivos reales corren en el computador del estudiante (real_devices.py).

  python run_cloud.py                    # modo normal
  python run_cloud.py --backfill-days 5  # pre-carga historial de gemelos y dataset + 24 h reales de la API
"""
import argparse
import logging
import os
import threading
import time

from api_devices import AirQualityStation, WeatherStation
from common import mqtt_client
from dataset_devices import OfficeReplay
from twins import ClassroomHVAC, Greenhouse, SolarBattery, WaterTank, WeatherInput

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("run_cloud")

UNAB = (7.1170, -73.1053)          # UNAB, Bucaramanga
FLORIDA = (7.0647, -73.0898)       # Floridablanca


def build():
    weather = WeatherInput("api-weather-unab")
    devs = []
    if os.getenv("AZURE_MAPS_KEY"):
        devs += [WeatherStation("api-weather-unab", *UNAB),
                 WeatherStation("api-weather-floridablanca", *FLORIDA),
                 AirQualityStation("api-air-unab", *UNAB)]
    else:
        log.warning("AZURE_MAPS_KEY vacía: los 3 dispositivos API quedan desactivados")
    devs += [Greenhouse("twin-greenhouse-01", weather), ClassroomHVAC("twin-hvac-aula-01", weather),
             SolarBattery("twin-solar-01", weather), WaterTank("twin-tank-01", weather),
             OfficeReplay("ds-office-a", "datatraining.txt"),
             OfficeReplay("ds-office-b", "datatest.txt"),
             OfficeReplay("ds-office-c", "datatest2.txt")]
    return weather, devs


def backfill(devs, days):
    now = time.time()
    start = now - days * 86400
    for d in devs:
        n = 0
        if d.kind == "api":
            if hasattr(d, "backfill_24h"):
                try:
                    n = d.backfill_24h()
                except Exception as e:      # la API puede fallar: no bloquea el resto
                    log.warning("%s: sin histórico de API (%s)", d.id, e)
            continue
        step = 300 if d.kind == "twin" else 120
        if d.kind == "twin":
            d.live, d.t_prev = False, None
        t = start
        while t < now:
            d.publish_values(d.read(t), t)
            n += 1
            t += step
            if n % 200 == 0:
                time.sleep(0.2)              # no saturar al broker
        if d.kind == "twin":
            d.live, d.t_prev = True, None
        log.info("%s: %d puntos históricos publicados (%s)", d.id, n,
                 "simulación del gemelo" if d.kind == "twin" else "reproducción del dataset")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-days", type=float, default=0)
    ap.add_argument("--backfill-only", action="store_true", help="pre-carga y termina")
    args = ap.parse_args()

    weather, devs = build()
    cli, host, port = mqtt_client("cloud-devices")
    connected = threading.Event()
    cli.on_connect = lambda c, u, f, rc, p: (weather.attach(c), [d.attach(c) for d in devs], connected.set())
    cli.connect(host, port, 60)
    cli.loop_start()
    if not connected.wait(30):
        raise SystemExit("No se pudo conectar al broker MQTT")
    log.info("conectado a %s:%s con %d dispositivos", host, port, len(devs))

    if args.backfill_days:
        backfill(devs, args.backfill_days)
        if args.backfill_only:
            time.sleep(5)
            cli.loop_stop()
            return

    stop = threading.Event()
    for d in devs:
        threading.Thread(target=d.loop, args=(stop,), daemon=True, name=d.id).start()
        time.sleep(0.5)
    try:
        while True:
            time.sleep(60)
            for d in devs:
                d.publish_state()            # latido de estado cada minuto
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
