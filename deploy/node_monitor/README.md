# IoTCore node telemetry

This agent publishes CPU utilization/clock, RAM utilization/capacity, network throughput and storage usage once per second.
The Django MQTT listener stores each payload as one `NodeMetricSample` row.
Dashboard cards poll the latest 60 seconds once per second.

## MQTT topics

- Pi 5: `iotcore/nodes/pi5/telemetry`
- AI PC: `iotcore/nodes/home-ai-main/telemetry`

Telemetry is **not retained**. Online/offline is determined from the most recent DB
sample; by default a node becomes offline after 5 seconds without data.

## Broker accounts

The Pi can reuse the existing `iotcore-pi5` account. Create one account for the AI PC:

```bash
sudo mosquitto_passwd /etc/mosquitto/iotcore-agent-passwords iotcore-ai-pc
sudo cp deploy/mosquitto/iotcore-agent-acl /etc/mosquitto/iotcore-agent-acl
sudo chown mosquitto:mosquitto /etc/mosquitto/iotcore-agent-acl
sudo systemctl restart mosquitto
```

Do **not** use `-c` when adding the AI PC account; `-c` would overwrite the existing
password file.

## Pi 5

```bash
python3 -m venv ~/.local/share/iotcore-node-telemetry/venv
~/.local/share/iotcore-node-telemetry/venv/bin/pip install paho-mqtt==2.1.0 psutil
mkdir -p ~/.local/share/iotcore-node-telemetry ~/.config/iotcore-node-telemetry
cp deploy/node_monitor/node_telemetry_agent.py ~/.local/share/iotcore-node-telemetry/
cp deploy/node_monitor/node-telemetry.env.pi5.example ~/.config/iotcore-node-telemetry/agent.env
chmod 600 ~/.config/iotcore-node-telemetry/agent.env
```

The Pi example reuses the existing private MQTT password file from the YouTube Music
agent. If that path is different on the Pi, adjust `IOTCORE_MQTT_PASSWORD_FILE`. Then
install `deploy/systemd/iotcore-node-telemetry.service`.

## Windows AI PC

Use Python 3.12+ and install:

```powershell
py -m venv C:\IoTCore\node-telemetry\venv
C:\IoTCore\node-telemetry\venv\Scripts\python.exe -m pip install paho-mqtt==2.1.0 psutil
```

Copy `node_telemetry_agent.py` and the AI-PC env example. A simple launcher can set
those environment variables and start the agent at logon/startup using Task Scheduler.
The `IOTCORE_NETWORK_INTERFACE` value is optional; when omitted, the agent reports
aggregate traffic across interfaces.

## DB retention

Raw one-second samples are kept for 24 hours by default. Install and enable the
cleanup timer from `deploy/systemd/iotcore-node-metric-cleanup.*` on the Django server.
