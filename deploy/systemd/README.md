# IoTCore automation services

After deploying the Django code to `/home/leedowon/Playground`:

```bash
cd /home/leedowon/Playground
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
sudo cp deploy/systemd/iotcore-scheduler.service /etc/systemd/system/
sudo cp deploy/systemd/iotcore-sequence-worker.service /etc/systemd/system/
sudo cp deploy/systemd/iotcore-automation-listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl reenable --now iotcore-scheduler iotcore-sequence-worker iotcore-automation-listener
sudo systemctl status iotcore-scheduler iotcore-sequence-worker iotcore-automation-listener --no-pager
```

These units are bound to `apache2.service`: starting Apache starts them, and
stopping or restarting Apache stops or restarts them with it. Verify the link
after installation with:

```bash
sudo systemctl restart apache2
sudo systemctl status apache2 iotcore-scheduler iotcore-sequence-worker iotcore-automation-listener --no-pager
```

The scheduler converts due time triggers into pending `AutomationRun` rows.
The MQTT listener does the same for matching sensor events. The worker executes
automation actions and any nested `SequenceRun` rows, so long-running device
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
