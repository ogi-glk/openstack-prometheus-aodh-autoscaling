#!/usr/bin/env python3
"""
Alertmanager -> OpenStack Native Heat Multi-Stack Signal Adapter
---------------------------------------------------------------
Dynamically routes webhook alerts to the exact Heat Stack matching
the alert's `server_group` label. Supports unlimited parallel stacks!
"""

import os
import sys
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import openstack

LISTEN_PORT = int(os.environ.get("ADAPTER_PORT", 9200))
DEFAULT_STACK = os.environ.get("HEAT_STACK_NAME", "autoscaling-stack")

def get_openstack_conn():
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

def signal_heat_resource(stack_identifier, resource_name):
    """Signals Heat ScalingPolicy dynamically by stack ID or stack name."""
    conn = get_openstack_conn()
    stack = conn.orchestration.find_stack(stack_identifier)
    if not stack:
        stack = conn.orchestration.find_stack(DEFAULT_STACK)
    if not stack:
        raise ValueError(f"Target stack '{stack_identifier}' not found in OpenStack")

    endpoint = f"/stacks/{stack.name}/{stack.id}/resources/{resource_name}/signal"
    resp = conn.orchestration.post(endpoint)
    return stack.name, resp.status_code if hasattr(resp, 'status_code') else 200

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
            target_stack = alert.get("labels", {}).get("server_group") or DEFAULT_STACK
            resource_name = "scaleup_policy" if alertname == "CpuHigh" else "scaledown_policy" if alertname == "CpuLow" else None
            
            if not resource_name or target_stack == "unknown":
                continue

            try:
                stack_name, status = signal_heat_resource(target_stack, resource_name)
                print(f"[+] SUCCESS: {alertname} -> Stack '{stack_name}' ({target_stack}) / Resource '{resource_name}' triggered (HTTP {status})", flush=True)
            except Exception as e:
                print(f"[!] ERROR: Failed to forward {alertname} signal to stack {target_stack}: {e}", flush=True)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    print(f"[*] Multi-Stack Native Heat Signal Adapter listening on port :{LISTEN_PORT}...", flush=True)
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    server.serve_forever()