"""
CSU33D03 - Main Project 2025-26
TURBINE NODE  -  Dynamic multi-instance version

This script simulates an offshore wind turbine. It generates realistic sensor
data, serves it over individual socket connections, and maintains a live uplink
to the satellite relay. It can also receive and act on control commands like
changing the yaw angle, blade pitch, or triggering an emergency stop.

Usage:
    python3 turbine.py                   defaults to TURBINE-01
    python3 turbine.py TURBINE-02        custom ID, ports auto assigned
    python3 turbine.py TURBINE-03 5020   custom ID and custom base port

Each turbine instance gets its own 4 sensor ports starting from the base port:
    TURBINE-01  uses ports 5011, 5012, 5013, 5014
    TURBINE-02  uses ports 5021, 5022, 5023, 5024
    TURBINE-03  uses ports 5031, 5032, 5033, 5034
"""

import socket, threading, time, random, json, logging, sys
from datetime import datetime

# Replace this with the IP address of the laptop running satellite.py
SATELLITE_HOST = "127.0.0.1"
SATELLITE_PORT = 9000

RECONNECT_DELAY = 5
SENSOR_INTERVAL = 2

# We read the turbine ID and optional base port from the command line
# This lets us run multiple turbines at the same time without port conflicts
TURBINE_ID = sys.argv[1] if len(sys.argv) > 1 else "TURBINE-01"
BASE_PORT  = int(sys.argv[2]) if len(sys.argv) > 2 else (
    5000 + (int(TURBINE_ID.split("-")[-1]) * 10)
    if TURBINE_ID.split("-")[-1].isdigit() else 5001
)

# Each sensor gets its own port so the ground station can query them individually
SENSOR_NAMES = ["wind_speed", "power_output", "rotor_rpm", "temperature"]
SENSOR_PORTS = {name: BASE_PORT + i + 1 for i, name in enumerate(SENSOR_NAMES)}
SENSOR_UNITS = {
    "wind_speed":   "m/s",
    "power_output": "kW",
    "rotor_rpm":    "RPM",
    "temperature":  "°C"
}

