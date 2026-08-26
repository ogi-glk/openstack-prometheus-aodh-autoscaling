# OpenStack Prometheus Autoscaling Layer

[Türkçe](README.md) | [English](README.en.md)

---

OpenStack ortamlarında Ceilometer, Gnocchi ve Aodh servisleri yerine **Prometheus + Alertmanager + libvirt-exporter** mimarisi kullanılarak CPU tabanlı otomatik ölçekleme (AutoScaling) yapılmasını sağlayan entegrasyon katmanıdır. 

Proje iki farklı şekilde kurulabilir:
1. **Ansible Rolü ile:** `openstack-ansible` veya standart `ansible-playbook` üzerinden envanterdeki sunuculara otomatik dağıtım.
2. **Standalone / Manuel:** Hedef sunucu üzerinde `setup.sh` betiği ile veya adım adım komutlarla kurulum.

---

## Mimari ve Çalışma Mantığı

```text
┌─────────────────┐       ┌────────────────────────┐
│ libvirt-exporter│ :9177 │ server-group-exporter  │ :9102 (60s TTL Cache)
│ (Ham CPU verisi)│       │ (Nova Metadata/StackID)│
└────────┬────────┘       └───────────┬────────────┘
         │                            │
         └──────────────┬─────────────┘
                        ▼
             ┌──────────────────────┐
             │      Prometheus      │ :9090 (PromQL Vector Matching)
             │  (Ortalama CPU > %70)│
             └───────────┬──────────┘
                        ▼
             ┌──────────────────────┐
             │     Alertmanager     │ :9093 (Webhook Yönlendirme)
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

### Bileşenler:
1. **libvirt-exporter (:9177):** KVM/libvirt üzerinden sanal makinelerin ham CPU kullanım sürelerini toplar.
2. **server-group-exporter (:9102):** Nova API'den VM isimleri ile Heat Stack ID (`metering.server_group`) eşleşmesini çeker. Nova API yükünü azaltmak için 60 saniyelik TTL önbellek (Cache) kullanır.
3. **Prometheus (:9090):** İki veri kaynağını `domain` etiketi üzerinden PromQL vektör eşlemesi (`vector matching`) ile birleştirir ve ortalama CPU'yu hesaplar.
4. **Alertmanager (:9093):** Eşik aşıldığında alarmı yerel adaptöre iletir.
5. **heat_signal_adapter (:9200):** Gelen webhook alarmını karşılar, Keystone'dan geçerli token alarak Heat'in ilgili ölçekleme sinyal URL'ine kimlik doğrulamalı POST isteği gönderir.

---

## Proje Dizini

```text
openstack-prometheus-autoscaling/
├── autoscaling.yml                    # Dağıtım Ansible Playbook'u
├── setup.sh                           # Standalone kurulum betiği
├── requirements.txt                   # Python paket bağımlılıkları
│
├── defaults/                          # Rol değişkenleri
│   └── main.yml                       # Eşik değerleri, portlar ve parametreler
│
├── tasks/                             # Ansible görevleri
│   ├── main.yml                       # Ana görev çağırma sırası
│   ├── prerequisites.yml              # Dizinler, sistem paketleri ve Python venv
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
│   └── heat_signal_adapter.py         # Alertmanager -> Heat API token adaptörü (:9200)
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

## Değişkenler ve Parametreler

Ansible Rolü kullanılırken değişkenler `defaults/main.yml` dosyasından okunur:

| Değişken | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `autoscaling_cpu_high_threshold` | `70` | Scale-Out tetikleme eşiği (% CPU) |
| `autoscaling_cpu_low_threshold` | `20` | Scale-In tetikleme eşiği (% CPU) |
| `autoscaling_evaluation_period` | `60s` | Alarm tetiklenme bekleme süresi |
| `heat_signal_adapter_port` | `9200` | Heat adaptörünün dinlediği port |
| `server_group_exporter_port` | `9102` | Metadata exporter dinleme portu |
| `heat_stack_name` | `autoscaling-stack` | Sinyal URL'lerinin otomatik çekileceği Heat Stack adı |

