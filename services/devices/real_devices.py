"""Dispositivos REALES: corren en el computador del estudiante y miden el propio equipo.

  real-pc-01   CPU, RAM, disco, batería y temperatura de CPU (psutil)
  real-net-01  latencia y pérdida de paquetes de la conexión a Internet (TCP connect)

Se conectan al broker de la nube por MQTT sobre WebSocket seguro (wss://<dominio>/mqtt).

  pip install paho-mqtt psutil
  python real_devices.py --host iot-juanrojas-capstone.mexicocentral.cloudapp.azure.com --password <MQTT_REAL_PASSWORD>
"""
import argparse
import logging
import socket
import statistics
import threading
import time

import psutil

from common import Device, mqtt_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")


class PCMonitor(Device):
    kind = "real"
    protocol = "MQTT sobre WebSocket TLS (wss)"
    default_period = 30
    variables = {"cpu_pct": ("%", 0, 100), "ram_pct": ("%", 0, 100), "disk_pct": ("%", 0, 100),
                 "battery_pct": ("%", 0, 100), "cpu_temp_c": ("°C", 30, 100),
                 "plugged_in": ("0/1", 0, 1)}

    def read(self, ts):
        v = {"cpu_pct": psutil.cpu_percent(interval=1), "ram_pct": psutil.virtual_memory().percent,
             "disk_pct": psutil.disk_usage("/").percent}
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if bat:
            v["battery_pct"], v["plugged_in"] = bat.percent, int(bool(bat.power_plugged))
        temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
        cores = [t.current for ts_ in temps.values() for t in ts_ if t.current]
        if cores:
            v["cpu_temp_c"] = max(cores)
        return v


class NetMonitor(Device):
    kind = "real"
    protocol = "MQTT sobre WebSocket TLS (wss)"
    default_period = 30
    TARGETS = [("8.8.8.8", 53), ("1.1.1.1", 443), ("www.unab.edu.co", 443)]
    variables = {"latency_ms": ("ms", 0, 500), "jitter_ms": ("ms", 0, 200),
                 "loss_pct": ("%", 0, 100)}

    def read(self, ts):
        samples, fails = [], 0
        for _ in range(3):
            for host, port in self.TARGETS:
                t0 = time.perf_counter()
                try:
                    with socket.create_connection((host, port), timeout=2):
                        samples.append((time.perf_counter() - t0) * 1000)
                except OSError:
                    fails += 1
        total = len(samples) + fails
        return {"latency_ms": statistics.median(samples) if samples else None,
                "jitter_ms": statistics.pstdev(samples) if len(samples) > 1 else 0,
                "loss_pct": 100 * fails / total}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True, help="dominio de la plataforma")
    ap.add_argument("--user", default="real-device")
    ap.add_argument("--password", required=True)
    ap.add_argument("--insecure-tcp", action="store_true", help="pruebas locales: MQTT TCP 1883 sin TLS")
    a = ap.parse_args()
    if a.insecure_tcp:
        cli, host, port = mqtt_client("real", a.host, 1883, a.user, a.password)
    else:
        cli, host, port = mqtt_client("real", a.host, 443, a.user, a.password,
                                      transport="websockets", tls=True)
    devs = [PCMonitor("real-pc-01"), NetMonitor("real-net-01")]
    cli.on_connect = lambda c, u, f, rc, p: [d.attach(c) for d in devs]
    cli.connect(host, port, 60)
    cli.loop_start()
    stop = threading.Event()
    for d in devs:
        threading.Thread(target=d.loop, args=(stop,), daemon=True).start()
    try:
        while True:
            time.sleep(60)
            for d in devs:
                d.publish_state()
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
