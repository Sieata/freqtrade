"""paper_restart.py 的纯逻辑单测（2026-11-08，schtasks 版）。不启动真实 bot。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paper_restart as P  # noqa: E402


def test_arms_registry():
    assert set(P.ARMS) == {"v2", "oi"}
    assert P.ARMS["v2"]["config"] == "user_data/config_paper_v2.json"
    assert P.ARMS["oi"]["config"] == "user_data/config_paper_oi.json"


def test_arms_ports():
    assert P.ARMS["v2"]["port"] == 8080
    assert P.ARMS["oi"]["port"] == 8082


def test_frozen_sha_matches_current_source():
    """两臂冻结 SHA 必须等于当前源码 SHA（防误改源码后仍能启动）。"""
    for arm in ("v2", "oi"):
        info = P.ARMS[arm]
        cur = P._src_sha(info["strategy"])
        assert cur == info["frozen"], f"{arm} 源码 SHA 漂移: {cur} != {info['frozen']}"


def test_task_name_stable():
    """任务名固定（重启用 -Force 覆盖，天然防重复实例）。"""
    assert P._task_name("v2") == "paper_v2"
    assert P._task_name("oi") == "paper_oi"


def test_kill_scoped_needle_construction():
    """--only 清理应按 config 文件名匹配，确保重启一臂不误杀另一臂。"""
    v2 = "config_paper_v2.json"
    oi = "config_paper_oi.json"
    cmd_v2 = "python -m freqtrade trade --config user_data/config_paper_v2.json --strategy WeekendReverseV2"
    cmd_oi = "python -m freqtrade trade --config user_data/config_paper_oi.json --strategy OIFlushV2"
    assert v2 in cmd_v2 and v2 not in cmd_oi
    assert oi in cmd_oi and oi not in cmd_v2


def test_kill_existing_no_match_returns_empty():
    res = P._kill_existing(only="__nonexistent_arm__")
    assert res == []


def test_patch_settings_xml_fixes_daemon_killers():
    """轮42：默认 Settings 的电池/空闲设置会误杀常驻 bot，须全部改 false + PT0S。"""
    xml = (
        "<Task><Settings>"
        "<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>"
        "<StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>"
        "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<ExecutionTimeLimit>PT72H</ExecutionTimeLimit>"
        "<IdleSettings><StopOnIdleEnd>true</StopOnIdleEnd></IdleSettings>"
        "</Settings></Task>"
    )
    out = P._patch_settings_xml(xml)
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in out
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in out
    assert "<StopOnIdleEnd>false</StopOnIdleEnd>" in out
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in out
    assert "true</DisallowStartIfOnBatteries>" not in out


def test_patch_settings_xml_adds_execution_limit_when_absent():
    """XML 无 ExecutionTimeLimit 时应补 PT0S（无限），避免默认 72h 强杀。"""
    xml = ("<Task><Settings>"
           "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
           "</Settings></Task>")
    out = P._patch_settings_xml(xml)
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in out
