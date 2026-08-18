#!/usr/bin/env python3
"""
Alertmanager -> Heat Webhook Adapter
------------------------------------
Alertmanager alarm verdiğinde :9200 portuna POST atar.
Bu adapter:
1. Gelen alert'in adını okur (CpuHigh veya CpuLow).
2. Keystone'dan taze bir yetkilendirme token'ı alır.
3. Heat'in scaleup_policy veya scaledown_policy signal URL'ine kimlik doğrulamalı POST atar.
"""

import os
import json
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import ssl

LISTEN_PORT = 9200

# URL yapılandırması ortam değişkeninden veya doğrudan dosyadan okunabilir
SIGNAL_URLS = {
    "CpuHigh": os.environ.get("HEAT_SCALEUP_URL", "https://10.8.133.99:8004/v1/b7966dc0e8c14e1494c1558c641a1030/stacks/lab-asg/fddfc109-f9b3-4171-94d1-1f2ca66a5759/resources/scaleup_policy/signal"),
    "CpuLow":  os.environ.get("HEAT_SCALEDOWN_URL", "https://10.8.133.99:8004/v1/b7966dc0e8c14e1494c1558c641a1030/stacks/lab-asg/fddfc109-f9b3-4171-94d1-1f2ca66a5759/resources/scaledown_policy/signal")
}

def get_openstack_token():
    """Keystone'dan güncel token çeker"""
    cmd = "openstack token issue -f value -c id"
    result = subprocess.run(
        cmd, shell=True,
        capture_output=True, text=True, check=True
    )
    return result.stdout.strip()

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        alerts = payload.get("alerts", [])
        for alert in alerts:
            if alert.get("status") != "firing":
                continue
            
            alertname = alert.get("labels", {}).get("alertname")
            target_url = SIGNAL_URLS.get(alertname)
            
            if not target_url:
                print(f"[!] Bilinmeyen alert: {alertname}")
                continue

            try:
                token = get_openstack_token()
                req = urllib.request.Request(target_url, data=b"", method="POST")
                req.add_header("X-Auth-Token", token)
                
                # Self-signed sertifikaları destekle
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(req, context=ctx) as resp:
                    print(f"[+] {alertname} sinyali gonderildi -> Heat HTTP {resp.status}")
            except Exception as e:
                print(f"[!] {alertname} sinyal iletim hatasi: {e}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print(f"[*] heat_signal_adapter :{LISTEN_PORT} uzerinde dinliyor...")
    server.serve_forever()
