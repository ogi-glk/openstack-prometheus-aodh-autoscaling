# OpenStack Prometheus + Aodh Autoscaling Layer

[English](README.md) | [Türkçe](README.tr.md)

---

This project provides dynamic, CPU-based horizontal auto-scaling (AutoScaling) for OpenStack environments using native **Prometheus** and **OpenStack Aodh (`OS::Aodh::PrometheusAlarm`)** integration, eliminating dependencies on legacy Ceilometer and Gnocchi telemetry services.

The design does not require external notification adapters or Alertmanager webhook bridges. Alarm definitions are declared directly within the Heat Orchestration Template (HOT YAML). The Aodh Evaluator periodically queries the Prometheus PromQL API; when thresholds are crossed, the Aodh Notifier dispatches HMAC-SHA256 pre-signed `alarm_url` signals directly to the Heat CloudFormation API to adjust AutoScalingGroup capacity.

---

## 1. Architecture and Data Flow

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
                   | (Scrape and Storage) |
                   +-----------+----------+
                               ^
                               | HTTP GET /api/v1/query (Every 60s)
                   +-----------+----------+
                   |    OpenStack Aodh    |
                   | * aodh-evaluator     |
                   | * aodh-notifier      |
                   +-----------+----------+
                               | HTTP POST (HMAC-SHA256 Signed alarm_url)
                               v
                   +----------------------+
                   |   Heat API (CFN)     | :8000
                   |   Heat Engine        |
                   | AutoScalingGroup     |
                   | (Scale-Out/Scale-In) |
                   +----------------------+
```

---

## 2. Component Analysis and Responsibilities

### A) `libvirt-exporter` (Port: 9177)
* Standard `prometheus-libvirt-exporter` package.
* Exposes kernel-level KVM vCPU execution time in seconds (`libvirt_domain_info_cpu_time_seconds_total`).
* Operates without OpenStack dependencies, tracking only local libvirt domain names (e.g. `instance-0000000d`).

### B) `server_group_exporter` (Port: 9102)
* Bridges local KVM domain names with OpenStack Heat Stack IDs (`metering.server_group`).
* Queries Nova API to map instances to their respective Stack ID and produces Prometheus labeled metrics (`openstack_instance_server_group{domain="...", server_group="..."}`).
* Uses an internal 60-second TTL cache to minimize Nova API overhead and respond to Prometheus scrapes in sub-millisecond latency.

### C) Prometheus Server (Port: 9090)
* Functions strictly as a Time-Series Database (TSDB) and PromQL query engine.
* Scrapes and aggregates metrics from `:9177` and `:9102`.
* Operates without Alertmanager; query evaluation is driven by Aodh.

### D) OpenStack Aodh (`OS::Aodh::PrometheusAlarm`)
* `aodh-evaluator` submits PromQL queries defined in the Heat template to Prometheus at scheduled intervals (default: 60 seconds).
* When thresholds are breached, `aodh-notifier` sends an HTTP POST request to Heat's pre-signed `alarm_url`.
* Configured with `repeat_actions: true` to ensure periodic signal dispatching as long as the alarm state persists.

---

## 3. Dynamic Stack Isolation and PromQL Injection

No manual Stack ID tracking is required by operators or end users. Heat injects its runtime Stack ID (`OS::stack_id`) into the PromQL query during stack synthesis using `str_replace`:

```yaml
cpu_alarm_high:
  type: OS::Aodh::PrometheusAlarm
  properties:
    repeat_actions: true
    description: "Trigger scale-out when average CPU exceeds threshold"
    comparison_operator: gt
    threshold: 70
    query:
      str_replace:
        template: "avg(rate(libvirt_domain_info_cpu_time_seconds_total[1m]) * 100 * on(domain) group_left(server_group) openstack_instance_server_group{server_group='MY_STACK_ID'})"
        params:
          MY_STACK_ID: { get_param: "OS::stack_id" }
    alarm_actions:
      - { get_attr: [scaleup_policy, alarm_url] }
```

Key operational guarantees:
1. Each Heat Stack evaluates metrics exclusively for its own instances.
2. Concurrent stacks maintain complete metric isolation and independent scaling decisions.
3. Using `alarm_url` guarantees secure, token-independent HMAC-SHA256 webhook execution.

---

## 4. Directory Structure

```text
openstack-prometheus-aodh-autoscaling/
├── aodh-autoscaling.yml               # Ansible deployment playbook
├── setup.sh                           # Standalone Bash setup script
├── requirements.txt                   # Python package dependencies
│
├── defaults/
│   └── main.yml                       # Default ports and configuration variables
│
├── tasks/
│   ├── main.yml                       # Task execution sequence
│   ├── prerequisites.yml              # Prerequisites, packages, and directories
│   ├── prometheus.yml                 # Standalone Prometheus setup tasks
│   ├── exporter.yml                   # server_group_exporter service tasks
│   └── aodh.yml                       # Aodh Prometheus and Heat integration tasks
│
├── templates/
│   ├── openrc.j2                      # Keystone authentication template
│   ├── prometheus.yml.j2              # Prometheus scrape configuration template
│   └── server-group-exporter.service.j2
│
├── handlers/
│   └── main.yml                       # Service restart handlers
│
├── exporters/
│   └── server_group_exporter.py       # Nova Metadata to Prometheus exporter
│
├── configs/
│   └── prometheus.yml                 # Reference Prometheus configuration
│
└── templates_heat/
    └── heat_template_aodh.yaml        # Ready-to-deploy HOT template with OS::Aodh::PrometheusAlarm
```

---

## 5. Deployment Methods

### Method 1: Ansible Role Deployment (Recommended)

From OpenStack-Ansible or a standard Ansible control node:

```bash
git clone https://github.com/ogi-glk/openstack-prometheus-aodh-autoscaling.git
cd openstack-prometheus-aodh-autoscaling

