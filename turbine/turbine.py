"""
CSU33D03 - Main Project 2025-26
TURBINE NODE  -  Device A
Fixed version:
- yaw/pitch commands now actually update
- actuator loop is started
- telemetry sends live actuator values
"""

import socket, threading, time, json, logging, sys, os, struct, random
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from security import sign_message, verify_message, strip_security_fields
from sensors  import SensorSuite, ALERT_THRESHOLDS, AUTO_ESTOP_THRESHOLDS

<<<<<<< HEAD
# ────────────────────────────────────────────────────────
SATELLITE_HOST = "127.0.0.1"   # ← replace with Laptop B's IP e.g. "192.168.1.11"
SATELLITE_PORT = 9000
# ─────────────────────────────────────────────────────────────────────────────
=======
SATELLITE_HOST   = "127.0.0.1"
SATELLITE_PORT   = 9000
RECONNECT_DELAY  = 5
SENSOR_INTERVAL  = 2
>>>>>>> 76f4de9 (edited turbine.py and sensors.py so the data generated is random)

MESH_MCAST_GROUP = "224.1.1.1"
MESH_MCAST_PORT  = 9003
PEER_BASE_PORT   = 9004
MESH_ANNOUNCE_S  = 5
NEGO_TIMEOUT_S   = 4

TURBINE_ID = sys.argv[1] if len(sys.argv) > 1 else "TURBINE-01"
BASE_PORT  = int(sys.argv[2]) if len(sys.argv) > 2 else (
    5000 + (int(TURBINE_ID.split("-")[-1]) * 10)
    if TURBINE_ID.split("-")[-1].isdigit() else 5001
)

SENSOR_NAMES = [
    "wind_speed", "power_output", "rotor_rpm", "temperature",
    "gearbox_temp", "vibration", "hydraulic_pressure", "nacelle_humidity"
]
SENSOR_PORTS = {name: BASE_PORT + i + 1 for i, name in enumerate(SENSOR_NAMES)}

PEER_PORT = PEER_BASE_PORT + (int(TURBINE_ID.split("-")[-1]) - 1
            if TURBINE_ID.split("-")[-1].isdigit() else 0)

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s [{TURBINE_ID}] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(TURBINE_ID)

suite = SensorSuite(turbine_id=TURBINE_ID, use_dataset=False)

state = {
    "yaw_angle":        180.0,
    "blade_pitch":      15.0,
    "target_yaw":       180.0,
    "target_pitch":     15.0,
    "emergency_stop":   False,
    "online":           True,
    "maintenance_mode": False,
    "rated_power_kw":   2000.0,
}
state_lock = threading.Lock()

random.seed(hash(TURBINE_ID) % 1000)
MY_SIGNAL_SCORE = round(random.uniform(0.60, 0.99), 3)

mesh_peers  = {}
mesh_lock   = threading.Lock()
is_leader   = False
leader_id   = None
mesh_ready  = threading.Event()

def discovery_broadcaster():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    while True:
        try:
            msg = json.dumps({
                "type":         "DISCOVER",
                "turbine_id":   TURBINE_ID,
                "signal_score": MY_SIGNAL_SCORE,
                "peer_port":    PEER_PORT,
                "timestamp":    datetime.utcnow().isoformat() + "Z",
            })
            sock.sendto(msg.encode(), (MESH_MCAST_GROUP, MESH_MCAST_PORT))
        except Exception as e:
            log.debug(f"Broadcast error: {e}")
        time.sleep(MESH_ANNOUNCE_S)

