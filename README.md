# OpenStack Prometheus Autoscaling Layer

[Türkçe](README.md) | [English](README.en.md)

---

OpenStack ortamlarında Ceilometer, Gnocchi ve Aodh servisleri yerine **Prometheus + Alertmanager + libvirt-exporter** mimarisi kullanılarak CPU tabanlı otomatik ölçekleme (AutoScaling) yapılmasını sağlayan kurumsal entegrasyon katmanıdır. 

Bu proje hem **doğrudan bash scripti ile elle (`setup.sh`)**, hem de **OpenStack-Ansible ile %100 otomatik dinamik bir Ansible Rolü (`autoscaling.yml`)** olarak kurulup çalıştırılabilir.

---

## 🏛️ Mimari ve Çalışma Mantığı

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

### Katmanlar:
1. **libvirt-exporter (:9177):** KVM/libvirt üzerinden sanal makinelerin anlık ham CPU kullanım sürelerini toplar.
2. **server-group-exporter (:9102):** Nova API'den VM isimleri ile Heat Stack ID (`metering.server_group`) eşleşmesini çeker. Nova API yükünü minimize etmek için 60 saniyelik TTL önbellek (Cache) kullanır.
3. **Prometheus (:9090):** İki veri kaynağını `domain` etiketi üzerinden PromQL vektör eşlemesi (`vector matching`) ile birleştirir ve ortalama CPU'yu hesaplar.
4. **Alertmanager (:9093):** Eşik aşıldığında alarmı yerel adaptöre iletir.
5. **heat_signal_adapter (:9200):** Gelen webhook alarmını karşılar, Keystone'dan geçerli token alarak Heat'in ilgili ölçekleme sinyal URL'ine kimlik doğrulamalı POST isteği gönderir.

---

## 📂 Proje ve Ansible Rol Yapısı

Proje, hem bağımsız bir Python/Shell projesi hem de dünya standartlarında bir **Ansible Rolü** çekmecesi şeklinde organize edilmiştir:

```text
openstack-prometheus-autoscaling/
├── autoscaling.yml                    # Tek tıkla kurulum sağlayan ana Ansible Playbook'u
├── setup.sh                           # Manuel/Standalone kurulum betiği
├── requirements.txt                   # Python bağımlılıkları (openstacksdk, prometheus-client)
│
├── defaults/                          # [Ansible Rolü] Dışarıdan yönetilebilir değişkenler
│   └── main.yml                       # (CPU eşikleri, portlar, zamanlama parametreleri)
│
├── tasks/                             # [Ansible Rolü] Otomasyon kurulum adımları
│   ├── main.yml                       # Ana görev orkestrasyonu
│   ├── prerequisites.yml              # Dizinler, sistem araçları ve Python venv kurulumu
│   ├── prometheus.yml                 # Prometheus ve Alertmanager kurulum/yapılandırması
│   └── adapter.yml                    # Adaptör, exporter ve systemd servislerinin devreye alınması
│
├── templates/                         # [Ansible Rolü] Dinamik Jinja2 (.j2) şablonları
│   ├── openrc.j2                      # Dinamik Keystone kimlik dosyası şablonu
│   ├── alert_rules.yml.j2             # Eşik değerlerine göre derlenen PromQL kuralları
│   ├── alertmanager.yml.j2            # Dinamik webhook yönlendirme şablonu
│   ├── prometheus.yml.j2              # Envanterdeki hipervizörleri toplayan metrik şablonu
│   ├── heat-signal-adapter.service.j2 # Otomatik Webhook URL enjeksiyonlu systemd servisi
│   └── server-group-exporter.service.j2
│
├── handlers/                          # [Ansible Rolü] Servis yeniden başlatma tetikleyicileri
│   └── main.yml
│
├── meta/                              # [Ansible Rolü] Galaxy uyumluluk kimlik kartı
│   └── main.yml
│
├── exporters/                         # Saf Python Kaynak Kodları
│   └── server_group_exporter.py       # Nova Metadata -> Prometheus exporter (:9102)
│
├── adapter/                           # Saf Python Kaynak Kodları
│   └── heat_signal_adapter.py         # Alertmanager -> Heat API token adaptörü (:9200)
│
├── configs/                           # Standart / Örnek Konfigürasyon Dosyaları (Manuel Kurulum İçin)
│   ├── prometheus.yml
│   ├── alert_rules.yml
│   └── alertmanager.yml
│
└── systemd/                           # Standart Systemd Servisleri (Manuel Kurulum İçin)
    ├── server-group-exporter.service
    └── heat-signal-adapter.service
```

---

## ⚙️ Dinamik Değişkenler ve Jinja2 Şablonlama Matrisi

Ansible Rolü kullanılırken hiçbir IP adresi, şifre veya Webhook URL'si elle girilmez. Jinja2 şablonları bunları ortamdan dinamik olarak üretir:

