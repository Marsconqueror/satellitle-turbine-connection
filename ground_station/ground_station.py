"""
CSU33D03 - Main Project 2025-26
GROUND CONTROL STATION  -  Device C

This is the human operator side of the system. It connects to the satellite,
receives live telemetry from wind turbines, displays their status, and lets
the operator send control commands like changing yaw, pitch or triggering an
emergency stop.
"""

import socket, threading, time, json, logging, sys
from datetime import datetime
from collections import defaultdict, deque

SATELLITE_HOST = "127.0.0.1"   # replace with the satellite laptop IP
SATELLITE_PORT = 9001           #satellite port 
DISC_UDP_PORT  = 9002

GROUND_ID       = "GROUND-CTRL-01"
RECONNECT_DELAY = 5
HISTORY_LEN     = 100

# These are the safe operating ranges for each sensor
# If a value goes outside these bounds we log an alert
ALERT_THRESHOLDS = {
    "wind_speed":   {"min": 0,   "max": 25,   "unit": "m/s"},
    "power_output": {"min": 0,   "max": 2000, "unit": "kW"},
    "rotor_rpm":    {"min": 0,   "max": 20,   "unit": "RPM"},
    "temperature":  {"min": -10, "max": 70,   "unit": "°C"},
}

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [GROUND] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("ground")

# We store the last HISTORY_LEN readings per turbine so operators can look back
# We also keep a dictionary of every turbine we have heard from
telemetry_history = defaultdict(lambda: deque(maxlen=HISTORY_LEN))
known_turbines    = {}
state_lock        = threading.Lock()

# This holds a reference to the current satellite socket so any thread can send through it
_sat_sock        = None
_sock_lock       = threading.Lock()
connected_to_sat = False

# We give every command a unique ID so we can track which ones have been acknowledged
_cmd_counter = 0
_cmd_lock    = threading.Lock()

def _next_id():
    global _cmd_counter
    with _cmd_lock:
        _cmd_counter += 1
        return f"CMD-{_cmd_counter:05d}"


# This is the main function for sending any message up to the satellite
def send_to_sat(msg):
    with _sock_lock:
        if _sat_sock is None:
            log.error("Not connected to satellite")
            return False
        try:
            _sat_sock.sendall((json.dumps(msg) + "\n").encode())
            return True
        except OSError as e:
            log.error(f"Send failed: {e}")
            return False


