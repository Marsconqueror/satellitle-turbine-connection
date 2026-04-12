"""
CSU33D03 - Main Project 2025-26
TURBINE NODE  -  Dynamic collaborative version (leader-based)

This script simulates an offshore wind turbine. One turbine acts as the LEADER
(elected by best link quality) and sends aggregated telemetry for the farm to
the satellite. Other turbines act as followers but still expose their own sensors
and can receive commands individually.

Main features:
- Sends telemetry to the satellite.
- Receives yaw, pitch, stop, and resume commands.
- Elects one turbine as the leader using simulated link quality.
- Sends a farm alert when wind becomes unsafe.
"""

import socket, threading, time, random, json, logging, sys, os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from security import sign_message, verify_message, strip_security_fields
from sensors import SensorSuite

# =========================================================
# NETWORK SETTINGS
# =========================================================
# Use localhost for running everything on one computer.
# For a real network test, set SATELLITE_HOST to the satellite computer IP.
SATELLITE_HOST = os.getenv("SATELLITE_HOST", "127.0.0.1")
SATELLITE_PORT = int(os.getenv("SATELLITE_TURBINE_PORT", "9000"))

RECONNECT_DELAY  = 5
SENSOR_INTERVAL  = 2
WIND_ESTOP_LIMIT = 25.0   # m/s — leader broadcasts FARM_ALERT above this

# =========================================================
# TURBINE IDENTITY
# Argv: python turbine.py <TURBINE-ID> [BASE_PORT] [T1,T2,T3,...]
# =========================================================
TURBINE_ID = sys.argv[1] if len(sys.argv) > 1 else "TURBINE-01"

BASE_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else (
    5000 + (int(TURBINE_ID.split("-")[-1]) * 10)
    if TURBINE_ID.split("-")[-1].isdigit() else 5001
)

# Allow passing the full farm list as a third argument so any number of turbines
# can participate without editing this file.
# e.g.  python turbine.py TURBINE-01 5010 TURBINE-01,TURBINE-02,TURBINE-03,TURBINE-04
# Can also be set via env var: FARM_TURBINES=TURBINE-01,TURBINE-02,...
if len(sys.argv) > 3:
    ALL_TURBINES = sys.argv[3].split(",")
elif os.getenv("FARM_TURBINES"):
    ALL_TURBINES = os.getenv("FARM_TURBINES").split(",")
else:
    ALL_TURBINES = ["TURBINE-01", "TURBINE-02", "TURBINE-03"]

# =========================================================
# DYNAMIC LEADER ELECTION  (best link quality wins)
# =========================================================
# Each turbine measures its own link quality independently in a real system.
# Here we simulate it with a fixed table that can be extended as ALL_TURBINES grows.
_BASE_LINK_QUALITY = {
    "TURBINE-01": {"avg_delay_ms": 140, "loss_pct": 2.8},
    "TURBINE-02": {"avg_delay_ms": 120, "loss_pct": 1.8},
    "TURBINE-03": {"avg_delay_ms": 160, "loss_pct": 3.5},
    "TURBINE-04": {"avg_delay_ms": 130, "loss_pct": 2.2},
    "TURBINE-05": {"avg_delay_ms": 150, "loss_pct": 3.0},
}

def _default_link(tid):
    """Generate plausible link stats for turbines not in the table."""
    seed = sum(ord(c) for c in tid)
    rng  = random.Random(seed)
    return {"avg_delay_ms": rng.randint(100, 180), "loss_pct": round(rng.uniform(1.5, 4.5), 1)}

SIMULATED_LINK_QUALITY = {
    tid: _BASE_LINK_QUALITY.get(tid, _default_link(tid))
    for tid in ALL_TURBINES
}

def compute_leader_score(delay_ms, loss_pct):
    """Higher score means a better turbine to act as leader."""
    return round(1000 - (delay_ms * 2) - (loss_pct * 100), 2)

def elect_leader():
    """Pick the turbine with the best simulated link quality."""
    best_tid   = None
    best_score = -999999
    for tid in ALL_TURBINES:
        stats = SIMULATED_LINK_QUALITY[tid]
        score = compute_leader_score(stats["avg_delay_ms"], stats["loss_pct"])
        if score > best_score:
            best_score = score
            best_tid   = tid
    return best_tid, best_score

LEADER_ID, LEADER_SCORE = elect_leader()
IS_LEADER = (TURBINE_ID == LEADER_ID)
FOLLOWERS = [tid for tid in ALL_TURBINES if tid != LEADER_ID]

