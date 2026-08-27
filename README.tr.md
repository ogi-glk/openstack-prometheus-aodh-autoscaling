# OpenStack Prometheus + Aodh Autoscaling Katmanı

[English](README.md) | [Türkçe](README.tr.md)

---

Bu proje, OpenStack ortamlarında Ceilometer ve Gnocchi telemetri servislerine ihtiyaç duymadan, doğrudan **Prometheus** ve **OpenStack Aodh (`OS::Aodh::PrometheusAlarm`)** entegrasyonu üzerinden CPU tabanlı dinamik yatay otomatik ölçekleme (AutoScaling) sağlar.

Tasarım, harici bir bildirim adaptörü veya Alertmanager webhook köprüsü gerektirmez. Alarm tanımları doğrudan Heat Orchestration Template (HOT YAML) içerisine tanımlanır. Aodh Evaluator servisi, Prometheus PromQL API'sini periyodik olarak sorgular; eşik değeri aşıldığında Aodh Notifier servisi kriptografik HMAC imzalı `alarm_url` üzerinden Heat CloudFormation API'sini tetikleyerek AutoScalingGroup kapasitesini günceller.

---

## 1. Mimari ve Veri Akışı

```text
+-----------------------+         +-------------------------------+
| libvirt-exporter      | :9177   | server-group-exporter         | :9102
| KVM Domain CPU Süresi |         | Nova Metadata -> Stack ID     |
| (instance-0000000X)   |         | (60s TTL Bellek Önbelleği)    |
+-----------+-----------+         +---------------+---------------+
            |                                     |
            +------------------+------------------+
                               |
                               v
                   +----------------------+
                   |   Prometheus TSDB    | :9090
                   |  (Toplama ve Depolama|
                   +-----------+----------+
                               ^
                               | HTTP GET /api/v1/query (Her 60s)
                   +-----------+----------+
                   |    OpenStack Aodh    |
                   | * aodh-evaluator     |
                   | * aodh-notifier      |
                   +-----------+----------+
                               | HTTP POST (HMAC-SHA256 İmzalı alarm_url)
                               v
                   +----------------------+
                   |   Heat API (CFN)     | :8000
                   |   Heat Engine        |
                   | AutoScalingGroup     |
                   | (Scale-Out/Scale-In) |
                   +----------------------+
```

---

## 2. Bileşen Analizi ve Sorumlulukları

### A) `libvirt-exporter` (Port: 9177)
* Standart `prometheus-libvirt-exporter` paketidir.
* KVM hipervizöründen sanal makinelerin çekirdek seviyesindeki CPU kullanım sürelerini saniye cinsinden metrik olarak üretir (`libvirt_domain_info_cpu_time_seconds_total`).
* OpenStack API bağımlılığı bulunmaz; yalnızca yerel libvirt domain adlarını (örneğin `instance-0000000d`) izler.

### B) `server_group_exporter` (Port: 9102)
* KVM domain adları ile OpenStack Heat Stack ID (`metering.server_group`) arasındaki ilişkiyi kurar.
* Nova API üzerinden sanal makineleri ait oldukları Heat Stack ID ile eşleştirir ve Prometheus formatında etiketli metrik sunar (`openstack_instance_server_group{domain="...", server_group="..."}`).
* 60 saniyelik dahili TTL önbellekleme mekanizması sayesinde Nova API üzerindeki sorgu yükünü minimize eder ve Prometheus kazımalarına 1 ms altında yanıt verir.

### C) Prometheus Sunucusu (Port: 9090)
* Saf zaman serisi veritabanı (TSDB) ve PromQL sorgu motoru olarak görev yapar.
* `:9177` ve `:9102` uç noktalarını kazıyarak verileri birleştirir.
* Sistemde Alertmanager bulunmaz; sorgu değerlendirme süreci Aodh tarafından yürütülür.

