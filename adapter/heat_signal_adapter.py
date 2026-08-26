#!/usr/bin/env python3
"""
Alertmanager -> OpenStack Native Heat Universal Multi-Metric & Auto-Healing Adapter
-----------------------------------------------------------------------------------
Dynamically routes webhook alerts to OpenStack Heat stacks and resources.
Supports:
  1. Label-driven dynamic routing (`target_resource`, `action`)
  2. Built-in fallback action map (CPU, RAM, Network, Auto-Healing)
  3. Multi-stack dynamic routing by `server_group` UUID
"""

import os
import sys
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import openstack

LISTEN_PORT = int(os.environ.get("ADAPTER_PORT", 9200))
DEFAULT_STACK = os.environ.get("HEAT_STACK_NAME", "autoscaling-stack")

# Fallback Action Map: Used when alert does not explicitly provide `target_resource` label
FALLBACK_ACTION_MAP = {
    # Scale-Out (+1 VM)
    "CpuHigh":            {"resource": "scaleup_policy", "action": "signal"},
    "MemoryHigh":         {"resource": "scaleup_policy", "action": "signal"},
    "NetworkTrafficHigh": {"resource": "scaleup_policy", "action": "signal"},
    "HttpRequestHigh":    {"resource": "scaleup_policy", "action": "signal"},

    # Scale-In (-1 VM)
    "CpuLow":             {"resource": "scaledown_policy", "action": "signal"},
    "MemoryLow":          {"resource": "scaledown_policy", "action": "signal"},

    # Auto-Healing (Mark unhealthy and replace)
    "InstanceCrashed":    {"resource": "asg", "action": "autoheal"},
    "InstanceDown":       {"resource": "asg", "action": "autoheal"},
    "PingFailed":         {"resource": "asg", "action": "autoheal"},
}

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

def signal_heat_resource(stack_identifier, resource_name, action="signal", extra_payload=None):
    """Signals Heat ScalingPolicy or AutoScalingGroup dynamically by stack ID or stack name."""
    conn = get_openstack_conn()
    stack = conn.orchestration.find_stack(stack_identifier)
    if not stack:
        stack = conn.orchestration.find_stack(DEFAULT_STACK)
    if not stack:
        raise ValueError(f"Target stack '{stack_identifier}' not found in OpenStack")

    endpoint = f"/stacks/{stack.name}/{stack.id}/resources/{resource_name}/signal"

    post_body = {}
    if action == "autoheal":
        post_body = {
            "mark_unhealthy": True,
            "reason": (extra_payload or {}).get("reason", "Prometheus alert triggered autoheal")
        }
        if extra_payload and extra_payload.get("resource_name"):
            post_body["resource_name"] = extra_payload["resource_name"]

    resp = conn.orchestration.post(endpoint, json=post_body if post_body else None)
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
            
            labels = alert.get("labels", {})
            alertname = labels.get("alertname", "")
            target_stack = labels.get("server_group") or DEFAULT_STACK

            if target_stack == "unknown":
                continue

            # 1. Label-driven priority: check if Prometheus alert specified target_resource
            target_resource = labels.get("target_resource")
            action = labels.get("action")

            # 2. Fallback to Action Map if not in labels
            if not target_resource and alertname in FALLBACK_ACTION_MAP:
                mapping = FALLBACK_ACTION_MAP[alertname]
                target_resource = mapping["resource"]
                action = action or mapping["action"]

            if not target_resource:
                print(f"[*] IGNORED: Alert '{alertname}' has no matching target resource. Skipping.", flush=True)
                continue

            action = action or "signal"
            extra_payload = {
                "reason": alert.get("annotations", {}).get("summary", f"Alert {alertname} fired"),
                "resource_name": labels.get("instance_id") or labels.get("domain")
            }

            try:
                stack_name, status = signal_heat_resource(target_stack, target_resource, action=action, extra_payload=extra_payload)
                print(f"[+] SUCCESS: {alertname} (Action: {action}) -> Stack '{stack_name}' ({target_stack}) / Resource '{target_resource}' triggered (HTTP {status})", flush=True)
            except Exception as e:
                print(f"[!] ERROR: Failed to forward {alertname} to stack {target_stack}: {e}", flush=True)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def do_GET(self):
        if self.path == "/healthz" or self.path == "/":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    print(f"[*] Universal Multi-Metric & Auto-Healing Heat Signal Adapter listening on port :{LISTEN_PORT}...", flush=True)
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    server.serve_forever()