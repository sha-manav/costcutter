"""Start and stop the capture proxy."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from shadow.config import Config, REPO_ROOT, get_config


def _port_open(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


class CaptureProxy:
    def __init__(self, cfg: Config | None = None, out_path: Path | None = None,
                 hosts: str = "") -> None:
        self.cfg = cfg or get_config()
        self.out_path = out_path or self.cfg.path("capture")
        # Default to the app host so browser-internal traffic (time sync,
        # update pings) never enters the record stream.
        self.hosts = hosts or urlparse(self.cfg.app.base_url).hostname or ""
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://{self.cfg.proxy.listen_host}:{self.cfg.proxy.port}"

    def start(self, timeout: float = 25.0) -> "CaptureProxy":
        if _port_open(self.cfg.proxy.listen_host, self.cfg.proxy.port):
            raise RuntimeError(
                f"port {self.cfg.proxy.port} already in use; stop the other proxy")
        mitmdump = Path(self.cfg.proxy.mitmdump)
        if not mitmdump.is_absolute():
            mitmdump = REPO_ROOT / mitmdump
        env = dict(os.environ)
        env["SHADOW_CAPTURE_OUT"] = str(self.out_path)
        env["SHADOW_CREDENTIALS"] = str(self.cfg.path("credentials"))
        env["SHADOW_CAPTURE_HOSTS"] = self.hosts
        env["PYTHONPATH"] = str(REPO_ROOT)
        # The sandbox's own HTTPS proxy must not be chained in front of the
        # capture proxy: it would hide the traffic we are here to record.
        for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            env.pop(key, None)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [str(mitmdump), "--listen-host", self.cfg.proxy.listen_host,
             "--listen-port", str(self.cfg.proxy.port),
             "--set", "confdir=" + os.path.expanduser(self.cfg.proxy.ca_dir),
             "-q", "-s", str(REPO_ROOT / "shadow" / "capture" / "addon.py")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(REPO_ROOT))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _port_open(self.cfg.proxy.listen_host, self.cfg.proxy.port):
                return self
            if self.proc.poll() is not None:
                out = self.proc.stdout.read().decode() if self.proc.stdout else ""
                raise RuntimeError(f"mitmdump exited early:\n{out[:2000]}")
            time.sleep(0.3)
        raise TimeoutError("capture proxy did not start")

    def stop(self, grace: float = 6.0) -> None:
        if self.proc is None:
            return
        # SIGTERM lets the addon flush its last records before exiting.
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None


@contextmanager
def capture_proxy(cfg: Config | None = None, out_path: Path | None = None,
                  hosts: str = ""):
    proxy = CaptureProxy(cfg, out_path, hosts).start()
    try:
        yield proxy
    finally:
        proxy.stop()
