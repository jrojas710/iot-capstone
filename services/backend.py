"""Backend de la plataforma (FastAPI).

  GET  /api/devices              catálogo + último estado y última lectura (caché MQTT)
  GET  /api/devices/{id}         detalle de un dispositivo
  POST /api/devices/{id}/cmd     RETROCESO: envía un comando (requiere cabecera X-Api-Key)
  POST /api/alerts               webhook de Grafana: registra alertas en InfluxDB
  GET  /api/health               estado del servicio
  /                              panel web (front) para monitorear y controlar
"""
import json
import os
import threading
import time

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from devices.common import mqtt_client

ADMIN = os.environ["ADMIN_TOKEN"]
ALERT_TOKEN = os.getenv("ALERT_TOKEN", "")
INFLUX = (f"{os.getenv('INFLUX_URL', 'http://influxdb:8086')}/api/v2/write"
          f"?org={os.environ['INFLUX_ORG']}&bucket={os.environ['INFLUX_BUCKET']}&precision=ms")
STATIC = os.path.join(os.path.dirname(__file__), "static")

devices, lock = {}, threading.Lock()
app = FastAPI(title="IoT Capstone – Juan Camilo Rojas", docs_url="/api/docs", openapi_url="/api/openapi.json")


def on_message(_c, _u, msg):
    parts = msg.topic.split("/")
    if len(parts) != 4:
        return
    _, kind, dev, chan = parts
    try:
        data = json.loads(msg.payload) if msg.payload else {}
    except ValueError:
        return
    with lock:
        d = devices.setdefault(dev, {"id": dev, "kind": kind, "state": {}, "last": {}, "last_ts": None,
                                     "cmd": {}})
        if chan == "telemetry":
            d["last"], d["last_ts"] = data.get("values", {}), data.get("ts")
        elif chan == "state":
            d["state"] = data
        elif chan == "cmd":
            d["cmd"] = data


cli, host, port = mqtt_client("backend")
cli.on_connect = lambda c, u, f, rc, p: c.subscribe("capstone/#", qos=1)
cli.on_message = on_message


@app.on_event("startup")
def start_mqtt():
    def run():
        while True:
            try:
                cli.connect(host, port, 60)
                cli.loop_forever()
            except OSError:
                time.sleep(5)
    threading.Thread(target=run, daemon=True).start()


@app.get("/api/health")
def health():
    return {"ok": True, "mqtt": cli.is_connected(), "devices": len(devices)}


@app.get("/api/devices")
def list_devices():
    now = time.time() * 1000
    with lock:
        out = []
        for d in sorted(devices.values(), key=lambda x: (x["kind"], x["id"])):
            age = (now - d["last_ts"]) / 1000 if d["last_ts"] else None
            out.append({**d, "age_s": age})
    return out


@app.get("/api/devices/{dev}")
def get_device(dev: str):
    with lock:
        if dev not in devices:
            raise HTTPException(404, "dispositivo no encontrado")
        return devices[dev]


ALLOWED = {"power", "period_s", "offset", "gain", "setpoint", "reset"}


@app.post("/api/devices/{dev}/cmd")
async def send_cmd(dev: str, request: Request, x_api_key: str = Header(default="")):
    if x_api_key != ADMIN:
        raise HTTPException(401, "llave de administración inválida")
    with lock:
        d = devices.get(dev)
    if not d:
        raise HTTPException(404, "dispositivo no encontrado")
    cmd = await request.json()
    if not isinstance(cmd, dict) or not set(cmd) <= ALLOWED:
        raise HTTPException(400, f"campos permitidos: {sorted(ALLOWED)}")
    cmd["ts"] = int(time.time() * 1000)
    # retained: si el dispositivo está desconectado, aplica el comando al reconectarse
    cli.publish(f"capstone/{d['kind']}/{dev}/cmd", json.dumps(cmd), qos=1, retain=True)
    return {"sent": cmd, "topic": f"capstone/{d['kind']}/{dev}/cmd"}


@app.post("/api/alerts")
async def alerts(request: Request, authorization: str = Header(default="")):
    if ALERT_TOKEN and authorization != f"Bearer {ALERT_TOKEN}":
        raise HTTPException(401)
    body = await request.json()
    lines = []
    for a in body.get("alerts", []):
        name = a.get("labels", {}).get("alertname", "alerta").replace(" ", r"\ ").replace(",", r"\,")
        dev = a.get("labels", {}).get("device", "na")
        firing = 1.0 if a.get("status") == "firing" else 0.0
        summ = (a.get("annotations", {}).get("summary") or "").replace('"', "'")
        lines.append(f'alerts,alertname={name},device={dev} firing={firing},summary="{summ}" '
                     f'{int(time.time() * 1000)}')
    if lines:
        requests.post(INFLUX, data="\n".join(lines).encode(),
                      headers={"Authorization": f"Token {os.environ['INFLUX_TOKEN']}"}, timeout=10)
    return {"received": len(lines)}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
