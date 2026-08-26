# OpenStack Prometheus Autoscaling Layer

[Türkçe](README.md) | [English](README.en.md)

---

An enterprise integration layer providing CPU-based autoscaling for OpenStack environments using a lightweight **Prometheus + Alertmanager + libvirt-exporter** architecture instead of legacy Ceilometer, Gnocchi, and Aodh services.

The project supports three deployment methods:
1. **Via Ansible Role:** Automated deployment across inventory hosts using `openstack-ansible` or standard `ansible-playbook`.
2. **Standalone Shell Script:** Direct automated installation on the target host using `setup.sh`.
3. **Step-by-Step Manual Setup:** Full manual configuration of virtual environments, systemd unit files, and background daemons.

---

## 1. Architecture and Operating Logic

```text
┌────────────────────────┐         ┌──────────────────────────────┐
│ libvirt-exporter       │ :9177   │ server-group-exporter        │ :9102
│ KVM domain CPU metrics │         │ Nova Metadata -> Stack ID    │
│ (instance-0000000X)    │         │ (60s TTL RAM Cache)          │
└───────────┬────────────┘         └──────────────┬───────────────┘
            │                                     │
            └──────────────────┬──────────────────┘
                               ▼
                   ┌──────────────────────┐
                   │      Prometheus      │ :9090
                   │ PromQL Vector Join   │
                   │ avg by (server_group)│
                   └───────────┬──────────┘
                               ▼
                   ┌──────────────────────┐
                   │     Alertmanager     │ :9093
                   │ (group_wait, repeat) │
                   └───────────┬──────────┘
                               ▼
                   ┌──────────────────────┐
                   │ heat_signal_adapter  │ :9200
                   │ OpenStack SDK        │
                   │ Native Heat REST API │
                   └───────────┬──────────┘
                               ▼
                   ┌──────────────────────┐
                   │     Heat Engine      │ :8004
                   │ AutoScalingGroup     │
                   │ (Scale-Out/Scale-In) │
                   └──────────────────────┘
```

### Components:
1. **libvirt-exporter (:9177):** Collects raw CPU time metrics directly from KVM/libvirt for running instances (`prometheus-libvirt-exporter`).
2. **server-group-exporter (:9102):** Connects to Nova API to map instance domain names to Heat Stack IDs (`metering.server_group`). Uses a 60-second TTL RAM cache to reduce Nova API load by 75%, responding to Prometheus scrapes in `< 1ms`.
3. **Prometheus (:9090):** Joins both streams via PromQL vector matching on the common `domain` label and calculates average CPU utilization per Stack.
4. **Alertmanager (:9093):** Evaluates firing thresholds and routes alerts to the local webhook adapter (`:9200`).
5. **heat_signal_adapter (:9200):** Receives Alertmanager webhooks, authenticates via `openstacksdk` with Keystone, and dispatches authorized HTTP POST signals directly to Heat's native REST API (`port 8004`).

---

## 2. Directory Structure

```text
openstack-prometheus-autoscaling/
├── autoscaling.yml                    # Deployment Ansible Playbook
├── setup.sh                           # Standalone installation script
├── requirements.txt                   # Python package dependencies
│
├── defaults/                          # Role variables
│   └── main.yml                       # Thresholds, ports, and cooldown intervals
│
├── tasks/                             # Ansible tasks
│   ├── main.yml                       # Task execution sequence
│   ├── prerequisites.yml              # Directories, system packages, and Python venv
│   ├── prometheus.yml                 # Prometheus and Alertmanager installation
│   └── adapter.yml                    # Adapter, exporter, and systemd services
│
├── templates/                         # Jinja2 (.j2) configuration templates
│   ├── openrc.j2                      # Keystone credentials template
│   ├── alert_rules.yml.j2             # PromQL alert rules template
│   ├── alertmanager.yml.j2            # Webhook routing template
│   ├── prometheus.yml.j2              # Prometheus scrape targets template
│   ├── heat-signal-adapter.service.j2 # Systemd adapter service template
│   └── server-group-exporter.service.j2
│
├── handlers/                          # Service restart handlers
│   └── main.yml
│
├── meta/                              # Ansible Galaxy metadata
│   └── main.yml
│
├── exporters/                         # Python source code
│   └── server_group_exporter.py       # Nova Metadata -> Prometheus exporter (:9102)
│
├── adapter/                           # Python source code
│   └── heat_signal_adapter.py         # Alertmanager -> Native Heat API adapter (:9200)
│
├── configs/                           # Sample config files (For manual setup)
│   ├── prometheus.yml
│   ├── alert_rules.yml
│   └── alertmanager.yml
│
└── systemd/                           # Sample systemd service units (For manual setup)
    ├── server-group-exporter.service
    └── heat-signal-adapter.service
```

---

## 3. Variables and Parameters

