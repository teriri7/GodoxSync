"""The G1 AVR format used by the verified TT685 and V860 II releases."""

from dataclasses import dataclass
from hashlib import md5
from struct import pack

from sync_plan import InputError


def _rotate(data: bytes, direction: int) -> bytes:
    tables = tuple(
        bytes(((value << shift) | (value >> (8 - shift))) & 255 for value in range(256))
        for offset in range(4)
        for shift in (direction * (2 + offset) % 8,)
    )
    result = bytearray(len(data))
    for offset, table in enumerate(tables):
        result[offset::4] = data[offset::4].translate(table)
    return bytes(result)


def _hex_record(address: int, content: bytes) -> bytes:
    body = bytes([len(content)]) + pack(">HB", address, 0) + content
    return b":" + (body + bytes([-sum(body) & 255])).hex().upper().encode("ascii")


@dataclass(frozen=True)
class FriImage:
    model_header: bytes
    flash: bytes
    # A None offset is a segment record or the final EOF record.
    records: tuple[tuple[int | None, bytes], ...]
    final_segment: int

    def encode(self, flash: bytes) -> bytes:
        if len(flash) < len(self.flash):
            raise InputError("G1 应用镜像长度异常。")
        lines = []
        for offset, line in self.records[:-1]:
            if offset is None:
                lines.append(line)
            else:
                count = int(line[1:3], 16)
                address = int(line[3:7], 16)
                lines.append(_hex_record(address, flash[offset:offset + count]))
        for offset in range(len(self.flash), len(flash), 16):
            address = offset - self.final_segment
            payload = flash[offset:offset + 16]
            if not 0 <= address < address + len(payload) <= 0x10000:
                raise InputError("G1 新增记录超出原厂 Intel HEX 地址段。")
            lines.append(_hex_record(address, payload))
        lines.append(self.records[-1][1])
        text = b"\r\n".join(lines) + b"\r\n"
        return _rotate(self.model_header + md5(text, usedforsecurity=False).digest() + text, -1)


def decode_fri(raw: bytes, model: str) -> FriImage:
    decoded = _rotate(raw, 1)
    header, digest, text = decoded[:16], decoded[16:32], decoded[32:]
    if header.partition(b"\0")[0] != model.encode("ascii"):
        raise InputError("G1 固件包中的设备标识与配置不一致。")
    if digest != md5(text, usedforsecurity=False).digest() or not text.endswith(b"\r\n"):
        raise InputError("G1 固件包的 Intel HEX 摘要异常。")
    image = bytearray()
    records = []
    segment = 0
    lines = text[:-2].split(b"\r\n")
    for index, line in enumerate(lines):
        try:
            record = bytes.fromhex(line[1:].decode("ascii"))
        except ValueError as error:
            raise InputError("G1 固件包含无效的 Intel HEX 字符。") from error
        if (not line.startswith(b":") or len(record) < 5 or record[0] != len(record) - 5
                or len(line) != 1 + 2 * len(record) or sum(record) & 255):
            raise InputError("G1 固件包的 Intel HEX 记录校验失败。")
        count, address, kind = record[0], int.from_bytes(record[1:3], "big"), record[3]
        data = record[4:-1]
        offset = None
        if kind == 0 and count and index < len(lines) - 1:
            offset = segment + address
            if offset != len(image) or address + count > 0x10000:
                raise InputError("G1 固件包含不连续或重叠的 Flash 记录。")
            image.extend(data)
        elif kind == 2 and count == 2 and address == 0 and index < len(lines) - 1:
            segment = int.from_bytes(data, "big") << 4
        elif not (kind == 1 and count == 0 and address == 0 and index == len(lines) - 1):
            raise InputError("G1 固件包的 Intel HEX 记录类型或位置异常。")
        records.append((offset, line))
    if not image:
        raise InputError("G1 固件包不含应用镜像。")
    return FriImage(header, bytes(image), tuple(records), segment)


def avr_call(byte_address: int) -> bytes:
    if byte_address % 2 or not 0 <= byte_address < 0x20000:
        raise RuntimeError("AVR CALL 目标超出已验证的程序空间。")
    return pack("<HH", 0x940E, byte_address // 2)


def _immediate(opcode: int, register: int, value: int) -> int:
    return opcode | ((value & 0xF0) << 4) | ((register - 16) << 4) | (value & 15)


def _reg_pair(opcode: int, dst: int, src: int) -> int:
    return opcode | (dst << 4) | ((src & 0x10) << 5) | (src & 15)


def _io(opcode: int, register: int, address: int) -> int:
    return opcode | (register << 4) | ((address & 0x30) << 5) | (address & 15)


def avr_lookup(offset: int, original: bytes) -> bytes:
    """r24 supplies the ID. r23:r22 supplies the quotient used by the stock caller."""
    if len(original) != 16:
        raise RuntimeError("AVR 同步字入口指令窗口长度异常。")
    fallback_word = 19
    table = offset + fallback_word * 2 + len(original) + 2
    if table >> 16 != (table + 199) >> 16:
        raise RuntimeError("AVR 同步字表跨越 RAMPZ 地址段。")
    instructions = (
        _immediate(0x3000, 24, 100),          # cpi r24, 100
        0xF400 | ((fallback_word - 2) << 3),  # brsh original divide
        _immediate(0xE000, 30, table & 255),  # ldi r30, lo8(table)
        _immediate(0xE000, 31, (table >> 8) & 255),
        _immediate(0xE000, 20, 0),
        _reg_pair(0x0C00, 30, 24),           # Z += 2 * ID, using r20 as zero
        _reg_pair(0x1C00, 31, 20),
        _reg_pair(0x0C00, 30, 24),
        _reg_pair(0x1C00, 31, 20),
        _io(0xB000, 19, 0x3F),              # save SREG and mask interrupts
        0x94F8,                             # cli
        _io(0xB000, 18, 0x3B),              # save RAMPZ
        _immediate(0xE000, 20, table >> 16),
        _io(0xB800, 20, 0x3B),
        0x9007 | (22 << 4),                  # elpm r22, Z+
        0x9006 | (23 << 4),                  # elpm r23, Z
        _io(0xB800, 18, 0x3B),
        _io(0xB800, 19, 0x3F),
    )
    # The table path returns before the relocated stock arithmetic.
    return pack("<18H", *instructions) + pack("<H", 0x9508) + original + pack("<H", 0x9508)
