import os, re, sys, json, shutil, io, contextlib, tempfile
from pathlib import Path
os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"
SP = Path(__file__).resolve().parent
ROOT = SP.parent
sys.path.insert(0, str(SP))
sys.path.insert(0, str(ROOT))
import mock_server, tesla_pc_invoices as T

http_port, smtp_port = mock_server.start()
work = Path(tempfile.mkdtemp(prefix="tpi-test-")); shutil.rmtree(work, ignore_errors=True); work.mkdir()
cfg = ((ROOT / "config.example.ini").read_text()
       .replace("vin =\n", "vin = TESTVIN\n[x]\n", 1))
cfg = cfg.replace("[x]\n", "auth_base = http://127.0.0.1:%d\nownership_base = http://127.0.0.1:%d\n" % (http_port, http_port))
cfg = cfg.replace("api_key =\n", "api_key = gmikey\napi_base = http://127.0.0.1:%d/v3\n" % http_port)
cfg = cfg.replace("to =\n", "to = import@gmi.test\n").replace("smtp_host =\n", "smtp_host = 127.0.0.1\n")
cfg = cfg.replace("smtp_port = 587", "smtp_port = %d" % smtp_port).replace("smtp_security = starttls", "smtp_security = none")
(work / "config.ini").write_text(cfg)
C = ["-c", str(work / "config.ini")]

def run(*a):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = T.main(C + list(a))
    print(f"$ {' '.join(a)}  -> rc={rc}\n{out.getvalue()}")
    return rc, out.getvalue()

L = mock_server.LOG
rc, o = run("run"); assert rc == 2 and "login" in o                     # kein Login
rc, _ = run("set-token", "RT0"); assert rc == 0
st = json.loads((work / "state.json").read_text()); assert st["tokens"]["refresh_token"] == "RT1"  # rotiert
rc, o = run("list"); assert "4 Rechnung" in o and "neu" in o
rc, o = run("list", "--year", "2026"); assert "3 Rechnung" in o
rc, o = run("run", "--dry-run", "--month", "2026-09"); assert "cccc3333" in o and not [x for x in L if x[0] == "download"]
rc, o = run("run", "--to", "2026-07", "--mark-only"); assert "markiert" in o   # Altbestand markieren
rc, o = run("run"); assert rc == 0 and "2 verarbeitet" in o
gm = [x for x in L if x[0] == "gmi"]; assert len(gm) == 2 and gm[0][2] == "2026-08-14", gm
assert (work / "rechnungen/2026/Tesla_PremiumConnectivity_2026-09-14_cccc3333.pdf").exists()
rc, o = run("run"); assert "0 verarbeitet" in o                           # nichts Neues
rc, o = run("run", "--reload", "--month", "2026-08", "--no-local"); assert "1 verarbeitet" in o and len([x for x in L if x[0]=="gmi"]) == 3
rc, o = run("run", "--gmi", "mail", "--no-local", "--from", "2026-08", "--to", "2026-09"); assert "2 verarbeitet" in o and len([x for x in L if x[0]=="mail"]) == 2
rc, o = run("list"); print(o)
rc, o = run("run", "--month", "2026-13"); assert rc == 2
st = json.loads((work / "state.json").read_text()); st["tokens"] = {"refresh_token": "bad"}; (work / "state.json").write_text(json.dumps(st))
rc, o = run("run"); assert rc == 2 and "neu anmelden" in o
print("ALLE TESTS OK")

# ---------------- Update-Funktion ----------------
import subprocess
env = dict(os.environ, TPI_GITHUB_API=f"http://127.0.0.1:{http_port}")
inst = Path(tempfile.mkdtemp(prefix="tpi-upd-"))            # Installation ohne Git (wie in HA)
for f in ("tesla_pc_invoices.py", "config.example.ini"):
    shutil.copy(ROOT / f, inst / f)
(inst / "config.ini").write_text("[tesla]\nvin = X\n")       # muss erhalten bleiben
(inst / "state.json").write_text('{"tokens": {"refresh_token": "keep"}}')

def cli(*a):
    r = subprocess.run([sys.executable, str(inst / "tesla_pc_invoices.py"), *a], capture_output=True, text=True, env=env)
    print(f"$ {' '.join(a)}  -> rc={r.returncode}\n{r.stdout}{r.stderr}")
    return r.returncode, r.stdout

local = (ROOT / "tesla_pc_invoices.py").read_text()
cur = re.search(r'^VERSION = "([^"]+)"', local, re.M)[1]
rc, o = cli("--version"); assert cur in o
mock_server.REMOTE_FILES["tesla_pc_invoices.py"] = local.encode()
rc, o = cli("--check-update"); assert rc == 0 and "aktuellen Stand" in o
rc, o = cli("--update"); assert rc == 0 and "Bereits aktuell" in o

newer = "99.0.0"
mock_server.REMOTE_FILES["tesla_pc_invoices.py"] = local.replace(f'VERSION = "{cur}"', f'VERSION = "{newer}"').encode()
mock_server.REMOTE_FILES["README.md"] = b"# neu\n"
mock_server.REMOTE_FILES["CHANGELOG.md"] = (f"# Changelog\n\n## [{newer}] - 2099-01-01\n- Tolles Feature\n\n## [{cur}] - alt\n- alt\n").encode()
rc, o = cli("-checkupdate"); assert rc == 3 and newer in o and "Tolles Feature" in o and "- alt" not in o
rc, o = cli("-update"); assert rc == 0 and "Update fertig" in o
rc, o = cli("--version"); assert newer in o
assert (inst / "README.md").read_text() == "# neu\n"
assert (inst / f"tesla_pc_invoices.py.{cur}.bak").exists()
assert (inst / "config.ini").read_text() == "[tesla]\nvin = X\n" and "keep" in (inst / "state.json").read_text()
assert os.access(inst / "tesla_pc_invoices.py", os.X_OK)

mock_server.REMOTE_FILES["tesla_pc_invoices.py"] = b'VERSION = "100.0.0"\ndef kaputt(:\n'
rc, o = cli("--update"); assert rc == 2 and "fehlerhaft" in o
rc, o = cli("--version"); assert newer in o                 # kaputtes Update nicht installiert
mock_server.REMOTE_FILES.clear()
rc, o = cli("--check-update"); assert rc == 2 and "nicht gefunden" in o
print("UPDATE-TESTS OK")
