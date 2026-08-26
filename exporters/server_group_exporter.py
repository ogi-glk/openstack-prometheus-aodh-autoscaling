#!/usr/bin/env python3
"""
OpenStack Nova Metadata -> Prometheus Metric Exporter (TTL Caching Background Worker)
-------------------------------------------------------------------------------------
Polls Nova API in a background thread every CACHE_TTL seconds (default: 60s).
Filters instances belonging to Heat AutoScaling groups (metering.server_group).
Optional: Set FALLBACK_SERVER_GROUP="unknown" to also track standalone non-Heat VMs.
Exposes `openstack_instance_server_group` gauge on :9102/metrics.
"""

import os
import time
import threading
import openstack
from http.server import HTTPServer, BaseHTTPRequestHandler

LISTEN_PORT = int(os.environ.get("EXPORTER_PORT", 9102))
CACHE_TTL = int(os.environ.get("CACHE_TTL", 60))
FALLBACK_SERVER_GROUP = os.environ.get("FALLBACK_SERVER_GROUP", "").strip()
CACHE_DATA = "# HELP openstack_instance_server_group Nova instance to Heat stack_id (server_group) mapping\n# TYPE openstack_instance_server_group gauge\n"
RUNNING = True

def poll_nova_loop():
    """Periodically refreshes instance list in background every CACHE_TTL seconds."""
    global CACHE_DATA
    while RUNNING:
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

            lines = [
                "# HELP openstack_instance_server_group Nova instance to Heat stack_id (server_group) mapping",
                "# TYPE openstack_instance_server_group gauge"
            ]

            for server in conn.compute.servers(details=True):
                domain_name = getattr(server, 'instance_name', None) or server.name
                metadata = server.metadata or {}
                # By default only export instances with valid Heat server_group.
                # If user configured FALLBACK_SERVER_GROUP (e.g. "unknown"), use that for standalone VMs.
                server_group = metadata.get("metering.server_group") or (FALLBACK_SERVER_GROUP if FALLBACK_SERVER_GROUP else None)
                instance_id = server.id

                if domain_name and server_group:
                    lines.append(
                        f'openstack_instance_server_group{{domain="{domain_name}",instance_id="{instance_id}",server_group="{server_group}"}} 1'
                    )
            lines.append("")
            CACHE_DATA = "\n".join(lines)
        except Exception:
            pass
        time.sleep(CACHE_TTL)

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            content = CACHE_DATA.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif self.path == "/healthz" or self.path == "/":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def run():
    fallback_msg = f", FALLBACK_SERVER_GROUP='{FALLBACK_SERVER_GROUP}'" if FALLBACK_SERVER_GROUP else ""
    print(f"[*] Nova Server Group Exporter starting (CACHE_TTL={CACHE_TTL}s, Port={LISTEN_PORT}{fallback_msg})...", flush=True)
    worker = threading.Thread(target=poll_nova_loop, daemon=True)
    worker.start()

    server_address = ("0.0.0.0", LISTEN_PORT)
    httpd = HTTPServer(server_address, MetricsHandler)
    httpd.serve_forever()

if __name__ == "__main__":
    run()