### D) OpenStack Aodh (`OS::Aodh::PrometheusAlarm`)
* `aodh-evaluator`, Heat şablonunda tanımlanan PromQL sorgusunu yapılandırılan aralıklarla (varsayılan: 60 saniye) Prometheus'a gönderir.
* Belirlenen eşik değer aşıldığında (Scale-Out veya Scale-In), `aodh-notifier` servisi Heat'in önceden imzalanmış `alarm_url` adresine HTTP POST isteği gönderir.
* `repeat_actions: true` parametresi ile alarm durumu sürdüğü müddetçe her periyotta sinyal iletilmeye devam eder.

---

## 3. Dinamik Stack İzolasyonu ve PromQL Enjeksiyonu

Mimaride operatör veya kullanıcı tarafından manuel Stack ID tanımlaması yapılmaz. Heat, şablon işleme esnasında çalışma zamanı Stack ID bilgisini (`OS::stack_id`) `str_replace` fonksiyonu ile PromQL sorgusuna dinamik olarak enjekte eder:

```yaml
cpu_alarm_high:
  type: OS::Aodh::PrometheusAlarm
  properties:
    repeat_actions: true
    description: "Stack ortalama CPU esigi asildiginda genisletme tetikle"
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

Bu yapılandırma ile:
1. Her Heat Stack'i yalnızca kendi altındaki sanal makinelerin metriklerini hesaplar.
2. Eşzamanlı çalışan farklı Stack'ler arasında tam metrik ve tetikleme izolasyonu sağlanır.
3. `alarm_url` kullanımı, OpenStack token süresi kısıtlamalarına takılmaksızın HMAC-SHA256 doğrulaması ile güvenli sinyal iletimi sağlar.

---

## 4. Dizin ve Dosya Yapısı

```text
openstack-prometheus-aodh-autoscaling/
├── aodh-autoscaling.yml               # Ansible dağıtım playbook'u
├── setup.sh                           # Bağımsız Bash kurulum betiği
├── requirements.txt                   # Python paket bağımlılıkları
│
├── defaults/
│   └── main.yml                       # Varsayılan port ve konfigürasyon değişkenleri
│
├── tasks/
│   ├── main.yml                       # Görev koordinasyon dosyası
│   ├── prerequisites.yml              # Bağımlılık paketleri ve dizin hazırlığı
│   ├── prometheus.yml                 # Standalone Prometheus kurulum adımları
│   ├── exporter.yml                   # server_group_exporter kurulumu ve servisi
│   └── aodh.yml                       # Aodh Prometheus ve Heat entegrasyon ayarları
│
├── templates/
│   ├── openrc.j2                      # Keystone kimlik doğrulama şablonu
│   ├── prometheus.yml.j2              # Prometheus kazıma yapılandırması
│   └── server-group-exporter.service.j2
│
├── handlers/
│   └── main.yml                       # Servis yeniden başlatma tetikleyicileri
│
├── exporters/
│   └── server_group_exporter.py       # Nova Metadata -> Prometheus etiketleyicisi
│
├── configs/
│   └── prometheus.yml                 # Örnek Prometheus konfigürasyonu
│
└── templates_heat/
    └── heat_template_aodh.yaml        # OS::Aodh::PrometheusAlarm içeren hazır HOT şablonu
```

---

## 5. Kurulum Yöntemleri

### Yöntem 1: Ansible Rolü ile Dağıtım (Önerilen)

OpenStack-Ansible veya bağımsız bir Ansible kontrol makinesinden:

```bash
git clone https://github.com/ogi-glk/openstack-prometheus-aodh-autoscaling.git
cd openstack-prometheus-aodh-autoscaling

