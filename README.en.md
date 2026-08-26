# OpenStack Prometheus Autoscaling Layer

[Türkçe](README.md) | [English](README.en.md)

---

An enterprise integration layer providing CPU-based autoscaling for OpenStack environments using a lightweight **Prometheus + Alertmanager + libvirt-exporter** architecture instead of legacy Ceilometer, Gnocchi, and Aodh services.

This project can be deployed either **manually via a standalone bash script (`setup.sh`)** or **fully automated as an official OpenStack-Ansible role (`autoscaling.yml`) with dynamic Jinja2 templating**.

---

## 🏛️ Architecture & Operational Workflow

```text
┌─────────────────┐       ┌────────────────────────┐
│ libvirt-exporter│ :9177 │ server-group-exporter  │ :9102 (60s TTL Cache)
│ (Raw CPU metrics│       │ (Nova Metadata/StackID)│
└────────┬────────┘       └───────────┬────────────┘
         │                            │
         └──────────────┬─────────────┘
                        ▼
             ┌──────────────────────┐
             │      Prometheus      │ :9090 (PromQL Vector Matching)
             │  (Avg CPU > 70%)     │
             └───────────┬──────────┘
                        ▼
             ┌──────────────────────┐
             │     Alertmanager     │ :9093 (Webhook Routing)
             └───────────┬──────────┘
                        ▼
             ┌──────────────────────┐
             │ heat_signal_adapter  │ :9200 (Keystone Token Bridge)
             └───────────┬──────────┘
                        ▼
             ┌──────────────────────┐
             │     Heat Engine      │ :8004 (Scale-Out / Scale-In)
             └───────────┬──────────┘
```

### Components:
1. **libvirt-exporter (:9177):** Collects raw CPU time metrics directly from KVM/libvirt for each running VM.
2. **server-group-exporter (:9102):** Connects to Nova API to map instance domain names to Heat Stack IDs (`metering.server_group`). Features a 60-second TTL cache to prevent API overhead.
3. **Prometheus (:9090):** Combines both data streams using PromQL vector matching on the `domain` label and evaluates cluster-wide average CPU utilization.
4. **Alertmanager (:9093):** Evaluates firing conditions and dispatches webhook payloads to the local adapter.
5. **heat_signal_adapter (:9200):** Receives the webhook alert, obtains a fresh Keystone auth token, and dispatches an authenticated HTTP POST signal to Heat's scaling policy URL.

---

## 📂 Project & Ansible Role Directory Structure

The project is structured both as a standalone application and as an official **Ansible Role**:

```text
openstack-prometheus-autoscaling/
├── autoscaling.yml                    # Turnkey Ansible Playbook for instant deployment
├── setup.sh                           # Standalone Bash installation script
├── requirements.txt                   # Python dependencies (openstacksdk, prometheus-client)
│
├── defaults/                          # [Ansible Role] User-configurable parameters
│   └── main.yml                       # (CPU thresholds, ports, evaluation intervals)
│
├── tasks/                             # [Ansible Role] Automation tasks
│   ├── main.yml                       # Master task orchestrator
│   ├── prerequisites.yml              # Directories, system tools, and isolated Python venv
│   ├── prometheus.yml                 # Prometheus & Alertmanager binaries & configuration
│   └── adapter.yml                    # Adapter, exporter, and systemd service deployment
│
├── templates/                         # [Ansible Role] Dynamic Jinja2 (.j2) templates
│   ├── openrc.j2                      # Dynamic Keystone credentials template
│   ├── alert_rules.yml.j2             # Compiled PromQL alert rules based on thresholds
│   ├── alertmanager.yml.j2            # Dynamic webhook routing template
│   ├── prometheus.yml.j2              # Dynamic hypervisor scrape targets template
│   ├── heat-signal-adapter.service.j2 # Systemd service with auto-discovered Webhook URLs
│   └── server-group-exporter.service.j2
│
├── handlers/                          # [Ansible Role] Service restart handlers
│   └── main.yml
│
├── meta/                              # [Ansible Role] Galaxy compatibility metadata
│   └── main.yml
│
├── exporters/                         # Pure Python Source Code
│   └── server_group_exporter.py       # Nova Metadata -> Prometheus exporter (:9102)
│
├── adapter/                           # Pure Python Source Code
│   └── heat_signal_adapter.py         # Alertmanager -> Heat API token bridge (:9200)
│
├── configs/                           # Reference / Static Configuration Files (Manual Setup)
│   ├── prometheus.yml
│   ├── alert_rules.yml
│   └── alertmanager.yml
│
└── systemd/                           # Reference Systemd Unit Files (Manual Setup)
    ├── server-group-exporter.service
    └── heat-signal-adapter.service
```

---

## ⚙️ Dynamic Variables & Jinja2 Templating Matrix

When deploying via the Ansible Role, zero manual IP or password editing is required. Jinja2 dynamically resolves all infrastructure endpoints:

