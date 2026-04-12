# Satellite Turbine Connection

This project is a simple Python simulation of a wind turbine system that talks through a satellite relay.

There are three main programs:

- `satellite/satellite.py` runs the satellite relay.
- `turbine/turbine.py` runs a turbine node and sends sensor data.
- `ground_station/ground_station.py` runs the operator terminal and sends commands.

The project can run locally on one computer. By default, the turbine and ground station connect to `127.0.0.1`, which means "this same computer".

## Requirements

- Python 3
- No extra Python packages are needed

## How To Run Locally

Open three terminals from the project folder.

Terminal 1, start the satellite:

```bash
python satellite/satellite.py
```

Terminal 2, start a turbine:

```bash
python turbine/turbine.py TURBINE-01
```

Terminal 3, start the ground station:

```bash
python ground_station/ground_station.py
```

In the ground station terminal, try these commands:

```text
help
discover
status
ping
yaw TURBINE-01 200
pitch TURBINE-01 20
estop TURBINE-01
resume TURBINE-01
quit
```

## Running More Than One Turbine

Open extra terminals and run:

```bash
python turbine/turbine.py TURBINE-02
python turbine/turbine.py TURBINE-03
```

The default farm list is `TURBINE-01`, `TURBINE-02`, and `TURBINE-03`.

## Running On Different Computers

Find the IP address of the computer running `satellite.py`.

Then set `SATELLITE_HOST` before starting the turbine and ground station.

PowerShell example:

```powershell
$env:SATELLITE_HOST="192.168.1.50"
python turbine/turbine.py TURBINE-01
```

In another terminal:

```powershell
$env:SATELLITE_HOST="192.168.1.50"
python ground_station/ground_station.py
```

Use `127.0.0.1` when all programs run on the same computer.

## Files

- `channel.py` simulates satellite delay, packet loss, and link visibility.
- `security.py` signs and checks messages with HMAC.
- `sensors.py` creates turbine sensor readings.
- `equipment.py` contains simple turbine equipment models.
- `docs/protocol.md` explains the messages passed between the programs.