openstack-ansible aodh-autoscaling.yml
# Or:
ansible-playbook -i hosts aodh-autoscaling.yml
```

### Method 2: Standalone Shell Script (`setup.sh`)

For direct deployment on the target host (Compute/Controller):

```bash
cd openstack-prometheus-aodh-autoscaling
chmod +x setup.sh
sudo bash setup.sh
```

---

## 6. Service Configuration and Compatibility

The installation automatically enforces the following system configurations:

1. **Aodh Client Configuration (`/etc/openstack/prometheus.yaml`):**
   Required by `observabilityclient` to discover the Prometheus query endpoint:
   ```yaml
   host: 127.0.0.1
   port: 9090
   ```

2. **Aodh Service Configuration (`/etc/aodh/aodh.conf`):**
   ```ini
   [prometheus]
   host = 127.0.0.1
   port = 9090
   url = http://127.0.0.1:9090
   ```

3. **Heat CloudFormation API Authentication (`/etc/heat/heat.conf`):**
   Configures service credentials under `[ec2authtoken]` to allow Keystone `/v3/ec2tokens` validation of AWS HMAC-SHA256 signatures received on `alarm_url`:
   ```ini
   [ec2authtoken]
   auth_uri = http://<LB_VIP>:5000
   auth_url = http://<LB_VIP>:5000/v3
   auth_type = password
   username = heat
   password = <HEAT_SERVICE_PASSWORD>
   project_name = service
   user_domain_id = default
   project_domain_id = default
   ```

4. **Ubuntu 24.04 / Python 3.12 Compatibility:**
   Applies a backward-compatible fix to `osprofiler` resolving `ExceptionContextImpl.chained_exception` failures encountered with SQLAlchemy 2.0.

---

## 7. Verification and Testing

### 1. Launch a Stack
```bash
openstack stack create -t templates_heat/heat_template_aodh.yaml aodh-autoscaling-stack
```

### 2. Verify Aodh Alarms
```bash
openstack alarm list
```
Confirm that alarms appear with `type: prometheus`.

### 3. CPU Load Testing
Connect to the instance and generate CPU load:
```bash
cat /dev/zero > /dev/null &
```

* Prometheus records the vCPU spike.
* `aodh-evaluator` flags the threshold breach and updates the alarm state to `alarm`.
* `aodh-notifier` transmits the signal to Heat CFN, receiving HTTP 200 OK.
* Heat AutoScalingGroup boots a new instance (Scale-Out).
* Terminating the load (`killall cat`) drops the CPU, triggering `cpu_alarm_low` and automatically removing the additional instance (Scale-In).

---

## 8. Architectural Comparison: Aodh vs Alertmanager

| Criterion | Alertmanager + Adapter Model | Aodh + Prometheus Model |
| :--- | :--- | :--- |
| **Alarm Placement** | In Prometheus `alert_rules.yml` | In Heat Orchestration Template (HOT YAML) |
| **User Experience** | Requires infrastructure admin changes | Fully self-service user template workflow |
| **Trigger Mechanism** | Webhook Push (Alertmanager) | Periodic PromQL Evaluation (Aodh Pull) |
| **Response Latency** | ~15-30 seconds | ~60 seconds (Configurable evaluation interval) |
| **OpenStack Native** | Requires custom adapter service | Native OpenStack resource types (`OS::Aodh::*`) |
| **Resource Overhead** | Low (Single lightweight daemon) | Standard (Aodh daemon set) |

---


---

## 9. How Customers Adapt Their Own Templates (Developer Guide)

To integrate existing standard Heat Orchestration Templates into this architecture, users must append **3 mandatory blocks** to their template. A fully documented reference template is available at [`templates_heat/heat_template_user_guide.yaml`](templates_heat/heat_template_user_guide.yaml).

### Block 1: Server Metadata Tagging
Inside the `properties` of the `OS::Nova::Server` resource, add:
```yaml
          metadata:
            metering.server_group: { get_param: "OS::stack_id" }
```
*This label allows the `server_group_exporter` to correlate raw KVM domain execution time with the customer's runtime Stack ID in Prometheus.*

### Block 2: Scaling Policies
Add scaling policies to the `resources` section:
```yaml
  scaleup_policy:
    type: OS::Heat::ScalingPolicy
    properties:
      auto_scaling_group_id: { get_resource: asg }
      adjustment_type: change_in_capacity
      scaling_adjustment: 1
      cooldown: 60

  scaledown_policy:
    type: OS::Heat::ScalingPolicy
    properties:
      auto_scaling_group_id: { get_resource: asg }
      adjustment_type: change_in_capacity
      scaling_adjustment: -1
      cooldown: 60
```

### Block 3: Aodh Prometheus Alarms
Declare `OS::Aodh::PrometheusAlarm` resources targeting the pre-signed `alarm_url`:
```yaml
  cpu_alarm_high:
    type: OS::Aodh::PrometheusAlarm
    properties:
      repeat_actions: true
      comparison_operator: gt
      threshold: 70
      query:
        str_replace:
          template: "avg(rate(libvirt_domain_info_cpu_time_seconds_total[1m]) * 100 * on(domain) group_left(server_group) openstack_instance_server_group{server_group='MY_STACK_ID'})"
          params:
            MY_STACK_ID: { get_param: "OS::stack_id" }
      alarm_actions:
        - { get_attr: [scaleup_policy, alarm_url] }
```
*Using `alarm_url` provides HMAC-SHA256 authenticated webhook invocation without requiring Keystone tokens.*

## License

Distributed under the [Apache-2.0](LICENSE) License.