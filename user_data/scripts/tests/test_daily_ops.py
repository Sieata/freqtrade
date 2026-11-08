"""daily_ops.py 的关键纯函数单测（2026-11-08）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import daily_ops as D  # noqa: E402


def test_v2_sha_matches_frozen():
    """当前 V2 源码 SHA 必须等于冻结值（防误改源码）。"""
    h, err = D.check_v2_sha()
    assert err is None, f"V2 源码 SHA 漂移: {err}"
    assert h == D.FROZEN_V2_SHA256


def test_frozen_sha_is_sha256_hex():
    """冻结常量应为 64 位十六进制（防止手抖写错长度）。"""
    s = D.FROZEN_V2_SHA256
    assert len(s) == 64
    int(s, 16)  # 不抛异常即合法十六进制


def test_load_pool_top10_exists():
    pairs = D._load_pool("top10")
    assert len(pairs) == 10
    assert all("/" in p for p in pairs)