---

## Kurulum Yöntemleri

### 1. Yöntem: Ansible ile Kurulum

Ansible kontrol makinesinden:

```bash
# 1. Repoyu indirin
git clone https://github.com/ogi-glk/openstack-prometheus-autoscaling.git

# 2. İhtiyaca göre defaults/main.yml içindeki parametreleri düzenleyin

# 3. Playbook'u çalıştırın:
openstack-ansible autoscaling.yml
# Veya:
ansible-playbook -i envanter_dosyasi autoscaling.yml
```

---

### 2. Yöntem: Bash Scripti ile Kurulum (`setup.sh`)

Ansible kullanmadan doğrudan hedef sunucu üzerinde kurmak isterseniz:

1. Heat sinyal URL'lerini ortam değişkeni olarak tanımlayın:
   ```bash
   export HEAT_SCALEUP_URL=$(openstack stack output show <STACK_ADI> scaleup_url -f value -c output_value)
   export HEAT_SCALEDOWN_URL=$(openstack stack output show <STACK_ADI> scaledown_url -f value -c output_value)
   ```
2. Kurulum betiğini çalıştırın:
   ```bash
   cd openstack-prometheus-autoscaling
   sudo bash setup.sh
   ```

---

### 3. Yöntem: Manuel Kurulum (Hata Ayıklama / Test)

Servisleri elle kurmak veya test amaçlı terminalde ön planda çalıştırmak isterseniz:

#### A) Dizinleri Oluşturun ve Dosyaları Kopyalayın:
```bash
sudo mkdir -p /opt/openstack-bridge/exporters /opt/openstack-bridge/adapter /etc/prometheus /etc/alertmanager

sudo cp -r exporters/* /opt/openstack-bridge/exporters/
sudo cp -r adapter/* /opt/openstack-bridge/adapter/
sudo cp configs/prometheus.yml /etc/prometheus/prometheus.yml
sudo cp configs/alert_rules.yml /etc/prometheus/alert_rules.yml
sudo cp configs/alertmanager.yml /etc/alertmanager/alertmanager.yml
```

#### B) Python Sanal Ortamını Hazırlayın:
```bash
sudo python3 -m venv /opt/openstack-bridge/venv
sudo /opt/openstack-bridge/venv/bin/pip install --upgrade pip
sudo /opt/openstack-bridge/venv/bin/pip install -r requirements.txt
```

#### C) Terminalde Ön Planda Çalıştırın:
```bash
# 1. Terminal: Exporter
source /root/openrc
/opt/openstack-bridge/venv/bin/python3 /opt/openstack-bridge/exporters/server_group_exporter.py

# 2. Terminal: Adaptör
source /root/openrc
export HEAT_SCALEUP_URL="https://<HEAT_IP>:8004/v1/.../scaleup_policy/signal"
/opt/openstack-bridge/venv/bin/python3 /opt/openstack-bridge/adapter/heat_signal_adapter.py
```

---

## Test ve Doğrulama

### 1. Servis Durum Kontrolleri
```bash
systemctl status server-group-exporter
systemctl status heat-signal-adapter

# Exporter metriğini test etme:
curl -s http://localhost:9102/metrics
```

### 2. Prometheus PromQL Sorgusu
Prometheus arayüzünde (`:9090`) aşağıdaki sorguyu çalıştırın:
```promql
avg by (server_group) (
  rate(libvirt_domain_info_cpu_time_seconds_total[5m]) 
  * on(domain) group_left(server_group) 
  openstack_instance_server_group
)
```

### 3. CPU Yük Testi
1. Stack altındaki bir sanal makinede CPU yükü başlatın:
   ```bash
   cat /dev/zero > /dev/null &
   ```
2. **Scale-Out:** Ortalama CPU kullanımı `%70` sınırını aştığında Alertmanager adaptöre webhook gönderir, adaptör Heat API'ye sinyal iletir ve yeni VM açılır.
3. Yükü durdurun:
   ```bash
   killall cat
   ```
