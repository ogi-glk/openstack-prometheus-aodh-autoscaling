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

# 8. Configure Aodh observabilityclient (/etc/openstack/prometheus.yaml)
mkdir -p /etc/openstack
cat <<EOF > /etc/openstack/prometheus.yaml
host: 127.0.0.1
port: 9090
EOF
chmod 644 /etc/openstack/prometheus.yaml

# 9. Configure Aodh in /etc/aodh/aodh.conf
if [ -f /etc/aodh/aodh.conf ]; then
    echo "[*] Configuring [prometheus] in /etc/aodh/aodh.conf..."
    crudini --set /etc/aodh/aodh.conf prometheus host "127.0.0.1" || true
    crudini --set /etc/aodh/aodh.conf prometheus port "9090" || true
    crudini --set /etc/aodh/aodh.conf prometheus url "http://127.0.0.1:9090" || true
    systemctl restart aodh-evaluator aodh-notifier aodh-listener || true
    echo "[+] Aodh configured to query Prometheus at http://127.0.0.1:9090"
fi

# 10. Ubuntu 24.04 osprofiler SQLAlchemy 2.0 chained_exception compatibility patch
python3 -c "
import os
path = '/usr/lib/python3/dist-packages/osprofiler/sqlalchemy.py'
if os.path.exists(path):
    with open(path, 'r') as f:
        content = f.read()
    old = 'chained_exception = str(exception_context.chained_exception)'
    new = 'chained_exception = str(getattr(exception_context, \"chained_exception\", getattr(exception_context, \"original_exception\", \"\")))'
    if old in content:
        with open(path, 'w') as f:
            f.write(content.replace(old, new))
        print('[+] Applied osprofiler SQLAlchemy 2.0 backwards-compatible patch')
" 2>/dev/null || true

# 11. Configure Heat CFN ec2authtoken credentials if in OpenStack-Ansible LXC environment
HEAT=$(lxc-ls -1 2>/dev/null | grep heat-api | head -n 1 || true)
if [ -n "$HEAT" ]; then
    echo "[*] Configuring Heat CFN ec2authtoken in container: $HEAT..."
    lxc-attach -n "$HEAT" -- python3 -c "
conf_path = '/etc/heat/heat.conf'
with open(conf_path) as f:
    content = f.read()
old = '[ec2authtoken]\nauth_uri = http://172.29.236.101:5000'
new = '''[ec2authtoken]
auth_uri = http://172.29.236.101:5000
auth_url = http://172.29.236.101:5000/v3
auth_type = password
username = heat
password = 5391e726a4d236d7587b3c3e6e2b
project_name = service
user_domain_id = default
project_domain_id = default'''
if 'auth_type' not in content.split('[ec2authtoken]')[-1].split('[')[0]:
    with open(conf_path, 'w') as f:
        f.write(content.replace(old, new))
    print('[+] ec2authtoken credentials configured')
" 2>/dev/null || true
    lxc-attach -n "$HEAT" -- systemctl restart heat-api-cfn 2>/dev/null || true
fi

echo "[+] Prometheus + Aodh Autoscaling setup completed successfully!"