# OpenStack Prometheus Autoscaling Layer

[Türkçe](README.md) | [English](README.en.md)

---

OpenStack ortamlarında Ceilometer, Gnocchi ve Aodh servisleri yerine **Prometheus + Alertmanager + libvirt-exporter** mimarisi kullanılarak CPU tabanlı otomatik ölçekleme (AutoScaling) yapılmasını sağlayan entegrasyon katmanıdır.

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

1. **libvirt-exporter (:9177):** KVM/libvirt üzerinden sanal makinelerin ham CPU kullanım sürelerini toplar.
2. **server-group-exporter (:9102):** Nova API'den VM isimleri ile Heat Stack ID (`metering.server_group`) eşleşmesini çeker. Nova API yükünü minimize etmek için 60 saniyelik TTL önbellek (Cache) kullanır.
3. **Prometheus (:9090):** İki veri kaynağını `domain` etiketi üzerinden PromQL vektör eşlemesi (`vector matching`) ile birleştirir ve ortalama CPU'yu hesaplar.
4. **Alertmanager (:9093):** Eşik aşıldığında alarmı yerel adaptöre iletir.
5. **heat_signal_adapter (:9200):** Gelen webhook alarmını karşılar, Keystone'dan geçerli token alarak Heat'in ilgili ölçekleme sinyal URL'ine kimlik doğrulamalı POST isteği gönderir.

---

## Proje Yapısı

```text
openstack-prometheus-autoscaling/
├── README.md                      # Proje dokümantasyonu
├── setup.sh                       # Kurulum ve servis aktivasyon betiği
├── requirements.txt               # Python bağımlılıkları
│
├── exporters/
│   └── server_group_exporter.py   # Nova Metadata -> Prometheus exporter (:9102)
│
├── adapter/
│   └── heat_signal_adapter.py     # Alertmanager -> Heat API token adaptörü (:9200)
│
├── configs/
│   ├── prometheus.yml             # Prometheus scrape ve alerting yapılandırması
│   ├── alert_rules.yml            # CpuHigh ve CpuLow PromQL kuralları
│   └── alertmanager.yml           # Webhook yönlendirme kuralları
│
└── systemd/
    ├── server-group-exporter.service
    └── heat-signal-adapter.service
```

---

## Ön Koşullar

Hedef sistemde bulunması gereken temel bileşenler:
* Çalışan bir OpenStack ortamı (Nova, Heat, Keystone, KVM/libvirt)
* Python 3 ve `python3-venv` paketi
* Prometheus & Alertmanager servisleri
* `prometheus-libvirt-exporter` (Varsayılan port: 9177)
* OpenStack kimlik bilgileri dosyası (`/root/openrc`)

---

## Kurulum ve Yapılandırma

### 1. Sinyal URL'lerinin Tanımlanması
Heat şablonunuzun ürettiği sinyal URL'lerini öğrenin:
```bash
openstack stack output show <STACK_ADI> scaleup_url
openstack stack output show <STACK_ADI> scaledown_url
```

Bu URL'leri `adapter/heat_signal_adapter.py` dosyasına yazabilir veya ortam değişkeni olarak tanımlayabilirsiniz:
```bash
export HEAT_SCALEUP_URL="https://<OPENSTACK_IP>:8004/v1/<PROJECT_ID>/stacks/<STACK>/<ID>/resources/scaleup_policy/signal"
export HEAT_SCALEDOWN_URL="https://<OPENSTACK_IP>:8004/v1/<PROJECT_ID>/stacks/<STACK>/<ID>/resources/scaledown_policy/signal"
```

### 2. Otomatik Kurulum
Projeyi hedef sunucuya kopyalayıp kurulum betiğini çalıştırın:
```bash
cd openstack-prometheus-autoscaling
sudo bash setup.sh
```

Kurulum betiği dosyaları `/opt/openstack-bridge` dizinine yerleştirir, Python sanal ortamını hazırlar ve systemd servislerini devreye alır.

### 3. Manuel Kurulum (Alternatif / Adım Adım)
Otomatik betik kullanmak istemiyorsanız veya hata alırsanız adımları elle uygulayabilirsiniz:

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

#### C) Systemd Servislerini Kurun ve Başlatın:
```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now server-group-exporter
sudo systemctl enable --now heat-signal-adapter

# Prometheus ve Alertmanager konfigürasyonlarını yeniden yükleyin
sudo systemctl restart prometheus alertmanager
```

> **İpucu (Hata Ayıklama / Servissiz Çalıştırma):** Systemd servisi kurmadan doğrudan terminal üzerinden test etmek isterseniz:
> ```bash
> source /root/openrc
> /opt/openstack-bridge/venv/bin/python3 /opt/openstack-bridge/exporters/server_group_exporter.py
> # Farklı bir terminalde:
> /opt/openstack-bridge/venv/bin/python3 /opt/openstack-bridge/adapter/heat_signal_adapter.py
> ```

---

## Doğrulama ve Test

### Servis ve Metrik Kontrolleri
```bash
# Servis durumlarını kontrol etme
systemctl status server-group-exporter
systemctl status heat-signal-adapter

# Exporter metriğini test etme (Stack ID eşleşmeleri listelenmelidir)
curl -s http://localhost:9102/metrics
```

### Prometheus PromQL Birleştirme (Join) Testi
Prometheus web arayüzünden (`:9090`) veya API üzerinden sorguyu çalıştırın:
```promql
avg by (server_group) (
  rate(libvirt_domain_info_cpu_time_seconds_total[5m]) 
  * on(domain) group_left(server_group) 
  openstack_instance_server_group
)
```
> **Beklenen Değer:** Stack ID bazında normalize edilmiş ortalama CPU oranı (%0.00 - %1.00 arası).

### Ölçekleme (Stres) Testi
1. Stack altındaki bir sanal makineye bağlanın ve CPU yükü oluşturun:
   ```bash
   cat /dev/zero > /dev/null &
   ```
2. **Scale-Out İzleme:** Ortalama CPU %70 üzerine çıktığında `CpuHigh` alarmı tetiklenir, adaptör Heat'e sinyal gönderir ve yeni bir VM başlatılır.
3. Yükü sonlandırın:
   ```bash
   killall cat
   ```
4. **Scale-In İzleme:** CPU kullanımı %20 altına düştüğünde `CpuLow` alarmı tetiklenir ve Heat tarafından fazla VM otomatik olarak silinir.

---