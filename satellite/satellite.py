"""
CSU33D03 - Main Project 2025-26
LEO SATELLITE RELAY  -  Device B

This script acts as the satellite relay sitting between the wind turbine
and the ground control station. It simulates a real Low Earth Orbit satellite
by adding realistic communication delays, packet loss, and visibility windows.
"""

import socket, threading, time, random, json, logging, sys, os, queue
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from security import sign_message, verify_message, strip_security_fields
from channel  import channel_delay, channel_loss, is_link_up, visibility_manager, get_stats

TURBINE_LISTEN_PORT = 9000
GROUND_LISTEN_PORT  = 9001
DISCOVERY_UDP_PORT  = 9002
SATELLITE_ID        = "LEO-SAT-01"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SATELLITE] %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("satellite")

# We keep two dictionaries, one for turbines and one for ground stations
# that are currently connected so we know where to send messages
turbine_connections = {}
ground_connections  = {}
t_lock = threading.Lock()
g_lock = threading.Lock()

# The relay queue holds telemetry messages waiting to be forwarded to the ground
# The command queue holds commands waiting to be sent to a specific turbine
relay_queue   = queue.Queue(maxsize=500)
command_queue = defaultdict(lambda: queue.Queue(maxsize=100))

# We use this to track sequence numbers per turbine so we can detect dropped packets
_seq_tracker = {}
_stats_lock  = threading.Lock()


# This function starts the server that listens for incoming turbine connections
def turbine_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", TURBINE_LISTEN_PORT))
        srv.listen(20)
        log.info(f"  Turbine uplink  -> 0.0.0.0:{TURBINE_LISTEN_PORT}")
        while True:
            conn, addr = srv.accept()
            # Each turbine gets its own thread so they dont block each other
            threading.Thread(target=handle_turbine,
                             args=(conn, addr), daemon=True).start()


# This handles everything that comes in from a single turbine connection
def handle_turbine(conn, addr):
    turbine_id = None
    buffer     = ""
    try:
        conn.settimeout(30)
        with conn:
            while True:
                chunk = conn.recv(4096)
                if not chunk: break
                buffer += chunk.decode()
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line: continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Every message must pass HMAC verification before we act on it
                    # If it fails we just drop it and log a warning
                    ok, reason = verify_message(msg)
                    if not ok:
                        log.warning(f"Rejected msg from {addr}: {reason}")
                        continue
                    msg = strip_security_fields(msg)
                    t   = msg.get("type", "")

                    if t == "REGISTER":
                        # The turbine is introducing itself for the first time
                        turbine_id = msg.get("turbine_id", str(addr))
                        with t_lock:
                            turbine_connections[turbine_id] = {"sock": conn, "meta": msg}
                        log.info(f"Turbine registered: {turbine_id}"
                                 f" [leader={msg.get('is_leader', '?')}]")
                        ack = sign_message({
                            "type":         "REGISTER_ACK",
                            "satellite_id": SATELLITE_ID,
                            "turbine_id":   turbine_id,
                            "timestamp":    datetime.utcnow().isoformat() + "Z",
                        })
                        conn.sendall((json.dumps(ack) + "\n").encode())

                    elif t == "TELEMETRY":
                        # Check for any gaps in the sequence numbers
                        _track_seq(msg)
                        # If the link is currently down or we simulate a packet loss drop it
                        if not is_link_up() or channel_loss():
                            continue
                        msg["relayed_by"]      = SATELLITE_ID
                        msg["relay_timestamp"] = datetime.utcnow().isoformat() + "Z"
                        try:
                            relay_queue.put_nowait(json.dumps(sign_message(msg)))
                        except queue.Full:
                            log.warning("Relay queue full - dropping telemetry")

                    elif t == "ACK":
                        # Forward the turbines acknowledgement back to the ground station
                        try:
                            relay_queue.put_nowait(json.dumps(sign_message(msg)))
                        except queue.Full:
                            pass

                    elif t == "BEACON":
                        # The turbine is broadcasting its presence so ground stations know its alive
                        beacon = sign_message({
                            "type":       "TURBINE_BEACON",
                            "turbine_id": turbine_id,
                            "satellite":  SATELLITE_ID,
                            "timestamp":  datetime.utcnow().isoformat() + "Z",
                        })
                        _broadcast_ground(json.dumps(beacon))

                    # After handling the message check if there are any commands
                    # sitting in the queue waiting to be sent to this turbine
                    if turbine_id:
                        cq = command_queue[turbine_id]
                        while not cq.empty():
                            try:
                                channel_delay()
                                conn.sendall((cq.get_nowait() + "\n").encode())
                            except (queue.Empty, OSError):
                                break

    except Exception as e:
        log.info(f"Turbine {turbine_id or addr} disconnected: {e}")
    finally:
        if turbine_id:
            with t_lock:
                turbine_connections.pop(turbine_id, None)