| Parametre / Alan | Manuel Kurulumdaki Durum | Ansible Rolündeki Dinamik Jinja2 Karşılığı | Açıklama |
| :--- | :--- | :--- | :--- |
| **`OS_AUTH_URL`** | Elle `openrc` içine yazılır | `https://{{ external_lb_vip_address }}:5000/v3` | Keystone uç noktası otomatik bağlanır |
| **`OS_PASSWORD`** | Elle şifre girilir | `{{ keystone_auth_admin_password }}` | Şifre kasasından güvenle enjekte edilir |
| **`HEAT_SCALEUP_URL`** | Terminalden kopyalanıp yapıştırılır | `heat_scaleup_url` (Heat API'den otomatik keşif) | Heat Stack'ten URL dinamik olarak okunur |
| **`CPU_HIGH_LIMIT`** | Statik kural (%70) | `{{ autoscaling_cpu_high_threshold }}` | `defaults/main.yml` üzerinden tek satırla yönetilir |
| **`Prometheus Targets`**| Statik localhost | `{% for host in groups['compute_hosts'] %}` | Kümedeki tüm hipervizörler otomatik scrape edilir |

---

## 🚀 Kurulum Yöntemleri

### Yöntem 1: Ansible Rolü ile Otomatik Kurulum (Önerilen / Kurumsal)

OpenStack-Ansible veya standart Ansible kontrol makinenizden hedef sunuculara tek komutla dağıtın:

```bash
# 1. Repoyu klonlayın
git clone https://github.com/ogi-glk/openstack-prometheus-autoscaling.git

# 2. İsteğe bağlı olarak eşik değerlerini defaults/main.yml içinden özelleştirin:
# autoscaling_cpu_high_threshold: 75
# autoscaling_cpu_low_threshold: 15

# 3. Playbook'u çalıştırın:
openstack-ansible autoscaling.yml
# Veya standart Ansible için:
ansible-playbook -i your_inventory autoscaling.yml
```
> **Avantajı:** Dizinleri oluşturur, izole Python `venv` kurar, Heat API'ye gidip Webhook URL'lerini otomatik keşfeder, systemd servislerini devreye alır ve Prometheus'u başlatır. Sıfır elle müdahale!

---

### Yöntem 2: Standart Bash Scripti ile Kurulum (`setup.sh`)

Ansible kullanmadan doğrudan hedef sunucu üzerinde kurmak isterseniz:

1. Heat sinyal URL'lerinizi öğrenip ortam değişkeni olarak tanımlayın:
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

### Yöntem 3: Adım Adım Manuel Kurulum (Hata Ayıklama / Debugging)

Sistemi adım adım elle kurmak veya servisleri arka planda değil terminalde test etmek isterseniz:

#### A) Dizinleri Oluşturun ve Dosyaları Kopyalayın:
```bash
sudo mkdir -p /opt/openstack-bridge/exporters /opt/openstack-bridge/adapter /etc/prometheus /etc/alertmanager

sudo cp -r exporters/* /opt/openstack-bridge/exporters/
sudo cp -r adapter/* /opt/openstack-bridge/adapter/
sudo cp configs/prometheus.yml /etc/prometheus/prometheus.yml
sudo cp configs/alert_rules.yml /etc/prometheus/alert_rules.yml
sudo cp configs/alertmanager.yml /etc/alertmanager/alertmanager.yml
```

#### B) Python Sanal Ortamını (venv) Hazırlayın:
```bash
sudo python3 -m venv /opt/openstack-bridge/venv
sudo /opt/openstack-bridge/venv/bin/pip install --upgrade pip
sudo /opt/openstack-bridge/venv/bin/pip install -r requirements.txt
```

#### C) Servisleri Terminalde Canlı Test Edin:
```bash
# 1. Terminal: Exporter'ı çalıştırın
source /root/openrc
/opt/openstack-bridge/venv/bin/python3 /opt/openstack-bridge/exporters/server_group_exporter.py

# 2. Terminal: Adaptörü çalıştırın
source /root/openrc
export HEAT_SCALEUP_URL="https://<HEAT_IP>:8004/v1/.../scaleup_policy/signal"
/opt/openstack-bridge/venv/bin/python3 /opt/openstack-bridge/adapter/heat_signal_adapter.py
```

---

## 🧪 Doğrulama ve Test Adımları

### 1. Servis Durum Kontrolleri
```bash
systemctl status server-group-exporter
systemctl status heat-signal-adapter

# Exporter metriğini test etme (Stack ID eşleşmeleri dönmelidir):
curl -s http://localhost:9102/metrics
```

### 2. Prometheus PromQL Vektör Eşleme Sorgusu
Prometheus Web Arayüzünde (`:9090`) aşağıdaki sorguyu çalıştırın:
```promql
avg by (server_group) (
  rate(libvirt_domain_info_cpu_time_seconds_total[5m]) 
  * on(domain) group_left(server_group) 
  openstack_instance_server_group
)
```

### 3. Canlı CPU Yük (Autoscaling) Testi
1. Stack altındaki bir sanal makineye SSH ile bağlanın ve yapay CPU yükü oluşturun:
   ```bash
   cat /dev/zero > /dev/null &
   ```
2. **Scale-Out İzleme:** CPU kullanımı `%70` sınırını aştığında Alertmanager alarmı `:9200` portundaki adaptöre iletir. Adaptör Keystone token'ı alarak Heat API'ye sinyal atar ve **+1 yeni sanal makine başlatılır.**
3. Yükü durdurun:
   ```bash
   killall cat
   ```
4. **Scale-In İzleme:** CPU kullanımı `%20` altına düştüğünde `CpuLow` alarmı tetiklenir ve Heat fazla sanal makineyi otomatik olarak kapatıp siler.

---

## 📄 Lisans
Bu proje [Apache-2.0](LICENSE) lisansı ile sunulmaktadır.
