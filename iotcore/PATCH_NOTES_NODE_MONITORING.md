# Node monitoring patch

## Added

- `NodeMetricSample`: one DB row per node telemetry payload.
- MQTT telemetry topics: `iotcore/nodes/<device_uid>/telemetry`.
- One-second telemetry agent for Linux/Windows (`psutil` + `paho-mqtt`).
- Dashboard system-monitor cards for AI PC and Raspberry Pi 5.
  - CPU: radial gauge
  - RAM: horizontal usage bar
  - Network: 60-second download/upload line chart
  - Storage: semi-circle gauge
  - Offline state after 5 seconds without telemetry
- `/iotcore/dashboard/node-metrics/` JSON polling endpoint.
- Raw telemetry cleanup command and hourly systemd timer (24-hour default retention).

## Deployment order

1. `python3 manage.py migrate --settings=playground.settings_mysql`
2. Restart `iotcore-automation-listener.service` and Apache.
3. Update Mosquitto ACL and restart Mosquitto.
4. Install/start Pi telemetry agent.
5. Install/start AI-PC telemetry agent when that PC is ready.
6. Enable `iotcore-node-metric-cleanup.timer`.