Configured via `defaults/main.yml`:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `autoscaling_cpu_high_threshold` | `70` | Scale-Out threshold (% CPU) |
| `autoscaling_cpu_low_threshold` | `20` | Scale-In threshold (% CPU) |
| `autoscaling_evaluation_period` | `60s` | Verification window before alert fires (`for: 60s`) |
| `autoscaling_alarm_cooldown` | `2m` | Alertmanager repeat interval (`repeat_interval`) |
| `server_group_exporter_cache_ttl` | `60` | Nova API polling interval in seconds |
| `heat_signal_adapter_port` | `9200` | Adapter listening port |
| `server_group_exporter_port` | `9102` | Metadata exporter listening port |
| `libvirt_exporter_port` | `9177` | KVM libvirt exporter port |
| `prometheus_port` | `9090` | Prometheus listening port |
| `alertmanager_port` | `9093` | Alertmanager listening port |
| `heat_stack_name` | `autoscaling-stack` | Target Heat Stack name |

---

## 4. Installation Methods

### Method 1: Automated Ansible Role (Recommended)

From the Ansible deployer node:

```bash
git clone https://github.com/ogi-glk/openstack-prometheus-autoscaling.git
cd openstack-prometheus-autoscaling

openstack-ansible autoscaling.yml
# or
ansible-playbook -i hosts autoscaling.yml
```

---

### Method 2: Standalone Shell Script (`setup.sh`)

Direct installation on target node without Ansible:

```bash
cd openstack-prometheus-autoscaling
chmod +x setup.sh
sudo bash setup.sh
```

---

### Method 3: Step-by-Step Manual Installation (Debugging / Custom Environments)

#### A) Install System Packages and Libvirt Exporter:
```bash
sudo apt update
sudo apt install -y python3-pip python3-venv python3-dev build-essential curl jq prometheus-node-exporter prometheus-libvirt-exporter
sudo systemctl enable --now prometheus-libvirt-exporter
```

#### B) Create Directories and Copy Application Files:
```bash
sudo mkdir -p /opt/openstack-autoscaling/exporters /opt/openstack-autoscaling/adapter /etc/prometheus /etc/alertmanager /var/lib/prometheus /var/lib/alertmanager

sudo cp exporters/server_group_exporter.py /opt/openstack-autoscaling/exporters/
sudo cp adapter/heat_signal_adapter.py /opt/openstack-autoscaling/adapter/
sudo cp configs/prometheus.yml /etc/prometheus/prometheus.yml
sudo cp configs/alert_rules.yml /etc/prometheus/alert_rules.yml
sudo cp configs/alertmanager.yml /etc/alertmanager/alertmanager.yml
```

#### C) Setup Python Virtual Environment:
```bash
sudo python3 -m venv /opt/openstack-autoscaling/venv
sudo /opt/openstack-autoscaling/venv/bin/pip install --upgrade pip
sudo /opt/openstack-autoscaling/venv/bin/pip install openstacksdk prometheus-client requests urllib3
```

#### D) Download Prometheus and Alertmanager Binaries:
```bash
curl -sSL https://github.com/prometheus/prometheus/releases/download/v2.51.0/prometheus-2.51.0.linux-amd64.tar.gz | tar -xz -C /tmp
sudo cp /tmp/prometheus-2.51.0.linux-amd64/prometheus /usr/local/bin/
sudo cp /tmp/prometheus-2.51.0.linux-amd64/promtool /usr/local/bin/

curl -sSL https://github.com/prometheus/alertmanager/releases/download/v0.27.0/alertmanager-0.27.0.linux-amd64.tar.gz | tar -xz -C /tmp
sudo cp /tmp/alertmanager-0.27.0.linux-amd64/alertmanager /usr/local/bin/
sudo cp /tmp/alertmanager-0.27.0.linux-amd64/amtool /usr/local/bin/
```

#### E) Create OpenStack Credentials File (`openrc`):
```bash
cat <<EOF > /opt/openstack-autoscaling/openrc
OS_AUTH_URL=http://172.29.236.101:5000/v3
OS_PROJECT_NAME=admin
OS_USERNAME=admin
OS_PASSWORD=SECRET_PASSWORD
OS_USER_DOMAIN_NAME=Default
OS_PROJECT_DOMAIN_NAME=Default
OS_IDENTITY_API_VERSION=3
HEAT_STACK_NAME=autoscaling-stack
EOF
chmod 600 /opt/openstack-autoscaling/openrc
```

#### F) Install Systemd Units and Start Services:
```bash
sudo cp systemd/server-group-exporter.service /etc/systemd/system/
sudo cp systemd/heat-signal-adapter.service /etc/systemd/system/

cat <<EOF > /etc/systemd/system/prometheus.service
[Unit]
Description=Prometheus
After=network.target

[Service]
ExecStart=/usr/local/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus/
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat <<EOF > /etc/systemd/system/alertmanager.service
[Unit]
Description=Alertmanager
After=network.target

[Service]
ExecStart=/usr/local/bin/alertmanager --config.file=/etc/alertmanager/alertmanager.yml --storage.path=/var/lib/alertmanager/
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now prometheus alertmanager server-group-exporter heat-signal-adapter
```