openstack-ansible aodh-autoscaling.yml
# Veya:
ansible-playbook -i hosts aodh-autoscaling.yml
```

### Yöntem 2: Bağımsız Betik ile Dağıtım (`setup.sh`)

Hedef ana makine (Compute/Controller) üzerinde doğrudan kurulum için:

```bash
cd openstack-prometheus-aodh-autoscaling
chmod +x setup.sh
sudo bash setup.sh
```

---

## 6. Servis Konfigürasyonu ve Sistem Uyumlulukları

Kurulum sırasında aşağıdaki sistem ayarları otomatik olarak gerçekleştirilir:

1. **Aodh İstemci Konfigürasyonu (`/etc/openstack/prometheus.yaml`):**
   `observabilityclient` kütüphanesinin Prometheus sunucusuna erişebilmesi için gerekli host ve port tanımları oluşturulur:
   ```yaml
   host: 127.0.0.1
   port: 9090
   ```

2. **Aodh Servis Konfigürasyonu (`/etc/aodh/aodh.conf`):**
   ```ini
   [prometheus]
   host = 127.0.0.1
   port = 9090
   url = http://127.0.0.1:9090
   ```

3. **Heat CloudFormation API Kimlik Doğrulaması (`/etc/heat/heat.conf`):**
   `alarm_url` üzerinden gelen AWS-v2/v4 HMAC imzalarının Keystone `/v3/ec2tokens` aracılığıyla doğrulanabilmesi için `[ec2authtoken]` servis kimlik bilgileri yapılandırılır:
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

4. **Ubuntu 24.04 / Python 3.12 Uyumluluğu:**
   SQLAlchemy 2.0 geçişinde oluşan `ExceptionContextImpl.chained_exception` hatası için geriye dönük uyumlu `osprofiler` yaması uygulanır.

---

## 7. Doğrulama ve Test Adımları

### 1. Şablonu Kullanarak Stack Başlatma
```bash
openstack stack create -t templates_heat/heat_template_aodh.yaml aodh-autoscaling-stack
```

### 2. Aodh Alarm Kayıtlarının Doğrulanması
```bash
openstack alarm list
```
Oluşturulan alarmların `type: prometheus` ve `state: ok` veya `insufficient data` durumunda olduğu teyit edilir.

### 3. Yük Testi ve Ölçekleme Doğrulaması
Sanal makine konsolu üzerinden CPU tüketimi başlatılır:
```bash
cat /dev/zero > /dev/null &
```

* Prometheus vCPU metrik artışını hesaplar.
* `aodh-evaluator` eşik değerin aşıldığını belirleyerek alarm durumunu `ok -> alarm` olarak günceller.
* `aodh-notifier`, Heat CloudFormation API'sine POST sinyali iletir ve HTTP 200 yanıtı alır.
* Heat AutoScalingGroup yeni bir sanal makine başlatır (Scale-Out).
* Yük durdurulduğunda (`killall cat`) CPU düşüşü ile `cpu_alarm_low` tetiklenir ve fazla makine silinir (Scale-In).

---

## 8. Mimari Karşılaştırma: Aodh vs Alertmanager

| Kriter | Alertmanager + Adaptör Modeli | Aodh + Prometheus Modeli |
| :--- | :--- | :--- |
| **Alarm Tanım Yeri** | Prometheus `alert_rules.yml` dosyası | Heat Orchestration Template (HOT YAML) |
| **Kullanıcı Modeli** | Altyapı yöneticisi müdahalesi gerektirir | Tamamen self-service kullanıcı şablonu |
| **Tetikleme Yöntemi** | Webhook Push (Alertmanager) | Periyodik PromQL Değerlendirme (Aodh Pull) |
| **Tepki Süresi** | ~15-30 saniye | ~60 saniye (Yapılandırılabilir evaluation_interval) |
| **OpenStack Entegrasyonu** | Harici adaptör servisi gerektirir | Yerel OpenStack kaynak tipleri (`OS::Aodh::*`) |
| **Sistem Kaynak İhtiyacı** | Düşük (Tek Python servisi) | Standart (Aodh servisleri kümesi) |

---

## Lisans

Bu proje [Apache-2.0](LICENSE) lisansı ile dağıtılmaktadır.