# We include the turbine ID in every log line so its easy to tell instances apart
logging.basicConfig(level=logging.INFO,
    format=f"%(asctime)s [{TURBINE_ID}] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(TURBINE_ID)

# This holds the current physical state of the turbine
# Multiple threads can read and write this so we protect it with a lock
state = {
    "yaw_angle":      180.0,
    "blade_pitch":    15.0,
    "emergency_stop": False,
    "online":         True
}
state_lock = threading.Lock()


# These functions simulate the physical sensors on the turbine
# They return slightly different values each time to mimic real world variation

def read_wind_speed():
    return round(12.0 + random.gauss(0, 2.5), 2)

def read_power_output():
    # Power output depends on wind speed using a simplified wind power equation
    ws = read_wind_speed()
    return round(min(2000.0, max(0.0, 0.5 * 1.225 * 3.14159 * (40**2) * (ws**3) / 1000)), 1)

def read_rotor_rpm():
    # RPM is affected by the current blade pitch angle
    with state_lock:
        pitch = state["blade_pitch"]
    return max(0.0, round(15.0 - pitch * 0.1 + random.gauss(0, 0.5), 2))

def read_temperature():
    return round(35.0 + random.gauss(0, 3.0), 1)

READERS = {
    "wind_speed":   read_wind_speed,
    "power_output": read_power_output,
    "rotor_rpm":    read_rotor_rpm,
    "temperature":  read_temperature
}


# This starts a TCP server for a single sensor on its own port
# Any client that connects and sends READ will get back the current sensor value
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


# This handles a single connection to a sensor port
# It reads the request, generates the sensor value, and sends it back
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


# This packages up the current state of the turbine into a telemetry message
# We also include the sensor port numbers so other nodes know how to query us directly
def build_telemetry():
    with state_lock:
        s = dict(state)
    return {
        "type":         "TELEMETRY",
        "turbine_id":   TURBINE_ID,
        "timestamp":    datetime.utcnow().isoformat() + "Z",
        "sensors":      {k: READERS[k]() for k in READERS},
        "actuators":    {"yaw_angle": s["yaw_angle"], "blade_pitch": s["blade_pitch"]},
        "status":       {"emergency_stop": s["emergency_stop"], "online": s["online"]},
        "sensor_ports": SENSOR_PORTS
    }


# This processes a command received from the ground station via the satellite
# It updates the turbine state and returns an acknowledgement message
def apply_command(cmd):
    action  = cmd.get("action", "")
    params  = cmd.get("params", {})
    success = True
    message = "OK"

    with state_lock:
        if action == "SET_YAW":
            # Clamp the angle to valid range before applying it
            angle = max(0.0, min(360.0, float(params.get("angle", state["yaw_angle"]))))
            state["yaw_angle"] = angle
            message = f"Yaw set to {angle} degrees"
            log.info(f"Yaw updated: {message}")

        elif action == "SET_PITCH":
            # Clamp pitch to valid range before applying it
            pitch = max(0.0, min(90.0, float(params.get("pitch", state["blade_pitch"]))))
            state["blade_pitch"] = pitch
            message = f"Pitch set to {pitch} degrees"
            log.info(f"Pitch updated: {message}")

        elif action == "EMERGENCY_STOP":
            # Feather the blades to 90 degrees to stop the rotor safely
            state["emergency_stop"] = True
            state["blade_pitch"]    = 90.0
            message = "Emergency stop activated - blades feathered"
            log.warning(message)

        elif action == "RESUME":
            state["emergency_stop"] = False
            message = "Resumed normal operation"
            log.info(message)

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


# This is the main uplink loop that keeps the turbine connected to the satellite
# It sends telemetry every few seconds and listens for commands coming back
def satellite_link():
    while True:
        log.info(f"Connecting to satellite {SATELLITE_HOST}:{SATELLITE_PORT} ...")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(10)
                sock.connect((SATELLITE_HOST, SATELLITE_PORT))
                sock.settimeout(SENSOR_INTERVAL + 1)

                # Introduce ourselves to the satellite with a REGISTER message
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
                log.info("Satellite link UP")

                buffer  = ""
                last_tx = 0.0

                while True:
                    now = time.time()

                    # Send fresh telemetry at the configured interval
                    if now - last_tx >= SENSOR_INTERVAL:
                        sock.sendall((json.dumps(build_telemetry()) + "\n").encode())
                        log.debug("Telemetry sent")
                        last_tx = now

                    # Check if the satellite has sent us any messages
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
                                    # Execute the command and send back an acknowledgement
                                    ack = apply_command(msg)
                                    sock.sendall((json.dumps(ack) + "\n").encode())
                                elif t == "REGISTER_ACK":
                                    log.info(f"Registered with satellite: {msg.get('satellite_id')}")
                            except json.JSONDecodeError:
                                pass
                    except socket.timeout:
                        pass

        except (ConnectionRefusedError, OSError) as e:
            log.warning(f"Satellite unreachable: {e}  - retry in {RECONNECT_DELAY}s ...")
            time.sleep(RECONNECT_DELAY)
        except Exception as e:
            log.error(f"Link error: {e}  - retry in {RECONNECT_DELAY}s ...")
            time.sleep(RECONNECT_DELAY)


def main():
    log.info("=" * 55)
    log.info(f"  TURBINE  -  {TURBINE_ID}")
    log.info(f"  Sensor ports: {SENSOR_PORTS}")
    log.info("=" * 55)

    # Start one background thread per sensor so they can all serve connections at once
    for name, port in SENSOR_PORTS.items():
        threading.Thread(target=sensor_server, args=(name, port), daemon=True).start()

    # Give the sensor servers a moment to start before we try to connect to the satellite
    time.sleep(0.3)
    satellite_link()


if __name__ == "__main__":
    main()