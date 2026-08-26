# OpenStack Prometheus Autoscaling Layer

[Türkçe](README.md) | [English](README.en.md)

---

OpenStack ortamlarında Ceilometer, Gnocchi ve Aodh servisleri yerine **Prometheus + Alertmanager + libvirt-exporter** mimarisi kullanılarak CPU tabanlı otomatik ölçekleme (AutoScaling) yapılmasını sağlayan kurumsal entegrasyon katmanıdır.

Bu proje üç farklı yöntemle kurulabilir:
1. **Ansible Rolü ile:** `openstack-ansible` veya standart `ansible-playbook` üzerinden envanterdeki sunuculara otomatik dağıtım.
2. **Standalone / Otomatik Bash Scripti:** Hedef sunucu üzerinde `setup.sh` betiği ile tek komutla kurulum.
3. **Tam Manuel / Adım Adım Kurulum:** Sanal ortam (venv), systemd birimleri ve servislerin elle yapılandırılması.

---

## 1. Mimari ve Çalışma Mantığı

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

### Bileşenler:
1. **libvirt-exporter (:9177):** KVM/libvirt üzerinden sanal makinelerin ham CPU kullanım sürelerini toplar (`prometheus-libvirt-exporter` paketi).
2. **server-group-exporter (:9102):** Nova API'den VM isimleri ile Heat Stack ID (`metering.server_group`) eşleşmesini çeker. Nova API yükünü %75 azaltmak için 60 saniyelik TTL RAM önbellek (Cache) kullanır. Prometheus scrape isteklerine `< 1ms` sürede yanıt verir.
3. **Prometheus (:9090):** İki veri kaynağını ortak `domain` etiketi üzerinden PromQL vektör eşlemesi (`vector matching`) ile birleştirir ve Stack bazlı ortalama CPU'yu hesaplar.
4. **Alertmanager (:9093):** Eşik aşıldığında alarmı yerel adaptöre (`:9200`) yönlendirir.
5. **heat_signal_adapter (:9200):** Alertmanager webhook alarmını karşılar, `openstacksdk` ile Keystone'dan yetki alarak Heat'in yerel REST API'sine (`port 8004`) doğrudan kimlik doğrulamalı POST sinyali gönderir.

---

## 2. Proje Dizin Yapısı

```text
openstack-prometheus-autoscaling/
├── autoscaling.yml                    # Dağıtım Ansible Playbook'u
├── setup.sh                           # Standalone kurulum betiği
├── requirements.txt                   # Python paket bağımlılıkları
│
├── defaults/                          # Rol değişkenleri
│   └── main.yml                       # Eşik değerleri, portlar ve bekleme süreleri
│
├── tasks/                             # Ansible görevleri
│   ├── main.yml                       # Ana görev çağırma sırası
│   ├── prerequisites.yml              # Dizinler, sistem paketleri (libvirt-exporter vb.) ve venv
│   ├── prometheus.yml                 # Prometheus ve Alertmanager kurulumu
│   └── adapter.yml                    # Adaptör, exporter ve systemd servisleri
│
├── templates/                         # Jinja2 (.j2) konfigürasyon şablonları
│   ├── openrc.j2                      # Keystone kimlik bilgileri şablonu
│   ├── alert_rules.yml.j2             # PromQL alarm kuralları şablonu
│   ├── alertmanager.yml.j2            # Webhook yönlendirme şablonu
│   ├── prometheus.yml.j2              # Prometheus scrape hedefleri şablonu
│   ├── heat-signal-adapter.service.j2 # Systemd adaptör servis şablonu
│   └── server-group-exporter.service.j2
│
├── handlers/                          # Servis yeniden başlatma tetikleyicileri
│   └── main.yml
│
├── meta/                              # Ansible Galaxy meta bilgileri
│   └── main.yml
│
├── exporters/                         # Python kaynak kodu
│   └── server_group_exporter.py       # Nova Metadata -> Prometheus exporter (:9102)
│
├── adapter/                           # Python kaynak kodu
│   └── heat_signal_adapter.py         # Alertmanager -> Yerel Heat REST API adaptörü (:9200)
│
├── configs/                           # Örnek konfigürasyon dosyaları (Manuel kurulum için)
│   ├── prometheus.yml
│   ├── alert_rules.yml
│   └── alertmanager.yml
│
└── systemd/                           # Örnek systemd servis dosyaları (Manuel kurulum için)
    ├── server-group-exporter.service
    └── heat-signal-adapter.service
```

