# IoTCore automation services

After deploying the Django code to `/home/leedowon/Playground`, install the
server-side stack with:

```bash
sudo bash deploy/systemd/install_iotcore_stack.sh
```

The installer performs all of the steps below, removes the legacy worker from
the active service set, and links Mosquitto and Zigbee2MQTT to Apache's
lifecycle. `ytmusic-cookie-agent` is intentionally excluded because it runs on
the separate Pi host.

Manual installation steps:


> 기존 설치에서 업그레이드하는 경우 먼저 구형 워커를 제거하세요.
> `sudo systemctl disable --now iotcore-sequence-worker.service || true`
> `sudo rm -f /etc/systemd/system/iotcore-sequence-worker.service`

```bash
cd /home/leedowon/Playground
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
sudo cp deploy/systemd/iotcore-scheduler.service /etc/systemd/system/
sudo cp deploy/systemd/iotcore-automation-worker.service /etc/systemd/system/
sudo cp deploy/systemd/iotcore-automation-listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl reenable --now iotcore-scheduler iotcore-automation-worker iotcore-automation-listener
sudo systemctl status iotcore-scheduler iotcore-automation-worker iotcore-automation-listener --no-pager
```

Install the Apache lifecycle drop-ins for Mosquitto and Zigbee2MQTT:

```bash
sudo install -D -m 0644 deploy/systemd/drop-ins/apache2-iotcore-stack.conf /etc/systemd/system/apache2.service.d/iotcore-stack.conf
sudo install -D -m 0644 deploy/systemd/drop-ins/mosquitto-iotcore-apache.conf /etc/systemd/system/mosquitto.service.d/iotcore-apache.conf
sudo install -D -m 0644 deploy/systemd/drop-ins/zigbee2mqtt-iotcore-apache.conf /etc/systemd/system/zigbee2mqtt.service.d/iotcore-apache.conf
sudo systemctl daemon-reload
```

The listener, scheduler, worker, Mosquitto, and Zigbee2MQTT are bound to
`apache2.service`: starting Apache starts them, and stopping or restarting
Apache stops or restarts them with it. Verify the links after installation
with:

```bash
sudo systemctl restart apache2
sudo systemctl status apache2 mosquitto zigbee2mqtt iotcore-scheduler iotcore-automation-worker iotcore-automation-listener --no-pager
```

The scheduler converts due time triggers into pending `AutomationRun` rows.
The MQTT listener does the same for matching sensor events. The worker executes
all immediate/scheduled `AutomationRun` actions, including nested Automation calls, so long-running device
operations do not block Apache requests or MQTT event handling.

## MQTT listener for Pi agents

The current broker listens only on loopback. Keep that listener for existing
IoTCore clients and add an authenticated LAN listener for Pi agents:

```bash
sudo mosquitto_passwd -c /etc/mosquitto/iotcore-agent-passwords iotcore-pi5
sudo chown root:mosquitto /etc/mosquitto/iotcore-agent-passwords
sudo chmod 640 /etc/mosquitto/iotcore-agent-passwords
sudo cp deploy/mosquitto/iotcore-agent-acl /etc/mosquitto/iotcore-agent-acl
sudo chown root:mosquitto /etc/mosquitto/iotcore-agent-acl
sudo chmod 640 /etc/mosquitto/iotcore-agent-acl
sudo cp deploy/mosquitto/iotcore-listeners.conf /etc/mosquitto/conf.d/
sudo systemctl restart mosquitto
sudo systemctl status mosquitto --no-pager
ss -ltn | grep ':1884'
```

If UFW is enabled, allow port 1884 only from the home subnet:

```bash
sudo ufw allow from 192.168.0.0/24 to any port 1884 proto tcp
```


## Node telemetry / metric retention

Pi 5 telemetry agent:

```bash
sudo cp deploy/systemd/iotcore-node-telemetry.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iotcore-node-telemetry.service
```

Django server raw metric cleanup (24-hour retention):

```bash
sudo cp deploy/systemd/iotcore-node-metric-cleanup.service /etc/systemd/system/
sudo cp deploy/systemd/iotcore-node-metric-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iotcore-node-metric-cleanup.timer
```