| Parameter / Field | Manual Installation Mode | Ansible Role (Dynamic Jinja2) | Description |
| :--- | :--- | :--- | :--- |
| **`OS_AUTH_URL`** | Hardcoded in `openrc` | `https://{{ external_lb_vip_address }}:5000/v3` | Automatically bound to Keystone endpoint |
| **`OS_PASSWORD`** | Manually entered | `{{ keystone_auth_admin_password }}` | Safely injected from credentials vault |
| **`HEAT_SCALEUP_URL`** | Copied manually from terminal | `heat_scaleup_url` (Auto-discovered via Heat API) | Discovered dynamically from stack outputs |
| **`CPU_HIGH_LIMIT`** | Static threshold (70%) | `{{ autoscaling_cpu_high_threshold }}` | Controlled centrally in `defaults/main.yml` |
| **`Prometheus Targets`**| Static localhost | `{% for host in groups['compute_hosts'] %}` | Dynamically scrapes all compute hypervisors |

---

## 🚀 Deployment Methods

### Method 1: Automated Deployment via Ansible Role (Recommended / Enterprise)

Deploy to all target hosts with a single command from your Deployer or Ansible controller:

```bash
# 1. Clone the repository
git clone https://github.com/ogi-glk/openstack-prometheus-autoscaling.git

# 2. (Optional) Customize threshold parameters in defaults/main.yml:
# autoscaling_cpu_high_threshold: 75
# autoscaling_cpu_low_threshold: 15

# 3. Execute the playbook:
openstack-ansible autoscaling.yml
# Or with standard Ansible:
ansible-playbook -i your_inventory autoscaling.yml
```
> **What it does:** Provisions required directories, builds an isolated Python `venv`, queries Heat API for webhook signals, deploys systemd units, and starts Prometheus services automatically. Zero manual intervention!

---

### Method 2: Script-based Installation (`setup.sh`)

To deploy standalone on a target host without Ansible:

1. Obtain and export your Heat scaling signal URLs:
   ```bash
   export HEAT_SCALEUP_URL=$(openstack stack output show <STACK_NAME> scaleup_url -f value -c output_value)
   export HEAT_SCALEDOWN_URL=$(openstack stack output show <STACK_NAME> scaledown_url -f value -c output_value)
   ```
2. Run the automated setup script:
   ```bash
   cd openstack-prometheus-autoscaling
   sudo bash setup.sh
   ```

---

### Method 3: Step-by-Step Manual Setup (Debugging / Development)

If you wish to test components interactively in the foreground:

#### A) Create Directories & Copy Files:
```bash
sudo mkdir -p /opt/openstack-bridge/exporters /opt/openstack-bridge/adapter /etc/prometheus /etc/alertmanager

sudo cp -r exporters/* /opt/openstack-bridge/exporters/
sudo cp -r adapter/* /opt/openstack-bridge/adapter/
sudo cp configs/prometheus.yml /etc/prometheus/prometheus.yml
sudo cp configs/alert_rules.yml /etc/prometheus/alert_rules.yml
sudo cp configs/alertmanager.yml /etc/alertmanager/alertmanager.yml
```

#### B) Prepare Isolated Virtual Environment:
```bash
sudo python3 -m venv /opt/openstack-bridge/venv
sudo /opt/openstack-bridge/venv/bin/pip install --upgrade pip
sudo /opt/openstack-bridge/venv/bin/pip install -r requirements.txt
```

#### C) Run Interactively in Foreground:
```bash
# Terminal 1: Launch Exporter
source /root/openrc
/opt/openstack-bridge/venv/bin/python3 /opt/openstack-bridge/exporters/server_group_exporter.py

# Terminal 2: Launch Webhook Adapter
source /root/openrc
export HEAT_SCALEUP_URL="https://<HEAT_IP>:8004/v1/.../scaleup_policy/signal"
/opt/openstack-bridge/venv/bin/python3 /opt/openstack-bridge/adapter/heat_signal_adapter.py
```

---

## 🧪 Verification & Autoscaling Stress Testing

### 1. Service & Metric Validation
```bash
systemctl status server-group-exporter
systemctl status heat-signal-adapter

# Query exporter metrics (Instance to Stack ID mappings should be listed):
curl -s http://localhost:9102/metrics
```

### 2. Prometheus PromQL Vector Matching Query
In Prometheus Web UI (`:9090`), execute:
```promql
avg by (server_group) (
  rate(libvirt_domain_info_cpu_time_seconds_total[5m]) 
  * on(domain) group_left(server_group) 
  openstack_instance_server_group
)
```

### 3. Live CPU Stress (Autoscaling) Test
1. SSH into an instance belonging to the Heat AutoScalingGroup and generate synthetic CPU load:
   ```bash
   cat /dev/zero > /dev/null &
   ```
2. **Observe Scale-Out:** Once CPU utilization exceeds `70%`, Alertmanager triggers `CpuHigh`, notifies the adapter on port `:9200`, and Heat **spawns +1 new virtual machine.**
3. Terminate the CPU stress load:
   ```bash
   killall cat
   ```
4. **Observe Scale-In:** Once CPU drops below `20%`, `CpuLow` fires and Heat automatically deletes the excess instance.

---

## 📄 License
This project is licensed under the [Apache-2.0](LICENSE) License.
