"""打印冻结的时间切分（测试集 / 验证集）——切分口径的唯一权威来源。

口径（详见 user_data/universe/splits.json）：
  测试集 TEST   20220101-20240828   调参、对比、逐年滚动只准用这段
  验证集 VAL    20240828-          定版候选只跑一次；跑过又改参 = 作废重来

用法:
  .venv/bin/python user_data/scripts/time_splits.py            # 人读格式
  .venv/bin/python user_data/scripts/time_splits.py --shell    # 可 eval 的 shell 变量
"""
import argparse
import json
import sys
from pathlib import Path

SPLITS_FILE = Path(__file__).resolve().parent.parent / "universe" / "splits.json"


def load_splits():
    with open(SPLITS_FILE) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shell", action="store_true", help="输出可 eval 的 shell 变量")
    args = ap.parse_args()

    s = load_splits()
    if args.shell:
        print(f"TR_TEST={s['test_timerange']}")
        print(f"TR_VAL={s['val_timerange']}")
        return

    print(f"时间切分 v{s['version']}（冻结于 {s['frozen_on']}，来源 {SPLITS_FILE.name}）")
    print()
    print(f"  测试集 TEST  --timerange {s['test_timerange']:<22} 调参/对比/滚动只准用这段")
    print(f"  验证集 VAL   --timerange {s['val_timerange']:<22} 定版前只跑一次")
    print()
    print(f"  数据自 {s['data_start']} 起，{s['data_start'][:4]} 仅作暖机不计入可交易区间")
    print(f"  纪律: {s['resplit_policy']}")


if __name__ == "__main__":
    main()
