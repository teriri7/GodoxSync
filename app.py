"""Run the Python firmware wizard in a standalone Windows app window."""

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory

from firmware import BRANDS, FIRMWARES, ROOT, SERIES, export_firmwares
from firmware_download import download_stock
from sync_plan import (
    DEFAULT_IDS, IDS, InputError, candidate_words, decode_plan, encode_plan, generate_plan,
)


ASSETS = {
    "": ("index.html", "text/html; charset=utf-8"),
    "app.css": ("app.css", "text/css; charset=utf-8"),
    "wizard.js": ("wizard.js", "text/javascript; charset=utf-8"),
}


def _catalog() -> dict:
    return {
        "series": SERIES,
        "defaultIds": DEFAULT_IDS,
        "candidates": len(candidate_words()),
        "firmwares": [
            {
                "key": key, "series": item.series, "model": item.model, "version": item.version,
                "variant": (f"{BRANDS[brand]} · {item.variant}"
                            if (brand := item.variant.split(" ", 1)[0]) in BRANDS else item.variant),
                "filename": item.filename, "label": item.label,
                "updater": ("G1" if item.fri_model is not None else
                            "G2" if item.dfuse_base is not None else "G3"),
                "available": item.available, "status": item.status,
            }
            for key, item in FIRMWARES.items()
        ],
    }


def _edge() -> Path:
    import winreg

    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key) as handle:
                executable = Path(winreg.QueryValue(handle, None))
            if executable.is_file():
                return executable
        except OSError:
            continue
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if directory := os.environ.get(variable):
            executable = Path(directory) / "Microsoft/Edge/Application/msedge.exe"
            if executable.is_file():
                return executable
    raise OSError("找不到 Microsoft Edge。请在 Windows 上安装 Edge 后再打开工具。")


def _choose_directory(initial: Path) -> str:
    from tkinter import Tk, filedialog

    root = Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        return filedialog.askdirectory(
            title="选择导出位置", parent=root, initialdir=str(initial), mustexist=True,
        )
    finally:
        root.destroy()


def _selection(value: object) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(key, str) or key not in FIRMWARES or not FIRMWARES[key].available
        for key in value
    ):
        raise InputError("请选择一个或多个可生成固件的设备版本。")
    return list(dict.fromkeys(value))


@dataclass
class Desktop:
    last_export: Path | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    originals: dict[str, bytes] = field(default_factory=dict)

    def action(self, route: str, data: object) -> dict:
        if route == "api/prepare":
            selection = _selection(data)
            if not self.lock.acquire(blocking=False):
                raise InputError("正在处理固件，请等待当前操作完成。")
            try:
                for key in selection:
                    if key not in self.originals:
                        self.originals[key] = download_stock(FIRMWARES[key])
                return {"prepared": selection}
            finally:
                self.lock.release()
        if route == "api/plan":
            if not isinstance(data, list) or any(type(value) is not int or value not in IDS
                                                 for value in data):
                raise InputError("请选择 01–99 的无线 ID。")
            return json.loads(encode_plan(generate_plan(set(data))))
        if route == "api/import":
            if not isinstance(data, str):
                raise InputError("请选择本工具导出的 sync-plan.json。")
            return json.loads(encode_plan(decode_plan(data.encode("utf-8"))))
        if route == "api/open":
            if self.last_export is None:
                raise InputError("请先导出配对固件。")
            os.startfile(self.last_export)
            return {"opened": True}
        if route != "api/export" or not isinstance(data, dict):
            raise InputError("未识别的操作。")
        selection = _selection(data.get("selection"))
        plan = decode_plan(_json_bytes(data.get("plan")))
        if not self.lock.acquire(blocking=False):
            raise InputError("正在处理固件，请等待当前操作完成。")
        try:
            if any(key not in self.originals for key in selection):
                raise InputError("请先返回设备步骤，下载并校验所选固件。")
            initial = self.last_export.parent if self.last_export else Path.home()
            destination = _choose_directory(initial)
            if not destination:
                return {"cancelled": True}
            self.last_export = export_firmwares(Path(destination), selection, plan, originals=self.originals)
            return {"folder": str(self.last_export),
                    "files": [FIRMWARES[key].filename for key in selection]}
        finally:
            self.lock.release()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _server(desktop: Desktop):
    prefix = "/" + token_urlsafe(24) + "/"
    actions = {"api/prepare", "api/plan", "api/import", "api/export", "api/open"}
    assets = {route: ((ROOT / "ui" / name).read_bytes(), mime)
              for route, (name, mime) in ASSETS.items()}
    assets["api/catalog"] = _json_bytes(_catalog()), "application/json; charset=utf-8"

    class Request(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            pass

        def _route(self):
            if self.headers.get("Host") == host and self.path.startswith(prefix):
                return self.path[len(prefix):]
            return None

        def _send(self, code, body, mime="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            asset = assets.get(self._route())
            if asset is None:
                self._send(404, b"{}")
                return
            self._send(200, *asset)

        def do_POST(self):
            route = self._route()
            if route not in actions or self.headers.get("Origin") != origin:
                self._send(403, b"{}")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_384 or self.headers.get_content_type() != "application/json":
                    raise InputError("请求格式不受支持。")
                value = json.loads(self.rfile.read(length))
                answer = desktop.action(route, value)
            except (ValueError, OSError, RuntimeError, RecursionError) as error:
                self._send(400, _json_bytes({"error": str(error)}))
                return
            self._send(200, _json_bytes(answer))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Request)
    host = f"127.0.0.1:{server.server_port}"
    origin = f"http://{host}"
    return server, origin + prefix


def main():
    from tkinter import Tk, messagebox

    try:
        edge = _edge()
        server, url = _server(Desktop())
        with server, TemporaryDirectory(prefix="GodoxSync-", ignore_cleanup_errors=True) as profile:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                subprocess.run(
                    [
                        str(edge), f"--user-data-dir={profile}", f"--app={url}",
                        "--window-size=1240,860", "--no-first-run", "--no-default-browser-check",
                        "--disable-background-mode", "--disable-background-networking",
                    ],
                    check=True,
                )
            finally:
                server.shutdown()
                worker.join()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        root = Tk()
        try:
            root.withdraw()
            messagebox.showerror("无法启动神牛 X 固件工具", str(error), parent=root)
        finally:
            root.destroy()


if __name__ == "__main__":
    main()
