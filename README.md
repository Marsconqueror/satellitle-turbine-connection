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

You can also run the three parts on three different computers if all of them are on the same Wi-Fi or LAN.

Use the computers like this:

- Computer 1 runs the satellite.
- Computer 2 runs the turbine.
- Computer 3 runs the ground station.

First, find the IP address of Computer 1, because that is where `satellite.py` is running.

On Computer 1:

```bash
python satellite/satellite.py
```

On Computer 2, set `SATELLITE_HOST` to Computer 1's IP address, then start the turbine.

PowerShell example:

```powershell
$env:SATELLITE_HOST="192.168.1.50"
python turbine/turbine.py TURBINE-01
```

On Computer 3, use the same satellite IP address, then start the ground station.

```powershell
$env:SATELLITE_HOST="192.168.1.50"
python ground_station/ground_station.py
```

In this example, `192.168.1.50` is only an example. Replace it with the real IP address of the satellite computer.

Use `127.0.0.1` only when all programs run on the same computer.

If the computers cannot connect, check that:

- all three computers are on the same network
- the satellite program is running first
- the firewall is not blocking ports `9000`, `9001`, and `9002`

## Files

- `channel.py` simulates satellite delay, packet loss, and link visibility.
- `security.py` signs and checks messages with HMAC.
- `sensors.py` creates turbine sensor readings.
- `equipment.py` contains simple turbine equipment models.
- `docs/protocol.md` explains the messages passed between the programs.