---

## 3. Değişkenler ve Parametreler

Ansible Rolü kullanılırken değişkenler `defaults/main.yml` dosyasından okunur:

| Değişken | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `autoscaling_cpu_high_threshold` | `70` | Scale-Out tetikleme eşiği (% CPU) |
| `autoscaling_cpu_low_threshold` | `20` | Scale-In tetikleme eşiği (% CPU) |
| `autoscaling_evaluation_period` | `60s` | Alarm tetiklenme bekleme süresi (`for: 60s`) |
| `autoscaling_alarm_cooldown` | `2m` | Alertmanager alarm tekrarlama aralığı (`repeat_interval`) |
| `server_group_exporter_cache_ttl` | `60` | Exporter'ın Nova API'yi sorgulama aralığı (saniye) |
| `heat_signal_adapter_port` | `9200` | Heat adaptörünün dinlediği port |
| `server_group_exporter_port` | `9102` | Metadata exporter dinleme portu |
| `libvirt_exporter_port` | `9177` | KVM libvirt exporter portu |
| `prometheus_port` | `9090` | Prometheus dinleme portu |
| `alertmanager_port` | `9093` | Alertmanager dinleme portu |
| `heat_stack_name` | `autoscaling-stack` | Sinyal gönderilecek Heat Stack adı |

---

## 4. Kurulum Yöntemleri

### 1. Yöntem: Ansible ile Otomatik Kurulum (Önerilen)

Ansible kontrol makinesinden (Deployer):

```bash
# 1. Repoyu indirin
git clone https://github.com/ogi-glk/openstack-prometheus-autoscaling.git
cd openstack-prometheus-autoscaling

# 2. İhtiyaca göre defaults/main.yml içindeki parametreleri düzenleyin

# 3. Playbook'u çalıştırın:
openstack-ansible autoscaling.yml
# Veya standart ansible ile:
ansible-playbook -i hosts autoscaling.yml
```

---

### 2. Yöntem: Bash Scripti ile Kurulum (`setup.sh`)

Ansible kullanmadan doğrudan hedef sunucu üzerinde kurmak isterseniz:

```bash
cd openstack-prometheus-autoscaling
chmod +x setup.sh
sudo bash setup.sh
```

---

### 3. Yöntem: Adım Adım Tam Manuel Kurulum (Hata Ayıklama / Özel Ortamlar)

Servisleri elle kurmak, bağımlılıkları görmek veya terminalde ön planda çalıştırmak isterseniz:

#### A) Sistem Paketlerini ve Libvirt Exporter'ı Kurun:
```bash
sudo apt update
sudo apt install -y python3-pip python3-venv python3-dev build-essential curl jq prometheus-node-exporter prometheus-libvirt-exporter
sudo systemctl enable --now prometheus-libvirt-exporter
```

#### B) Çalışma Dizinlerini Oluşturun ve Dosyaları Kopyalayın:
```bash
sudo mkdir -p /opt/openstack-autoscaling/exporters /opt/openstack-autoscaling/adapter /etc/prometheus /etc/alertmanager /var/lib/prometheus /var/lib/alertmanager

sudo cp exporters/server_group_exporter.py /opt/openstack-autoscaling/exporters/
sudo cp adapter/heat_signal_adapter.py /opt/openstack-autoscaling/adapter/
sudo cp configs/prometheus.yml /etc/prometheus/prometheus.yml
sudo cp configs/alert_rules.yml /etc/prometheus/alert_rules.yml
sudo cp configs/alertmanager.yml /etc/alertmanager/alertmanager.yml
```

#### C) Python Sanal Ortamını (venv) Hazırlayın:
```bash
sudo python3 -m venv /opt/openstack-autoscaling/venv
sudo /opt/openstack-autoscaling/venv/bin/pip install --upgrade pip
sudo /opt/openstack-autoscaling/venv/bin/pip install openstacksdk prometheus-client requests urllib3
```

