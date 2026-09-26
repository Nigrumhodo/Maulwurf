"""Sink de prueba para I-S1-JF-02 (J1.3).

Consume el cuerpo de la petición en streaming y responde 202 sin persistir nada.
Se usa como upstream temporal de Caddy en la prueba de no-buffering; no forma
parte de la aplicación.
"""
from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Sink(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _discard(self) -> None:
        received = 0
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    continue
                size = int(line.split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()
                    break
                received += len(self.rfile.read(size))
                self.rfile.readline()
        else:
            remaining = int(self.headers.get("Content-Length", "0"))
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                received += len(chunk)
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()
        print(f"received {received} bytes", flush=True)

    do_PUT = _discard
    do_POST = _discard

    def log_message(self, *args: object) -> None:  # noqa: ANN002
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    ThreadingHTTPServer(("0.0.0.0", port), Sink).serve_forever()
