"""paper_refresh_task.py 的纯函数单测（2026-11-08）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paper_refresh_task as T  # noqa: E402


def test_ps_single_plain():
    assert T._ps_single("abc") == "'abc'"


def test_ps_single_escapes_inner_quote():
    # 内部单引号须翻倍，否则会截断 PowerShell 字符串
    assert T._ps_single("a'b") == "'a''b'"


def test_ps_single_with_windows_path():
    s = r"C:\Program Files\Git\bin\bash.exe"
    assert T._ps_single(s) == "'" + s + "'"


def test_find_bash_returns_existing_path_or_none():
    b = T._find_bash()
    # 本机应能找到 Git bash；若找不到只能是 None（不得返回不存在的路径）
    if b is not None:
        assert Path(b).exists()


def test_task_name_and_pool_constants():
    assert T.TASK == "paper_refresh"
    assert T.POOL == "top10"
