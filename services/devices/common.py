"""Base común de los dispositivos IoT del proyecto.

Cada dispositivo:
  - publica telemetría JSON en   capstone/<kind>/<device_id>/telemetry   (QoS 1)
  - publica su estado en         capstone/<kind>/<device_id>/state       (QoS 1, retained)
  - escucha comandos en          capstone/<kind>/<device_id>/cmd         (QoS 1, retained)

Retroceso (comandos aceptados, JSON):
  {"power": "on" | "off"}                    encender / apagar el envío
  {"period_s": 30}                           cambiar el periodo de muestreo
  {"offset": {"temperature_c": -0.5}}         calibración aditiva por variable
  {"gain": {"temperature_c": 1.02}}           calibración multiplicativa por variable
  {"setpoint": {"setpoint_c": 23}}            ajustes propios del dispositivo (gemelos)
  {"reset": true}                             vuelve a la configuración de fábrica
"""
import json
import logging
import os
import random
import threading
import time

import paho.mqtt.client as mqtt

log = logging.getLogger("devices")
PREFIX = "capstone"


def mqtt_client(client_id, host=None, port=None, user=None, password=None,
                transport="tcp", tls=False, ws_path="/mqtt"):
    host = host or os.getenv("MQTT_HOST", "localhost")
    port = int(port or os.getenv("MQTT_PORT", "1883"))
    user = user or os.getenv("MQTT_USER")
    password = password or os.getenv("MQTT_PASSWORD")
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                      client_id=f"{client_id}-{random.randint(0, 99999)}",
                      transport=transport)
    if transport == "websockets":
        cli.ws_set_options(path=ws_path)
    if tls:
        cli.tls_set()
    if user:
        cli.username_pw_set(user, password)
    cli.reconnect_delay_set(1, 30)
    cli.max_queued_messages_set(0)
    return cli, host, port


class Device:
    """Dispositivo genérico con telemetría periódica y canal de comandos."""

    kind = "generic"
    protocol = "MQTT"
    variables = {}          # nombre -> (unidad, min, max)
    default_period = 60

    def __init__(self, device_id, client=None, **cfg):
        self.id = device_id
        self.cfg = cfg
        self.power = True
        self.period_s = self.default_period
        self.offset = {}
        self.gain = {}
        self.setpoint = dict(getattr(self, "default_setpoint", {}))
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.client = client
        self.base = f"{PREFIX}/{self.kind}/{self.id}"

    # ---------- MQTT ----------
    def attach(self, client):
        self.client = client
        client.message_callback_add(self.base + "/cmd", self._on_cmd)
        client.subscribe(self.base + "/cmd", qos=1)
        self.publish_state()

    def _on_cmd(self, _c, _u, msg):
        try:
            cmd = json.loads(msg.payload or b"{}")
        except ValueError:
            log.warning("%s: comando inválido %r", self.id, msg.payload)
            return
        if not cmd:
            return
        self.apply(cmd)

    def apply(self, cmd):
        with self.lock:
            if cmd.get("reset"):
                self.power, self.period_s = True, self.default_period
                self.offset, self.gain = {}, {}
                self.setpoint = dict(getattr(self, "default_setpoint", {}))
            if "power" in cmd:
                self.power = str(cmd["power"]).lower() in ("on", "1", "true")
            if "period_s" in cmd:
                self.period_s = max(5, min(3600, int(cmd["period_s"])))
            for k in ("offset", "gain", "setpoint"):
                if isinstance(cmd.get(k), dict):
                    getattr(self, k).update({a: float(b) for a, b in cmd[k].items()})
        log.info("%s: comando aplicado %s", self.id, cmd)
        self.wake.set()
        self.publish_state(last_cmd=cmd)

    def state(self):
        return {"power": "on" if self.power else "off", "period_s": self.period_s,
                "offset": self.offset, "gain": self.gain, "setpoint": self.setpoint,
                "kind": self.kind, "protocol": self.protocol}

    def publish_state(self, last_cmd=None):
        if not self.client:
            return
        st = self.state()
        st["ts"] = int(time.time() * 1000)
        if last_cmd is not None:
            st["last_cmd"] = last_cmd
        self.client.publish(self.base + "/state", json.dumps(st), qos=1, retain=True)

    # ---------- telemetría ----------
    def calibrate(self, values):
        out = {}
        for k, v in values.items():
            if isinstance(v, (int, float)) and v is not None:
                v = v * self.gain.get(k, 1.0) + self.offset.get(k, 0.0)
                out[k] = round(float(v), 3)
        return out

    def read(self, ts):
        """Devuelve dict variable->valor para el instante ts (epoch s). Se sobreescribe."""
        raise NotImplementedError

    def publish_values(self, values, ts=None):
        ts = ts or time.time()
        payload = {"device": self.id, "kind": self.kind, "ts": int(ts * 1000),
                   "values": self.calibrate(values)}
        if self.client and payload["values"]:
            self.client.publish(self.base + "/telemetry", json.dumps(payload), qos=1)
        return payload

    def tick(self):
        with self.lock:
            on = self.power
        if not on:
            return None
        values = self.read(time.time())
        return self.publish_values(values) if values else None

    def loop(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception:                         # un fallo no tumba al resto
                log.exception("%s: error leyendo", self.id)
            self.wake.wait(self.period_s)
            self.wake.clear()
            if stop.is_set():
                break
