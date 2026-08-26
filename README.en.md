# OpenStack Prometheus + Aodh Autoscaling Layer

[Türkçe](README.md) | [English](README.en.md)

---

An integration layer providing CPU-based autoscaling for OpenStack environments using a lightweight **Prometheus + OpenStack Aodh (`OS::Aodh::PrometheusAlarm`)** architecture instead of legacy Ceilometer and Gnocchi services.

In this architecture, **Alertmanager and custom webhook adapters are completely removed.** Scaling alarm definitions are embedded directly inside the Heat Orchestration Template (`heat_template.yaml`) as native OpenStack resources; Aodh queries Prometheus PromQL API directly and triggers Heat scaling policies.

---

## 1. Architecture and Data Flow

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
                   │   Prometheus TSDB    │ :9090
                   │  (Scrape & Storage)  │
                   └───────────┬──────────┘
                               ▲
                               │ HTTP GET /api/v1/query (Every 60s)
                   ┌───────────┴──────────┐
                   │    OpenStack Aodh    │
                   │ • aodh-evaluator     │
                   │ • aodh-notifier      │
                   └───────────┬──────────┘
                               ▼ HTTP POST /resources/{policy}/signal
                   ┌──────────────────────┐
                   │     Heat Engine      │ :8004
                   │ AutoScalingGroup     │
                   │ (Scale-Out/Scale-In) │
                   └──────────────────────┘
```

---

## 2. Component Breakdown

### A) `libvirt-exporter` (Port: 9177)
* Standard `prometheus-libvirt-exporter` package from Ubuntu 24.04 official repositories.
* Collects raw vCPU time metrics in seconds directly from the KVM hypervisor (`libvirt_domain_info_cpu_time_seconds_total`).
* Zero OpenStack dependency; only tracks libvirt domain names (`instance-00000009`).

### B) `server_group_exporter` (Port: 9102) - *Indispensable Bridge!*
* **Why is it essential?** KVM only knows instance domain names (`instance-00000009`), while Heat only knows Stack UUIDs (`server_group: 9b5093f6...`). Without this exporter, when Aodh queries Prometheus for Stack `9b5093f6`, Prometheus would return empty data!
* Maps Nova instance domains to Heat Stack IDs (`metering.server_group`) and caches them in memory.
* **Performance:** 60-second TTL RAM cache cuts Nova API calls by 75%, answering Prometheus scrapes in `< 1ms`.

### C) Prometheus Server (Port: 9090)
* Operates strictly as a Time-Series Database (TSDB).
* Scrapes metrics from `:9177` and `:9102`.
* **NO Alertmanager and NO Alert Rules!** Serves only as the PromQL evaluation engine for Aodh.

### D) OpenStack Aodh (`OS::Aodh::PrometheusAlarm`)
* Configured via `/etc/aodh/aodh.conf` with `[prometheus] url = http://localhost:9090`.
* `aodh-evaluator` queries Prometheus every 60 seconds with the PromQL query defined in the HOT template.
* When thresholds are exceeded, `aodh-notifier` dispatches authenticated HTTP POST signals directly to Heat's scaling policy URL.

---

## 3. Dynamic Stack ID Injection (Isolation)

In the Aodh model, the operator or developer **never manually looks up or hardcodes Stack IDs!**  
Heat dynamically injects its own runtime Stack ID (`OS::stack_id`) into the PromQL query using `str_replace`:

```yaml
# From templates_heat/heat_template_aodh.yaml:
cpu_alarm_high:
  type: OS::Aodh::PrometheusAlarm
  properties:
    description: "Scale-Out when CPU exceeds threshold"
    comparison_operator: gt
    threshold: 70
    query:
      str_replace:
        template: "avg(rate(libvirt_domain_info_cpu_time_seconds_total[1m]) * 100 * on(domain) group_left(server_group) openstack_instance_server_group{server_group='STACK_ID'})"
        params:
          STACK_ID: { get_param: "OS::stack_id" }
    alarm_actions:
      - { get_attr: [scaleup_policy, signal_url] }
```

---

## 4. Directory Structure

```text
openstack-prometheus-aodh-autoscaling/
├── aodh-autoscaling.yml                     # Ansible Deployment Playbook
├── setup.sh                            # Standalone installation script
├── requirements.txt                    # Python dependencies
│
├── defaults/
│   └── main.yml                        # Ports and Aodh Prometheus URL
│
├── tasks/
│   ├── main.yml                        # Task execution sequence
│   ├── prerequisites.yml               # libvirt-exporter, venv and openrc
│   ├── prometheus.yml                  # Standalone Prometheus installation
│   ├── exporter.yml                    # server_group_exporter installation
│   └── aodh.yml                        # Aodh prometheus configuration task
│
├── templates/
│   ├── openrc.j2                       # Keystone credentials template
│   ├── prometheus.yml.j2               # Pure scrape Prometheus template
│   └── server-group-exporter.service.j2
│
├── handlers/
│   └── main.yml
│
├── exporters/
│   └── server_group_exporter.py        # Nova Metadata -> Prometheus exporter (:9102)
│
├── configs/
│   └── prometheus.yml                  # Sample config
│
└── templates_heat/
    └── heat_template_aodh.yaml         # Ready-to-deploy HOT template with OS::Aodh::PrometheusAlarm
```

---

## 5. Deployment

### Method 1: Ansible Role (Recommended)
```bash
git clone https://github.com/ogi-glk/openstack-prometheus-aodh-autoscaling.git
cd openstack-prometheus-aodh-autoscaling

openstack-ansible aodh-autoscaling.yml
# or
ansible-playbook -i hosts aodh-autoscaling.yml
```

### Method 2: Standalone Shell Script
```bash
cd openstack-prometheus-aodh-autoscaling
chmod +x setup.sh
sudo bash setup.sh
```

---

## 6. Aodh Configuration (`/etc/aodh/aodh.conf`)

Add the following section to `/etc/aodh/aodh.conf`:

```ini
[prometheus]
url = http://localhost:9090
```

Restart Aodh services:
```bash
systemctl restart aodh-evaluator aodh-notifier
```

---

## 7. Architecture Comparison: Aodh vs Alertmanager

| Criterion | Model A (Alertmanager + Adapter) | Model B (Aodh + Prometheus) |
| :--- | :--- | :--- |
| **Alarm Definition** | In Prometheus `alert_rules.yml` | Inside Heat Template (`HOT YAML`) |
| **User Experience** | Requires admin alert rule management | Single-file developer workflow |
| **Response Latency** | ⚡ **Milliseconds (Push)** | ⏱️ **~60 seconds (Polling / Pull)** |
| **System Overhead** | 🍃 **35 MB RAM (Zero DB / Zero MQ)** | 🐘 **4 Daemons + MariaDB + RabbitMQ** |
| **Required Services** | Prometheus + 1 lightweight Python adapter | Full OpenStack Aodh cluster |

---

## License
Licensed under [Apache-2.0](LICENSE).