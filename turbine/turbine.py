"""
CSU33D03 - Main Project 2025-26
TURBINE NODE  –  Dynamic multi-instance version

Usage:
    python3 turbine.py                        # defaults to TURBINE-01, base port 5000
    python3 turbine.py TURBINE-02             # custom ID, base port 5010
    python3 turbine.py TURBINE-03 5020        # custom ID and custom base port

Each turbine instance gets its own 4 sensor ports starting from base_port:
    TURBINE-01  → ports 5001, 5002, 5003, 5004
    TURBINE-02  → ports 5011, 5012, 5013, 5014
    TURBINE-03  → ports 5021, 5022, 5023, 5024
    ...and so on
"""

import socket, threading, time, random, json, logging, sys
from datetime import datetime

# ── !! FILL THIS IN !! ────────────────────────────────────────────────────────
SATELLITE_HOST = "127.0.0.1"   # ← replace with Laptop B's IP e.g. "192.168.1.11"
SATELLITE_PORT = 9000
# ─────────────────────────────────────────────────────────────────────────────

RECONNECT_DELAY = 5
SENSOR_INTERVAL = 2

# ── Parse command line args ───────────────────────────────────────────────────
TURBINE_ID   = sys.argv[1] if len(sys.argv) > 1 else "TURBINE-01"
BASE_PORT    = int(sys.argv[2]) if len(sys.argv) > 2 else (
    5000 + (int(TURBINE_ID.split("-")[-1]) * 10)
    if TURBINE_ID.split("-")[-1].isdigit() else 5001
)

SENSOR_NAMES = ["wind_speed", "power_output", "rotor_rpm", "temperature"]
SENSOR_PORTS = {name: BASE_PORT + i + 1 for i, name in enumerate(SENSOR_NAMES)}
SENSOR_UNITS = {
    "wind_speed":   "m/s",
    "power_output": "kW",
    "rotor_rpm":    "RPM",
    "temperature":  "°C"
}