# This loop keeps trying to connect to the satellite and reconnects if the link drops
def connect_loop():
    global _sat_sock, connected_to_sat
    while True:
        log.info(f"Connecting to satellite {SATELLITE_HOST}:{SATELLITE_PORT} ...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((SATELLITE_HOST, SATELLITE_PORT))
            sock.settimeout(5)
            with _sock_lock:
                _sat_sock = sock

            # Tell the satellite who we are as soon as we connect
            sock.sendall((json.dumps({
                "type":      "REGISTER",
                "node_type": "GROUND",
                "ground_id": GROUND_ID,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }) + "\n").encode())

            connected_to_sat = True
            log.info("Connected to satellite")
            _receive_loop(sock)

        except (ConnectionRefusedError, OSError) as e:
            log.warning(f"Cannot reach satellite: {e}  - retry in {RECONNECT_DELAY}s ...")
        except Exception as e:
            log.error(f"Connection error: {e}  - retry in {RECONNECT_DELAY}s ...")
        finally:
            connected_to_sat = False
            with _sock_lock:
                _sat_sock = None

        time.sleep(RECONNECT_DELAY)


# This reads incoming messages from the satellite and passes them to the right handler
def _receive_loop(sock):
    buffer = ""
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                log.warning("Satellite closed connection")
                break
            buffer += chunk.decode()
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    _dispatch(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except socket.timeout:
            continue
        except OSError:
            break


# This decides what to do with each message type we receive from the satellite
def _dispatch(msg):
    t = msg.get("type", "")
    if t == "REGISTER_ACK":
        log.info(f"Satellite confirmed ({msg.get('satellite_id')})")
    elif t == "TELEMETRY":
        _process_telemetry(msg)
    elif t == "ACK":
        sym = "OK" if msg.get("success") else "FAIL"
        log.info(f"{sym} ACK [{msg.get('turbine_id')}] '{msg.get('action')}': {msg.get('message')}")
    elif t == "ROUTE_ACK":
        # The satellite is telling us whether it delivered the command or had to queue it
        if msg.get("queued"):
            log.warning(f"Command '{msg.get('action')}' QUEUED (link down)")
        else:
            log.info(f"Command '{msg.get('action')}' routed to {msg.get('turbine_id')}")
    elif t == "TURBINE_BEACON":
        # A turbine has announced itself so we add it to our known list
        tid = msg.get("turbine_id")
        log.info(f"Beacon from {tid}")
        with state_lock:
            if tid not in known_turbines:
                known_turbines[tid] = {}
    elif t == "DISCOVER_RESPONSE":
        turbines = msg.get("turbines", [])
        log.info(f"Discovery: {len(turbines)} turbine(s) | link={'UP' if msg.get('link_up') else 'DOWN'}")
        with state_lock:
            for ti in turbines:
                known_turbines[ti["turbine_id"]] = ti.get("meta", {})
    elif t == "PONG":
        log.info(f"PONG from satellite - link={'UP' if msg.get('link_up') else 'DOWN'}")


# This processes incoming telemetry and checks if any sensor values are out of range
def _process_telemetry(msg):
    tid     = msg.get("turbine_id", "?")
    sensors = msg.get("sensors", {})

    with state_lock:
        known_turbines[tid] = msg
        telemetry_history[tid].append(msg)

    log.info(
        f"[{tid}]  Wind={sensors.get('wind_speed', '?')}m/s  "
        f"Power={sensors.get('power_output', '?')}kW  "
        f"RPM={sensors.get('rotor_rpm', '?')}  "
        f"Temp={sensors.get('temperature', '?')}°C  "
        f"Yaw={msg.get('actuators', {}).get('yaw_angle', '?')}°  "
        f"Pitch={msg.get('actuators', {}).get('blade_pitch', '?')}°"
    )

    # Check every sensor against its safe range and alert if something is wrong
    for sensor, th in ALERT_THRESHOLDS.items():
        val = sensors.get(sensor)
        if val is not None and (val < th["min"] or val > th["max"]):
            log.warning(f"ALERT [{tid}] {sensor}={val}{th['unit']} (safe range {th['min']} to {th['max']})")

    # If the temperature is critically high we automatically trigger an emergency stop
    if sensors.get("temperature", 0) > 65:
        log.warning(f"Critical temperature on {tid} - sending automatic emergency stop")
        send_command(tid, "EMERGENCY_STOP", {})


# This builds and sends a command message to a specific turbine through the satellite
def send_command(turbine_id, action, params):
    cid = _next_id()
    send_to_sat({
        "type":       "COMMAND",
        "turbine_id": turbine_id,
        "ground_id":  GROUND_ID,
        "cmd_id":     cid,
        "action":     action,
        "params":     params,
        "timestamp":  datetime.utcnow().isoformat() + "Z"
    })
    log.info(f"Sent {action} to {turbine_id}  (id={cid})")


# These are shorthand helpers for common one off messages
def discover():
    send_to_sat({"type": "DISCOVER", "ground_id": GROUND_ID, "timestamp": datetime.utcnow().isoformat() + "Z"})

def ping():
    send_to_sat({"type": "PING", "ground_id": GROUND_ID, "timestamp": datetime.utcnow().isoformat() + "Z"})


# This prints a nicely formatted status summary of all known turbines
def display_status():
    print("\n" + "=" * 62)
    print(f"  GROUND CONTROL  -  {datetime.utcnow().strftime('%H:%M:%S')} UTC")
    print(f"  Satellite : {'CONNECTED' if connected_to_sat else 'DISCONNECTED'}")
    print("=" * 62)
    with state_lock:
        if not known_turbines:
            print("  No turbines yet - run: discover")
        for tid, data in known_turbines.items():
            s     = data.get("sensors", {})
            a     = data.get("actuators", {})
            st    = data.get("status", {})
            estop = "EMERGENCY STOP" if st.get("emergency_stop") else "Normal"
            print(f"\n  Turbine : {tid}  [{estop}]")
            print(f"    Wind speed   : {s.get('wind_speed', '?'):>8} m/s")
            print(f"    Power output : {s.get('power_output', '?'):>8} kW")
            print(f"    Rotor RPM    : {s.get('rotor_rpm', '?'):>8}")
            print(f"    Temperature  : {s.get('temperature', '?'):>8} °C")
            print(f"    Yaw angle    : {a.get('yaw_angle', '?'):>8} °")
            print(f"    Blade pitch  : {a.get('blade_pitch', '?'):>8} °")
    print("=" * 62 + "\n")


HELP = """
  status                       show all turbine data
  discover                     find turbines via satellite
  ping                         ping the satellite
  yaw    <turbine_id> <0-360>  set yaw angle
  pitch  <turbine_id> <0-90>   set blade pitch
  estop  <turbine_id>          emergency stop
  resume <turbine_id>          resume normal operation
  history <turbine_id>         last 5 readings
  turbines                     list known turbine IDs
  quit                         exit
"""


# This is the main command line interface that the operator types into
def cli():
    time.sleep(2)
    print("\nGround Control ready. Type 'help'.\n")
    while True:
        try:
            raw = input("ground> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            sys.exit(0)

        if not raw:
            continue

        parts = raw.split()
        cmd   = parts[0].lower()

        if cmd == "help":
            print(HELP)
        elif cmd == "status":
            display_status()
        elif cmd == "discover":
            discover()
        elif cmd == "ping":
            ping()
        elif cmd == "turbines":
            with state_lock:
                ids = list(known_turbines.keys())
            print("Known turbines:", ids if ids else "(none - run discover)")

        elif cmd == "yaw" and len(parts) >= 3:
            try:
                send_command(parts[1], "SET_YAW", {"angle": float(parts[2])})
            except ValueError:
                print("Angle must be a number")

        elif cmd == "pitch" and len(parts) >= 3:
            try:
                send_command(parts[1], "SET_PITCH", {"pitch": float(parts[2])})
            except ValueError:
                print("Pitch must be a number")

        elif cmd == "estop" and len(parts) >= 2:
            send_command(parts[1], "EMERGENCY_STOP", {})

        elif cmd == "resume" and len(parts) >= 2:
            send_command(parts[1], "RESUME", {})

        elif cmd == "history" and len(parts) >= 2:
            tid = parts[1]
            with state_lock:
                hist = list(telemetry_history.get(tid, []))[-5:]
            if not hist:
                print(f"No history for {tid}")
            else:
                print(f"\nLast {len(hist)} readings for {tid}:")
                for e in hist:
                    s = e.get("sensors", {})
                    print(
                        f"  [{e.get('timestamp', '?')}]  "
                        f"Wind={s.get('wind_speed', '?')}m/s  "
                        f"Power={s.get('power_output', '?')}kW  "
                        f"Temp={s.get('temperature', '?')}°C"
                    )

        elif cmd in ("quit", "exit", "q"):
            print("Goodbye.")
            sys.exit(0)
        else:
            print(f"Unknown command: '{raw}'. Type 'help'.")


def main():
    log.info("=" * 55)
    log.info(f"  GROUND CONTROL  -  {GROUND_ID}")
    log.info("=" * 55)

    # Start the satellite connection in the background so the CLI stays responsive
    threading.Thread(target=connect_loop, daemon=True).start()
    cli()


if __name__ == "__main__":
    main()