#### D) Prometheus ve Alertmanager Binary'lerini İndirin:
```bash
# Prometheus:
curl -sSL https://github.com/prometheus/prometheus/releases/download/v2.51.0/prometheus-2.51.0.linux-amd64.tar.gz | tar -xz -C /tmp
sudo cp /tmp/prometheus-2.51.0.linux-amd64/prometheus /usr/local/bin/
sudo cp /tmp/prometheus-2.51.0.linux-amd64/promtool /usr/local/bin/

# Alertmanager:
curl -sSL https://github.com/prometheus/alertmanager/releases/download/v0.27.0/alertmanager-0.27.0.linux-amd64.tar.gz | tar -xz -C /tmp
sudo cp /tmp/alertmanager-0.27.0.linux-amd64/alertmanager /usr/local/bin/
sudo cp /tmp/alertmanager-0.27.0.linux-amd64/amtool /usr/local/bin/
```

#### E) OpenStack Kimlik Bilgilerini (`openrc`) Tanımlayın:
`/opt/openstack-autoscaling/openrc` dosyasını oluşturun:
```bash
cat <<EOF > /opt/openstack-autoscaling/openrc
OS_AUTH_URL=http://172.29.236.101:5000/v3
OS_PROJECT_NAME=admin
OS_USERNAME=admin
OS_PASSWORD=GIZLI_SIFRE
OS_USER_DOMAIN_NAME=Default
OS_PROJECT_DOMAIN_NAME=Default
OS_IDENTITY_API_VERSION=3
HEAT_STACK_NAME=autoscaling-stack
EOF
chmod 600 /opt/openstack-autoscaling/openrc
```

#### F) Systemd Servislerini Kurun ve Başlatın:
```bash
# Servis dosyalarını kopyalayın:
sudo cp systemd/server-group-exporter.service /etc/systemd/system/
sudo cp systemd/heat-signal-adapter.service /etc/systemd/system/

# Prometheus servis birimi:
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

# Alertmanager servis birimi:
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

# Servisleri başlatın:
sudo systemctl daemon-reload
sudo systemctl enable --now prometheus alertmanager server-group-exporter heat-signal-adapter
```

---

## 5. Test, Doğrulama ve Canlı Kanıtlar

### 1. Servis Durum Kontrolleri
```bash
systemctl is-active prometheus alertmanager prometheus-libvirt-exporter server-group-exporter heat-signal-adapter
```

### 2. Exporter Metrik Çıktısı (Canlı Çoklu Makine Eşleştirmesi):
```text
$ curl -s http://localhost:9102/metrics
# HELP openstack_instance_server_group Nova instance to Heat stack_id (server_group) mapping
# TYPE openstack_instance_server_group gauge
openstack_instance_server_group{domain="instance-00000007",instance_id="2972726a...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
openstack_instance_server_group{domain="instance-00000008",instance_id="55497eaa...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
openstack_instance_server_group{domain="instance-00000009",instance_id="1810fd03...",server_group="9b5093f6-83a5-4da2-8bae-9ca1545edd0d"} 1
```

### 3. Prometheus PromQL Sorgusu:
Prometheus arayüzünde (`:9090`) veya API üzerinden sorgulayın:
```promql
avg by (server_group) (
  rate(libvirt_domain_info_cpu_time_seconds_total[1m]) * 100
  * on(domain) group_left(server_group) 
  openstack_instance_server_group
)
```

### 4. Adaptör Canlı Günlüğü (Sıfır Müdahale ile Büyüme ve Küçülme Kanıtı):
```text
$ journalctl -u heat-signal-adapter -n 10 --no-pager
[*] Native Heat Signal Adapter listening on port :9200 for stack 'autoscaling-stack'...
[+] SUCCESS: CpuHigh -> Native Heat signal 'scaleup_policy' triggered (HTTP 200)   <-- 1. VM'den 2. VM'e Büyüme (Scale-Out)
[+] SUCCESS: CpuHigh -> Native Heat signal 'scaleup_policy' triggered (HTTP 200)   <-- 2. VM'den 3. VM'e Büyüme (Maksimum Kapasite)
[+] SUCCESS: CpuLow  -> Native Heat signal 'scaledown_policy' triggered (HTTP 200) <-- Yük bitti: 3'ten 2'ye Küçülme (Scale-In)
[+] SUCCESS: CpuLow  -> Native Heat signal 'scaledown_policy' triggered (HTTP 200) <-- Yük bitti: 2'den 1'e Küçülme (Taban Limit)
```

