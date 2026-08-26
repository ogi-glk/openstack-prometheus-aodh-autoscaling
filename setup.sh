#!/usr/bin/env bash
# ==============================================================================
# OpenStack Prometheus + Aodh Autoscaling Layer — Automated Standalone Setup
# ==============================================================================
set -e

INSTALL_DIR="/opt/openstack-autoscaling"
CONFIG_DIR="/etc/prometheus"

echo "[*] Starting OpenStack Prometheus + Aodh Autoscaling setup..."

# 1. Install prerequisites and libvirt-exporter
apt-get update -y
apt-get install -y python3-pip python3-venv python3-dev build-essential curl jq \
    prometheus-node-exporter prometheus-libvirt-exporter

systemctl enable --now prometheus-libvirt-exporter

# 2. Create required directories
mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/exporters" "$CONFIG_DIR" /var/lib/prometheus

# 3. Copy exporter and prometheus config
cp -r exporters/* "$INSTALL_DIR/exporters/"
cp configs/prometheus.yml "$CONFIG_DIR/prometheus.yml"

# 4. Python Virtual Environment Setup
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r requirements.txt

# 5. Install systemd service for server-group-exporter
cp systemd/server-group-exporter.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now server-group-exporter

# 6. Download and install Prometheus binary
if ! command -v prometheus &> /dev/null; then
    echo "[*] Downloading Prometheus 2.51.0..."
    curl -sSL https://github.com/prometheus/prometheus/releases/download/v2.51.0/prometheus-2.51.0.linux-amd64.tar.gz | tar -xz -C /tmp
    cp /tmp/prometheus-2.51.0.linux-amd64/prometheus /usr/local/bin/
    cp /tmp/prometheus-2.51.0.linux-amd64/promtool /usr/local/bin/
fi

# 7. Prometheus systemd unit
cat <<EOF > /etc/systemd/system/prometheus.service
[Unit]
Description=Prometheus for OpenStack Aodh
After=network.target

[Service]
ExecStart=/usr/local/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus/
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now prometheus

# 8. Configure Aodh if aodh.conf exists
if [ -f /etc/aodh/aodh.conf ]; then
    echo "[*] Configuring [prometheus] in /etc/aodh/aodh.conf..."
    crudini --set /etc/aodh/aodh.conf prometheus url "http://localhost:9090" || true
    systemctl restart aodh-evaluator aodh-notifier || true
    echo "[+] Aodh configured to query Prometheus at http://localhost:9090"
fi

echo "[+] Prometheus + Aodh Autoscaling setup completed successfully!"