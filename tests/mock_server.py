import json, threading, socketserver, sys, base64
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = []
INVOICES = [
    {"InvoiceId": "aaaa1111-0000", "InvoiceDate": "2026-07-14T00:00:00", "InvoiceFileName": "x.pdf", "Amount": 9.99, "Currency": "EUR"},
    {"InvoiceId": "bbbb2222-0000", "InvoiceDate": "2026-08-14T00:00:00", "Amount": 9.99},
    {"InvoiceId": "cccc3333-0000", "InvoiceDate": "2026-09-14T00:00:00", "Amount": 9.99},
    {"InvoiceId": "dddd4444-0000", "InvoiceDate": "2025-12-14", "Amount": 9.99},
]
TOKENS = {"n": 0}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ct="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/oauth2/v3/token":
            assert body["client_id"] == "ownerapi"
            if body["grant_type"] == "refresh_token" and body["refresh_token"] == "bad":
                return self._send(401, {"error": "invalid_grant"})
            TOKENS["n"] += 1
            LOG.append(("token", body["grant_type"]))
            return self._send(200, {"access_token": f"AT{TOKENS['n']}", "refresh_token": f"RT{TOKENS['n']}", "expires_in": 28800})
        if self.path == "/v3/documents":
            assert self.headers["X-API-KEY"] == "gmikey"
            pdf = base64.b64decode(body["fileContent"])
            assert pdf.startswith(b"%PDF")
            LOG.append(("gmi", body["fileName"], body.get("documentDate"), body["documentType"], body.get("tags")))
            return self._send(200, {"documentUid": f"doc-{len(LOG)}"})
        self._send(404, {})
    def do_GET(self):
        assert self.headers["Authorization"].startswith("Bearer AT")
        if self.path.startswith("/mobile-app/subscriptions/invoices"):
            assert "optionCode=%24CPF1" in self.path and "vin=TESTVIN" in self.path
            return self._send(200, {"data": INVOICES})
        if self.path.startswith("/mobile-app/documents/invoices/"):
            iid = self.path.split("/")[-1].split("?")[0]
            LOG.append(("download", iid))
            return self._send(200, b"%PDF-1.4 fake " + iid.encode(), "application/pdf")
        self._send(404, {})

class SMTPH(socketserver.StreamRequestHandler):
    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())
        w("220 mock")
        data = False
        while True:
            raw = self.rfile.readline()
            if not raw: break
            line = raw.decode().rstrip("\r\n")
            if data:
                if line == ".": data = False; w("250 ok"); LOG.append(("mail",))
                continue
            cmd = line.split()[0].upper()
            if cmd in ("EHLO", "HELO"): w("250 mock")
            elif cmd == "DATA": data = True; w("354 go")
            elif cmd == "QUIT": w("221 bye"); break
            else: w("250 ok")

def start():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTPH)
    threading.Thread(target=smtp.serve_forever, daemon=True).start()
    return srv.server_address[1], smtp.server_address[1]
