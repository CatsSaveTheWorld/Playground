#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this installer with sudo." >&2
    exit 1
fi

project_dir="${1:-/home/leedowon/Playground}"
unit_dir=/etc/systemd/system

install -m 0644 \
    "${project_dir}/deploy/systemd/iotcore-automation-worker.service" \
    "${unit_dir}/iotcore-automation-worker.service"

install -d -m 0755 \
    "${unit_dir}/apache2.service.d" \
    "${unit_dir}/mosquitto.service.d" \
    "${unit_dir}/zigbee2mqtt.service.d"

install -m 0644 \
    "${project_dir}/deploy/systemd/drop-ins/apache2-iotcore-stack.conf" \
    "${unit_dir}/apache2.service.d/iotcore-stack.conf"
install -m 0644 \
    "${project_dir}/deploy/systemd/drop-ins/mosquitto-iotcore-apache.conf" \
    "${unit_dir}/mosquitto.service.d/iotcore-apache.conf"
install -m 0644 \
    "${project_dir}/deploy/systemd/drop-ins/zigbee2mqtt-iotcore-apache.conf" \
    "${unit_dir}/zigbee2mqtt.service.d/iotcore-apache.conf"

# The old command is a compatibility alias for run_automation_worker. Keeping
# both services enabled would create two consumers for the same queue.
systemctl disable --now iotcore-sequence-worker.service || true
systemctl daemon-reload
systemctl enable iotcore-automation-worker.service

# Restarting Apache now restarts the complete server-side IoTCore stack.
systemctl restart apache2.service

services=(
    apache2.service
    mosquitto.service
    zigbee2mqtt.service
    iotcore-automation-listener.service
    iotcore-scheduler.service
    iotcore-automation-worker.service
)

for service in "${services[@]}"; do
    # Apache can become active before its asynchronous Wants dependencies have
    # finished starting. Wait briefly so a healthy stack is not reported as a
    # failed installation merely because a dependency is still "activating".
    for _ in {1..30}; do
        state="$(systemctl is-active "${service}" 2>/dev/null || true)"
        [[ ${state} == active ]] && break
        [[ ${state} == failed ]] && break
        sleep 1
    done

    if ! systemctl is-active --quiet "${service}"; then
        systemctl --no-pager --full status "${service}" || true
        exit 1
    fi
    echo "active ${service}"
done
