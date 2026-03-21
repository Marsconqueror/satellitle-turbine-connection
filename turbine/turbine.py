"""
CSU33D03 - Main Project 2025-26
TURBINE NODE  –  Device A  (single-machine mode: all on localhost)
"""

import socket, threading, time, random, json, logging, sys
from datetime import datetime

SATELLITE_HOST = "127.0.0.1"
SATELLITE_PORT = 9000

TURBINE_ID   = "TURBINE-01"
SENSOR_PORTS = {"wind_speed": 5001, "power_output": 5002, "rotor_rpm": 5003, "temperature": 5004}
SENSOR_UNITS = {"wind_speed": "m/s", "power_output": "kW", "rotor_rpm": "RPM", "temperature": "°C"}
RECONNECT_DELAY = 5
SENSOR_INTERVAL = 2

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [TURBINE] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("turbine")

state = {"yaw_angle": 180.0, "blade_pitch": 15.0, "emergency_stop": False, "online": True}
state_lock = threading.Lock()

# ── Sensors ──────────────────────────────────────────────────────────────────
def read_wind_speed():    return round(12.0 + random.gauss(0, 2.5), 2)
def read_power_output():
    ws = read_wind_speed()
    return round(min(2000.0, max(0.0, 0.5 * 1.225 * 3.14159 * (40**2) * (ws**3) / 1000)), 1)
def read_rotor_rpm():
    with state_lock: pitch = state["blade_pitch"]
    return max(0.0, round(15.0 - pitch * 0.1 + random.gauss(0, 0.5), 2))
def read_temperature():   return round(35.0 + random.gauss(0, 3.0), 1)

READERS = {"wind_speed": read_wind_speed, "power_output": read_power_output,
           "rotor_rpm": read_rotor_rpm,   "temperature": read_temperature}

# ── Sensor socket servers (one per port) ─────────────────────────────────────
def sensor_server(name, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(5)
        log.info(f"  Sensor '{name}' on port {port}")
        while True:
            try:
                conn, addr = srv.accept()
                threading.Thread(target=_handle_sensor, args=(conn, name), daemon=True).start()
            except Exception as e:
                log.error(f"Sensor '{name}' error: {e}"); time.sleep(1)

def _handle_sensor(conn, name):
    with conn:
        try:
            if conn.recv(64).decode().strip() == "READ":
                conn.sendall((json.dumps({"sensor": name, "value": READERS[name](),
                    "unit": SENSOR_UNITS[name], "turbine_id": TURBINE_ID,
                    "timestamp": datetime.utcnow().isoformat()+"Z"}) + "\n").encode())
        except Exception: pass

# ── Telemetry & command logic ─────────────────────────────────────────────────
def build_telemetry():
    with state_lock: s = dict(state)
    return {"type": "TELEMETRY", "turbine_id": TURBINE_ID,
            "timestamp": datetime.utcnow().isoformat()+"Z",
            "sensors":   {k: READERS[k]() for k in READERS},
            "actuators": {"yaw_angle": s["yaw_angle"], "blade_pitch": s["blade_pitch"]},
            "status":    {"emergency_stop": s["emergency_stop"], "online": s["online"]}}

def apply_command(cmd):
    action, params, success, message = cmd.get("action",""), cmd.get("params",{}), True, "OK"
    with state_lock:
        if action == "SET_YAW":
            angle = max(0.0, min(360.0, float(params.get("angle", state["yaw_angle"]))))
            state["yaw_angle"] = angle; message = f"Yaw set to {angle}°"; log.info(f"⚙️  {message}")
        elif action == "SET_PITCH":
            pitch = max(0.0, min(90.0, float(params.get("pitch", state["blade_pitch"]))))
            state["blade_pitch"] = pitch; message = f"Pitch set to {pitch}°"; log.info(f"⚙️  {message}")
        elif action == "EMERGENCY_STOP":
            state["emergency_stop"] = True; state["blade_pitch"] = 90.0
            message = "EMERGENCY STOP – blades feathered"; log.warning(f"⚠️  {message}")
        elif action == "RESUME":
            state["emergency_stop"] = False; message = "Resumed"; log.info(f"✅ {message}")
        elif action == "PING": message = "PONG"
        else: success = False; message = f"Unknown: {action}"
    return {"type": "ACK", "turbine_id": TURBINE_ID, "action": action,
            "success": success, "message": message, "timestamp": datetime.utcnow().isoformat()+"Z"}

# ── Satellite uplink ──────────────────────────────────────────────────────────
def satellite_link():
    while True:
        log.info(f"Connecting to satellite {SATELLITE_HOST}:{SATELLITE_PORT} …")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(10); sock.connect((SATELLITE_HOST, SATELLITE_PORT))
                sock.settimeout(SENSOR_INTERVAL + 1)
                sock.sendall((json.dumps({"type": "REGISTER", "node_type": "TURBINE",
                    "turbine_id": TURBINE_ID, "services": list(SENSOR_PORTS.keys()),
                    "sensor_ports": SENSOR_PORTS,
                    "timestamp": datetime.utcnow().isoformat()+"Z"}) + "\n").encode())
                log.info("✅ Satellite link UP")
                buffer = ""; last_tx = 0.0
                while True:
                    now = time.time()
                    if now - last_tx >= SENSOR_INTERVAL:
                        sock.sendall((json.dumps(build_telemetry()) + "\n").encode())
                        log.debug("📡 Telemetry sent"); last_tx = now
                    try:
                        chunk = sock.recv(4096).decode()
                        if not chunk: log.warning("Satellite closed"); break
                        buffer += chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1); line = line.strip()
                            if not line: continue
                            try:
                                msg = json.loads(line); t = msg.get("type","")
                                if t == "COMMAND":
                                    sock.sendall((json.dumps(apply_command(msg))+"\n").encode())
                                elif t == "REGISTER_ACK":
                                    log.info(f"Registered ✓ (sat: {msg.get('satellite_id')})")
                            except json.JSONDecodeError: pass
                    except socket.timeout: pass
        except (ConnectionRefusedError, OSError) as e:
            log.warning(f"Satellite unreachable: {e}  – retry in {RECONNECT_DELAY}s …")
            time.sleep(RECONNECT_DELAY)
        except Exception as e:
            log.error(f"Link error: {e}  – retry in {RECONNECT_DELAY}s …"); time.sleep(RECONNECT_DELAY)

def main():
    log.info("="*55 + f"\n  🌬️  TURBINE  –  {TURBINE_ID}\n" + "="*55)
    for name, port in SENSOR_PORTS.items():
        threading.Thread(target=sensor_server, args=(name, port), daemon=True).start()
    time.sleep(0.3)
    satellite_link()

if __name__ == "__main__":
    main()