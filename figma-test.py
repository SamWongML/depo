#!/usr/bin/env python3
"""Mock Figma API. Exercises every branch of figma_probe.py."""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MODE = sys.argv[1] if len(sys.argv) > 1 else "high"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8731


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, extra=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path
        tok = self.headers.get("X-Figma-Token")
        bearer = self.headers.get("Authorization")

        if MODE == "noauth" and not tok and not bearer:
            return self._send(401, {"status": 401, "err": "Not authorized"})

        if MODE == "bearer_only" and tok and not bearer:
            return self._send(401, {"status": 401, "err": "Invalid token"})

        if p.startswith("/v1/me"):
            return self._send(200, {"id": "123", "email": "u@example.com",
                                    "handle": "u", "img_url": "x"})

        if "/meta" in p:
            if MODE == "low":
                return self._send(200, {"file": {
                    "name": "Checkout Flows", "folder_name": "Drafts",
                    "last_touched_at": "2026-08-01T00:00:00Z",
                    "editor_type": "figma", "link_access": "view"}})
            return self._send(200, {"file": {
                "name": "Checkout Flows", "editor_type": "figma",
                "last_touched_at": "2026-08-01T00:00:00Z",
                "link_access": "org_view"}})

        if "/versions" in p:
            return self._send(200, {"versions": [
                {"id": "998877", "created_at": "2026-08-01T00:00:00Z",
                 "label": "v3"},
                {"id": "998866", "created_at": "2026-07-01T00:00:00Z"}]})

        if "/comments" in p:
            return self._send(200, {"comments": [{"id": "1", "message": "hi"}]})

        if "/dev_resources" in p:
            return self._send(403, {
                "status": 403, "error": True,
                "message": "Invalid scope(s): current_user:read, "
                           "file_content:read. This endpoint requires the "
                           "file_dev_resources:read scope"})

        if "/variables/local" in p:
            if MODE == "low":
                return self._send(403, {
                    "status": 403, "error": True,
                    "message": "Invalid scope(s): current_user:read. This "
                               "endpoint requires the file_variables:read scope"})
            return self._send(200, {"meta": {
                "variables": {"a": {}, "b": {}, "c": {}},
                "variableCollections": {"col1": {}}}})

        if "/nodes" in p:
            if MODE == "low":
                return self._send(429, {"status": 429},
                                  {"Retry-After": "34200",
                                   "X-Figma-Plan-Tier": "starter",
                                   "X-Figma-Rate-Limit-Type": "low",
                                   "X-Figma-Upgrade-Link": "https://figma.com/pricing"})
            return self._send(200, {"nodes": {"24626:100": {"document": {
                "id": "24626:100", "name": "Home", "type": "FRAME",
                "transitionNodeID": "24626:200",
                "interactions": [
                    {"trigger": {"type": "ON_CLICK"},
                     "actions": [{"type": "NODE", "destinationId": "24626:200",
                                  "navigation": "NAVIGATE"}]}],
                "children": [
                    {"id": "24626:101", "name": "Btn", "type": "INSTANCE",
                     "interactions": [
                         {"trigger": {"type": "ON_CLICK"},
                          "actions": [{"type": "NODE",
                                       "destinationId": "24626:300",
                                       "navigation": "OVERLAY"},
                                      {"type": "SET_VARIABLE"}]}],
                     "children": []}]}}}})

        if p.startswith("/v1/files/"):
            if MODE == "low":
                return self._send(429, {"status": 429},
                                  {"Retry-After": "34200",
                                   "X-Figma-Plan-Tier": "starter",
                                   "X-Figma-Rate-Limit-Type": "low"})
            return self._send(200, {"name": "Checkout Flows",
                                    "lastModified": "2026-08-01T00:00:00Z",
                                    "document": {"id": "0:0", "children": [
                                        {"id": "0:1", "name": "Onboarding"},
                                        {"id": "0:2", "name": "Checkout"},
                                        {"id": "0:3", "name": "Components"}]}})

        return self._send(404, {"status": 404, "err": "Not found"})


if __name__ == "__main__":
    print("mock figma mode=%s port=%d" % (MODE, PORT), flush=True)
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
