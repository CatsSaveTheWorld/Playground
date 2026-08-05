# Pi 5 cookie agent

The agent receives `ytmusic.refresh_cookie` over MQTT, runs the dedicated
Chromium collector, updates the existing YouTube Music provider through the
Music Assistant API, and publishes only a safe status result.

Secrets stay on the Pi. The cookie file and Music Assistant token must both be
mode `600`; neither belongs in Git or in the IoTCore database.

## Install

```bash
sudo apt install python3-paho-mqtt python3-requests
mkdir -p ~/.config/ytmusic-cookie-agent
chmod 700 ~/.config/ytmusic-cookie-agent
cp deploy/pi5/ytmusic-cookie-agent.env.example ~/.config/ytmusic-cookie-agent/agent.env
chmod 600 ~/.config/ytmusic-cookie-agent/agent.env
cp deploy/pi5/ytmusic_cookie_agent.py ~/ytmusic-cookie-collector/agent.py
chmod 700 ~/ytmusic-cookie-collector/agent.py
```

Create the MQTT password file using the password configured for the
`iotcore-pi5` Mosquitto account on the IoTCore server:

```bash
read -rsp "MQTT password: " MQTT_PASSWORD
printf '%s' "$MQTT_PASSWORD" > ~/.config/ytmusic-cookie-agent/mqtt-password
unset MQTT_PASSWORD
chmod 600 ~/.config/ytmusic-cookie-agent/mqtt-password
```

Create a dedicated Music Assistant long-lived access token with administrator
permission, then write it without displaying it in shell history:

```bash
read -rsp "Music Assistant token: " TOKEN
printf '%s' "$TOKEN" > ~/.config/ytmusic-cookie-agent/music-assistant-token
unset TOKEN
chmod 600 ~/.config/ytmusic-cookie-agent/music-assistant-token
```

Install and start the service:

```bash
sudo cp deploy/systemd/ytmusic-cookie-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ytmusic-cookie-agent
sudo systemctl status ytmusic-cookie-agent --no-pager
```

The interactive Chromium login must have been completed once with
`collector.py --login` before unattended refreshes can succeed.
