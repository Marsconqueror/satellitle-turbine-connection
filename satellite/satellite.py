"""Satellite relay between the turbine and ground station."""

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

turbine_connections = {}
ground_connections  = {}
t_lock = threading.Lock()
g_lock = threading.Lock()

relay_queue   = queue.Queue(maxsize=500)
command_queue = defaultdict(lambda: queue.Queue(maxsize=100))

_seq_tracker = {}
_stats_lock  = threading.Lock()


def turbine_listener():
    """Wait for turbine connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", TURBINE_LISTEN_PORT))
        srv.listen(20)
        log.info(f"  Turbine uplink  -> 0.0.0.0:{TURBINE_LISTEN_PORT}")
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle_turbine,
                             args=(conn, addr), daemon=True).start()


def handle_turbine(conn, addr):
    """Receive messages from one turbine."""
    turbine_id = None
    buffer     = ""
    try:
        conn.settimeout(30)
        with conn:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk.decode()

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    ok, reason = verify_message(msg)
                    if not ok:
                        log.warning(f"Rejected msg from {addr}: {reason}")
                        continue
                    msg = strip_security_fields(msg)
                    t   = msg.get("type", "")

                    if t == "REGISTER":
                        turbine_id = msg.get("turbine_id", str(addr))
                        with t_lock:
                            turbine_connections[turbine_id] = {"sock": conn, "meta": msg}
                        log.info(
                            f"Turbine registered: {turbine_id}"
                            f" [leader={msg.get('is_leader', '?')}]"
                        )
                        ack = sign_message({
                            "type":         "REGISTER_ACK",
                            "satellite_id": SATELLITE_ID,
                            "turbine_id":   turbine_id,
                            "timestamp":    datetime.utcnow().isoformat() + "Z",
                        })
                        conn.sendall((json.dumps(ack) + "\n").encode())

                    elif t == "TELEMETRY":
                        # Forward one turbine telemetry message.
                        _track_seq(msg)
                        if not is_link_up() or channel_loss():
                            pass  # drop packet during simulated loss
                        else:
                            msg["relayed_by"]      = SATELLITE_ID
                            msg["relay_timestamp"] = datetime.utcnow().isoformat() + "Z"
                            try:
                                relay_queue.put_nowait(json.dumps(sign_message(msg)))
                            except queue.Full:
                                log.warning("Relay queue full - dropping telemetry")

                    elif t == "FARM_ALERT":
                        # Forward important farm alerts without dropping them.
                        msg["relayed_by"]      = SATELLITE_ID
                        msg["relay_timestamp"] = datetime.utcnow().isoformat() + "Z"
                        log.warning(
                            f"FARM_ALERT from {msg.get('leader_id')}: "
                            f"{msg.get('alert_type')} wind={msg.get('wind_speed')}m/s"
                        )
                        try:
                            relay_queue.put_nowait(json.dumps(sign_message(msg)))
                        except queue.Full:
                            # Clear one old item if the queue is full.
                            try:
                                relay_queue.get_nowait()
                            except queue.Empty:
                                pass
                            relay_queue.put_nowait(json.dumps(sign_message(msg)))

                    elif t == "ACK":
                        try:
                            relay_queue.put_nowait(json.dumps(sign_message(msg)))
                        except queue.Full:
                            pass

                    elif t == "BEACON":
                        beacon = sign_message({
                            "type":       "TURBINE_BEACON",
                            "turbine_id": turbine_id,
                            "satellite":  SATELLITE_ID,
                            "timestamp":  datetime.utcnow().isoformat() + "Z",
                        })
                        _broadcast_ground(json.dumps(beacon))

                    # Send any commands waiting for this turbine.
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


def _track_seq(msg):
    """Check if telemetry packets were missed."""
    tid = msg.get("turbine_id")
    seq = msg.get("seq")
    if not tid or seq is None:
        return
    with _stats_lock:
        prev = _seq_tracker.get(tid)
        if prev is not None and seq != prev + 1:
            dropped = seq - prev - 1
            if dropped > 0:
                log.warning(
                    f"Seq gap on {tid}: {dropped} packet(s) lost "
                    f"(expected {prev+1}, got {seq})"
                )
        _seq_tracker[tid] = seq


def ground_listener():
    """Wait for ground station connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", GROUND_LISTEN_PORT))
        srv.listen(20)
        log.info(f"  Ground downlink -> 0.0.0.0:{GROUND_LISTEN_PORT}")
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle_ground,
                             args=(conn, addr), daemon=True).start()


