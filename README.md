# OpenStack Prometheus + Aodh Autoscaling Layer

[Türkçe](README.md) | [English](README.en.md)

---

Bu proje, OpenStack ortamlarında hantal telemetri servisleri (Ceilometer ve Gnocchi) yerine **Prometheus + OpenStack Aodh (`OS::Aodh::PrometheusAlarm`)** mimarisini kullanarak CPU tabanlı otomatik ölçekleme (AutoScaling) sağlar.

Bu mimaride **Alertmanager ve harici adaptör kullanılmaz.** Alarm kuralları doğrudan Heat şablonunun (`heat_template.yaml`) içine bir OpenStack kaynağı olarak yazılır; Aodh doğrudan Prometheus'un PromQL API'sini sorgulayarak Heat'i tetikler.

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
                   │   Prometheus TSDB    │ :9090
                   │  (Scrape & Storage)  │
                   └───────────┬──────────┘
                               ▲
                               │ HTTP GET /api/v1/query (Her 60s)
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

## 2. Bileşenler Nasıl Çalışır?

### A) `libvirt-exporter` (Port: 9177)
* Ubuntu 24.04 resmi deposundaki `prometheus-libvirt-exporter` paketidir.
* KVM hipervizöründeki her sanal makinenin harcadığı CPU süresini saniye cinsinden üretir (`libvirt_domain_info_cpu_time_seconds_total`).
* Bu bileşen OpenStack'ten veya Heat Stack'ten haberdar değildir; sadece libvirt domain adını (`instance-00000009`) bilir.

### B) `server_group_exporter` (Port: 9102) - *Kritik Köprü!*
* **Neden Şart?** KVM sadece `instance-00000009` adını bilir, Heat ise sadece `server_group: 9b5093f6...` UUID'sini bilir. Bu exporter olmasaydı Aodh Prometheus'a *"Bana Stack 9b5093f6'nın CPU'sunu ver"* dediğinde Prometheus boş veri dönerdi!
* Nova API'den sanal makineleri ait oldukları Heat Stack ID (`metering.server_group`) ile eşleştirir ve RAM'e kaydeder.
* **Optimizasyon:** 60 saniyelik TTL Cache sayesinde Nova API yükünü %75 düşürür; Prometheus sorgularına `< 1ms` sürede yanıt verir.

### C) Prometheus Server (Port: 9090)
* Sadece bir zaman serisi veritabanı (TSDB) olarak çalışır.
* `:9177` ve `:9102` metriklerini toplar.
* **Alertmanager ve Alert Rules YOKTUR!** Prometheus sadece Aodh'un sorgu atacağı PromQL motorunu sunar.

### D) OpenStack Aodh (`OS::Aodh::PrometheusAlarm`)
* `/etc/aodh/aodh.conf` içinde `[prometheus] url = http://localhost:9090` tanımlıdır.
* `aodh-evaluator`, Heat şablonunda tanımlanan PromQL sorgusunu her 60 saniyede bir Prometheus'a sorar.
* Eşik aşılırsa `aodh-notifier` doğrudan Heat'in `signal_url` adresine yetkili `POST` gönderir ve makine sayısını artırır/azaltır.

---

## 3. Stack ID Enjeksiyonu (Nasıl İzole Ediliyor?)

Aodh modelinde geliştirici veya operatör **el ile Stack ID kopyalamaz!**  
Heat, şablonu ayağa kaldırırken kendi Stack ID'sini (`OS::stack_id`) PromQL sorgusunun içine `str_replace` ile otomatik olarak gömer:

