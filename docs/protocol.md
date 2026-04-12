# Communication Protocol

This project uses a simple text-based protocol.

Each message is JSON. Each JSON message is sent on one line, ending with a newline character.

Example:

```json
{"type":"PING","ground_id":"GROUND-CTRL-01","timestamp":"2026-04-12T10:00:00Z"}
```

Before a message is sent, `security.py` adds:

- `sent_at`, which is the send time
- `sig`, which is the HMAC signature

The receiver checks these fields. If the signature is wrong, or the message is too old, the message is rejected.

## Main Connections

- Turbine connects to satellite on TCP port `9000`.
- Ground station connects to satellite on TCP port `9001`.
- Satellite also listens for UDP discovery on port `9002`.

For local testing, the turbine and ground station use `127.0.0.1`.

For network testing, set `SATELLITE_HOST` to the satellite computer IP address.

## Message Types

`REGISTER`

Sent when a turbine or ground station first connects to the satellite.

`REGISTER_ACK`

Sent by the satellite to confirm that registration worked.

`TELEMETRY`

Sent by the turbine to the satellite, then forwarded to the ground station.
It contains sensor values such as wind speed, power output, rotor RPM, temperature, gearbox temperature, vibration, hydraulic pressure, and humidity.

`COMMAND`

Sent by the ground station to control a turbine. The satellite routes it to the right turbine.

Common actions:

- `SET_YAW`
- `SET_PITCH`
- `EMERGENCY_STOP`
- `RESUME`

`ACK`

Sent by the turbine after it receives and handles a command.

`ROUTE_ACK`

Sent by the satellite to say whether a command was routed or queued.

`DISCOVER`

Sent by the ground station to ask which turbines are connected.

`DISCOVER_RESPONSE`

Sent by the satellite with the list of known turbines.

`PING` and `PONG`

Used to test that the satellite connection is alive.

`FARM_ALERT`

Sent by the leader turbine when high wind is detected. The ground station can then stop all turbines.

## Simple Data Flow

1. Start the satellite.
2. Turbine connects and sends `REGISTER`.
3. Ground station connects and sends `REGISTER`.
4. Turbine sends `TELEMETRY`.
5. Satellite forwards telemetry to the ground station.
6. Ground station sends `COMMAND` when the user types a command.
7. Satellite routes the command to the turbine.
8. Turbine sends `ACK` after applying the command.

This keeps the system easy to test because every message is readable JSON.
