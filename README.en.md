# OpenStack Prometheus Autoscaling Layer

[Türkçe](README.md) | [English](README.en.md)

---

An integration layer providing CPU-based autoscaling for OpenStack environments using a lightweight **Prometheus + Alertmanager + Libvirt Exporter** architecture instead of legacy Ceilometer, Gnocchi, and Aodh services.

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

---

## 2. Component Breakdown

### A) `libvirt-exporter` (Port: 9177)
* Standard `prometheus-libvirt-exporter` package from Ubuntu 24.04 official repositories.
* Collects raw vCPU time metrics in seconds directly from the KVM hypervisor:
  ```text
  libvirt_domain_info_cpu_time_seconds_total{domain="instance-00000007"} 584.41
  ```
* This exporter has zero dependency on OpenStack; it only tracks libvirt domain names (`instance-00000007`).

### B) `server_group_exporter` (Port: 9102)
* Lightweight Python service.
* A background worker thread queries Nova API every **60 seconds** (`CACHE_TTL=60`).
* Maps instance domain names to Heat Stack IDs (`metering.server_group`) and caches them in memory:
  ```text
  openstack_instance_server_group{domain="instance-00000007",instance_id="2972726a...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
  ```
* **Performance:** When Prometheus scrapes `:9102/metrics`, response time is **< 1 millisecond** from RAM cache. Zero latency, no API timeouts.

### C) Prometheus & PromQL Vector Matching (Port: 9090)
* Joins raw CPU metrics with metadata on the common `domain` label:
  ```promql
  avg by (server_group) (
    rate(libvirt_domain_info_cpu_time_seconds_total[1m]) * 100
    * on(domain) group_left(server_group)
    openstack_instance_server_group
  )
  ```
* **Isolation:** `by (server_group)` ensures CPU load is aggregated per Heat Stack. Other VMs on the same hypervisor do not affect scaling decisions.

### D) Alertmanager (Port: 9093)
* Evaluates `CpuHigh` (> 70%) and `CpuLow` (< 20%) alerts.
* Prevents flapping via `group_wait` (10s) and triggers periodic notifications via `repeat_interval` (2m) to `http://localhost:9200`.

### E) `heat_signal_adapter` (Port: 9200)
* Webhook receiver written in Python.
* Bypasses deprecated AWS CloudFormation port 8000 pre-signed URLs.
* Uses `openstacksdk` with Keystone admin credentials to dispatch authorized HTTP POST signals directly to Heat's native REST API (`port 8004`):
  * `CpuHigh` $\rightarrow$ `POST /stacks/<STACK_NAME>/<STACK_ID>/resources/scaleup_policy/signal`
  * `CpuLow`  $\rightarrow$ `POST /stacks/<STACK_NAME>/<STACK_ID>/resources/scaledown_policy/signal`
* Heat adjusts AutoScalingGroup capacity upon receiving the signal.

---

## 3. Configuration & Timing Parameters

Configured via `defaults/main.yml`:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `autoscaling_cpu_high_threshold` | `70` | Scale-Out threshold (% CPU) |
| `autoscaling_cpu_low_threshold` | `20` | Scale-In threshold (% CPU) |
| `autoscaling_evaluation_period` | `60s` | Verification window before alert fires (`for: 60s`) |
| `autoscaling_alarm_cooldown` | `2m` | Alertmanager repeat interval |
| `server_group_exporter_cache_ttl` | `60` | Nova API polling interval in seconds |
| `heat_signal_adapter_port` | `9200` | Adapter listening port |
| `server_group_exporter_port` | `9102` | Metadata exporter listening port |
| `heat_stack_name` | `autoscaling-stack` | Target Heat Stack name |

---

## 4. Deployment

### Method 1: Ansible Role (Recommended)
From deployer machine:
```bash
git clone https://github.com/ogi-glk/openstack-prometheus-autoscaling.git
cd openstack-prometheus-autoscaling

openstack-ansible autoscaling.yml
# or
ansible-playbook -i hosts autoscaling.yml
```

### Method 2: Standalone Shell Script
```bash
cd openstack-prometheus-autoscaling
sudo bash setup.sh
```

---

## 5. Live Production Evidence

### A) Exporter Scrape Output (Multi-Instance Discovery):
```text
$ curl -s http://localhost:9102/metrics
openstack_instance_server_group{domain="instance-00000007",instance_id="2972726a...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
openstack_instance_server_group{domain="instance-00000008",instance_id="55497eaa...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
openstack_instance_server_group{domain="instance-00000009",instance_id="1810fd03...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
```

### B) Adapter Execution Log (Touchless Scale-Out & Scale-In):
```text
$ journalctl -u heat-signal-adapter -n 10 --no-pager
[*] Native Heat Signal Adapter listening on port :9200 for stack 'autoscaling-stack'...
[+] SUCCESS: CpuHigh -> Native Heat signal 'scaleup_policy' triggered (HTTP 200)   <-- Scale-Out: 1 to 2 VMs
[+] SUCCESS: CpuHigh -> Native Heat signal 'scaleup_policy' triggered (HTTP 200)   <-- Scale-Out: 2 to 3 VMs (Max capacity)
[+] SUCCESS: CpuLow  -> Native Heat signal 'scaledown_policy' triggered (HTTP 200) <-- Load removed: 3 to 2 VMs
[+] SUCCESS: CpuLow  -> Native Heat signal 'scaledown_policy' triggered (HTTP 200) <-- Load removed: 2 to 1 VM (Min baseline)
```

---

## 6. Troubleshooting

1. **BrokenPipeError on Port 9102:**  
   * *Root Cause:* Synchronous Nova API queries exceeding Prometheus scrape timeout.
   * *Solution:* Exporter decoupled into background worker thread; metrics served instantly from RAM.
2. **HTTP 403 AccessDenied on Port 8000:**  
   * *Root Cause:* Heat CFN signed URL token validation conflicts with Keystone v3.
   * *Solution:* Direct signal dispatch to native Heat REST API (`port 8004`) using Keystone credentials via `openstacksdk`.
3. **Systemd `%` Specifier Failure:**  
   * *Root Cause:* Systemd unit files treating URL query strings (`%3A`) as invalid specifiers.
   * *Solution:* Environment variables sourced directly from `EnvironmentFile` (`openrc`).

---

## License
Licensed under [Apache-2.0](LICENSE).