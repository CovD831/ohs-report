#!/usr/bin/env python3
"""V-03 必覆盖要素 单一产地 + 防漂移门禁 (2026-10-14)

旧态: web/validators.MUST_COVER (关键词) 与 web/structure_data.MUST_COVER_INFO (散文)
      两份平行清单, 各改各的 —— 生成侧加了要素, 校验侧不知道 = 漏检且无人报警。
现态: 登记表唯一产地 = web/structure_data.MUST_COVER_CHECK; validators 只读它;
      本测试把"两份必须对齐"从注释自觉升级为机器门禁。
用法: .venv/bin/python tools/test_must_cover_parity.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import web.validators as V  # noqa: E402
from web.structure_data import MUST_COVER_CHECK, MUST_COVER_INFO, chapter_must_cover  # noqa: E402

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


print("### 唯一产地")
check("validators 不再自带清单 (旧 MUST_COVER 已删)", hasattr(V, "MUST_COVER"), False)
check("判据读的是 structure_data.MUST_COVER_CHECK", "MUST_COVER_CHECK" in
      __import__("inspect").getsource(V._check_must_cover), True)

print("\n### 两份清单必须同节")
check("键集完全一致", set(MUST_COVER_CHECK) ^ set(MUST_COVER_INFO), set())
check("节数 >= 20 (登记规模未缩水)", len(MUST_COVER_CHECK) >= 20, True)

print("\n### 判据要素必须真的投喂给了 LLM")
# 每个要素至少一个关键词出现在 chapter_must_cover(sec) (= 骨架+笔法+标准条款全文)
# ⚠ 比对全文而非静态散文: 11.6「气体检测」的 氧含量/19.5% 由 _std_clauses 动态注入
missing = []
for sec, items in MUST_COVER_CHECK.items():
    full = chapter_must_cover(sec)
    for name, kws in items:
        if not any(k in full for k in kws):
            missing.append((sec, name, list(kws)[:3]))
check(f"全部 {sum(len(v) for v in MUST_COVER_CHECK.values())} 个要素均被投喂", missing, [])

print("\n### 判据行为 (缺一要素必须报, 齐了必须静默)")
full_116 = ("作业前插入盲板并拆除管道、断电上锁挂牌; 清洗置换后通风; "
            "氧含量 19.5%~21% 检测合格; 办理作业许可证并设专人监护; 作业中断超时须重新检测")
check("要素齐全 → 0 issue", V._check_must_cover("11.6", full_116), [])
check("只写清洗置换+氧含量 → 隔绝/作业许可/中断重测 三项全报",
      sorted(i["item"] for i in V._check_must_cover("11.6", "清洗置换后测氧含量 19.5% 合格")),
      ["中断重测", "作业许可", "隔绝"])
check("补上许可与中断复测 → 仅剩隔绝",
      [i["item"] for i in V._check_must_cover(
          "11.6", "清洗置换后测氧含量 19.5% 合格; 办理作业许可证并专人监护; 中断超时须复测")],
      ["隔绝"])
check("未登记的节不臆造要求", V._check_must_cover("3.1", "任意正文"), [])

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