# This checks the sequence number on incoming telemetry and warns us if packets were skipped
def _track_seq(msg):
    tid = msg.get("turbine_id")
    seq = msg.get("seq")
    if not tid or seq is None: return
    with _stats_lock:
        prev = _seq_tracker.get(tid)
        if prev is not None and seq != prev + 1:
            dropped = seq - prev - 1
            if dropped > 0:
                log.warning(f"Seq gap on {tid}: {dropped} packet(s) lost "
                            f"(expected {prev+1}, got {seq})")
        _seq_tracker[tid] = seq


# This starts the server that listens for ground station connections
def ground_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", GROUND_LISTEN_PORT))
        srv.listen(20)
        log.info(f"  Ground downlink -> 0.0.0.0:{GROUND_LISTEN_PORT}")
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle_ground,
                             args=(conn, addr), daemon=True).start()


# This handles everything that comes in from a single ground station connection
def handle_ground(conn, addr):
    ground_id = None
    buffer    = ""
    try:
        conn.settimeout(60)
        with conn:
            while True:
                chunk = conn.recv(4096)
                if not chunk: break
                buffer += chunk.decode()
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line: continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Verify the message signature before doing anything with it
                    ok, reason = verify_message(msg)
                    if not ok:
                        log.warning(f"Rejected ground msg: {reason}")
                        continue
                    msg = strip_security_fields(msg)
                    t   = msg.get("type", "")

                    if t == "REGISTER":
                        # Ground station is connecting for the first time
                        ground_id = msg.get("ground_id", str(addr))
                        with g_lock:
                            ground_connections[ground_id] = {"sock": conn}
                        log.info(f"Ground registered: {ground_id}")
                        ack = sign_message({
                            "type":         "REGISTER_ACK",
                            "satellite_id": SATELLITE_ID,
                            "ground_id":    ground_id,
                            "timestamp":    datetime.utcnow().isoformat() + "Z",
                        })
                        conn.sendall((json.dumps(ack) + "\n").encode())

                    elif t == "COMMAND":
                        # Ground wants to send a control command to a turbine
                        _route_command(conn, msg)

                    elif t == "DISCOVER":
                        # Ground is asking which turbines are currently connected
                        with t_lock:
                            known = [{"turbine_id": tid, "meta": info["meta"]}
                                     for tid, info in turbine_connections.items()]
                        resp = sign_message({
                            "type":         "DISCOVER_RESPONSE",
                            "satellite_id": SATELLITE_ID,
                            "turbines":     known,
                            "link_up":      is_link_up(),
                            "timestamp":    datetime.utcnow().isoformat() + "Z",
                        })
                        conn.sendall((json.dumps(resp) + "\n").encode())

                    elif t == "PING":
                        # Ground is checking if the satellite link is alive
                        pong = sign_message({
                            "type":         "PONG",
                            "satellite_id": SATELLITE_ID,
                            "link_up":      is_link_up(),
                            "sent_at_echo": msg.get("sent_at_echo"),
                            "timestamp":    datetime.utcnow().isoformat() + "Z",
                        })
                        conn.sendall((json.dumps(pong) + "\n").encode())

                    elif t == "STATUS_REQUEST":
                        # Ground wants a full health report from the satellite
                        _send_status(conn)

    except Exception as e:
        log.info(f"Ground {ground_id or addr} disconnected: {e}")
    finally:
        if ground_id:
            with g_lock:
                ground_connections.pop(ground_id, None)