# ── Logging (include turbine ID in every line) ────────────────────────────────
logging.basicConfig(level=logging.INFO,
    format=f"%(asctime)s [{TURBINE_ID}] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(TURBINE_ID)

# ── Shared turbine state ──────────────────────────────────────────────────────
state = {
    "yaw_angle":     180.0,
    "blade_pitch":   15.0,
    "emergency_stop": False,
    "online":        True
}
state_lock = threading.Lock()

# ── Sensor readers ────────────────────────────────────────────────────────────
def read_wind_speed():
    return round(12.0 + random.gauss(0, 2.5), 2)

def read_power_output():
    ws = read_wind_speed()
    return round(min(2000.0, max(0.0, 0.5 * 1.225 * 3.14159 * (40*2) * (ws*3) / 1000)), 1)

def read_rotor_rpm():
    with state_lock: pitch = state["blade_pitch"]
    return max(0.0, round(15.0 - pitch * 0.1 + random.gauss(0, 0.5), 2))

def read_temperature():
    return round(35.0 + random.gauss(0, 3.0), 1)

READERS = {
    "wind_speed":   read_wind_speed,
    "power_output": read_power_output,
    "rotor_rpm":    read_rotor_rpm,
    "temperature":  read_temperature
}

# ── Sensor socket servers (one per port) ──────────────────────────────────────
def sensor_server(name, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(5)
        log.info(f"  Sensor '{name}' listening on port {port}")
        while True:
            try:
                conn, addr = srv.accept()
                threading.Thread(target=_handle_sensor, args=(conn, name), daemon=True).start()
            except Exception as e:
                log.error(f"Sensor '{name}' error: {e}")
                time.sleep(1)

def _handle_sensor(conn, name):
    with conn:
        try:
            req = conn.recv(64).decode().strip()
            if req == "READ":
                payload = {
                    "sensor":     name,
                    "value":      READERS[name](),
                    "unit":       SENSOR_UNITS[name],
                    "turbine_id": TURBINE_ID,
                    "timestamp":  datetime.utcnow().isoformat() + "Z"
                }
                conn.sendall((json.dumps(payload) + "\n").encode())
        except Exception:
            pass

# ── Telemetry builder ─────────────────────────────────────────────────────────
def build_telemetry():
    with state_lock: s = dict(state)
    return {
        "type":       "TELEMETRY",
        "turbine_id": TURBINE_ID,
        "timestamp":  datetime.utcnow().isoformat() + "Z",
        "sensors":    {k: READERS[k]() for k in READERS},
        "actuators":  {"yaw_angle": s["yaw_angle"], "blade_pitch": s["blade_pitch"]},
        "status":     {"emergency_stop": s["emergency_stop"], "online": s["online"]},
        "sensor_ports": SENSOR_PORTS   # advertise ports so ground can query directly
    }

# ── Command handler ───────────────────────────────────────────────────────────
def apply_command(cmd):
    action  = cmd.get("action", "")
    params  = cmd.get("params", {})
    success = True
    message = "OK"

    with state_lock:
        if action == "SET_YAW":
            angle = max(0.0, min(360.0, float(params.get("angle", state["yaw_angle"]))))
            state["yaw_angle"] = angle
            message = f"Yaw set to {angle}°"
            log.info(f"⚙️  {message}")

        elif action == "SET_PITCH":
            pitch = max(0.0, min(90.0, float(params.get("pitch", state["blade_pitch"]))))
            state["blade_pitch"] = pitch
            message = f"Pitch set to {pitch}°"
            log.info(f"⚙️  {message}")

        elif action == "EMERGENCY_STOP":
            state["emergency_stop"] = True
            state["blade_pitch"]    = 90.0
            message = "EMERGENCY STOP – blades feathered"
            log.warning(f"⚠️  {message}")

        elif action == "RESUME":
            state["emergency_stop"] = False
            message = "Resumed normal operation"
            log.info(f"✅ {message}")

        elif action == "PING":
            message = "PONG"

        else:
            success = False
            message = f"Unknown action: {action}"

    return {
        "type":       "ACK",
        "turbine_id": TURBINE_ID,
        "action":     action,
        "success":    success,
        "message":    message,
        "timestamp":  datetime.utcnow().isoformat() + "Z"
    }

# ── Satellite uplink loop ─────────────────────────────────────────────────────
def satellite_link():
    while True:
        log.info(f"Connecting to satellite {SATELLITE_HOST}:{SATELLITE_PORT} …")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(10)
                sock.connect((SATELLITE_HOST, SATELLITE_PORT))
                sock.settimeout(SENSOR_INTERVAL + 1)

                # Register with satellite
                reg = {
                    "type":         "REGISTER",
                    "node_type":    "TURBINE",
                    "turbine_id":   TURBINE_ID,
                    "services":     list(SENSOR_PORTS.keys()),
                    "sensor_ports": SENSOR_PORTS,
                    "base_port":    BASE_PORT,
                    "timestamp":    datetime.utcnow().isoformat() + "Z"
                }
                sock.sendall((json.dumps(reg) + "\n").encode())
                log.info("✅ Satellite link UP")

                buffer  = ""
                last_tx = 0.0

                while True:
                    now = time.time()

                    # Send telemetry on interval
                    if now - last_tx >= SENSOR_INTERVAL:
                        sock.sendall((json.dumps(build_telemetry()) + "\n").encode())
                        log.debug("📡 Telemetry sent")
                        last_tx = now

                    # Receive messages from satellite
                    try:
                        chunk = sock.recv(4096).decode()
                        if not chunk:
                            log.warning("Satellite closed connection")
                            break
                        buffer += chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                msg = json.loads(line)
                                t   = msg.get("type", "")
                                if t == "COMMAND":
                                    ack = apply_command(msg)
                                    sock.sendall((json.dumps(ack) + "\n").encode())
                                elif t == "REGISTER_ACK":
                                    log.info(f"Registered ✓  (satellite: {msg.get('satellite_id')})")
                            except json.JSONDecodeError:
                                pass
                    except socket.timeout:
                        pass

        except (ConnectionRefusedError, OSError) as e:
            log.warning(f"Satellite unreachable: {e}  – retry in {RECONNECT_DELAY}s …")
            time.sleep(RECONNECT_DELAY)
        except Exception as e:
            log.error(f"Link error: {e}  – retry in {RECONNECT_DELAY}s …")
            time.sleep(RECONNECT_DELAY)

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    log.info("=" * 55)
    log.info(f"  🌬️  TURBINE  –  {TURBINE_ID}")
    log.info(f"  Sensor ports: {SENSOR_PORTS}")
    log.info("=" * 55)

    # Start one sensor server thread per sensor
    for name, port in SENSOR_PORTS.items():
        threading.Thread(target=sensor_server, args=(name, port), daemon=True).start()

    time.sleep(0.3)
    satellite_link()

if __name__ == "__main__":
    main()