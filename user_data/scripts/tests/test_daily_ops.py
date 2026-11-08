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


def test_paper_bots_registered():
    """paper bot 注册表应含 V2:8080 与 OI:8082。"""
    names = {n for n, _ in D.PAPER_BOTS}
    ports = {p for _, p in D.PAPER_BOTS}
    assert "V2" in names and "OI" in names
    assert ports == {8080, 8082}


def test_check_paper_bots_splits_alive_dead(monkeypatch):
    """check_paper_bots 应按 ping 结果分到 alive/dead，且覆盖全部注册 bot。"""
    def fake_ping(port, timeout=2.0):
        return port == 8080  # 只 V2 活着

    monkeypatch.setattr(D, "_paper_ping", fake_ping)
    alive, dead = D.check_paper_bots()
    assert ("V2", 8080) in alive
    assert ("OI", 8082) in dead
    assert len(alive) + len(dead) == len(D.PAPER_BOTS)


def test_paper_ping_dead_port_returns_false():
    """对一个必然无服务的端口做 ping 必须安全返回 False（不抛异常）。"""
    assert D._paper_ping(59999, timeout=0.5) is False


def test_staleness_hours_none():
    assert D.staleness_hours(None) is None


def test_staleness_hours_computes_gap():
    import pandas as pd

    newest = pd.Timestamp("2026-11-08 04:00:00", tz="UTC")
    now = pd.Timestamp("2026-11-08 16:00:00", tz="UTC")
    assert abs(D.staleness_hours(newest, now) - 12.0) < 1e-6


def test_staleness_threshold_flags_stale():
    """>12h 应被判为陈旧（落下一根以上 4h bar）。"""
    import pandas as pd

    newest = pd.Timestamp("2026-11-08 00:00:00", tz="UTC")
    now = pd.Timestamp("2026-11-08 20:00:00", tz="UTC")  # 20h
    assert D.staleness_hours(newest, now) > 12


def test_write_snapshot_appends_jsonl(tmp_path, monkeypatch):
    """_write_snapshot 必须追加而非覆盖，且每行是合法 JSON、含 ts_utc/alerts。"""
    import json

    monkeypatch.setattr(D, "ROOT", tmp_path)
    snap = {"newest_bar": "2026-11-08 00:00:00", "bots_alive": ["V2", "OI"],
            "recon": {"v2": {"signals": 4, "trades": 0, "missed": 0}}}
    D._write_snapshot(snap, 0)
    D._write_snapshot({"newest_bar": "2026-11-08 04:00:00"}, 1)
    p = tmp_path / "logs" / "daily_ops.jsonl"
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, "两次调用应追加两行，不能覆盖"
    r0, r1 = json.loads(lines[0]), json.loads(lines[1])
    assert r0["ts_utc"].endswith("Z") and r0["alerts"] == 0
    assert r0["bots_alive"] == ["V2", "OI"]
    assert r0["recon"]["v2"]["missed"] == 0
    assert r1["alerts"] == 1
