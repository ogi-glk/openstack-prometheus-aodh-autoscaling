# OpenStack Prometheus Autoscaling Layer

[Türkçe](README.md) | [English](README.en.md)

---

Bu proje, OpenStack ortamlarında hantal ve bakımı zor olan telemetri servisleri (Ceilometer, Gnocchi, Aodh) yerine **Prometheus + Alertmanager + Libvirt Exporter** mimarisi kullanarak CPU tabanlı otomatik ölçekleme (AutoScaling) sağlar.

---

## 1. Mimari ve Veri Akışı

```text
┌────────────────────────┐         ┌──────────────────────────────┐
│ libvirt-exporter       │ :9177   │ server-group-exporter        │ :9102
│ KVM domain CPU verisi  │         │ Nova Metadata -> Stack ID    │
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

## 2. Bileşenler Nasıl Çalışır?

### A) `libvirt-exporter` (Port: 9177)
* Ubuntu 24.04 resmi deposundaki `prometheus-libvirt-exporter` paketidir.
* KVM hipervizöründeki her sanal makinenin harcadığı CPU süresini saniye cinsinden üretir:
  ```text
  libvirt_domain_info_cpu_time_seconds_total{domain="instance-00000007"} 584.41
  ```
* Bu bileşen OpenStack'ten veya Heat Stack'ten haberdar değildir; sadece libvirt domain adını (`instance-00000007`) bilir.

### B) `server_group_exporter` (Port: 9102)
* Python ile yazılmış hafif bir servistir.
* Arka planda bağımsız bir iş parçacığı (worker thread) ile her **60 saniyede bir** (`CACHE_TTL=60`) Nova API'ye bağlanır.
* Sanal makinelerin libvirt domain adı ile ait oldukları Heat Stack ID'sini (`metering.server_group`) eşleştirir ve RAM'e kaydeder:
  ```text
  openstack_instance_server_group{domain="instance-00000007",instance_id="2972726a...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
  ```
* **Optimizasyon:** Prometheus `:9102/metrics` endpoint'ini her 15 saniyede bir sorguladığında, Nova API beklenmez; veri RAM'den **< 1 milisaniyede** döner. Nova API gereksiz sorgularla boğulmaz.

### C) Prometheus & PromQL Eşleştirmesi (Port: 9090)
* İki farklı kaynaktan gelen metrikleri ortak `domain` etiketi üzerinden çarparak birleştirir:
  ```promql
  avg by (server_group) (
    rate(libvirt_domain_info_cpu_time_seconds_total[1m]) * 100
    * on(domain) group_left(server_group)
    openstack_instance_server_group
  )
  ```
* **İzolasyon Mantığı:** `by (server_group)` sayesinde her Heat Stack'in CPU ortalaması birbirinden bağımsız hesaplanır. Hipervizördeki diğer makinelerin yükü bu grubu etkilemez.

### D) Alertmanager (Port: 9093)
* Prometheus'tan gelen `CpuHigh` (> %70) veya `CpuLow` (< %20) alarmlarını karşılar.
* Dalgalanmayı (flapping) önlemek için `group_wait` (10s) ve `repeat_interval` (2m) kurallarıyla `heat-signal-adapter`'a (`http://localhost:9200`) iletir.

### E) `heat_signal_adapter` (Port: 9200)
* Alertmanager webhook'larını karşılayan yerel Python köprüsüdür.
* Eski/güvensiz AWS CloudFormation (CFN port 8000) linklerini kullanmaz.
* `openstacksdk` kütüphanesini kullanarak Keystone'dan geçerli yönetici yetkisini alır.
* Heat'in yerel REST API'sine (`port 8004`) doğrudan yetkili sinyal gönderir:
  * `CpuHigh` geldiğinde $\rightarrow$ `POST /stacks/<STACK_NAME>/<STACK_ID>/resources/scaleup_policy/signal`
  * `CpuLow` geldiğinde $\rightarrow$ `POST /stacks/<STACK_NAME>/<STACK_ID>/resources/scaledown_policy/signal`
* Heat bu sinyali aldığı anda AutoScaling grubundaki makine sayısını artırır veya azaltır.

---

## 3. Konfigürasyon ve Süre Ayarları

Bütün süreler ve eşik değerleri `defaults/main.yml` dosyasından yönetilir:

| Parametre | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `autoscaling_cpu_high_threshold` | `70` | Scale-Out eşiği (% CPU) |
| `autoscaling_cpu_low_threshold` | `20` | Scale-In eşiği (% CPU) |
| `autoscaling_evaluation_period` | `60s` | Yükün eşik üzerinde kalması gereken doğrulama süresi (`for: 60s`) |
| `autoscaling_alarm_cooldown` | `2m` | Alertmanager alarm tekrarlama aralığı (`repeat_interval`) |
| `server_group_exporter_cache_ttl` | `60` | Exporter'ın Nova API'yi sorgulama aralığı (saniye) |
| `heat_signal_adapter_port` | `9200` | Adaptör dinleme portu |
| `server_group_exporter_port` | `9102` | Metadata exporter dinleme portu |
| `heat_stack_name` | `autoscaling-stack` | Yönetilecek Heat Stack adı |

---

## 4. Kurulum

### Yöntem 1: Ansible Rolü ile (Önerilen)
Deployer makinesi üzerinden:
```bash
git clone https://github.com/ogi-glk/openstack-prometheus-autoscaling.git
cd openstack-prometheus-autoscaling

# İhtiyaç varsa defaults/main.yml dosyasını düzenleyin
openstack-ansible autoscaling.yml
# veya
ansible-playbook -i hosts autoscaling.yml
```

### Yöntem 2: Hedef Sunucuda Manuel / Standalone Kurulum
```bash
cd openstack-prometheus-autoscaling
sudo bash setup.sh
```

---

## 5. Doğrulama ve Canlı Test Kanıtları

### A) Exporter Metrik Çıktısı (Canlı Çoklu Makine Eşleştirmesi):
```text
$ curl -s http://localhost:9102/metrics
openstack_instance_server_group{domain="instance-00000007",instance_id="2972726a...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
openstack_instance_server_group{domain="instance-00000008",instance_id="55497eaa...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
openstack_instance_server_group{domain="instance-00000009",instance_id="1810fd03...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
```

### B) Adaptör Canlı Günlüğü (Sıfır Müdahale ile Büyüme ve Küçülme):
```text
$ journalctl -u heat-signal-adapter -n 10 --no-pager
[*] Native Heat Signal Adapter listening on port :9200 for stack 'autoscaling-stack'...
[+] SUCCESS: CpuHigh -> Native Heat signal 'scaleup_policy' triggered (HTTP 200)   <-- 1. VM'den 2. VM'e büyüme
[+] SUCCESS: CpuHigh -> Native Heat signal 'scaleup_policy' triggered (HTTP 200)   <-- 2. VM'den 3. VM'e büyüme (Tavan)
[+] SUCCESS: CpuLow  -> Native Heat signal 'scaledown_policy' triggered (HTTP 200) <-- Yük bitti: 3. VM silindi
[+] SUCCESS: CpuLow  -> Native Heat signal 'scaledown_policy' triggered (HTTP 200) <-- Yük bitti: 2. VM silindi (Taban: 1 VM)
```

---

## 6. Sık Karşılaşılan Sorunlar ve Çözümler

1. **BrokenPipeError (Port 9102):**  
   * *Sorun:* Prometheus scrape ederken Nova API 5 saniyeden uzun sürerse bağlantı zaman aşımına uğrar.
   * *Çözüm:* Exporter bağımsız bir arka plan thread'i ile çalışacak şekilde tasarlandı. Veriler RAM'den milisaniyede verilir.
2. **HTTP 403 AccessDenied (Port 8000 CFN Sinyali):**  
   * *Sorun:* Heat'in port 8000 AWS/CFN taklidi servisi, Keystone v3 ve Trust mimarisinde imza hatası verebilir.
   * *Çözüm:* Adaptör port 8000 yerine doğrudan port 8004 yerel Heat REST API'sine admin token'ı ile bağlanır.
3. **Systemd `%` Specifier Hatası:**  
   * *Sorun:* Systemd unit dosyalarında `%` karakterleri (`%3A`, `%2F`) geçersiz slot sanılıp satır yoksayılabilir.
   * *Çözüm:* Değişkenler servis dosyasında değil, `openrc` (`EnvironmentFile`) içinde tutulur.

---

## Lisans
Bu proje [Apache-2.0](LICENSE) lisansı ile sunulmaktadır.