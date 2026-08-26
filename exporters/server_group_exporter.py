#!/usr/bin/env python3
"""
OpenStack Nova Metadata -> Prometheus Metric Exporter (TTL Cached)
------------------------------------------------------------------
This service connects to Nova API:
1. Locates libvirt domain names (instance-XXXXXXXX) for running VMs.
2. Reads `metering.server_group` (Heat Stack ID) metadata attached by Heat.
3. Employs a 60-second TTL cache to prevent overloading Nova API.
4. Exposes `openstack_instance_server_group` gauge on :9102/metrics.
"""

import os
import time
import openstack
from http.server import HTTPServer, BaseHTTPRequestHandler

LISTEN_PORT = 9102
CACHE_TTL = 60  # Cache TTL in seconds

# Global cache storage
CACHE_DATA = None
LAST_FETCH_TIME = 0

def fetch_nova_metrics():
    """Queries Nova API to collect current VM list and stack mapping."""
    try:
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
    except Exception as e:
        return f"# Error connecting to OpenStack: {e}\n"

    lines = [
        "# HELP openstack_instance_server_group Nova instance to Heat stack_id (server_group) mapping",
        "# TYPE openstack_instance_server_group gauge"
    ]

    try:
        # Fetch all servers with detailed metadata
        for server in conn.compute.servers(details=True):
            domain_name = getattr(server, 'instance_name', None) or server.name
            metadata = server.metadata or {}
            server_group = metadata.get("metering.server_group", "unknown")
            instance_id = server.id

            if domain_name and server_group:
                lines.append(
                    f'openstack_instance_server_group{{domain="{domain_name}",instance_id="{instance_id}",server_group="{server_group}"}} 1'
                )
    except Exception as e:
        lines.append(f"# Error querying servers: {e}")

    lines.append("")
    return "\n".join(lines)

def get_metrics_cached():
    """Returns cached metrics or triggers refresh if TTL has expired."""
    global CACHE_DATA, LAST_FETCH_TIME
    now = time.time()

    if CACHE_DATA is None or (now - LAST_FETCH_TIME) > CACHE_TTL:
        CACHE_DATA = fetch_nova_metrics()
        LAST_FETCH_TIME = now

    return CACHE_DATA

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            content = get_metrics_cached().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/healthz" or self.path == "/":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress standard HTTP access logs to keep terminal clean
        pass

def run():
    server_address = ("0.0.0.0", LISTEN_PORT)
    httpd = HTTPServer(server_address, MetricsHandler)
    print(f"[*] Nova Server Group Exporter listening on http://0.0.0.0:{LISTEN_PORT}/metrics")
    httpd.serve_forever()

if __name__ == "__main__":
    run()