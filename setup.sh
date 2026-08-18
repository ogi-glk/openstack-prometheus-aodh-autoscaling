#!/usr/bin/env bash
# ==============================================================================
# OpenStack Prometheus Autoscaling Layer — Otomatik Kurulum Betiği
# ==============================================================================
set -e

INSTALL_DIR="/opt/openstack-bridge"
CONFIG_DIR="/etc/prometheus"
ALERTMANAGER_DIR="/etc/alertmanager"

echo "[*] OpenStack Prometheus Autoscaling kurulumu baslatiliyor..."

# 1. Klasörleri Oluştur
mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/exporters" "$INSTALL_DIR/adapter" "$CONFIG_DIR" "$ALERTMANAGER_DIR"

# 2. Dosyaları Hedef Konuma Kopyala
cp -r exporters/* "$INSTALL_DIR/exporters/"
cp -r adapter/* "$INSTALL_DIR/adapter/"
cp configs/prometheus.yml "$CONFIG_DIR/prometheus.yml"
cp configs/alert_rules.yml "$CONFIG_DIR/alert_rules.yml"
cp configs/alertmanager.yml "$ALERTMANAGER_DIR/alertmanager.yml"

# 3. Python Virtual Environment (venv) Kurulumu
echo "[*] Python venv ve bagimliliklar kuruluyor..."
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r requirements.txt

# 4. Systemd Servislerini Kopyala ve Etkinleştir
echo "[*] Systemd servisleri yukleniyor..."
cp systemd/*.service /etc/systemd/system/
systemctl daemon-reload

systemctl enable --now server-group-exporter
systemctl enable --now heat-signal-adapter

# 5. Prometheus ve Alertmanager'ı Yeniden Yükle (Varsa)
if systemctl is-active --quiet prometheus; then
    systemctl restart prometheus
    echo "[+] Prometheus servisi yeniden baslatildi."
fi

if systemctl is-active --quiet alertmanager; then
    systemctl restart alertmanager
    echo "[+] Alertmanager servisi yeniden baslatildi."
fi

echo ""
echo "================================================================================"
echo "KURULUM TAMAMLANDI"
echo "================================================================================"
echo "Kontrol Komutları:"
echo "  - Exporter Durumu : systemctl status server-group-exporter"
echo "  - Adapter Durumu  : systemctl status heat-signal-adapter"
echo "  - Metrik Testi    : curl http://localhost:9102/metrics"
echo "================================================================================"
