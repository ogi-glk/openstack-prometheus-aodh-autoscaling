#!/usr/bin/env bash
# ==============================================================================
# OpenStack Prometheus Autoscaling Layer — Automated Standalone Setup Script
# ==============================================================================
set -e

INSTALL_DIR="/opt/openstack-autoscaling"
CONFIG_DIR="/etc/prometheus"
ALERTMANAGER_DIR="/etc/alertmanager"

echo "[*] Starting OpenStack Prometheus Autoscaling installation..."

# 1. Install prerequisites and official Ubuntu libvirt-exporter
echo "[*] Installing system dependencies and prometheus-libvirt-exporter..."
apt-get update -y
apt-get install -y python3-pip python3-venv python3-dev build-essential curl jq \
    prometheus-node-exporter prometheus-libvirt-exporter

systemctl enable --now prometheus-libvirt-exporter

# 2. Create required directories
mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/exporters" "$INSTALL_DIR/adapter" "$CONFIG_DIR" "$ALERTMANAGER_DIR" \
    /var/lib/prometheus /var/lib/alertmanager

# 3. Copy source and configuration files to destination
cp -r exporters/* "$INSTALL_DIR/exporters/"
cp -r adapter/* "$INSTALL_DIR/adapter/"
cp configs/prometheus.yml "$CONFIG_DIR/prometheus.yml"
cp configs/alert_rules.yml "$CONFIG_DIR/alert_rules.yml"
cp configs/alertmanager.yml "$ALERTMANAGER_DIR/alertmanager.yml"

# 4. Python Virtual Environment (venv) Setup
echo "[*] Setting up Python virtual environment and dependencies..."
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r requirements.txt

# 5. Install and enable systemd service units
echo "[*] Installing systemd service units..."
cp systemd/*.service /etc/systemd/system/
systemctl daemon-reload

systemctl enable --now server-group-exporter
systemctl enable --now heat-signal-adapter

# 6. Reload Prometheus and Alertmanager if active
if systemctl is-active --quiet prometheus; then
    systemctl restart prometheus
    echo "[+] Prometheus service restarted successfully."
fi

if systemctl is-active --quiet alertmanager; then
    systemctl restart alertmanager
    echo "[+] Alertmanager service restarted successfully."
fi

echo "[+] Setup completed successfully!"