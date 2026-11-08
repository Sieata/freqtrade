# -*- coding: utf-8 -*-
"""paper8 池摩擦分析工具单测（轮40-41）：
- pairs_paper8.txt 存在且与 config_paper_v2.json whitelist 一致（防漂移）
- paper8_fee_ladder / paper8_compound_fee 常量与池一致、费率阶梯单调
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ROOT = Path(__file__).resolve().parents[3]  # user_data 的父＝仓库根
import research_lib as R


def _paper8_from_config():
    cfg = json.loads((ROOT / "user_data" / "config_paper_v2.json").read_text(encoding="utf-8"))
    return cfg["exchange"]["pair_whitelist"]


def test_pairs_paper8_file_exists():
    p = ROOT / "user_data" / "universe" / "pairs_paper8.txt"
    assert p.exists()


def test_pairs_paper8_matches_config_whitelist():
    pool = [x for x, _ in R.load_pool("paper8")]
    cfg = _paper8_from_config()
    assert pool == cfg, f"pairs_paper8.txt 与 config whitelist 漂移: {pool} vs {cfg}"


def test_fee_ladder_constants():
    import paper8_fee_ladder as L
    assert L.PAPER8 == _paper8_from_config()
    assert L.SMALL == {"BANK/USDT:USDT", "CYS/USDT:USDT"}
    # 单调递增
    assert L.FEES == sorted(L.FEES)
    assert all(f > 0 for f in L.FEES)


def test_compound_fee_pool_and_fees():
    import paper8_compound_fee as C
    assert C.POOL == "paper8"
    assert C.FEES == sorted(C.FEES)
    assert C.WALLET > 0
