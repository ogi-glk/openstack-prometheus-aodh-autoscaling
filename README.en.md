# OpenStack Prometheus Autoscaling Layer

[Türkçe](README.md) | [English](README.en.md)

---

An integration layer providing CPU-based autoscaling for OpenStack environments using a lightweight **Prometheus + Alertmanager + libvirt-exporter** architecture instead of legacy Ceilometer, Gnocchi, and Aodh services.

The project can be deployed in two ways:
1. **Via Ansible Role:** Automated deployment across inventory hosts using `openstack-ansible` or standard `ansible-playbook`.
2. **Standalone / Manual:** Direct installation on the target host using `setup.sh` or step-by-step commands.

---

## Architecture and Operating Logic

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
1. **libvirt-exporter (:9177):** Collects raw CPU time metrics directly from KVM/libvirt for running instances.
2. **server-group-exporter (:9102):** Connects to Nova API to map instance domain names to Heat Stack IDs (`metering.server_group`). Features a 60-second TTL cache to minimize Nova API load.
3. **Prometheus (:9090):** Combines both data streams using PromQL vector matching on the `domain` label and calculates average CPU utilization.
4. **Alertmanager (:9093):** Evaluates firing conditions and routes webhook payloads to the local adapter.
5. **heat_signal_adapter (:9200):** Receives the webhook alert, obtains a valid Keystone auth token, and dispatches an authenticated HTTP POST signal to Heat's scaling policy URL.

---

## Directory Structure

```text
openstack-prometheus-autoscaling/
├── autoscaling.yml                    # Deployment Ansible Playbook
├── setup.sh                           # Standalone installation script
├── requirements.txt                   # Python package dependencies
│
├── defaults/                          # Role variables
│   └── main.yml                       # Thresholds, ports, and parameters
│
├── tasks/                             # Ansible task files
│   ├── main.yml                       # Task execution entry point
│   ├── prerequisites.yml              # Directories, system packages, and Python venv
│   ├── prometheus.yml                 # Prometheus and Alertmanager setup
│   └── adapter.yml                    # Adapter, exporter, and systemd service deployment
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
│   └── heat_signal_adapter.py         # Alertmanager -> Heat API token adapter (:9200)
│
├── configs/                           # Reference configuration files (Manual installation)
│   ├── prometheus.yml
│   ├── alert_rules.yml
│   └── alertmanager.yml
│
└── systemd/                           # Reference systemd service files (Manual installation)
    ├── server-group-exporter.service
    └── heat-signal-adapter.service
```

---

## Variables and Parameters

When using the Ansible Role, variables are defined in `defaults/main.yml`:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `autoscaling_cpu_high_threshold` | `70` | Scale-Out trigger threshold (% CPU) |
| `autoscaling_cpu_low_threshold` | `20` | Scale-In trigger threshold (% CPU) |
| `autoscaling_evaluation_period` | `60s` | Evaluation wait time before firing |
| `heat_signal_adapter_port` | `9200` | Heat adapter listening port |
| `server_group_exporter_port` | `9102` | Metadata exporter listening port |
| `heat_stack_name` | `autoscaling-stack` | Heat Stack name for automatic signal URL discovery |

---

## Installation Methods

### 1. Deployment via Ansible Role

From your Ansible control machine:

```bash
# 1. Clone the repository
git clone https://github.com/ogi-glk/openstack-prometheus-autoscaling.git

# 2. Adjust parameters in defaults/main.yml as needed

# 3. Execute the playbook:
openstack-ansible autoscaling.yml
# Or:
ansible-playbook -i inventory_file autoscaling.yml
```

---

### 2. Standalone Script Installation (`setup.sh`)

To deploy directly on the target host without Ansible:

1. Define Heat scaling signal URLs as environment variables:
   ```bash
   export HEAT_SCALEUP_URL=$(openstack stack output show <STACK_NAME> scaleup_url -f value -c output_value)
   export HEAT_SCALEDOWN_URL=$(openstack stack output show <STACK_NAME> scaledown_url -f value -c output_value)
   ```
2. Run the installation script:
   ```bash
   cd openstack-prometheus-autoscaling
   sudo bash setup.sh
   ```

---

### 3. Step-by-Step Manual Setup (Debugging / Foreground Testing)

To install manually or test components in the foreground:

#### A) Create Directories and Copy Files:
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

#### C) Run in Foreground:
```bash
# Terminal 1: Launch Exporter
source /root/openrc
/opt/openstack-bridge/venv/bin/python3 /opt/openstack-bridge/exporters/server_group_exporter.py

# Terminal 2: Launch Adapter
source /root/openrc
export HEAT_SCALEUP_URL="https://<HEAT_IP>:8004/v1/.../scaleup_policy/signal"
/opt/openstack-bridge/venv/bin/python3 /opt/openstack-bridge/adapter/heat_signal_adapter.py
```

---

## Verification and Testing

### 1. Service Status Checks
```bash
systemctl status server-group-exporter
systemctl status heat-signal-adapter

# Test exporter metric output:
curl -s http://localhost:9102/metrics
```

### 2. Prometheus PromQL Query
In Prometheus UI (`:9090`), execute:
```promql
avg by (server_group) (
  rate(libvirt_domain_info_cpu_time_seconds_total[5m]) 
  * on(domain) group_left(server_group) 
  openstack_instance_server_group
)
```

### 3. CPU Load Testing
1. SSH into an instance within the stack and generate synthetic load:
   ```bash
   cat /dev/zero > /dev/null &
   ```
2. **Scale-Out:** When average CPU utilization exceeds `70%`, Alertmanager triggers the webhook adapter, which signals Heat to spawn a new instance.
3. Stop the load:
   ```bash
   killall cat
   ```
4. **Scale-In:** When CPU drops below `20%`, `CpuLow` fires and Heat terminates the excess instance.

---

## License
This project is licensed under the [Apache-2.0](LICENSE) License.
