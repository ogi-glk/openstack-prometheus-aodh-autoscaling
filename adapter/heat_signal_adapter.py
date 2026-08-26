#!/usr/bin/env python3
"""
Alertmanager -> Heat Webhook Signal Adapter
-------------------------------------------
Alertmanager sends POST alerts to :9200 upon threshold breaches.
This adapter:
1. Catches alertname (CpuHigh or CpuLow).
2. Authenticates with Keystone via openstacksdk to fetch a fresh valid token.
3. Attaches X-Auth-Token header and forwards POST request to Heat scaling policy URL.
"""

import os
import sys
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import ssl

LISTEN_PORT = int(os.environ.get("ADAPTER_PORT", 9200))

SIGNAL_URLS = {
    "CpuHigh": os.environ.get("HEAT_SCALEUP_URL", ""),
    "CpuLow":  os.environ.get("HEAT_SCALEDOWN_URL", "")
}

def get_openstack_token():
    """Retrieves fresh Keystone auth token using openstacksdk."""
    try:
        import openstack
        auth_url = os.environ.get("OS_AUTH_URL")
        if auth_url:
            conn = openstack.connect(
                auth_url=auth_url,
                project_name=os.environ.get("OS_PROJECT_NAME", "admin"),
                username=os.environ.get("OS_USERNAME", "admin"),
                password=os.environ.get("OS_PASSWORD", ""),
                user_domain_name=os.environ.get("OS_USER_DOMAIN_NAME", "Default"),
                project_domain_name=os.environ.get("OS_PROJECT_DOMAIN_NAME", "Default"),
            )
        else:
            conn = openstack.connect(cloud="envvars")
        
        token = conn.session.get_token() if hasattr(conn, 'session') else getattr(conn, 'auth_token', None)
        return token
    except Exception as e:
        print(f"[!] Warning: Unable to acquire Keystone token: {e}", flush=True)
        return None

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
            target_url = SIGNAL_URLS.get(alertname) or os.environ.get("HEAT_SCALEUP_URL" if alertname == "CpuHigh" else "HEAT_SCALEDOWN_URL", "")
            
            if not target_url:
                print(f"[!] Unknown or unconfigured alert: {alertname}", flush=True)
                continue

            try:
                req = urllib.request.Request(target_url, data=b"", method="POST")
                
                # Always fetch and attach fresh Keystone token as requested
                token = get_openstack_token()
                if token:
                    req.add_header("X-Auth-Token", token)
                
                # Support self-signed SSL certificates in lab environments
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(req, context=ctx) as resp:
                    print(f"[+] {alertname} signal successfully forwarded -> Heat HTTP {resp.status}", flush=True)
            except Exception as e:
                print(f"[!] {alertname} signal forwarding error: {e}", flush=True)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    print(f"[*] heat_signal_adapter listening on port :{LISTEN_PORT}...", flush=True)
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    server.serve_forever()