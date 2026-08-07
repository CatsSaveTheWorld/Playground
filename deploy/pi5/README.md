# Pi 5 YouTube Music cookie agent

The Django server publishes `ytmusic.refresh_cookie` to MQTT. The Pi agent
runs `collector.py`, then `applier.py`, and publishes a correlated final result.
Cookie and access-token contents never leave the Pi.

## MQTT broker on the IoTCore server

Create the Pi account. Enter a new password when prompted and use the same
password later on the Pi.

```bash
sudo mosquitto_passwd -c /etc/mosquitto/iotcore-agent-passwords iotcore-pi5
sudo chown mosquitto:mosquitto /etc/mosquitto/iotcore-agent-passwords
sudo chmod 600 /etc/mosquitto/iotcore-agent-passwords
sudo cp deploy/mosquitto/iotcore-agent-listeners.conf /etc/mosquitto/conf.d/
sudo systemctl restart mosquitto
sudo systemctl status mosquitto --no-pager
sudo ss -ltnp | grep -E ':1883|:1884'
```

Port 1883 remains loopback-only for Django. Port 1884 accepts authenticated Pi
connections over the LAN.

## Pi configuration

The code uses an isolated environment containing only `paho-mqtt`:

```bash
python3 -m venv ~/ytmusic-cookie-collector/.agent-venv
~/ytmusic-cookie-collector/.agent-venv/bin/pip install paho-mqtt==2.1.0
```

Create the configuration and password file:

```bash
mkdir -p ~/.config/ytmusic-cookie-agent
chmod 700 ~/.config/ytmusic-cookie-agent
cp deploy/pi5/ytmusic-cookie-agent.env.example \
  ~/.config/ytmusic-cookie-agent/agent.env
chmod 600 ~/.config/ytmusic-cookie-agent/agent.env
read -rsp "MQTT password: " MQTT_PASSWORD
printf '%s' "$MQTT_PASSWORD" > \
  ~/.config/ytmusic-cookie-agent/mqtt-password
unset MQTT_PASSWORD
chmod 600 ~/.config/ytmusic-cookie-agent/mqtt-password
```

The Music Assistant token used by `applier.py` stays at:

```text
~/.config/iotcore/music_assistant.token
```

Install and start the service:

```bash
sudo cp deploy/systemd/ytmusic-cookie-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ytmusic-cookie-agent
sudo systemctl status ytmusic-cookie-agent --no-pager
```

## Direct pipeline test

This bypasses MQTT but exercises the exact collector-to-applier pipeline:

```bash
~/ytmusic-cookie-collector/.agent-venv/bin/python \
  ~/ytmusic-cookie-collector/agent.py --run-once
```

## MQTT contract

Command topic:

```text
iotcore/agents/pi5/commands
```

Command payload:

```json
{"request_id":"unique-id","action":"ytmusic.refresh_cookie","parameters":{}}
```

Result topic:

```text
iotcore/agents/pi5/results/<request_id>
```

The result contains only status, timestamps, provider id, and a cookie
fingerprint. It never contains the cookie or access token.