---

## 5. Testing, Verification, and Live Evidence

### 1. Service Health Checks:
```bash
systemctl is-active prometheus alertmanager prometheus-libvirt-exporter server-group-exporter heat-signal-adapter
```

### 2. Exporter Scrape Output (Multi-Instance Discovery):
```text
$ curl -s http://localhost:9102/metrics
# HELP openstack_instance_server_group Nova instance to Heat stack_id (server_group) mapping
# TYPE openstack_instance_server_group gauge
openstack_instance_server_group{domain="instance-00000007",instance_id="2972726a...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
openstack_instance_server_group{domain="instance-00000008",instance_id="55497eaa...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
openstack_instance_server_group{domain="instance-00000009",instance_id="1810fd03...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
```

### 3. Prometheus PromQL Verification:
```promql
avg by (server_group) (
  rate(libvirt_domain_info_cpu_time_seconds_total[1m]) * 100
  * on(domain) group_left(server_group) 
  openstack_instance_server_group
)
```

### 4. Adapter Live Execution Log (Touchless Scale-Out & Scale-In):
```text
$ journalctl -u heat-signal-adapter -n 10 --no-pager
[*] Native Heat Signal Adapter listening on port :9200 for stack 'autoscaling-stack'...
[+] SUCCESS: CpuHigh -> Native Heat signal 'scaleup_policy' triggered (HTTP 200)   <-- Scale-Out: 1 to 2 VMs
[+] SUCCESS: CpuHigh -> Native Heat signal 'scaleup_policy' triggered (HTTP 200)   <-- Scale-Out: 2 to 3 VMs (Max limit)
[+] SUCCESS: CpuLow  -> Native Heat signal 'scaledown_policy' triggered (HTTP 200) <-- Load removed: 3 to 2 VMs
[+] SUCCESS: CpuLow  -> Native Heat signal 'scaledown_policy' triggered (HTTP 200) <-- Load removed: 2 to 1 VM (Baseline limit)
```

---

## 6. Troubleshooting Guide

### 1. Server Group Exporter & Prometheus Timeout (BrokenPipeError)
* **Symptom:** `BrokenPipeError: [Errno 32] Broken pipe` in exporter journal and Prometheus marks target as `DOWN`.
* **Root Cause:** Synchronous Nova API calls during Prometheus scrape requests. Slow API responses exceeded Prometheus's 10s scrape timeout.
* **Solution:** Decoupled Nova polling into an asynchronous background thread with a 60s TTL cache. Prometheus scrape requests are served from RAM in `< 1ms`.

### 2. Heat CloudFormation Port 8000 Permission Denied (HTTP 403 AccessDenied)
* **Symptom:** `[!] signal forwarding error: HTTP Error 403: AccessDenied (User is not authorized)`.
* **Root Cause:** Heat's legacy AWS/CFN port 8000 endpoint failed HMAC token validation with Keystone v3 stack domain users.
* **Solution:** Bypassed port 8000 CFN endpoints entirely; the adapter uses `openstacksdk` to directly signal Heat's native REST API (`port 8004: /stacks/{name}/{id}/resources/{policy}/signal`) with valid Keystone admin credentials.

### 3. Systemd `EnvironmentFile` & Windows UTF-8 BOM / CRLF Corruption
* **Symptom:** Services report `# Error connecting to OpenStack: Auth plugin requires parameters: auth_url`.
* **Root Cause:** 3-byte UTF-8 BOM (`\xef\xbb\xbf`) added by Windows editors causes systemd to read `\ufeffOS_AUTH_URL`, which is silently dropped as an invalid key.
* **Solution:** Sanitization via `tr -d '\r'` and `sed` integrated directly into `tasks/prerequisites.yml`.

### 4. Systemd URL `%` Specifier Conflicts
* **Symptom:** `Failed to resolve specifiers in HEAT_SCALEUP_URL=... ignoring: Invalid slot`.
* **Root Cause:** Systemd interprets `%3A` or `%2F` inside unit files as system specifiers.
* **Solution:** Parameters sourced via `EnvironmentFile=/opt/openstack-autoscaling/openrc` where systemd disables specifier parsing.

### 5. Prometheus Alert Rules & Ansible Jinja2 Syntax Conflicts
* **Symptom:** `AnsibleError: template error while templating string: unexpected char '$'`.
* **Root Cause:** Conflict between Ansible Jinja2 interpolation and Prometheus alert labels (`{{ $labels.server_group }}`).
* **Solution:** Wrapped in `{% raw %}...{% endraw %}` blocks within `templates/alert_rules.yml.j2`.

---

## License
Licensed under [Apache-2.0](LICENSE).