# =========================================================
# SENSOR SUITES
# One SensorSuite for this turbine; one per follower so each
# gets independent, physically-consistent readings.
# =========================================================
_local_suite = SensorSuite(turbine_id=TURBINE_ID)
_follower_suites = {tid: SensorSuite(turbine_id=tid) for tid in FOLLOWERS}
# Tracks last-known actuator/status state for each follower (updated on ACK or command)
_follower_states = {
    tid: {"yaw_angle": 180.0, "blade_pitch": 15.0, "emergency_stop": False, "online": True}
    for tid in FOLLOWERS
}

# =========================================================
# SENSOR PORTS
# =========================================================
SENSOR_NAMES = ["wind_speed", "power_output", "rotor_rpm", "temperature"]
SENSOR_PORTS = {name: BASE_PORT + i + 1 for i, name in enumerate(SENSOR_NAMES)}
SENSOR_UNITS = {
    "wind_speed":   "m/s",
    "power_output": "kW",
    "rotor_rpm":    "RPM",
    "temperature":  "°C"
}

# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [{TURBINE_ID}] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(TURBINE_ID)

# =========================================================
# TURBINE STATE
# =========================================================
state = {
    "yaw_angle":      180.0,
    "blade_pitch":    15.0,
    "emergency_stop": False,
    "online":         True,
}
state_lock = threading.Lock()

# Track whether we already sent a farm-level wind alert so we don't spam
_wind_alert_sent = False
_wind_alert_lock = threading.Lock()

# =========================================================
# SENSOR READERS
# =========================================================
def read_wind_speed():
    """Return a simple wind speed value for the small sensor server."""
    return round(12.0 + random.gauss(0, 2.5), 2)

def read_power_output():
    """Estimate power from the current wind speed."""
    ws = read_wind_speed()
    return round(min(2000.0, max(0.0, 0.5 * 1.225 * 3.14159 * (40**2) * (ws**3) / 1000)), 1)

def read_rotor_rpm():
    """Return rotor speed, or zero when emergency stop is active."""
    with state_lock:
        pitch = state["blade_pitch"]
        estop = state["emergency_stop"]
    if estop:
        return 0.0
    return max(0.0, round(15.0 - pitch * 0.1 + random.gauss(0, 0.5), 2))

def read_temperature():
    """Return a simple generator temperature value."""
    return round(35.0 + random.gauss(0, 3.0), 1)

READERS = {
    "wind_speed":   read_wind_speed,
    "power_output": read_power_output,
    "rotor_rpm":    read_rotor_rpm,
    "temperature":  read_temperature,
}