---

## 6. Karşılaşılan Sorunlar ve Çözümleri (Troubleshooting)

### 1. Server Group Exporter & Prometheus Zaman Aşımı (BrokenPipeError)
* **Belirti:** `journalctl -u server-group-exporter` içinde `BrokenPipeError: [Errno 32] Broken pipe` görülmesi ve Prometheus'ta hedefin `DOWN` olması.
* **Kök Neden:** Prometheus `/metrics` endpoint'ini çağırdığında kodun eşzamanlı (senkron) olarak Nova API'ye bağlanıp liste beklemesi. Nova API yanıtı birkaç saniye gecikince Prometheus 10 saniyelik zaman aşımına ulaşıp soketi kapatıyordu.
* **Çözüm:** Exporter, Nova sorgusunu bağımsız bir arka plan thread'inde her 60 saniyede bir yapacak şekilde mimarilendirildi. Prometheus scrape isteklerine hafızadaki (RAM) hazır veri `< 1ms` sürede yanıt verir.

### 2. Heat CloudFormation Port 8000 Yetki Reddi (HTTP 403 AccessDenied)
* **Belirti:** `heat_signal_adapter` loglarında `[!] signal forwarding error: HTTP Error 403: AccessDenied (User is not authorized)` hatası alınması.
* **Kök Neden:** Heat'in port 8000 AWS/CFN taklidi servisi, Keystone v3 ve Trust mimarisinde stack domain kullanıcısının HMAC imzasını reddediyordu.
* **Çözüm:** Port 8000'deki CFN imza mekanizması yerine, adaptör `openstacksdk` kütüphanesi ile doğrudan yerel Heat REST API'sine (`port 8004: /stacks/{name}/{id}/resources/{policy}/signal`) admin yetkisiyle bağlandı.

### 3. Systemd `EnvironmentFile` & Windows UTF-8 BOM / CRLF Karakter Sorunu
* **Belirti:** Servisler çalışırken `# Error connecting to OpenStack: Auth plugin requires parameters: auth_url` hatası vermesi.
* **Kök Neden:** Windows ortamında düzenlenen `openrc` dosyasının başına eklenen 3 baytlık UTF-8 BOM (`\xef\xbb\xbf`) karakteri, systemd tarafından `\ufeffOS_AUTH_URL` olarak algılanır ve geçersiz bulunup yoksayılır.
* **Çözüm:** `tasks/prerequisites.yml` içine `tr -d '\r'` ve `sed` ile BOM temizleme adımı eklendi.

### 4. Systemd URL `%` Specifier Çakışması
* **Belirti:** `Failed to resolve specifiers in HEAT_SCALEUP_URL=... ignoring: Invalid slot` uyarısı.
* **Kök Neden:** Systemd unit dosyaları içinde `%3A`, `%2F` gibi URL kodlu ifadeleri systemd değişken kodu (specifier) sanarak satırı iptal eder.
* **Çözüm:** Değişkenler doğrudan unit dosyasının `Environment=` satırına değil, systemd'nin `%` karakterini yorumlamadığı `EnvironmentFile=/opt/openstack-autoscaling/openrc` dosyasına yazıldı.

### 5. Prometheus Alert Rules & Ansible Jinja2 Sözdizimi Çakışması
* **Belirti:** Playbook çalıştırılırken `AnsibleError: template error while templating string: unexpected char '$'` hatası.
* **Kök Neden:** Prometheus alarm kurallarındaki `{{ $labels.server_group }}` ifadesi ile Ansible'ın Jinja2 motorunun çakışması.
* **Çözüm:** `alert_rules.yml.j2` şablonundaki Prometheus değişkenleri `{% raw %}{{ $labels.server_group }}{% endraw %}` bloklarına alındı.

---

## Lisans
Bu proje [Apache-2.0](LICENSE) lisansı ile sunulmaktadır.