def discovery_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("", MESH_MCAST_PORT))
    mreq = struct.pack("4sL", socket.inet_aton(MESH_MCAST_GROUP), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    log.info(f"  Mesh discovery  -> multicast {MESH_MCAST_GROUP}:{MESH_MCAST_PORT}")
    while True:
        try:
            data, addr = sock.recvfrom(512)
            msg = json.loads(data.decode())
            if msg.get("type") == "DISCOVER":
                tid = msg.get("turbine_id")
                if tid and tid != TURBINE_ID:
                    with mesh_lock:
                        is_new = tid not in mesh_peers
                        mesh_peers[tid] = {
                            "ip":           addr[0],
                            "peer_port":    msg.get("peer_port"),
                            "signal_score": msg.get("signal_score", 0),
                            "last_seen":    time.time(),
                        }
                    if is_new:
                        log.info(f"[DISCOVERY] Found peer: {tid} "
                                 f"signal={msg.get('signal_score')} at {addr[0]}")
        except Exception:
            pass

def peer_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", PEER_PORT))
        srv.listen(10)
        log.info(f"  Peer negotiation -> 0.0.0.0:{PEER_PORT}")
        while True:
            try:
                conn, addr = srv.accept()
                threading.Thread(target=_handle_peer,
                                 args=(conn, addr), daemon=True).start()
            except Exception as e:
                log.error(f"Peer server error: {e}")

def _handle_peer(conn, addr):
    with conn:
        try:
            data = conn.recv(1024).decode().strip()
            msg  = json.loads(data)
            if msg.get("type") == "NEGOTIATE":
                peer_id = msg.get("turbine_id")
                log.info(f"[NEGOTIATION] Peer {peer_id} offers: "
                         f"signal={msg.get('signal_score')}, "
                         f"sensors={msg.get('sensor_count')}, "
                         f"estop={msg.get('emergency_stop')}")
                with state_lock:
                    estop = state["emergency_stop"]
                reply = json.dumps({
                    "type":           "NEGOTIATE_REPLY",
                    "turbine_id":     TURBINE_ID,
                    "signal_score":   MY_SIGNAL_SCORE,
                    "sensor_count":   len(SENSOR_NAMES),
                    "emergency_stop": estop,
                    "peer_port":      PEER_PORT,
                    "timestamp":      datetime.utcnow().isoformat() + "Z",
                })
                conn.sendall((reply + "\n").encode())
        except Exception as e:
            log.debug(f"Peer handle error: {e}")

def negotiate_with_peer(peer_id, peer_info):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect((peer_info["ip"], peer_info["peer_port"]))
            with state_lock:
                estop = state["emergency_stop"]
            offer = json.dumps({
                "type":           "NEGOTIATE",
                "turbine_id":     TURBINE_ID,
                "signal_score":   MY_SIGNAL_SCORE,
                "sensor_count":   len(SENSOR_NAMES),
                "emergency_stop": estop,
                "timestamp":      datetime.utcnow().isoformat() + "Z",
            })
            sock.sendall(offer.encode())
            reply = sock.recv(1024).decode().strip()
            return json.loads(reply)
    except Exception:
        return None

def run_election():
    global is_leader, leader_id
    scoreboard = {TURBINE_ID: MY_SIGNAL_SCORE}
    with mesh_lock:
        for tid, info in mesh_peers.items():
            scoreboard[tid] = info["signal_score"]

    winner    = max(scoreboard, key=lambda k: scoreboard[k])
    is_leader = (winner == TURBINE_ID)
    leader_id = winner

    scores_str = ", ".join(f"{k}={v:.3f}" for k, v in sorted(scoreboard.items()))
    log.info(f"[AGREEMENT] Scoreboard: {scores_str}")
    if is_leader:
        log.info(f"[AGREEMENT] I WON - elected relay leader (score={MY_SIGNAL_SCORE})")
    else:
        log.info(f"[AGREEMENT] {leader_id} won "
                 f"(score={scoreboard[leader_id]:.3f} > mine={MY_SIGNAL_SCORE})")

def mesh_coordinator():
    log.info(f"[DISCOVERY] Announcing presence... (signal={MY_SIGNAL_SCORE})")
    time.sleep(NEGO_TIMEOUT_S)

    with mesh_lock:
        peers_snapshot = dict(mesh_peers)

    if peers_snapshot:
        log.info(f"[NEGOTIATION] {len(peers_snapshot)} peer(s) found - negotiating")
        for pid, pinfo in peers_snapshot.items():
            result = negotiate_with_peer(pid, pinfo)
            if result:
                with mesh_lock:
                    if pid in mesh_peers:
                        mesh_peers[pid]["signal_score"] = result.get(
                            "signal_score", mesh_peers[pid]["signal_score"])
    else:
        log.info("[NEGOTIATION] No peers - proceeding as sole turbine")

    run_election()
    mesh_ready.set()

def standby_loop():
    log.info("[ACTION] Standing by as follower")
    while True:
        time.sleep(30)

def actuator_loop():
    while True:
        with state_lock:
            if not state["emergency_stop"]:
                yaw_diff = state["target_yaw"] - state["yaw_angle"]
                if abs(yaw_diff) > 0.5:
                    step = 3 if yaw_diff > 0 else -3
                    state["yaw_angle"] += step
                    if abs(state["target_yaw"] - state["yaw_angle"]) < 3:
                        state["yaw_angle"] = state["target_yaw"]

                pitch_diff = state["target_pitch"] - state["blade_pitch"]
                if abs(pitch_diff) > 0.2:
                    step = 1 if pitch_diff > 0 else -1
                    state["blade_pitch"] += step
                    if abs(state["target_pitch"] - state["blade_pitch"]) < 1:
                        state["blade_pitch"] = state["target_pitch"]
            else:
                state["target_pitch"] = 90.0
                if state["blade_pitch"] < 90.0:
                    state["blade_pitch"] += 2
                    if state["blade_pitch"] > 90.0:
                        state["blade_pitch"] = 90.0
        time.sleep(1)

def sensor_server(name, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(5)
        log.info(f"  Sensor '{name}' on port {port}")
        while True:
            try:
                conn, _ = srv.accept()
                threading.Thread(target=_handle_sensor,
                                 args=(conn, name), daemon=True).start()
            except Exception as e:
                log.error(f"Sensor '{name}' error: {e}")
                time.sleep(1)

def _handle_sensor(conn, name):
    with conn:
        try:
            req = conn.recv(64).decode().strip()
            if req == "READ":
                with state_lock:
                    yaw   = state["yaw_angle"]
                    pitch = state["blade_pitch"]
                payload = sign_message(suite.get_single(name, yaw, pitch))
                conn.sendall((json.dumps(payload) + "\n").encode())
        except Exception:
            pass

def build_telemetry():
    with state_lock:
        s = dict(state)
    reading = suite.next_reading(yaw=s["yaw_angle"], pitch=s["blade_pitch"])
    msg = {
        "type":       "TELEMETRY",
        "turbine_id": TURBINE_ID,
        "seq":        reading["seq"],
        "timestamp":  reading["timestamp"],
        "sensors":    reading["sensors"],
        "derived":    reading["derived"],
        "actuators":  {
            "yaw_angle": round(s["yaw_angle"], 1),
            "blade_pitch": round(s["blade_pitch"], 1)
        },
        "status":     {
            "emergency_stop":   s["emergency_stop"],
            "online":           s["online"],
            "maintenance_mode": s["maintenance_mode"]
        },
        "meta":       {
            "rated_kw": s["rated_power_kw"],
            "is_leader": is_leader,
            "leader_id": leader_id
        },
        "checksum":   reading["checksum"],
        "sensor_ports": SENSOR_PORTS,
    }
    return sign_message(msg)

def apply_command(raw_cmd):
    ok, reason = verify_message(raw_cmd)
    if not ok:
        log.warning(f"Rejected command: {reason}")
        return sign_message(_ack(raw_cmd.get("action", "?"), False, f"Security: {reason}"))

    cmd     = strip_security_fields(raw_cmd)
    action  = cmd.get("action", "")
    params  = cmd.get("params", {})
    success = True
    message = "OK"

    with state_lock:
        if action == "SET_YAW":
            angle = float(params.get("angle", state["yaw_angle"]))
            state["target_yaw"] = max(0, min(360, angle))
            message = f"Yaw target set to {state['target_yaw']} deg"
            log.info(f"CMD: {message}")

        elif action == "SET_PITCH":
            pitch = float(params.get("pitch", state["blade_pitch"]))
            state["target_pitch"] = max(0, min(90, pitch))
            message = f"Pitch target set to {state['target_pitch']} deg"
            log.info(f"CMD: {message}")

        elif action == "EMERGENCY_STOP":
            state["emergency_stop"] = True
            state["target_pitch"]   = 90.0
            message = "EMERGENCY STOP - blades feathering"
            log.warning(f"CMD: {message}")

        elif action == "RESUME":
            if state["maintenance_mode"]:
                success = False
                message = "Cannot resume - maintenance mode active"
            else:
                state["emergency_stop"] = False
                state["target_pitch"]   = 15.0
                message = "Resumed normal operation"
                log.info(f"CMD: {message}")

        elif action == "SET_MAINTENANCE":
            state["maintenance_mode"] = bool(params.get("active", False))
            message = f"Maintenance {'ON' if state['maintenance_mode'] else 'OFF'}"
            log.info(f"CMD: {message}")

        elif action == "PING":
            message = "PONG"
        else:
            success = False
            message = f"Unknown action: {action}"

    return sign_message(_ack(action, success, message))

def _ack(action, success, message):
    return {
        "type":       "ACK",
        "turbine_id": TURBINE_ID,
        "action":     action,
        "success":    success,
        "message":    message,
        "timestamp":  datetime.utcnow().isoformat() + "Z",
    }

def satellite_link():
    while True:
        log.info(f"[ACTION] Connecting to satellite as relay leader ...")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(10)
                sock.connect((SATELLITE_HOST, SATELLITE_PORT))
                sock.settimeout(SENSOR_INTERVAL + 1)

                reg = sign_message({
                    "type":         "REGISTER",
                    "node_type":    "TURBINE",
                    "turbine_id":   TURBINE_ID,
                    "sensor_ports": SENSOR_PORTS,
                    "base_port":    BASE_PORT,
                    "sensors":      SENSOR_NAMES,
                    "is_leader":    True,
                    "timestamp":    datetime.utcnow().isoformat() + "Z",
                })
                sock.sendall((json.dumps(reg) + "\n").encode())
                log.info("[ACTION] Satellite link UP - relaying telemetry")

                buffer = ""
                last_tx = 0.0

                while True:
                    now = time.time()
                    if now - last_tx >= SENSOR_INTERVAL:
                        sock.sendall((json.dumps(build_telemetry()) + "\n").encode())
                        last_tx = now
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
                                t = msg.get("type", "")
                                if t == "COMMAND":
                                    ack = apply_command(msg)
                                    sock.sendall((json.dumps(ack) + "\n").encode())
                                elif t == "REGISTER_ACK":
                                    log.info(f"Registered with satellite {msg.get('satellite_id')}")
                            except json.JSONDecodeError:
                                pass
                    except socket.timeout:
                        pass

        except (ConnectionRefusedError, OSError) as e:
            log.warning(f"Satellite unreachable: {e} - retry in {RECONNECT_DELAY}s")
            time.sleep(RECONNECT_DELAY)
        except Exception as e:
            log.error(f"Link error: {e} - retry in {RECONNECT_DELAY}s")
            time.sleep(RECONNECT_DELAY)

def main():
    log.info("=" * 60)
    log.info(f"  TURBINE NODE  -  {TURBINE_ID}")
    log.info(f"  Signal score  : {MY_SIGNAL_SCORE}")
    log.info(f"  Peer port     : {PEER_PORT}")
    log.info(f"  Sensor ports  : {BASE_PORT+1} - {BASE_PORT+len(SENSOR_NAMES)}")
    log.info(f"  Satellite     : {SATELLITE_HOST}:{SATELLITE_PORT}")
    log.info(f"  Mesh protocol : DISCOVERY -> NEGOTIATION -> AGREEMENT -> ACTION")
    log.info("=" * 60)

    for name, port in SENSOR_PORTS.items():
        threading.Thread(target=sensor_server, args=(name, port), daemon=True).start()

    threading.Thread(target=discovery_broadcaster, daemon=True).start()
    threading.Thread(target=discovery_listener,    daemon=True).start()
    threading.Thread(target=peer_server,           daemon=True).start()
    threading.Thread(target=actuator_loop,         daemon=True).start()

    time.sleep(6)
    mesh_coordinator()

    if is_leader:
        satellite_link()
    else:
        standby_loop()

if __name__ == "__main__":
    main()