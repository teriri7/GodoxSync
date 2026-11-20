"""Build a portable Windows ZIP from a standard python.org CPython install."""

import io
import sys
from pathlib import Path
from sysconfig import get_platform
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

RUNTIME_DLLS = {
    (3, 10): ("libcrypto-1_1.dll", "libssl-1_1.dll", "libffi-7.dll"),
    (3, 13): ("libcrypto-3.dll", "libssl-3.dll", "libffi-8.dll", "zlib1.dll"),
}


def build() -> Path:
    version = sys.version_info[:2]
    if get_platform() != "win-amd64" or version not in RUNTIME_DLLS:
        raise SystemExit("Run this with CPython 3.13 or 3.10 for Windows x64.")
    source = Path(__file__).resolve().parent
    python_home = Path(sys.base_prefix)
    major, minor = version
    pydll = f"python{major}{minor}"
    target = source / "dist" / "GodoxSync-Windows-x64.zip"
    target.parent.mkdir(exist_ok=True)

    stdlib = io.BytesIO()
    lib = python_home / "Lib"
    excluded = {"site-packages", "tests", "test", "ensurepip", "idlelib", "venv"}
    with ZipFile(stdlib, "w", ZIP_DEFLATED) as archive:
        for path in sorted(lib.rglob("*.py")):
            relative = path.relative_to(lib)
            if excluded.isdisjoint(relative.parts[:-1]):
                archive.write(path, relative.as_posix())

    with TemporaryDirectory(dir=target.parent) as staging:
        package = Path(staging) / target.name
        with ZipFile(package, "w", ZIP_DEFLATED) as archive:
            prefix = "GodoxSync/"
            runtime = prefix + "runtime/"
            for filename in (
                "app.py", "firmware.py", "firmware_download.py", "fri.py", "sync_plan.py", "profiles.json", "启动工具.cmd",
                "README.md", "固件分析.md", "ui/index.html", "ui/app.css", "ui/wizard.js",
            ):
                archive.write(source / filename, prefix + filename)
            for name in ("python.exe", "pythonw.exe", pydll + ".dll", "LICENSE.txt"):
                archive.write(python_home / name, runtime + name)
            for path in sorted(python_home.glob("vcruntime*.dll")):
                archive.write(path, runtime + path.name)
            extensions = (
                "_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "_ctypes.pyd",
                "_hashlib.pyd", "_bz2.pyd", "_lzma.pyd", "unicodedata.pyd",
                "_socket.pyd", "_ssl.pyd", "select.pyd", *RUNTIME_DLLS[version],
            )
            for name in extensions:
                archive.write(python_home / "DLLs" / name, runtime + "DLLs/" + name)
            for folder in ("tcl8", "tcl8.6", "tk8.6"):
                for path in sorted((python_home / "tcl" / folder).rglob("*")):
                    if path.is_file():
                        archive.write(path, runtime + path.relative_to(python_home).as_posix())
            archive.writestr(runtime + pydll + ".zip", stdlib.getvalue())
            archive.writestr(runtime + pydll + "._pth", f"{pydll}.zip\nDLLs\n..\n")
        package.replace(target)
    return target


if __name__ == "__main__":
    print(build())
