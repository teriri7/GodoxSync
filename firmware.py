"""Version-specific patches for the Godox X 2.4 GHz wireless-ID lookup."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import md5, sha256
from pathlib import Path
from secrets import token_hex
from struct import pack, pack_into, unpack_from
from tempfile import TemporaryDirectory
from zlib import crc32

from fri import avr_call, avr_lookup, decode_fri
from sync_plan import InputError, STOCK_WORDS, encode_plan


BRANDS = {
    "C": "佳能", "N": "尼康", "S": "索尼", "F": "富士",
    "O": "奥林巴斯 / 松下", "L": "徕卡", "P": "宾得",
}

ROOT = Path(__file__).resolve().parent
SERIES_ORDER = ("X 系列", "V 系列", "TT 系列", "AD 系列", "其他闪光灯")
DFUSE_PREFIX_SIZE = 0x125
DFUSE_SUFFIX_SIZE = 16


@dataclass(frozen=True)
class CallSite:
    offset: int
    stock: bytes
    before_call: bytes = b""
    after_call: bytes = b""


@dataclass(frozen=True)
class Firmware:
    series: str
    model: str
    variant: str
    version: str
    relative_path: str
    size: int
    digest: str
    download_url: str
    status: str
    helper_offset: int | None = None
    helper_fill: int = 0xFF
    sites: tuple[CallSite, ...] = ()
    md5_offset: int | None = None
    append_limit: int | None = None
    thumb1_divider: int | None = None
    dfuse_base: int | None = None
    fri_model: str | None = None

    @property
    def filename(self) -> str:
        return self.relative_path.rsplit("/", 1)[-1]

    @property
    def label(self) -> str:
        variant = "" if self.variant == "通用" else self.variant
        return " ".join(part for part in (self.model, variant, self.version) if part)

    @property
    def folder(self) -> str:
        return self.label.replace(" ", "-")

    @property
    def available(self) -> bool:
        return self.helper_offset is not None and bool(self.sites)

def _catalog() -> dict[str, Firmware]:
    indexed = {}
    rows = json.loads((ROOT / "profiles.json").read_text(encoding="utf-8"))
    for row in rows:
        patch = row.get("patch", {})
        sites = tuple(
            CallSite(int(site["offset"], 16), bytes.fromhex(site["stock"]),
                     bytes.fromhex(site.get("before", "")), bytes.fromhex(site.get("after", "")))
            for site in patch.get("sites", ())
        )
        firmware = Firmware(
            series=row["series"], model=row["model"], variant=row["variant"], version=row["version"],
            relative_path=row["firmware"], size=row["size"], digest=row["sha256"],
            download_url=row["download_url"], status=row["status"],
            helper_offset=int(patch["helper"], 16) if patch else None,
            helper_fill=int(patch.get("fill", "ff"), 16), sites=sites,
            md5_offset=int(patch["md5"], 16) if "md5" in patch else None,
            append_limit=int(patch["limit"], 16) if "limit" in patch else None,
            thumb1_divider=int(patch["thumb1_divider"], 16) if "thumb1_divider" in patch else None,
            dfuse_base=int(row["analysis"]["base"], 16) if row["analysis"].get("container") == "DfuSe" else None,
            fri_model=row["analysis"].get("fri_model"),
        )
        if firmware.folder in indexed:
            raise RuntimeError(f"重复的固件配置：{firmware.label}")
        indexed[firmware.folder] = firmware
    return indexed


def _ad_power(model: str) -> int | float:
    power = re.match(r"AD([0-9]+)", model)
    return int(power[1]) if power else float("inf")


FIRMWARES = _catalog()
SERIES = {
    series: tuple(dict.fromkeys(item.model for item in FIRMWARES.values() if item.series == series))
    for series in SERIES_ORDER
    if any(item.series == series for item in FIRMWARES.values())
}
if "AD 系列" in SERIES:
    SERIES["AD 系列"] = tuple(sorted(SERIES["AD 系列"], key=_ad_power))


# Thumb-2 leaf routine. Input r0 = ID, output r4 = sync word.
# Offsets are relative to a 4-byte-aligned entry point.
#   +00  cmp     r0, #99
#   +02  bhi     +12
#   +04  adr     r1, +24
#   +06  ldrh.w  r4, [r1, r0, lsl #1]
#   +10  bx      lr
#   +12  adds    r0, #1
#   +14  movw    r1, #0xc368
#   +18  udiv    r4, r1, r0
#   +22  bx      lr
#   +24  .hword  100 little-endian ID entries, including stock OFF at index 0
LOOKUP = bytes.fromhex("632803d804a131f810407047401c4cf26831b1fbf0f47047")

# ARMv6-M lookup. Input r0 = ID, output r0 = sync word to match __aeabi_uidiv.
# IDs through 99 load table[r0]. For larger values load the stock dividend
# and tail-call the original divider through an odd Thumb function pointer.
# Literal words at +20 and +24 are the dividend and divider pointer.
# The 100-entry table follows at +28.
THUMB1_LOOKUP = bytes.fromhex("632803d805a14000085a7047411c0148014a1047")


def _thumb_bl(address: int, target: int) -> bytes:
    """Encode BL immediate. Both addresses are offsets in the same image."""
    displacement = target - (address + 4)
    if displacement % 2 or not -(1 << 24) <= displacement < (1 << 24):
        raise RuntimeError("Invalid Thumb BL displacement")
    imm = displacement & 0x1FFFFFF
    s = imm >> 24
    j1 = 1 ^ ((imm >> 23) & 1) ^ s
    j2 = 1 ^ ((imm >> 22) & 1) ^ s
    return pack("<HH", 0xF000 | (s << 10) | ((imm >> 12) & 0x3FF),
                0xD000 | (j1 << 13) | (j2 << 11) | ((imm >> 1) & 0x7FF))


def verify_stock(raw: bytes, firmware: Firmware) -> None:
    if len(raw) != firmware.size or sha256(raw).hexdigest() != firmware.digest:
        raise InputError(
            f"{firmware.label} 的原厂固件大小或 SHA-256 校验失败。请检查官网下载地址和版本。"
        )


def _checked_write(output: bytearray, offset: int, stock: bytes, replacement: bytes) -> None:
    if len(stock) != len(replacement) or output[offset:offset + len(stock)] != stock:
        raise RuntimeError(f"补丁位置 0x{offset:X} 与原始字节不匹配。")
    output[offset:offset + len(stock)] = replacement


def dfuse_image(raw: bytes, base: int) -> bytes:
    """Read the exact one-target, one-element DfuSe layout used by AD200."""
    if (len(raw) < DFUSE_PREFIX_SIZE + DFUSE_SUFFIX_SIZE or
            raw[:6] != b"DfuSe\x01" or raw[10:17] != b"\x01Target" or raw[-8:-4] != b"UFD\x10"):
        raise InputError("DfuSe 升级包头部或尾部格式异常。")
    target_size, count, address, size = unpack_from("<4I", raw, 0x115)
    if (count != 1 or address != base or target_size != size + 8 or
            unpack_from("<I", raw, 6)[0] != len(raw) - DFUSE_SUFFIX_SIZE or
            DFUSE_PREFIX_SIZE + size + DFUSE_SUFFIX_SIZE != len(raw) or
            unpack_from("<I", raw, len(raw) - 4)[0] != (crc32(raw[:-4]) ^ 0xFFFFFFFF)):
        raise InputError("DfuSe 升级包的地址、长度或 CRC 校验失败。")
    return raw[DFUSE_PREFIX_SIZE:-DFUSE_SUFFIX_SIZE]


def _pack_dfuse(raw: bytes, image: bytes, base: int) -> bytes:
    prefix = bytearray(raw[:DFUSE_PREFIX_SIZE])
    pack_into("<I", prefix, 6, len(prefix) + len(image))
    pack_into("<I", prefix, 0x115, len(image) + 8)
    pack_into("<I", prefix, 0x121, len(image))
    body = prefix + image + raw[-DFUSE_SUFFIX_SIZE:-4]
    result = bytes(body) + pack("<I", crc32(body) ^ 0xFFFFFFFF)
    if dfuse_image(result, base) != image:
        raise RuntimeError("DfuSe 封装修复后的镜像校验失败。")
    return result


def _apply_patch(raw: bytes, firmware: Firmware, plan: dict[int, int]) -> bytes:
    if not firmware.available:
        raise InputError(f"{firmware.label} 暂停生成修改固件。{firmware.status}")
    fri = decode_fri(raw, firmware.fri_model) if firmware.fri_model is not None else None
    if fri is not None:
        if (len(firmware.sites) != 1 or firmware.append_limit is None
                or firmware.dfuse_base is not None or firmware.md5_offset is not None
                or firmware.thumb1_divider is not None):
            raise RuntimeError("G1 固件补丁配置异常。")
        image = fri.flash
    else:
        image = dfuse_image(raw, firmware.dfuse_base) if firmware.dfuse_base is not None else raw
    trailer = b""
    if firmware.md5_offset is not None:
        offset = firmware.md5_offset
        image, trailer = image[:offset], image[offset:]
        if len(trailer) < 26 or trailer[:16] != md5(image, usedforsecurity=False).digest():
            raise InputError("原厂固件 MD5 封装校验失败。")
    output = bytearray(image)
    edits = []
    for site in firmware.sites:
        branch = (avr_call(firmware.helper_offset) if fri is not None else
                  _thumb_bl(site.offset + len(site.before_call), firmware.helper_offset))
        body = site.before_call + branch + site.after_call
        padding = len(site.stock) - len(body)
        if padding < 0 or padding % 2:
            raise RuntimeError("补丁指令长度错误。")
        nop = b"\x00\x00" if fri is not None else b"\x00\xbf"
        edits.append((site.offset, site.stock, body + nop * (padding // 2)))

    code = LOOKUP
    if fri is not None:
        code = avr_lookup(firmware.helper_offset, firmware.sites[0].stock)
    elif firmware.thumb1_divider is not None:
        code = THUMB1_LOOKUP + pack("<II", 0xC368, firmware.thumb1_divider | 1)
    helper = code + pack("<100H", *(plan.get(i, stock) for i, stock in enumerate(STOCK_WORDS)))
    if firmware.helper_offset % (2 if fri is not None else 4):
        raise RuntimeError("固件查表地址未对齐。")
    tail = b""
    if firmware.append_limit is None:
        edits.append((firmware.helper_offset, bytes([firmware.helper_fill]) * len(helper), helper))
    else:
        if firmware.helper_offset != len(image) or len(image) + len(helper) > firmware.append_limit:
            raise RuntimeError("查表代码超出固件配置的追加区间。")
        tail = helper
        output.extend(tail)

    for offset, stock, replacement in edits:
        _checked_write(output, offset, stock, replacement)

    cursor = 0
    for offset, stock, replacement in sorted(edits):
        if offset < cursor or output[cursor:offset] != image[cursor:offset]:
            raise RuntimeError("补丁产生了非预期改动。")
        cursor = offset + len(stock)
        if output[offset:cursor] != replacement:
            raise RuntimeError("补丁回读校验失败。")
    if len(output) != len(image) + len(tail) or output[cursor:] != image[cursor:] + tail:
        raise RuntimeError("固件尾部回读校验失败。")
    patched = bytes(output)
    if fri is not None:
        package = fri.encode(patched)
        if decode_fri(package, firmware.fri_model).flash != patched:
            raise RuntimeError("G1 升级包回读失败。")
        return package
    if trailer:
        patched += md5(patched, usedforsecurity=False).digest() + trailer[16:]
    if firmware.dfuse_base is not None:
        return _pack_dfuse(raw, patched, firmware.dfuse_base)
    return patched


def export_firmwares(destination: Path, selection: list[str], plan: dict[int, int],
                     *, originals: Mapping[str, bytes]) -> Path:
    """Validate every input, then commit the whole set to a fresh directory."""
    if not selection:
        raise InputError("请先选择至少一个设备版本。")
    plan_text = encode_plan(plan)
    images = {}
    for key in dict.fromkeys(selection):
        firmware = FIRMWARES.get(key)
        if firmware is None or not firmware.available:
            raise InputError("所选设备包含暂停导出的固件。")
        raw = originals.get(key)
        if raw is None:
            raise InputError(f"请先下载并校验 {firmware.label} 的原厂固件。")
        verify_stock(raw, firmware)
        images[firmware] = _apply_patch(raw, firmware, plan)

    folder = destination / f"Godox-Sync-{datetime.now():%Y%m%d-%H%M%S}-{token_hex(4)}"
    with TemporaryDirectory(prefix=".godox-sync-", dir=destination) as temporary:
        stage = Path(temporary) / "export"
        stage.mkdir()
        for firmware, image in images.items():
            target = stage / firmware.folder / firmware.filename
            target.parent.mkdir()
            target.write_bytes(image)
            if target.read_bytes() != image:
                raise OSError(f"写入后的固件校验失败：{target.name}")
        (stage / "sync-plan.json").write_text(plan_text, encoding="utf-8")
        if folder.exists():
            raise OSError("输出目录已存在，请重新导出。")
        stage.rename(folder)
    return folder
