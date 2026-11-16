"""Puente MQTT -> InfluxDB 2.

Suscribe capstone/# y escribe en el bucket:
  telemetry,device=<id>,kind=<tipo> <variable>=<valor>,...   <ts ms>
  device_state,device=<id>,kind=<tipo> power=1|0,period_s=..  <ts ms>
Escribe por lotes cada 2 s (API HTTP /api/v2/write, line protocol).
"""
import json
import logging
import os
import threading
import time

import requests

from devices.common import mqtt_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s bridge %(message)s")
log = logging.getLogger()
URL = (f"{os.getenv('INFLUX_URL', 'http://influxdb:8086')}/api/v2/write"
       f"?org={os.environ['INFLUX_ORG']}&bucket={os.environ['INFLUX_BUCKET']}&precision=ms")
HDR = {"Authorization": f"Token {os.environ['INFLUX_TOKEN']}"}
buf, lock = [], threading.Lock()


def esc(s):
    return str(s).replace(" ", r"\ ").replace(",", r"\,").replace("=", r"\=")


def to_lines(topic, payload):
    parts = topic.split("/")                       # capstone/<kind>/<id>/<canal>
    if len(parts) != 4:
        return []
    _, kind, dev, chan = parts
    d = json.loads(payload)
    ts = int(d.get("ts") or time.time() * 1000)
    tags = f"device={esc(dev)},kind={esc(kind)}"
    if chan == "telemetry":
        fields = ",".join(f"{esc(k)}={float(v)}" for k, v in d.get("values", {}).items()
                          if isinstance(v, (int, float)))
        return [f"telemetry,{tags} {fields} {ts}"] if fields else []
    if chan == "state":
        f = [f"power={1.0 if d.get('power') == 'on' else 0.0}", f"period_s={float(d.get('period_s', 0))}"]
        for grp in ("offset", "gain", "setpoint"):
            f += [f"{grp}_{esc(k)}={float(v)}" for k, v in (d.get(grp) or {}).items()]
        return [f"device_state,{tags} {','.join(f)} {ts}"]
    return []


def on_message(_c, _u, msg):
    try:
        lines = to_lines(msg.topic, msg.payload)
    except (ValueError, TypeError):
        return
    with lock:
        buf.extend(lines)


def flusher():
    while True:
        time.sleep(2)
        with lock:
            lines = buf[:]
            buf.clear()
        while lines:
            chunk = lines[:5000]
            try:
                r = requests.post(URL, data="\n".join(chunk).encode(), headers=HDR, timeout=15)
                r.raise_for_status()
                lines = lines[5000:]
                log.info("escritos %d puntos", len(chunk))
            except requests.RequestException as e:
                log.warning("InfluxDB no disponible (%s), reintento en 5 s", e)
                time.sleep(5)


def main():
    threading.Thread(target=flusher, daemon=True).start()
    cli, host, port = mqtt_client("bridge")
    cli.on_connect = lambda c, u, f, rc, p: (c.subscribe("capstone/#", qos=1), log.info("suscrito a capstone/#"))
    cli.on_message = on_message
    while True:
        try:
            cli.connect(host, port, 60)
            break
        except OSError:
            time.sleep(5)
    cli.loop_forever()


if __name__ == "__main__":
    main()
