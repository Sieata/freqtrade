"""disp_monitor 单元测试：锁住滚动分位的防前视语义与分档判定。

离线合成数据，不碰真实 K 线。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import disp_monitor as dm  # noqa: E402


def test_rpct_no_lookahead_and_range():
    # 单调上升序列：滚动分位 shift(1) 后应 <=1，且最后值接近 1（最新点高于所有历史）
    s = pd.Series(np.arange(2000, dtype=float))
    p = dm.rpct(s, win=100, minp=50)
    assert p.iloc[0] is np.nan or pd.isna(p.iloc[0])
    valid = p.dropna()
    assert (valid >= 0).all() and (valid <= 1).all()
    assert valid.iloc[-1] > 0.95
    # 防前视：值只依赖过去，单调上升时不会出现下降
    diffs = np.diff(valid.values)
    assert (diffs >= -1e-9).all()


def test_rpct_shift_prevents_self_inclusion():
    # shift(1) 语义：p[i] = s[i-1] 在其截至 i-1 的窗口中的分位（当根不参与自身排名）。
    # 构造 [1.0]*100 + [10.0, 1.0]：s[-2]=10 是窗口内最大 → p[-1]（查看 s[-2] 的分位）=1；
    # 而 p[-2]（查看 s[-3]=1.0）≈0。这证明分位用的是「上一根」且无前视。
    s = pd.Series([1.0] * 100 + [10.0, 1.0])
    p = dm.rpct(s, win=50, minp=20)
    assert abs(p.iloc[-1] - 1.0) < 1e-9      # 上一根=10（窗口最大）
    assert abs(p.iloc[-2] - 0.0) < 1e-9      # 再上一根=1（非最大）


def test_hi_q_threshold_constant():
    assert dm.HI_Q == 0.80
    assert dm.WIN == 1080 and dm.MINP == 360
    assert dm.PAIRS == ["BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "ZEC", "DOGE", "XMR"]
