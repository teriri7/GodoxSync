"""Download the exact factory image assigned to a firmware profile."""

import os
import subprocess
from http.client import HTTPException
from io import BytesIO
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from threading import Timer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import BadZipFile, ZipFile

from firmware import Firmware, verify_stock
from sync_plan import InputError


GODOX_HOSTS = {"godox.com", "www.godox.com", "godox.com.cn", "www.godox.com.cn"}
ARCHIVE_LIMIT = 32 * 1024 * 1024
TIMEOUT = 25


def _check_url(url: str) -> str:
    address = urlsplit(url)
    if address.scheme != "https" or address.netloc.lower() not in GODOX_HOSTS:
        raise InputError("固件下载地址必须是神牛官网的 HTTPS 链接。")
    suffix = PurePosixPath(address.path).suffix.lower()
    if suffix not in {".zip", ".rar"}:
        raise InputError("固件下载地址必须指向 ZIP 或 RAR 升级包。")
    return suffix


class _OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _zip_image(payload: bytes, item: Firmware) -> bytes:
    with ZipFile(BytesIO(payload)) as archive:
        suffix = Path(item.filename).suffix.lower()
        for member in archive.infolist():
            if PurePosixPath(member.filename).suffix.lower() != suffix or member.file_size != item.size:
                continue
            with archive.open(member) as source:
                raw = source.read(item.size + 1)
            try:
                verify_stock(raw, item)
            except InputError:
                continue
            return raw
    raise InputError("压缩包中找不到大小和 SHA-256 均匹配的固件。")


def _rar_image(payload: bytes, item: Firmware) -> bytes:
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if not tar.is_file():
        raise InputError("系统中找不到 Windows 自带的 tar.exe，无法读取此 RAR 固件包。")
    with TemporaryDirectory(prefix="GodoxSync-download-") as folder:
        archive = Path(folder) / "factory.rar"
        archive.write_bytes(payload)
        # Stream only the named member, with a byte limit and no extraction to disk.
        with subprocess.Popen(
            [str(tar), "-xOf", str(archive), "--include", "*" + item.filename],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ) as process:
            timer = Timer(TIMEOUT, process.kill)
            timer.start()
            try:
                raw = process.stdout.read(item.size + 1)
                if len(raw) > item.size or process.wait():
                    raise InputError("RAR 固件包无法读取或文件大小不匹配。")
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
        verify_stock(raw, item)
        return raw


def download_stock(item: Firmware) -> bytes:
    try:
        suffix = _check_url(item.download_url)
        request = Request(item.download_url, headers={"User-Agent": "GodoxSync/1.0"})
        with build_opener(_OfficialRedirects()).open(request, timeout=TIMEOUT) as response:
            payload = response.read(ARCHIVE_LIMIT + 1)
        if len(payload) > ARCHIVE_LIMIT:
            raise InputError("官网下载包超出大小限制。")
        return _zip_image(payload, item) if suffix == ".zip" else _rar_image(payload, item)
    except HTTPError as error:
        raise InputError(f"{item.label} 下载失败（HTTP {error.code}）。请稍后重试。") from error
    except (URLError, OSError, HTTPException) as error:
        raise InputError(f"无法从神牛官网下载 {item.label}。请检查网络后重试。") from error
    except (BadZipFile, NotImplementedError, RuntimeError) as error:
        raise InputError(f"{item.label} 的原厂压缩包无法读取。") from error
    except InputError as error:
        raise InputError(f"{item.label} 下载或校验失败。{error}") from error
