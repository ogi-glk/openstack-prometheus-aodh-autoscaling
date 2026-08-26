#!/usr/bin/env python3
"""
Alertmanager -> Heat Webhook Signal Adapter
-------------------------------------------
Alertmanager issues POST notifications to :9200 upon threshold breaches.
This adapter:
1. Parses alertname (CpuHigh or CpuLow).
2. Obtains a valid Keystone authentication token.
3. Forwards an authenticated POST request to Heat scaling policy signal URL.
"""

import os
import json
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import ssl

LISTEN_PORT = 9200

# URL configuration read from environment or fallback defaults
SIGNAL_URLS = {
    "CpuHigh": os.environ.get("HEAT_SCALEUP_URL", ""),
    "CpuLow":  os.environ.get("HEAT_SCALEDOWN_URL", "")
}

def get_openstack_token():
    """Retrieves current Keystone auth token using openstack CLI."""
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
                print(f"[!] Unknown or unconfigured alert: {alertname}")
                continue

            try:
                token = get_openstack_token()
                req = urllib.request.Request(target_url, data=b"", method="POST")
                req.add_header("X-Auth-Token", token)
                
                # Support self-signed SSL certificates in lab environments
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(req, context=ctx) as resp:
                    print(f"[+] {alertname} signal sent -> Heat HTTP {resp.status}")
            except Exception as e:
                print(f"[!] {alertname} signal forwarding error: {e}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print(f"[*] heat_signal_adapter listening on port :{LISTEN_PORT}...")
    server.serve_forever()