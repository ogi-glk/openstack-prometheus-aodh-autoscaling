#!/usr/bin/env python3
"""
Alertmanager -> OpenStack Native Heat Signal Adapter
---------------------------------------------------
Receives webhook alerts from Alertmanager on :9200.
Translates alerts to native OpenStack Heat resource signals via openstacksdk:
- CpuHigh -> autoscaling-stack / scaleup_policy
- CpuLow  -> autoscaling-stack / scaledown_policy
"""

import os
import sys
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import openstack

LISTEN_PORT = int(os.environ.get("ADAPTER_PORT", 9200))
STACK_NAME = os.environ.get("HEAT_STACK_NAME", "autoscaling-stack")

def get_openstack_conn():
    """Establishes authenticated OpenStack SDK connection using environment variables."""
    auth_url = os.environ.get("OS_AUTH_URL")
    if auth_url:
        return openstack.connect(
            auth_url=auth_url,
            project_name=os.environ.get("OS_PROJECT_NAME", "admin"),
            username=os.environ.get("OS_USERNAME", "admin"),
            password=os.environ.get("OS_PASSWORD", ""),
            user_domain_name=os.environ.get("OS_USER_DOMAIN_NAME", "Default"),
            project_domain_name=os.environ.get("OS_PROJECT_DOMAIN_NAME", "Default"),
        )
    return openstack.connect(cloud="envvars")

def signal_heat_resource(resource_name):
    """Signals Heat ScalingPolicy via native Heat REST API."""
    conn = get_openstack_conn()
    stack = conn.orchestration.find_stack(STACK_NAME)
    if not stack:
        raise ValueError(f"Stack '{STACK_NAME}' not found in OpenStack Orchestration service")

    # POST to native Heat signal endpoint: /stacks/{stack_name}/{stack_id}/resources/{resource_name}/signal
    endpoint = f"/stacks/{stack.name}/{stack.id}/resources/{resource_name}/signal"
    resp = conn.orchestration.post(endpoint)
    return resp.status_code if hasattr(resp, 'status_code') else 200

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
            resource_name = "scaleup_policy" if alertname == "CpuHigh" else "scaledown_policy" if alertname == "CpuLow" else None
            
            if not resource_name:
                print(f"[!] Unknown alert '{alertname}', skipping", flush=True)
                continue

            try:
                status = signal_heat_resource(resource_name)
                print(f"[+] SUCCESS: {alertname} -> Native Heat signal '{resource_name}' triggered (HTTP {status})", flush=True)
            except Exception as e:
                print(f"[!] ERROR: Failed to forward {alertname} signal to Heat: {e}", flush=True)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    print(f"[*] Native Heat Signal Adapter listening on port :{LISTEN_PORT} for stack '{STACK_NAME}'...", flush=True)
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    server.serve_forever()