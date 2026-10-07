# 已归档脚本（2026-10-07 项目收缩，80 → 24）

结论已固化在 RESEARCH.md 的一次性研究脚本、已收线方向（套利族、单品种 CTA、新策略族搜索、
合成期权等）的工具。RESEARCH 里引用的 `user_data/scripts/X.py` 若在此目录，路径加 `archive/`。

**复用前必须先迁移**：本目录脚本未接入 `research_lib`（相对路径 / 硬编码切分 / ÷$1,000 口径 /
按"最新 zip/报告"取数等旧问题都可能存在），部分 import research_lib 的脚本在此目录下也找不到它。
迁移 = 移回上一级 + 改用 research_lib + 跑 `pytest user_data/scripts/tests`。

H8c 双腿基差套利 paper 模拟器（h8c_paper.py / h8c_paper_start.sh）随套利族收线一并归档；
paper 设备上的对应 cron（`17 * * * * ... h8c_paper.py`）需手动删除。