# This takes a command from the ground and tries to deliver it to the right turbine
# If the link is down or the turbine is unreachable it queues it for later
def _route_command(conn, msg):
    target = msg.get("turbine_id")
    msg["routed_via"]      = SATELLITE_ID
    msg["route_timestamp"] = datetime.utcnow().isoformat() + "Z"
    payload = json.dumps(sign_message(msg))
    queued  = not is_link_up() or channel_loss()

    if queued:
        command_queue[target].put(payload)
        log.warning(f"Command '{msg.get('action')}' queued for {target}")
    else:
        with t_lock:
            tc = turbine_connections.get(target)
        if tc:
            try:
                channel_delay()
                tc["sock"].sendall((payload + "\n").encode())
            except OSError:
                # If sending fails mid attempt we queue it instead
                command_queue[target].put(payload)
                queued = True
        else:
            # Turbine isnt connected right now so we hold the command until it reconnects
            command_queue[target].put(payload)
            queued = True

    # Send the ground station a confirmation that we received and routed the command
    try:
        ack = sign_message({
            "type":       "ROUTE_ACK",
            "turbine_id": target,
            "action":     msg.get("action"),
            "queued":     queued,
            "satellite":  SATELLITE_ID,
            "timestamp":  datetime.utcnow().isoformat() + "Z",
        })
        conn.sendall((json.dumps(ack) + "\n").encode())
    except OSError:
        pass


# This sends a full status report back to whoever requested it
# It includes how many turbines and ground stations are connected and channel stats
def _send_status(conn):
    with t_lock: tc = len(turbine_connections)
    with g_lock: gc = len(ground_connections)
    stats = get_stats()
    resp  = sign_message({
        "type":            "SATELLITE_STATUS",
        "satellite_id":    SATELLITE_ID,
        "link_up":         is_link_up(),
        "turbines_online": tc,
        "grounds_online":  gc,
        "relay_q_depth":   relay_queue.qsize(),
        "channel_stats":   stats,
        "timestamp":       datetime.utcnow().isoformat() + "Z",
    })
    try:
        conn.sendall((json.dumps(resp) + "\n").encode())
    except OSError:
        pass


# This loop constantly pulls messages from the relay queue and sends them to all ground stations
def relay_loop():
    while True:
        try:
            payload = relay_queue.get(timeout=1)
            _broadcast_ground(payload)
        except queue.Empty:
            pass


# This sends a message to every ground station that is currently connected
# If a ground station has disconnected it gets removed from the list
def _broadcast_ground(payload):
    channel_delay()
    with g_lock:
        dead = []
        for gid, info in ground_connections.items():
            try:
                info["sock"].sendall((payload + "\n").encode())
            except OSError:
                dead.append(gid)
        for gid in dead:
            ground_connections.pop(gid, None)


# This listens on a UDP port so that new devices can discover the satellite
# without needing to know the IP in advance
def udp_discovery():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind(("0.0.0.0", DISCOVERY_UDP_PORT))
        log.info(f"  UDP discovery   -> 0.0.0.0:{DISCOVERY_UDP_PORT}")
        while True:
            try:
                data, addr = udp.recvfrom(512)
                msg = json.loads(data.decode())
                if msg.get("type") == "DISCOVER_UDP":
                    resp = json.dumps({
                        "type":         "SATELLITE_INFO",
                        "satellite_id": SATELLITE_ID,
                        "turbine_port": TURBINE_LISTEN_PORT,
                        "ground_port":  GROUND_LISTEN_PORT,
                        "link_up":      is_link_up(),
                        "timestamp":    datetime.utcnow().isoformat() + "Z",
                    })
                    udp.sendto(resp.encode(), addr)
            except Exception:
                pass


# This prints a summary of the satellite state every 20 seconds so we can see whats happening
def status_printer():
    while True:
        time.sleep(20)
        with t_lock: tc = len(turbine_connections)
        with g_lock: gc = len(ground_connections)
        stats = get_stats()
        log.info(
            f"STATUS | link={'UP  ' if is_link_up() else 'DOWN'} | "
            f"turbines={tc} | ground={gc} | "
            f"relay_q={relay_queue.qsize()} | "
            f"loss={stats['loss_pct']}% | avg_delay={stats['avg_delay_ms']}ms"
        )


def main():
    log.info("=" * 60)
    log.info(f"  LEO SATELLITE  -  {SATELLITE_ID}")
    log.info(f"  Turbine uplink  : port {TURBINE_LISTEN_PORT}")
    log.info(f"  Ground downlink : port {GROUND_LISTEN_PORT}")
    log.info(f"  UDP discovery   : port {DISCOVERY_UDP_PORT}")
    log.info(f"  Security        : HMAC-SHA256 enabled")
    log.info(f"  Channel         : realistic LEO simulation via channel.py")
    log.info("=" * 60)

    # Start all the background services as daemon threads
    for svc in [turbine_listener, ground_listener, udp_discovery,
                relay_loop, visibility_manager, status_printer]:
        threading.Thread(target=svc, daemon=True).start()

    log.info("All services running. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Satellite shutting down.")


if __name__ == "__main__":
    main()