```yaml
# templates_heat/heat_template_aodh.yaml içinden:
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

Bu sayede:
1. `openstack stack create my-stack` komutunu verdiğinizde Heat Stack ID'yi üretir.
2. Aodh alarmının içine `{server_group='O_STACK_ID'}` filtresini otomatik yapıştırır.
3. Her Stack sadece ve sadece kendi makinelerinin CPU ortalamasını izler. İzolasyon %100 otomatik sağlanır!

---

## 4. Proje Dizin Yapısı

```text
openstack-prometheus-aodh-autoscaling/
├── autoscaling.yml                     # Ansible Dağıtım Playbook'u
├── setup.sh                            # Standalone bash kurulum betiği
├── requirements.txt                    # Python paket bağımlılıkları
│
├── defaults/
│   └── main.yml                        # Portlar ve Aodh Prometheus URL ayarları
│
├── tasks/
│   ├── main.yml                        # Görev çağırma sırası
│   ├── prerequisites.yml               # libvirt-exporter, venv ve openrc
│   ├── prometheus.yml                  # Standalone Prometheus kurulumu
│   ├── exporter.yml                    # server_group_exporter kurulumu
│   └── aodh.yml                        # Aodh konfigürasyonunu Prometheus'a bağlama
│
├── templates/
│   ├── openrc.j2                       # Keystone kimlik şablonu
│   ├── prometheus.yml.j2               # Saf scrape Prometheus şablonu
│   └── server-group-exporter.service.j2
│
├── handlers/
│   └── main.yml
│
├── exporters/
│   └── server_group_exporter.py        # Nova Metadata -> Prometheus exporter (:9102)
│
├── configs/
│   └── prometheus.yml                  # Örnek konfigürasyon
│
└── templates_heat/
    └── heat_template_aodh.yaml         # OS::Aodh::PrometheusAlarm içeren hazır HOT şablonu
```

---

## 5. Kurulum Yöntemleri

### 1. Yöntem: Ansible Rolü ile Kurulum (Önerilen)

Deployer makinesinden:

```bash
git clone https://github.com/ogi-glk/openstack-prometheus-aodh-autoscaling.git
cd openstack-prometheus-aodh-autoscaling

openstack-ansible autoscaling.yml
# Veya:
ansible-playbook -i hosts autoscaling.yml
```

### 2. Yöntem: Standalone Script ile Kurulum (`setup.sh`)

Hedef sunucu üzerinde doğrudan çalıştırmak için:

```bash
cd openstack-prometheus-aodh-autoscaling
chmod +x setup.sh
sudo bash setup.sh
```

---

## 6. Aodh Konfigürasyonu (`/etc/aodh/aodh.conf`)

Aodh'un Prometheus ile konuşabilmesi için Aodh konfigürasyon dosyasına şu satır eklenir:

```ini
[prometheus]
url = http://localhost:9090
```

Servisler yeniden başlatılır:
```bash
systemctl restart aodh-evaluator aodh-notifier
```

---

## 7. Test ve Doğrulama

### 1. Stack'i Oluşturun:
```bash
openstack stack create -t templates_heat/heat_template_aodh.yaml aodh-autoscaling-stack
```

### 2. Aodh Alarmının Oluştuğunu Doğrulayın:
```bash
openstack alarm list
```
*(Burada `cpu_alarm_high` ve `cpu_alarm_low` alarmlarının `type: prometheus` olarak listelendiğini göreceksiniz).*

### 3. CPU Yük Testi Yapın:
Açılan sanal makinenin içine girip yükü başlatın:
```bash
cat /dev/zero > /dev/null &
```
* Prometheus CPU'nun %100'e çıktığını hesaplar.
* Aodh `aodh-evaluator` sorguyu atar ve eşiğin aşıldığını görür (`alarm` durumuna geçer).
* Aodh Heat'e sinyali gönderir ve Stack 2 makineye çıkar!

---

## 8. Mimari Kıyaslama: Aodh vs Alertmanager

| Kriter | Model A (Alertmanager + Adaptör) | Model B (Aodh + Prometheus) |
| :--- | :--- | :--- |
| **Alarm Tanımı** | Prometheus `alert_rules.yml` dosyasında | Doğrudan Heat Şablonu (`HOT YAML`) içinde |
| **Geliştirici Deneyimi**| Sistem yöneticisi müdahalesi ister | Geliştirici tek şablonda her şeyi çözer |
| **Tepki Süresi** | ⚡ **Milisaniyeler (Push)** | ⏱️ **~60 saniye (Polling / Pull)** |
| **Sistem Kaynağı** | 🍃 **35 MB RAM (Sıfır DB / Sıfır MQ)** | 🐘 **4 Daemon + MariaDB + RabbitMQ** |
| **Gereken Servisler**| Sadece Prometheus + 1 hafif Python adaptör | Tam OpenStack Aodh kümesi |

---

## Lisans
Bu proje [Apache-2.0](LICENSE) lisansı ile sunulmaktadır.