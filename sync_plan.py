"""Create and validate reusable wireless-ID plans shared by all devices."""

import json
import re
from functools import lru_cache
from pathlib import Path
from random import SystemRandom


class InputError(ValueError):
    """An input the photographer can correct."""


IDS = range(1, 100)
DEFAULT_IDS = tuple(range(90, 100))
STOCK_WORDS = tuple(0xC368 // (wireless_id + 1) for wireless_id in range(100))
PLAN_FORMAT = "godox-sync-plan-v1"
WORD_DISTANCE = 3


@lru_cache(maxsize=1)
def candidate_words() -> tuple[int, ...]:
    # D391 is the CC2500 power-on default. 5555 and AAAA are preamble phases.
    reserved = (*STOCK_WORDS, 0xD391)
    candidates = []
    for word in range(1 << 16):
        if not 6 <= word.bit_count() <= 10:
            continue
        if any((word ^ phase).bit_count() < 4 for phase in (0x5555, 0xAAAA)):
            continue
        bits = f"{word:016b}" * 2  # Includes the join when a sync word is repeated.
        if "000000" in bits or "111111" in bits:
            continue
        if any((word ^ stock).bit_count() < WORD_DISTANCE for stock in reserved):
            continue
        candidates.append(word)
    return tuple(candidates)


def generate_plan(selected: set[int]) -> dict[int, int]:
    if not selected or not selected.issubset(IDS):
        raise InputError("请选择至少一个 01–99 之间的无线 ID。")
    shuffled = list(candidate_words())
    SystemRandom().shuffle(shuffled)
    chosen = []
    for word in shuffled:
        if all((word ^ other).bit_count() >= WORD_DISTANCE for other in chosen):
            chosen.append(word)
            if len(chosen) == len(selected):
                return dict(zip(sorted(selected), chosen))
    raise InputError("可用同步字不足，请减少所选 ID 数量。")


def validate_plan(plan: dict[int, int]) -> None:
    if not plan or any(type(wireless_id) is not int or wireless_id not in IDS
                       for wireless_id in plan):
        raise InputError("配对方案必须包含 01–99 之间的无线 ID。")
    eligible = set(candidate_words())
    words = []
    for wireless_id, word in sorted(plan.items()):
        if type(word) is not int or word not in eligible:
            raise InputError(f"ID {wireless_id:02d} 的同步字不在候选集合内。")
        if any((word ^ other).bit_count() < WORD_DISTANCE for other in words):
            raise InputError(f"ID {wireless_id:02d} 的同步字与另一个 ID 相同或过于接近。")
        words.append(word)


def encode_plan(plan: dict[int, int]) -> str:
    validate_plan(plan)
    words = {f"{wireless_id:02d}": f"0x{word:04X}" for wireless_id, word in sorted(plan.items())}
    return json.dumps({"format": PLAN_FORMAT, "sync_words": words}, indent=2) + "\n"


def _unique_keys(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("配对方案含有重复字段。")
        result[key] = value
    return result


def load_plan(path: Path) -> dict[int, int]:
    with path.open("rb") as source:
        return decode_plan(source.read(4097))


def decode_plan(raw: bytes) -> dict[int, int]:
    if len(raw) > 4096:
        raise InputError("配对方案文件过大。")
    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_keys)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise InputError("无法读取配对方案，请选择本工具导出的 sync-plan.json。") from error
    if not isinstance(data, dict) or data.get("format") != PLAN_FORMAT:
        raise InputError("配对方案格式不受支持。")
    words = data.get("sync_words")
    if not isinstance(words, dict):
        raise InputError("配对方案缺少 ID 同步字。")
    plan = {}
    for key, value in words.items():
        if (re.fullmatch(r"[0-9]{2}", key) is None or not isinstance(value, str)
                or re.fullmatch(r"0x[0-9a-fA-F]{4}", value) is None):
            raise InputError("配对方案包含无效 ID 或同步字。")
        plan[int(key)] = int(value, 16)
    validate_plan(plan)
    return plan
