"""依赖闭包检查器：打印 venv 缺失的传递依赖（空格分隔，可直接喂给 uv/pip）。

背景见 user_data/docs/ENGINEERING_NOTES.md 第一节：freqtrade editable 安装会让 uv
解析强行拉 ta-lib/bottleneck/technical，故依赖需 --no-deps 精确安装 + 本脚本迭代补闭包：

    for i in 1 2 3 4 5; do
        MISS=$(.venv/bin/python user_data/scripts/dep_closure.py)
        [ -z "$MISS" ] && break
        uv pip install --python .venv/bin/python $MISS --no-deps
    done

SKIP 中的包为本项目有意跳过的（零引用或依赖陷阱），视为已满足。
"""
import os
import re
import sys
import site

from packaging.markers import default_environment
from packaging.requirements import Requirement

# 本项目有意不装的包（freqtrade 与策略均零引用；technical 会拖入 ta-lib/bottleneck）
SKIP = {"ta-lib", "bottleneck", "technical"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def site_packages() -> str:
    for p in site.getsitepackages():
        if os.path.basename(p) == "site-packages":
            return p
    return os.path.join(sys.prefix, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages")


def main():
    sp = site_packages()
    env = default_environment()
    env["extra"] = None

    installed, reqs = set(), []
    for d in os.listdir(sp):
        if not d.endswith(".dist-info"):
            continue
        installed.add(norm(d.split("-")[0]))
        meta = os.path.join(sp, d, "METADATA")
        if os.path.exists(meta):
            with open(meta, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("Requires-Dist:"):
                        reqs.append(line.split(":", 1)[1].strip())

    required = set()
    for r in reqs:
        try:
            req = Requirement(r)
            if req.marker and not req.marker.evaluate(env):
                continue
            required.add(norm(req.name))
        except Exception:
            pass

    missing = sorted(required - installed - {norm(s) for s in SKIP})
    print(" ".join(missing), end="")


if __name__ == "__main__":
    main()