# =========================================================
# SENSOR TCP SERVERS  (individual sensor ports)
# =========================================================
def sensor_server(name, port):
    """Start a tiny TCP server for one sensor value."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(5)
        log.info(f"Sensor '{name}' listening on port {port}")
        while True:
            try:
                conn, _ = srv.accept()
                threading.Thread(target=_handle_sensor, args=(conn, name), daemon=True).start()
            except Exception as e:
                log.error(f"Sensor '{name}' error: {e}")
                time.sleep(1)

def _handle_sensor(conn, name):
    """Reply to a READ request from a sensor client."""
    with conn:
        try:
            req = conn.recv(64).decode().strip()
            if req == "READ":
                payload = {
                    "sensor":     name,
                    "value":      READERS[name](),
                    "unit":       SENSOR_UNITS[name],
                    "turbine_id": TURBINE_ID,
                    "timestamp":  datetime.utcnow().isoformat() + "Z",
                }
                conn.sendall((json.dumps(payload) + "\n").encode())
        except Exception:
            pass

# =========================================================
# TELEMETRY BUILDERS
# =========================================================
def build_local_telemetry():
    """Build a single TELEMETRY message for this turbine, including derived metrics."""
    with state_lock:
        s = dict(state)
    reading = _local_suite.next_reading(yaw=s["yaw_angle"], pitch=s["blade_pitch"])
    sensors = reading["sensors"]
    derived = reading["derived"]
    # Emergency stop overrides RPM
    if s["emergency_stop"]:
        sensors["rotor_rpm"] = 0.0
    return {
        "type":         "TELEMETRY",
        "turbine_id":   TURBINE_ID,
        "timestamp":    datetime.utcnow().isoformat() + "Z",
        "is_leader":    IS_LEADER,
        "leader_id":    LEADER_ID,
        "link_metrics": SIMULATED_LINK_QUALITY.get(TURBINE_ID, {}),
        "sensors":  sensors,
        "derived":  derived,
        "actuators": {
            "yaw_angle":   s["yaw_angle"],
            "blade_pitch": s["blade_pitch"],
        },
        "status": {
            "emergency_stop": s["emergency_stop"],
            "online":         s["online"],
        },
        "sensor_ports": SENSOR_PORTS,
    }

def build_follower_telemetry(tid):
    """
    Leader relays each follower's data using its own SensorSuite so readings
    are physically consistent and reflect any commands sent to that follower.
    Follower actuator/status state is tracked in _follower_states.
    """
    fstate = _follower_states.get(tid, {"yaw_angle": 180.0, "blade_pitch": 15.0,
                                        "emergency_stop": False, "online": True})
    suite   = _follower_suites[tid]
    reading = suite.next_reading(yaw=fstate["yaw_angle"], pitch=fstate["blade_pitch"])
    sensors = reading["sensors"]
    derived = reading["derived"]
    if fstate["emergency_stop"]:
        sensors["rotor_rpm"] = 0.0
    return {
        "type":         "TELEMETRY",
        "turbine_id":   tid,
        "timestamp":    datetime.utcnow().isoformat() + "Z",
        "is_leader":    False,
        "leader_id":    LEADER_ID,
        "link_metrics": SIMULATED_LINK_QUALITY.get(tid, {}),
        "sensors":  sensors,
        "derived":  derived,
        "actuators": {
            "yaw_angle":   fstate["yaw_angle"],
            "blade_pitch": fstate["blade_pitch"],
        },
        "status": {
            "emergency_stop": fstate["emergency_stop"],
            "online":         fstate["online"],
        },
    }

# =========================================================
# COMMAND HANDLER
# =========================================================
def _apply_state_change(action, params, s):
    """Apply a single action to a state dict (in-place). Returns (success, message)."""
    if action == "SET_YAW":
        angle = max(0.0, min(360.0, float(params.get("angle", s["yaw_angle"]))))
        s["yaw_angle"] = angle
        return True, f"Yaw set to {angle} degrees"
    elif action == "SET_PITCH":
        pitch = max(0.0, min(90.0, float(params.get("pitch", s["blade_pitch"]))))
        s["blade_pitch"] = pitch
        return True, f"Pitch set to {pitch} degrees"
    elif action == "EMERGENCY_STOP":
        s["emergency_stop"] = True
        s["blade_pitch"]    = 90.0
        return True, "Emergency stop activated - blades feathered"
    elif action == "RESUME":
        s["emergency_stop"] = False
        s["blade_pitch"]    = 15.0
        return True, "Resumed normal operation"
    elif action == "PING":
        return True, "PONG"
    else:
        return False, f"Unknown action: {action}"


def apply_command(cmd):
    """Run a command received from the ground station."""
    action  = cmd.get("action", "")
    params  = cmd.get("params", {})
    target  = cmd.get("turbine_id", TURBINE_ID)
    success = True
    message = "OK"

    # Apply to this turbine's own state
    with state_lock:
        try:
            success, message = _apply_state_change(action, params, state)
            if success and action not in ("PING",):
                log.info(f"[{TURBINE_ID}] {message}")
            elif not success:
                log.warning(f"[{TURBINE_ID}] {message}")
        except Exception as e:
            success = False
            message = f"Command error: {e}"

    # If this turbine is the leader and the command was broadcast to ALL,
    # also mirror the state change into all follower shadow states so
    # build_follower_telemetry() reflects the correct pitch/estop.
    if IS_LEADER and target == "ALL":
        for tid, fstate in _follower_states.items():
            try:
                _apply_state_change(action, params, fstate)
            except Exception:
                pass

    return {
        "type":       "ACK",
        "turbine_id": TURBINE_ID,
        "action":     action,
        "success":    success,
        "message":    message,
        "timestamp":  datetime.utcnow().isoformat() + "Z",
    }

# =========================================================
# SATELLITE LINK
# =========================================================
def satellite_link():
    """Connect to the satellite and exchange telemetry and commands."""
    global _wind_alert_sent

    while True:
        log.info(f"Connecting to satellite {SATELLITE_HOST}:{SATELLITE_PORT} ...")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(10)
                sock.connect((SATELLITE_HOST, SATELLITE_PORT))
                sock.settimeout(SENSOR_INTERVAL + 1)

                reg = {
                    "type":         "REGISTER",
                    "node_type":    "TURBINE",
                    "turbine_id":   TURBINE_ID,
                    "services":     list(SENSOR_PORTS.keys()),
                    "sensor_ports": SENSOR_PORTS,
                    "base_port":    BASE_PORT,
                    "is_leader":    IS_LEADER,
                    "leader_id":    LEADER_ID,
                    "link_metrics": SIMULATED_LINK_QUALITY.get(TURBINE_ID, {}),
                    "timestamp":    datetime.utcnow().isoformat() + "Z",
                }
                sock.sendall((json.dumps(sign_message(reg)) + "\n").encode())
                log.info(f"Satellite link UP | is_leader={IS_LEADER} | elected_leader={LEADER_ID}")

                buffer  = ""
                last_tx = 0.0

                while True:
                    now = time.time()

                    # ---- Periodic telemetry transmission ----
                    if now - last_tx >= SENSOR_INTERVAL:
                        last_tx = now

                        # Always send our own telemetry
                        local = build_local_telemetry()
                        sock.sendall((json.dumps(sign_message(local)) + "\n").encode())

                        # Leader additionally relays all follower telemetry
                        if IS_LEADER:
                            for tid in FOLLOWERS:
                                follower_msg = build_follower_telemetry(tid)
                                sock.sendall((json.dumps(sign_message(follower_msg)) + "\n").encode())

                            # Check for farm-wide high wind and send a FARM_ALERT
                            wind = local["sensors"]["wind_speed"]
                            with _wind_alert_lock:
                                if wind > WIND_ESTOP_LIMIT and not _wind_alert_sent:
                                    alert = sign_message({
                                        "type":       "FARM_ALERT",
                                        "alert_type": "HIGH_WIND",
                                        "wind_speed": wind,
                                        "threshold":  WIND_ESTOP_LIMIT,
                                        "leader_id":  LEADER_ID,
                                        "turbines":   ALL_TURBINES,
                                        "action":     "EMERGENCY_STOP_ALL",
                                        "timestamp":  datetime.utcnow().isoformat() + "Z",
                                    })
                                    sock.sendall((json.dumps(alert) + "\n").encode())
                                    log.warning(
                                        f"FARM_ALERT sent: wind={wind}m/s exceeds {WIND_ESTOP_LIMIT}m/s"
                                    )
                                    _wind_alert_sent = True
                                elif wind <= WIND_ESTOP_LIMIT * 0.85:
                                    # Wind has dropped back to safe range — allow future alerts
                                    _wind_alert_sent = False

                    # ---- Receive commands / ACKs ----
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
                                raw_msg = json.loads(line)
                                ok, reason = verify_message(raw_msg)
                                if not ok:
                                    log.warning(f"Rejected satellite msg: {reason}")
                                    continue
                                msg = strip_security_fields(raw_msg)
                                t   = msg.get("type", "")

                                if t == "COMMAND":
                                    target = msg.get("turbine_id")
                                    if target in (TURBINE_ID, "ALL"):
                                        ack = apply_command(msg)
                                        sock.sendall(
                                            (json.dumps(sign_message(ack)) + "\n").encode()
                                        )

                                elif t == "REGISTER_ACK":
                                    log.info(f"Registered with satellite: {msg.get('satellite_id')}")

                            except json.JSONDecodeError:
                                pass

                    except socket.timeout:
                        pass

        except (ConnectionRefusedError, OSError) as e:
            log.warning(f"Satellite unreachable: {e} - retry in {RECONNECT_DELAY}s ...")
        except Exception as e:
            log.error(f"Link error: {e} - retry in {RECONNECT_DELAY}s ...")

        time.sleep(RECONNECT_DELAY)


def main():
    log.info("=" * 60)
    log.info(f"TURBINE NODE  : {TURBINE_ID}")
    log.info(f"Is leader     : {IS_LEADER}")
    log.info(f"Elected leader: {LEADER_ID}  (score={LEADER_SCORE})")
    log.info(f"Farm turbines : {ALL_TURBINES}")
    log.info(f"Sensor ports  : {SENSOR_PORTS}")
    log.info("=" * 60)

    for name, port in SENSOR_PORTS.items():
        threading.Thread(target=sensor_server, args=(name, port), daemon=True).start()

    time.sleep(0.3)
    satellite_link()


if __name__ == "__main__":
    main()