4. **Scale-In:** CPU kullanımı `%20` altına düştüğünde `CpuLow` alarmı tetiklenir ve Heat fazla makineyi siler.

---


---

## Karşılaşılan Sorunlar ve Çözümleri (Troubleshooting)

### 1. Systemd `EnvironmentFile` & Windows UTF-8 BOM Karakter Sorunu
* **Belirti:** `server-group-exporter` veya `heat-signal-adapter` servisleri çalışırken `# Error connecting to OpenStack: Auth plugin requires parameters which were not given: auth_url` hatası vermesi.
* **Kök Neden:** Windows ortamında hazırlanan veya PowerShell ile düzenlenen konfigürasyon dosyalarının (`openrc`) başına UTF-8 BOM (`\xef\xbb\xbf`) ve Windows satır sonları (`\r\n`) eklenebilir. Linux `systemd` servisi `EnvironmentFile` direktifini işlerken ilk satırdaki değişken adını `\ufeffOS_AUTH_URL` olarak algılar ve geçersiz karakter içerdiği için bu satırı yok sayar. İkinci satırdan itibaren (`OS_USERNAME`) okuduğu için `OS_AUTH_URL` process ortamına aktarılamaz.
* **Çözüm:** 
  1. `openrc.j2` şablonu doğrudan Unix satır sonu (`\n`) ve kesinlikle BOM'suz (UTF-8 without BOM) formatta tutulmalıdır.
  2. Dosyada `export ` anahtar kelimesi kullanılmamalı, doğrudan `KEY=VALUE` biçimi uygulanmalıdır.
  3. Hedef sunucuda temizlik komutu:
     ```bash
     sed -i '1s/^\xef\xbb\xbf//; s/\r$//' /opt/openstack-autoscaling/openrc
     systemctl restart server-group-exporter heat-signal-adapter
     ```

### 2. Prometheus Alert Rules & Ansible Jinja2 Sözdizimi Çakışması
* **Belirti:** Playbook çalıştırılırken `AnsibleError: template error while templating string: unexpected char '$'` hatası alınması.
* **Kök Neden:** Prometheus alarm kuralları etiket ve değerler için `{{ $labels.server_group }}` ve `{{ $value }}` biçimini kullanır. Ansible'ın Jinja2 şablon motoru da değişken interpolation için `{{ ... }}` sözdizimini kullandığından, `$` karakterini geçersiz değişken olarak değerlendirip çöker.
* **Çözüm:** `alert_rules.yml.j2` şablonundaki Prometheus değişkenleri `{% raw %}{{ $labels.server_group }}{% endraw %}` ve `{% raw %}{{ $value }}{% endraw %}` şeklinde raw bloklarına alınmıştır.

### 3. OpenStack Python SDK Açık Parametre Geçişi
* **Belirti:** `openstack.connect()` çağrısının kimlik parametrelerini bulamaması.
* **Kök Neden:** Modern `openstacksdk` kütüphanesi parametresiz çağrıldığında varsayılan olarak `clouds.yaml` arar. Ortam değişkenlerinden kimlik doğrulaması için parametrelerin açıkça iletilmesi gerekir.
* **Çözüm:** `server_group_exporter.py` içinde `openstack.connect()` çağrısına ortam değişkenleri doğrudan aktarılmıştır:
  ```python
  conn = openstack.connect(
      auth_url=os.environ.get("OS_AUTH_URL"),
      project_name=os.environ.get("OS_PROJECT_NAME", "admin"),
      username=os.environ.get("OS_USERNAME", "admin"),
      password=os.environ.get("OS_PASSWORD", ""),
      user_domain_name=os.environ.get("OS_USER_DOMAIN_NAME", "Default"),
      project_domain_name=os.environ.get("OS_PROJECT_DOMAIN_NAME", "Default"),
  )
  ```
## Lisans
Bu proje [Apache-2.0](LICENSE) lisansı ile sunulmaktadır.
