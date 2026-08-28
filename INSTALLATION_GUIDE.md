# OpenStack Prometheus + Aodh Autoscaling — Installation Guide

> **Version:** 1.0  
> **Last Updated:** August 2026  
> **Target Audience:** Cloud infrastructure engineers deploying autoscaling on OpenStack environments

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Prerequisites](#3-prerequisites)
4. [Deployment](#4-deployment)
5. [Configuration Reference](#5-configuration-reference)
6. [Creating an Autoscaling Heat Stack](#6-creating-an-autoscaling-heat-stack)
7. [Verification and Testing](#7-verification-and-testing)
8. [Troubleshooting](#8-troubleshooting)
9. [Appendix](#9-appendix)

---

## 1. Overview

This project provides **CPU-based horizontal auto-scaling** for OpenStack environments using native **Prometheus** metrics and **OpenStack Aodh** (`OS::Aodh::PrometheusAlarm`). It eliminates dependencies on legacy Ceilometer and Gnocchi telemetry services.

### How It Works (In Brief)

1. **Prometheus** collects real-time CPU metrics from the KVM hypervisor via `libvirt-exporter`.
2. A custom **Server Group Exporter** maps each virtual machine to its Heat Stack ID via Nova metadata.
3. **Aodh** periodically evaluates PromQL queries defined in the Heat template. When CPU thresholds are crossed, Aodh sends a pre-signed webhook signal to **Heat**, which automatically adds or removes instances.

### Key Benefits

- No Ceilometer or Gnocchi required
- Alarm definitions live inside the Heat template (fully self-service for tenants)
- Each Heat Stack's metrics are automatically isolated (multi-stack safe)
- Built-in Autohealing: if an instance crashes, Heat's `min_size` constraint automatically replaces it

---

## 2. Architecture

```text
+-----------------------+         +-------------------------------+
| libvirt-exporter      | :9177   | server-group-exporter         | :9102
| KVM Domain CPU Time   |         | Nova Metadata -> Stack ID     |
| (instance-0000000X)   |         | (60s TTL In-Memory Cache)     |
+-----------+-----------+         +---------------+---------------+
            |                                     |
            +------------------+------------------+
                               |
                               v
                   +----------------------+
                   |   Prometheus TSDB    | :9090
                   | (Scrape & Storage)   |
                   +-----------+----------+
                               ^
                               | PromQL Query (Every 60s)
                   +-----------+----------+
                   |    OpenStack Aodh    |
                   | * aodh-evaluator     |
                   | * aodh-notifier      |
                   +-----------+----------+
                               | HTTP POST (HMAC-SHA256 Signed)
                               v
                   +----------------------+
                   |   Heat API (CFN)     | :8000
                   |   Heat Engine        |
                   | AutoScalingGroup     |
                   | (Scale-Out/Scale-In) |
                   +----------------------+
```

### Component Summary

| Component | Default Port | Purpose |
|:--|:--|:--|
| `libvirt-exporter` | 9177 | Exposes KVM vCPU execution time per domain |
| `server-group-exporter` | 9102 | Maps Nova instance names to Heat Stack IDs via metadata |
| Prometheus | 9090 | Time-series database and PromQL query engine |
| Aodh Evaluator | — | Periodically queries Prometheus and evaluates alarm thresholds |
| Aodh Notifier | — | Sends HMAC-signed webhook to Heat when alarms fire |
| Heat CFN API | 8000 | Receives alarm signals and adjusts AutoScalingGroup capacity |

---

## 3. Prerequisites

### 3.1 Host Requirements

> [!IMPORTANT]
> The deployment target must be an OpenStack **compute host** (the machine running the KVM hypervisor). In an All-in-One (AIO) setup, this is the same machine as the controller.

| Requirement | Details |
|:--|:--|
| **Operating System** | Ubuntu 22.04 LTS (Jammy) or Ubuntu 24.04 LTS (Noble) |
| **Minimum RAM** | 8 GB (16 GB recommended for production) |
| **Disk Space** | At least 5 GB free for Prometheus TSDB storage |
| **Python** | Python 3.10+ (included with Ubuntu 22.04/24.04) |
| **Network** | Outbound internet access for downloading packages and Prometheus binaries |
| **Privileges** | Root or sudo access on the target host |

### 3.2 Required OpenStack Services

The following OpenStack services must already be installed and operational before deploying this autoscaling layer:

| Service | Purpose | How to Verify |
|:--|:--|:--|
| **Keystone** (Identity) | Authentication and service catalog | `openstack token issue` |
| **Nova** (Compute) | Virtual machine lifecycle management | `openstack server list` |
| **Heat** (Orchestration) | Stack and AutoScalingGroup management | `openstack stack list` |
| **Heat CFN API** (Port 8000) | Receives HMAC-signed `alarm_url` signals from Aodh | `curl http://<VIP>:8000/` |
| **Neutron** (Networking) | Virtual network for instances | `openstack network list` |
| **Glance** (Image) | OS images for instances | `openstack image list` |

> [!NOTE]
> **Aodh is NOT required to be pre-installed.** The deployment will automatically install and configure Aodh if it is not already present on the target host.

### 3.3 Required OpenStack Resources

Before creating your first autoscaling stack, ensure the following resources exist:

```bash
# 1. Verify a bootable image exists
openstack image list
# Example output: cirros, ubuntu-22.04, etc.

# 2. Verify a flavor exists
openstack flavor list
# Example output: m1.tiny, m1.small, etc.

# 3. Verify a network exists
openstack network list
# Example output: test-net, provider-net, etc.
```

### 3.4 Python Dependencies (`requirements.txt`)

The project requires the following Python packages (installed automatically during deployment inside an isolated virtual environment):

```text
openstacksdk>=1.0.0
requests>=2.28.0
```

These are installed into `/opt/openstack-autoscaling/venv/` and do **not** affect system-level Python packages.

### 3.5 LXC Container Requirements (OpenStack-Ansible Environments Only)

If your OpenStack was deployed using **OpenStack-Ansible (OSA)**, the following LXC containers must be running on the target host:

| Container | Purpose |
|:--|:--|
| `*-heat-api-*` | Heat API and CFN services |
| `*-galera-*` | MariaDB database for Aodh |
| `*-rabbit-mq-*` | RabbitMQ message broker |
| `*-keystone-*` | Keystone identity service |
| `*-utility-*` | OpenStack CLI tools |

Verify containers are running:
```bash
lxc-ls -f | grep -E "heat|galera|rabbit|keystone|utility"
```

---

## 4. Deployment

Two deployment methods are available. Choose the one that matches your environment.

### Method 1: Ansible Role Deployment (Recommended)

This method is recommended for **OpenStack-Ansible (OSA) environments** or any environment managed by Ansible.

#### Step 1: Clone the Repository

```bash
git clone https://github.com/ogi-glk/openstack-prometheus-aodh-autoscaling.git
cd openstack-prometheus-aodh-autoscaling
```

#### Step 2: Review and Customize Variables (Optional)

Default variables are defined in [defaults/main.yml](defaults/main.yml). You can override any variable in your OpenStack-Ansible `user_variables.yml` or by passing extra variables on the command line.

Key variables you may want to customize:

| Variable | Default | Description |
|:--|:--|:--|
| `prometheus_version` | `2.51.0` | Prometheus binary version to install |
| `prometheus_port` | `9090` | Prometheus listening port |
| `server_group_exporter_port` | `9102` | Server Group Exporter listening port |
| `server_group_exporter_cache_ttl` | `60` | Nova API polling interval (seconds) |
| `openstack_auth_url` | Auto-detected | Keystone v3 authentication URL |
| `openstack_username` | `admin` | Keystone admin username |
| `openstack_password` | Auto-detected | Keystone admin password |

#### Step 3: Run the Playbook

**From an OpenStack-Ansible deployer:**
```bash
cd /opt/openstack-ansible/playbooks
openstack-ansible /path/to/openstack-prometheus-aodh-autoscaling/aodh-autoscaling.yml
```

**From a standard Ansible control node:**
```bash
ansible-playbook -i inventory/hosts aodh-autoscaling.yml
```

#### What the Playbook Does (Execution Sequence)

1. **Prerequisites** (`tasks/prerequisites.yml`):
   - Installs system packages: `python3-pip`, `python3-venv`, `build-essential`, `prometheus-node-exporter`, `prometheus-libvirt-exporter`
   - Creates working directories under `/opt/openstack-autoscaling/`
   - Creates an isolated Python virtual environment with `openstacksdk` and `requests`
   - Generates Keystone credential file (`openrc`)

2. **Prometheus** (`tasks/prometheus.yml`):
   - Downloads and installs Prometheus v2.51.0 binary
   - Deploys Prometheus configuration with scrape targets (`:9177` and `:9102`)
   - Creates and enables the `prometheus.service` systemd unit
   - Starts `prometheus` and `prometheus-libvirt-exporter` services

3. **Server Group Exporter** (`tasks/exporter.yml`):
   - Deploys the `server_group_exporter.py` script
   - Creates and enables the `server-group-exporter.service` systemd unit
   - Starts the exporter service on port 9102

4. **Aodh Integration** (`tasks/aodh.yml`):
   - Detects whether Aodh is already installed; if not, installs Aodh packages (`aodh-api`, `aodh-evaluator`, `aodh-notifier`, `aodh-listener`)
   - Deploys `aodh.conf` with database, RabbitMQ, Keystone, and Prometheus settings
   - Registers `aodh` service and endpoints in Keystone catalog
   - Configures `/etc/openstack/prometheus.yaml` for `observabilityclient`
   - Starts `aodh-evaluator`, `aodh-notifier`, `aodh-listener` services
   - Applies Ubuntu 24.04 `osprofiler` compatibility patch
   - Configures Heat CFN `[ec2authtoken]` credentials for `alarm_url` signature validation

---

### Method 2: Standalone Shell Script

This method is for **direct deployment on the target host** without Ansible.

#### Step 1: Clone and Run

```bash
git clone https://github.com/ogi-glk/openstack-prometheus-aodh-autoscaling.git
cd openstack-prometheus-aodh-autoscaling
chmod +x setup.sh
sudo bash setup.sh
```

#### What the Script Does

The `setup.sh` script performs the same operations as the Ansible playbook:
1. Installs system packages and `libvirt-exporter`
2. Creates Python virtual environment and installs dependencies
3. Deploys Prometheus binary and configuration
4. Installs and starts `server-group-exporter` service
5. Configures Aodh to query Prometheus
6. Applies `osprofiler` compatibility patch for Ubuntu 24.04
7. Configures Heat CFN `[ec2authtoken]` credentials

> [!WARNING]
> The `setup.sh` script contains hardcoded default values for some credentials. Review and edit the script before running in production to ensure passwords and endpoints match your environment.

---

## 5. Configuration Reference

### Do I Need to Edit Any Configuration Files?

All configuration files listed below are **automatically generated** by the deployment. Whether you need to manually adjust any variables depends on your environment:

#### OpenStack-Ansible (OSA) Environments — No Manual Changes Required ✅

The playbook automatically loads your existing credentials from:
- `/etc/openstack_deploy/user_secrets.yml` — all service passwords
- `/etc/openstack_deploy/user_variables.yml` — VIP addresses and environment settings

All configuration files (Prometheus, Aodh, Heat CFN, observabilityclient) are populated from these sources. **Simply run the playbook and everything is configured automatically.**

#### Standalone (Non-OSA) Environments — 6 Variables Must Be Set ⚠️

If your OpenStack was **not** deployed using OpenStack-Ansible, the playbook does not have access to `user_secrets.yml`. You must provide your environment-specific values by editing `defaults/main.yml` before running the playbook, or by passing them via `--extra-vars` on the command line.

**Required variables to set:**

| Variable | Description | Default (Must Change) |
|:--|:--|:--|
| `openstack_password` | Keystone admin password | `''` (empty) |
| `aodh_service_password` | Aodh Keystone service user password | `AodhSecretPassword123!` |
| `aodh_db_password` | Aodh MariaDB database password | `AodhDbSecretPassword123!` |
| `aodh_rabbit_password` | RabbitMQ message broker password | `RabbitSecretPassword123!` |
| `heat_service_password` | Heat service password (for ec2authtoken) | `CHANGE_ME` |
| `internal_lb_vip_address` | Controller or HAProxy VIP address | `172.29.236.101` |

**Option A — Edit `defaults/main.yml` directly:**
```bash
cd openstack-prometheus-aodh-autoscaling
vi defaults/main.yml
# Update the 6 variables above to match your environment
```

**Option B — Pass variables on the command line:**
```bash
ansible-playbook -i inventory/hosts aodh-autoscaling.yml \
  --extra-vars "openstack_password=MyAdminPass123 \
                aodh_service_password=MyAodhPass123 \
                aodh_db_password=MyAodhDbPass123 \
                aodh_rabbit_password=MyRabbitPass123 \
                heat_service_password=MyHeatPass123 \
                internal_lb_vip_address=192.168.1.100"
```

> [!TIP]
> After deployment, you generally do **not** need to edit the generated configuration files. If you need to tune specific parameters (e.g., Prometheus scrape interval, exporter cache TTL), modify the variables in `defaults/main.yml` and re-run the playbook — it will regenerate the configuration files automatically.

---

### 5.1 Prometheus Configuration (`/etc/prometheus/prometheus.yml`)

After deployment, Prometheus is configured to scrape two targets:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'libvirt'
    static_configs:
      - targets: ["localhost:9177"]    # KVM CPU metrics

  - job_name: 'openstack-server-group'
    static_configs:
      - targets: ["localhost:9102"]    # Nova metadata -> Stack ID mapping
```

**Customization:** If running exporters on different hosts, replace `localhost` with the appropriate IP address.

### 5.2 Aodh Configuration (`/etc/aodh/aodh.conf`)

Key sections automatically configured:

```ini
[DEFAULT]
transport_url = rabbit://<user>:<password>@<host>:5671/?ssl=1

[database]
connection = mysql+pymysql://aodh:<password>@<host>:3306/aodh?charset=utf8

[keystone_authtoken]
auth_type = password
auth_url = http://<VIP>:5000/v3
# ... (service credentials)

[prometheus]
host = 127.0.0.1
port = 9090
url = http://127.0.0.1:9090
```

### 5.3 Observability Client Configuration (`/etc/openstack/prometheus.yaml`)

Required by Aodh's `observabilityclient` to locate Prometheus:

```yaml
host: 127.0.0.1
port: 9090
```

### 5.4 Heat CFN `ec2authtoken` Configuration (`/etc/heat/heat.conf`)

> [!IMPORTANT]
> This is critical for `alarm_url` to work. Without these credentials, Heat cannot validate the HMAC-SHA256 signed webhook signals from Aodh, and autoscaling actions will silently fail.

```ini
[ec2authtoken]
auth_uri = http://<VIP>:5000
auth_url = http://<VIP>:5000/v3
auth_type = password
username = heat
password = <HEAT_SERVICE_PASSWORD>
project_name = service
user_domain_id = default
project_domain_id = default
```

The deployment handles this automatically for OpenStack-Ansible environments by detecting the Heat API container and injecting the credentials.

### 5.5 Systemd Services

After deployment, the following services should be running:

| Service | Unit Name | Description |
|:--|:--|:--|
| Prometheus | `prometheus.service` | Time-series database |
| Libvirt Exporter | `prometheus-libvirt-exporter.service` | KVM CPU metrics |
| Server Group Exporter | `server-group-exporter.service` | Nova metadata bridge |
| Aodh Evaluator | `aodh-evaluator.service` | PromQL alarm evaluation |
| Aodh Notifier | `aodh-notifier.service` | Webhook signal dispatch |
| Aodh Listener | `aodh-listener.service` | Event listener |

---

## 6. Creating an Autoscaling Heat Stack

### 6.1 Using the Provided Template

A ready-to-deploy Heat template is included at `templates_heat/heat_template_aodh.yaml`.

**Create the stack:**
```bash
openstack stack create -t templates_heat/heat_template_aodh.yaml \
  --parameter image=ubuntu-22.04 \
  --parameter flavor=m1.small \
  --parameter network=my-network \
  --parameter cpu_high_threshold=70 \
  --parameter cpu_low_threshold=20 \
  my-autoscaling-stack
```

**Parameters:**

| Parameter | Type | Default | Description |
|:--|:--|:--|:--|
| `image` | string | `cirros` | Image name or ID for instances |
| `flavor` | string | `m1.tiny` | Flavor (CPU/RAM size) for instances |
| `network` | string | `test-net` | Network for instances |
| `cpu_high_threshold` | number | `70` | CPU % above which a new instance is added (Scale-Out) |
| `cpu_low_threshold` | number | `20` | CPU % below which an instance is removed (Scale-In) |

### 6.2 Adapting Your Own Heat Template

If you already have a Heat template and want to add autoscaling capabilities, you need to add **three blocks** to your existing template:

#### Block 1: Instance Metadata (REQUIRED)

Add the `metadata` section to your `OS::Nova::Server` resource inside an `OS::Heat::AutoScalingGroup`:

```yaml
resources:
  asg:
    type: OS::Heat::AutoScalingGroup
    properties:
      min_size: 1
      max_size: 5
      desired_capacity: 1
      cooldown: 60
      resource:
        type: OS::Nova::Server
        properties:
          name: my-vm
          image: { get_param: image }
          flavor: { get_param: flavor }
          networks:
            - network: { get_param: network }
          # ================================================
          # THIS METADATA BLOCK IS MANDATORY
          # Without it, Prometheus cannot associate the VM
          # with this specific Heat Stack.
          # ================================================
          metadata:
            metering.server_group: { get_param: "OS::stack_id" }
```

#### Block 2: Scaling Policies (REQUIRED)

```yaml
  scaleup_policy:
    type: OS::Heat::ScalingPolicy
    properties:
      auto_scaling_group_id: { get_resource: asg }
      adjustment_type: change_in_capacity
      scaling_adjustment: 1     # Add 1 instance
      cooldown: 60

  scaledown_policy:
    type: OS::Heat::ScalingPolicy
    properties:
      auto_scaling_group_id: { get_resource: asg }
      adjustment_type: change_in_capacity
      scaling_adjustment: -1    # Remove 1 instance
      cooldown: 60
```

#### Block 3: Prometheus Alarms (REQUIRED)

```yaml
  cpu_alarm_high:
    type: OS::Aodh::PrometheusAlarm
    properties:
      repeat_actions: true
      description: "Scale-Out when average CPU exceeds threshold"
      comparison_operator: gt
      threshold: 70
      query:
        str_replace:
          template: >-
            avg(rate(libvirt_domain_info_cpu_time_seconds_total[1m]) * 100
            * on(domain) group_left(server_group)
            openstack_instance_server_group{server_group='MY_STACK_ID'})
          params:
            MY_STACK_ID: { get_param: "OS::stack_id" }
      alarm_actions:
        - { get_attr: [scaleup_policy, alarm_url] }

  cpu_alarm_low:
    type: OS::Aodh::PrometheusAlarm
    properties:
      repeat_actions: true
      description: "Scale-In when average CPU drops below threshold"
      comparison_operator: lt
      threshold: 20
      query:
        str_replace:
          template: >-
            avg(rate(libvirt_domain_info_cpu_time_seconds_total[1m]) * 100
            * on(domain) group_left(server_group)
            openstack_instance_server_group{server_group='MY_STACK_ID'})
          params:
            MY_STACK_ID: { get_param: "OS::stack_id" }
      alarm_actions:
        - { get_attr: [scaledown_policy, alarm_url] }
```

> [!IMPORTANT]
> **`repeat_actions: true`** is mandatory. Without it, the alarm fires only once and autoscaling will not continuously adjust capacity based on ongoing load changes.

> [!TIP]
> **`alarm_url`** (not `signal_url`) must be used. It is a pre-signed HMAC-SHA256 URL that does not require a Keystone token, making it reliable for automated webhook callbacks.

### 6.3 Understanding the PromQL Query

The PromQL query used in the alarm is:

```promql
avg(
  rate(libvirt_domain_info_cpu_time_seconds_total[1m]) * 100
  * on(domain) group_left(server_group)
  openstack_instance_server_group{server_group='<STACK_ID>'}
)
```

| Component | Purpose |
|:--|:--|
| `rate(libvirt_domain_info_cpu_time_seconds_total[1m])` | Calculates the per-second CPU utilization rate over the last 1 minute |
| `* 100` | Converts to percentage (0-100+) |
| `* on(domain) group_left(server_group)` | Joins the CPU metric with the server group metric using the `domain` label |
| `openstack_instance_server_group{server_group='<STACK_ID>'}` | Filters to only include instances belonging to this specific Heat Stack |
| `avg(...)` | Averages across all instances in the group |

**Why `str_replace`?** Each Heat Stack has a unique UUID. The `str_replace` function injects this UUID at stack creation time, ensuring that each stack only evaluates its own instances' CPU metrics.

### 6.4 Multiple Independent Stacks

You can create multiple autoscaling stacks simultaneously. Each stack operates independently:

```bash
openstack stack create -t heat_template_aodh.yaml --parameter image=ubuntu-22.04 stack-web-app
openstack stack create -t heat_template_aodh.yaml --parameter image=ubuntu-22.04 stack-api-server
openstack stack create -t heat_template_aodh.yaml --parameter image=ubuntu-22.04 stack-worker
```

Each stack:
- Gets its own unique `OS::stack_id` injected into the PromQL query
- Evaluates only its own instances' CPU metrics
- Scales independently without affecting other stacks

---

## 7. Verification and Testing

### 7.1 Verify All Services Are Running

```bash
# Check all autoscaling services
for svc in prometheus prometheus-libvirt-exporter server-group-exporter aodh-evaluator aodh-notifier aodh-listener; do
  systemctl is-active "$svc" && echo "$svc: OK" || echo "$svc: FAILED"
done
```

Expected output:
```text
prometheus: OK
prometheus-libvirt-exporter: OK
server-group-exporter: OK
aodh-evaluator: OK
aodh-notifier: OK
aodh-listener: OK
```

### 7.2 Verify Prometheus Metrics

```bash
# Check libvirt-exporter metrics
curl -s http://localhost:9177/metrics | grep libvirt_domain_info_cpu_time

# Check server-group-exporter metrics
curl -s http://localhost:9102/metrics | grep openstack_instance_server_group

# Query Prometheus directly
curl -s "http://localhost:9090/api/v1/query?query=up" | python3 -m json.tool
```

### 7.3 Verify Aodh Alarms

After creating a stack:

```bash
# List all alarms
openstack alarm list

# Check alarm details
openstack alarm show <alarm-id>
```

You should see alarms with `type: prometheus` and `state: ok` (or `insufficient data` if no instances are running yet).

### 7.4 End-to-End Scale-Out / Scale-In Test

#### Step 1: Create a Test Stack

```bash
openstack stack create -t templates_heat/heat_template_aodh.yaml \
  --parameter cpu_high_threshold=50 \
  --parameter cpu_low_threshold=10 \
  test-autoscale
```

#### Step 2: Wait for the Stack to Be Ready

```bash
openstack stack show test-autoscale -c stack_status
# Wait until: CREATE_COMPLETE
```

#### Step 3: Generate CPU Load (Scale-Out Test)

Connect to the instance and generate CPU load:

```bash
# Find the instance IP
openstack server list --name asg-vm

# SSH into the instance
ssh user@<instance-ip>

# Generate 100% CPU load
cat /dev/zero > /dev/null &
```

#### Step 4: Observe Scale-Out

Monitor the alarm state and instance count:

```bash
# Watch alarm state change from 'ok' to 'alarm'
watch -n 10 openstack alarm list

# Watch instance count increase
watch -n 10 openstack server list
```

Within 1-2 minutes, you should see:
1. Aodh alarm state changes to `alarm`
2. Heat creates a new instance in the AutoScalingGroup
3. `openstack server list` shows the additional instance

#### Step 5: Remove Load and Observe Scale-In

```bash
# SSH back into the instance
ssh user@<instance-ip>

# Stop the CPU load
killall cat
```

Within 1-2 minutes:
1. CPU drops below the low threshold
2. `cpu_alarm_low` state changes to `alarm`
3. Heat removes the extra instance

### 7.5 Autohealing Verification

The `min_size: 1` constraint in the AutoScalingGroup provides built-in autohealing:

```bash
# Delete an instance manually
openstack server delete <instance-id>

# Heat will detect the missing instance and automatically create a replacement
watch -n 5 openstack server list
```

---

## 8. Troubleshooting

### Aodh Alarm Stays in `insufficient data`

**Cause:** Prometheus does not have metrics for the instances yet.

**Solution:**
```bash
# 1. Verify libvirt-exporter is producing metrics
curl -s http://localhost:9177/metrics | grep libvirt_domain_info_cpu_time

# 2. Verify server-group-exporter has mapped the instances
curl -s http://localhost:9102/metrics | grep openstack_instance_server_group

# 3. Test the full PromQL query in Prometheus
curl -s "http://localhost:9090/api/v1/query?query=openstack_instance_server_group" | python3 -m json.tool
```

If `server-group-exporter` returns no metrics, check the OpenStack credentials:
```bash
source /opt/openstack-autoscaling/openrc
openstack server list
```

### Alarm Fires but Heat Does Not Scale

**Cause:** Heat CFN API cannot validate the `alarm_url` HMAC signature.

**Solution:** Verify `[ec2authtoken]` in `/etc/heat/heat.conf`:
```bash
grep -A 8 '\[ec2authtoken\]' /etc/heat/heat.conf
```

Ensure it contains `auth_type = password` and valid service credentials. Restart Heat CFN after changes:
```bash
systemctl restart heat-api-cfn
```

### `osprofiler` Error on Ubuntu 24.04

**Error:** `AttributeError: 'ExceptionContextImpl' object has no attribute 'chained_exception'`

**Solution:** The deployment applies this patch automatically. If needed manually:
```bash
python3 -c "
path = '/usr/lib/python3/dist-packages/osprofiler/sqlalchemy.py'
with open(path, 'r') as f:
    content = f.read()
old = 'chained_exception = str(exception_context.chained_exception)'
new = 'chained_exception = str(getattr(exception_context, \"chained_exception\", getattr(exception_context, \"original_exception\", \"\")))'
with open(path, 'w') as f:
    f.write(content.replace(old, new))
"
```

### Server Group Exporter Shows No Metrics

**Cause:** Instances do not have `metering.server_group` metadata.

**Solution:** Ensure your Heat template includes:
```yaml
metadata:
  metering.server_group: { get_param: "OS::stack_id" }
```

Instances created without this metadata will not appear in the exporter output.

---

## 9. Appendix

### A. File and Directory Structure

```text
openstack-prometheus-aodh-autoscaling/
├── aodh-autoscaling.yml               # Ansible deployment playbook
├── setup.sh                           # Standalone Bash setup script
├── requirements.txt                   # Python dependencies
├── README.md                          # English documentation
├── README.tr.md                       # Turkish documentation
│
├── defaults/
│   └── main.yml                       # Default variables (ports, paths, credentials)
│
├── tasks/
│   ├── main.yml                       # Task execution sequence
│   ├── prerequisites.yml              # System packages, venv, directories
│   ├── prometheus.yml                 # Prometheus binary and service setup
│   ├── exporter.yml                   # Server Group Exporter setup
│   └── aodh.yml                       # Aodh installation and Prometheus integration
│
├── templates/
│   ├── openrc.j2                      # Keystone credential file template
│   ├── aodh.conf.j2                   # Aodh configuration template
│   ├── prometheus.yml.j2              # Prometheus scrape config template
│   └── server-group-exporter.service.j2  # Systemd unit template
│
├── handlers/
│   └── main.yml                       # Service restart handlers
│
├── meta/
│   └── main.yml                       # Ansible Galaxy metadata
│
├── exporters/
│   └── server_group_exporter.py       # Nova metadata -> Prometheus bridge
│
├── configs/
│   └── prometheus.yml                 # Reference Prometheus configuration
│
├── systemd/
│   └── server-group-exporter.service  # Reference systemd unit file
│
└── templates_heat/
    ├── heat_template_aodh.yaml        # Ready-to-deploy autoscaling HOT template
    └── heat_template_user_guide.yaml  # Annotated guide template for customization
```

### B. Default Ports

| Port | Service | Protocol |
|:--|:--|:--|
| 9090 | Prometheus | HTTP |
| 9177 | libvirt-exporter | HTTP |
| 9102 | server-group-exporter | HTTP |
| 8042 | Aodh API | HTTP |
| 8000 | Heat CFN API | HTTP |
| 5000 | Keystone | HTTP |

### C. Default Variable Reference

| Variable | Default Value | Description |
|:--|:--|:--|
| `autoscaling_install_dir` | `/opt/openstack-autoscaling` | Installation root directory |
| `autoscaling_venv_dir` | `/opt/openstack-autoscaling/venv` | Python virtual environment path |
| `prometheus_version` | `2.51.0` | Prometheus release version |
| `prometheus_port` | `9090` | Prometheus HTTP port |
| `libvirt_exporter_port` | `9177` | libvirt-exporter port |
| `server_group_exporter_port` | `9102` | Server Group Exporter port |
| `server_group_exporter_cache_ttl` | `60` | Nova API polling interval (seconds) |
| `aodh_port` | `8042` | Aodh API port |
| `aodh_prometheus_host` | `127.0.0.1` | Prometheus host for Aodh |
| `aodh_prometheus_port` | `9090` | Prometheus port for Aodh |

### D. Quick Reference Commands

```bash
# Service management
systemctl status prometheus server-group-exporter aodh-evaluator aodh-notifier

# View Prometheus targets
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool

# List alarms
openstack alarm list

# Create a stack
openstack stack create -t templates_heat/heat_template_aodh.yaml my-stack

# Delete a stack
openstack stack delete my-stack

# Check stack events
openstack stack event list my-stack

# Monitor real-time CPU via PromQL
curl -s "http://localhost:9090/api/v1/query?query=rate(libvirt_domain_info_cpu_time_seconds_total[1m])*100"
```
