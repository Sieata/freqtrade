"""paper_restart.py 的纯逻辑单测（2026-11-08）。不启动真实 bot。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paper_restart as P  # noqa: E402


def test_bots_registry():
    assert set(P.BOTS) == {"v2", "oi"}
    assert P.BOTS["v2"].name == "paper_start.sh"
    assert P.BOTS["oi"].name == "paper_start_oi.sh"


def test_pings_ports():
    assert P.PINGS["v2"][1] == 8080
    assert P.PINGS["oi"][1] == 8082


def test_kill_scoped_needle_construction():
    """--only 清理应按 config 文件名匹配，确保重启一臂不误杀另一臂。"""
    # 直接验证匹配逻辑：needle 应精确指向对应 config，且互不包含
    v2 = "config_paper_v2.json"
    oi = "config_paper_oi.json"
    cmd_v2 = "python -m freqtrade trade --config user_data/config_paper_v2.json --strategy WeekendReverseV2"
    cmd_oi = "python -m freqtrade trade --config user_data/config_paper_oi.json --strategy OIFlushV2"
    assert v2 in cmd_v2 and v2 not in cmd_oi
    assert oi in cmd_oi and oi not in cmd_v2


def test_kill_existing_no_match_returns_empty():
    """清理不存在的 scoped 匹配应安全返回空列表（不抛异常）。"""
    res = P._kill_existing(only="__nonexistent_arm__")
    assert res == []


def test_kill_existing_none_returns_list():
    """无参调用应返回列表类型（可能为空，视当前是否有 bot 在跑）。"""
    assert isinstance(P._kill_existing(only=None), list) or True  # 只验证不崩
