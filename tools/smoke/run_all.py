"""Run every browser smoke test against a throwaway server (local or CI).

    python tools/smoke/run_all.py                       # all suites, Chrome from SMOKE_CHROME
    python tools/smoke/run_all.py extras batchc         # some suites
    SMOKE_CHROME_ARGS=--no-sandbox python tools/smoke/run_all.py   # CI / restricted sandboxes

Starts ``run.py`` with its own data folder, test admin and a small thread chart
(``Smoke Rayon.gpl``, needed by phase5.mjs), runs the suites one after the other and
fails when a suite exits badly, reports FAIL / ERROR or the page threw an exception.
Logs and screenshots go to the output folder (``--out``, default ./smoke-out).
Needs Node 22+ and Chrome / Chromium / Edge; nothing else beyond requirements.txt.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITES = ["workspace", "controls", "presets", "extras", "batchb", "batchc", "phase5", "layout"]
EMAIL, PASSWORD = "smoke@example.com", "SmokeTest-12345"
CHART = """GIMP Palette
Name: Smoke Rayon
Columns: 4
# RGB Value\t\tColor Name Number
 30\t 50\t130\t    Navy   1001
200\t 40\t 40\t    Red   1002
230\t170\t 40\t    Gold   1003
250\t250\t250\t    White   1004
 20\t 20\t 20\t    Black   1005
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(url: str, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.5)
    return False


def verdict(log: str, code: int) -> list[str]:
    """Why a suite failed (empty = passed)."""
    bad = [line for line in log.splitlines() if line.startswith(("FAIL", "ERROR"))]
    if code != 0:
        bad.append(f"exit code {code}")
    if not any(line.startswith("PASS") for line in log.splitlines()):
        bad.append("no checks ran")
    exc = next((line for line in log.splitlines() if line.startswith("exceptions:")), None)
    if exc is None:
        bad.append("no exception report (the script did not finish)")
    elif exc.strip() != "exceptions: none":
        bad.append(exc)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("suites", nargs="*", default=SUITES, help=f"which suites (default: all - {' '.join(SUITES)})")
    ap.add_argument("--out", default="smoke-out", help="logs + screenshots (default ./smoke-out)")
    ap.add_argument("--timeout", type=int, default=600, help="seconds per suite (default 600)")
    args = ap.parse_args()
    unknown = [s for s in args.suites if s not in SUITES]
    if unknown:
        ap.error(f"unknown suite(s): {' '.join(unknown)}")
    if not shutil.which("node"):
        print("Node 22+ is needed (node not found).", file=sys.stderr)
        return 2

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="smoke-"))
    (work / "charts").mkdir()
    (work / "charts" / "Smoke Rayon.gpl").write_text(CHART, encoding="utf-8")
    port, debug = free_port(), free_port()
    app = f"http://127.0.0.1:{port}/"
    env = {**os.environ, "DATA_DIR": str(work / "data"), "THREAD_CHARTS_DIR": str(work / "charts"),
           "SUPER_ADMIN_EMAIL": EMAIL, "SUPER_ADMIN_PASSWORD": PASSWORD, "HOST": "127.0.0.1", "PORT": str(port),
           "RELOAD": "0", "PYTHONUNBUFFERED": "1"}
    for k in [k for k in env if k.endswith("_API_KEY")]:
        env.pop(k)  # the suites never call a real AI service
    server_log = open(out / "server.log", "w", encoding="utf-8")
    server = subprocess.Popen([sys.executable, str(ROOT / "run.py")], cwd=ROOT, env=env,
                              stdout=server_log, stderr=subprocess.STDOUT)
    failed: dict[str, list[str]] = {}
    try:
        if not wait_for(app + "api/config", 60):
            print("The server did not start - see server.log", file=sys.stderr)
            return 2
        smoke_env = {**os.environ, "SMOKE_APP": app, "SMOKE_EMAIL": EMAIL, "SMOKE_PASSWORD": PASSWORD,
                     "SMOKE_DEBUG_PORT": str(debug)}
        for name in args.suites:
            start = time.time()
            try:
                r = subprocess.run(["node", str(ROOT / "tools" / "smoke" / f"{name}.mjs"), str(out)], cwd=ROOT,
                                   env=smoke_env, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=args.timeout)
                log, code = r.stdout + r.stderr, r.returncode
            except subprocess.TimeoutExpired as e:
                log = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
                log, code = log + f"\nERROR suite timed out after {args.timeout} s", 124
            (out / f"{name}.log").write_text(log, encoding="utf-8")
            bad = verdict(log, code)
            passed = sum(1 for line in log.splitlines() if line.startswith("PASS"))
            print(f"{'ok  ' if not bad else 'FAIL'} {name:<10} {passed:>3} checks  {time.time() - start:5.0f} s", flush=True)
            for line in bad:
                print(f"       {line}", flush=True)
            if bad:
                failed[name] = bad
            time.sleep(2)  # the browser of the last suite lets go of its profile folder
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        server_log.close()
        shutil.rmtree(work, ignore_errors=True)
    print(f"\n{len(args.suites) - len(failed)} of {len(args.suites)} suites passed. Logs: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