def handle_ground(conn, addr):
    """Receive messages from one ground station."""
    ground_id = None
    buffer    = ""
    try:
        conn.settimeout(60)
        with conn:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk.decode()

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    ok, reason = verify_message(msg)
                    if not ok:
                        log.warning(f"Rejected ground msg: {reason}")
                        continue
                    msg = strip_security_fields(msg)
                    t   = msg.get("type", "")

                    if t == "REGISTER":
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
                        _route_command(conn, msg)

                    elif t == "DISCOVER":
                        with t_lock:
                            known = [
                                {"turbine_id": tid, "meta": info["meta"]}
                                for tid, info in turbine_connections.items()
                            ]
                        resp = sign_message({
                            "type":         "DISCOVER_RESPONSE",
                            "satellite_id": SATELLITE_ID,
                            "turbines":     known,
                            "link_up":      is_link_up(),
                            "timestamp":    datetime.utcnow().isoformat() + "Z",
                        })
                        conn.sendall((json.dumps(resp) + "\n").encode())

                    elif t == "PING":
                        pong = sign_message({
                            "type":         "PONG",
                            "satellite_id": SATELLITE_ID,
                            "link_up":      is_link_up(),
                            "sent_at_echo": msg.get("sent_at_echo"),
                            "timestamp":    datetime.utcnow().isoformat() + "Z",
                        })
                        conn.sendall((json.dumps(pong) + "\n").encode())

                    elif t == "STATUS_REQUEST":
                        _send_status(conn)

    except Exception as e:
        log.info(f"Ground {ground_id or addr} disconnected: {e}")
    finally:
        if ground_id:
            with g_lock:
                ground_connections.pop(ground_id, None)


def _route_command(conn, msg):
    """Route a command to a turbine."""
    target = msg.get("turbine_id")
    msg["routed_via"]      = SATELLITE_ID
    msg["route_timestamp"] = datetime.utcnow().isoformat() + "Z"
    payload = json.dumps(sign_message(msg))
    queued  = not is_link_up() or channel_loss()

    if target == "ALL":
        # Send to every connected turbine.
        with t_lock:
            targets = list(turbine_connections.keys())
        for tid in targets:
            _deliver_or_queue(tid, payload, queued)
        queued = False
    else:
        _deliver_or_queue(target, payload, queued)

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


def _deliver_or_queue(target, payload, queued):
    """Send a command now or save it for later."""
    if queued:
        command_queue[target].put(payload)
        log.warning(f"Command queued for {target}")
        return
    with t_lock:
        tc = turbine_connections.get(target)
    if tc:
        try:
            channel_delay()
            tc["sock"].sendall((payload + "\n").encode())
        except OSError:
            command_queue[target].put(payload)
    else:
        command_queue[target].put(payload)
        log.warning(f"Turbine {target} not connected - command queued")


def _send_status(conn):
    """Send satellite status to the ground station."""
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


def relay_loop():
    """Forward queued telemetry to ground stations."""
    while True:
        try:
            payload = relay_queue.get(timeout=1)
            _broadcast_ground(payload)
        except queue.Empty:
            pass


def _broadcast_ground(payload):
    """Send one message to all ground stations."""
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


def udp_discovery():
    """Reply to UDP discovery requests."""
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


def status_printer():
    """Print satellite status sometimes."""
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
