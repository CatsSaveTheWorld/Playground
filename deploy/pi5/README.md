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

## Projector video control

The same Pi agent also handles the first-stage IoTCore media-server API. Keeping
these actions in the existing agent is intentional: running a second MQTT client
that subscribes to the same `iotcore/agents/pi5/commands` topic could publish a
competing result for the same request id.

Supported actions:

```text
media.list_videos
media.play_video
media.stop
```

The default video directory is:

```text
/home/leedowon/qleto_2tb/wallpaper/videos
```

The list action scans that directory recursively and returns the filename stem as
the UI title. Supported extensions are MP4, MKV, WebM, MOV, and M4V. The play
action accepts only a relative path that resolves inside this directory; arbitrary
shell commands and paths outside the media root are rejected.

Playback uses the same Wayland/mpv environment as the manually verified command:

```text
XDG_RUNTIME_DIR=/run/user/1000
WAYLAND_DISPLAY=wayland-0
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
```

with `--vo=gpu --gpu-context=wayland --profile=fast --fullscreen
--loop-file=inf --no-border --no-audio`.

When replacing an already deployed Pi agent, copy the updated repository file to
the path used by the existing systemd service and restart it:

```bash
cp deploy/pi5/ytmusic_cookie_agent.py ~/ytmusic-cookie-collector/agent.py
chmod 755 ~/ytmusic-cookie-collector/agent.py
sudo systemctl restart ytmusic-cookie-agent.service
sudo systemctl status ytmusic-cookie-agent.service --no-pager
```

If the environment file already exists, add the `IOTCORE_MEDIA_*`, mpv, and
Wayland variables from `ytmusic-cookie-agent.env.example`, then restart the
service. The defaults match the current Pi setup, so adding them is optional
unless the paths/display names differ.
