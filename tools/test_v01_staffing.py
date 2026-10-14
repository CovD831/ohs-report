#!/usr/bin/env python3
"""V-01 定员校验回归 (2026-10-14 接通)

背景: 旧实现算出 total 却从未放进 checks → 正文 293 人(合计行+明细行双计)全库无人报警。
现口径唯一产地 = web.derived_fields.staffing_totals (与班制定员表/投影层同源)。
用法: .venv/bin/python tools/test_v01_staffing.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


from web.validators import _check_numbers  # noqa: E402
from web.derived_fields import staffing_totals  # noqa: E402

# staffing: 明细 11 行(求和 144) + 材料自带「合计」149 —— 长兴项目真实形态
STAFF = [{"post": "制造课", "dept": "制造部", "count": "46"},
         {"post": "总务课", "dept": "管理部", "count": "21"},
         {"post": "营业部", "dept": "华东营业部", "count": "11"},
         {"post": "厂务课", "dept": "厂务部", "count": "10"},
         {"post": "管理部", "dept": "管理部", "count": "10"},
         {"post": "品保课", "dept": "品保部", "count": "8"},
         {"post": "技术课", "dept": "技术部", "count": "8"},
         {"post": "修护班", "dept": "制造部", "count": "8"},
         {"post": "公用班", "dept": "制造部", "count": "8"},
         {"post": "财务", "dept": "常熟财务区", "count": "11"},
         {"post": "化验", "dept": "品保部", "count": "3"},
         {"post": "合计", "dept": "合计", "count": "149"}]
PROJ = {"staffing": STAFF, "investment": "5000万元", "capacity": "12000吨/年"}

print("### 口径计算")
check("材料自带合计 → 以合计为准 (149)", staffing_totals(STAFF)[0], 149)
check("明细行求和 (144, 汇总行不计入)", staffing_totals(STAFF)[1], 144)
check("无合计行 → 明细求和",
      staffing_totals([{"post": "a", "count": "40"}, {"post": "b", "count": "60"}])[0], 100)
check("多个汇总行取最大值",
      staffing_totals([{"post": "小计", "count": "50"}, {"post": "合计", "count": "149"}])[0], 149)
check("空 staffing → None (不校验)", staffing_totals([])[0], None)

print("\n### 正文比对")


def hits(text):
    return [i for i in _check_numbers(text, PROJ) if i.get("field") == "定员"]


h = hits("全厂共设31个岗位，定员293人。")
check("双计错值 293 报警", len(h), 1)
check("诊断语指出双计成因", "二次相加" in (h[0]["note"] if h else ""), True)
check("正确口径 149 不报", hits("本项目劳动定员合计149人，其中反应班14人。"), [])
check("跨短句写法 293 也报 (旧版漏检)", len(hits("现有定员按31个岗位设置，共计293人。")), 1)
check("扩建口径「新增定员46人」不误报", hits("本项目拟新增定员46人，依托现有辅助用室。"), [])
check("子集人数「接触人数合计41人」不误报", hits("投料、包装环节接触人数合计41人。"), [])
check("女工数不参与比对", hits("全厂定员149人，其中女工 41 人。"), [])
check("中文数字=已知限制(不误报)", hits("全厂定员合计一百四十九人。"), [])
check("投资/产能旧校验未受影响",
      len([i for i in _check_numbers("本项目年产5000吨。", PROJ) if i.get("field") == "产能"]), 1)

print("\n### 无 staffing 数据时静默 (不把没材料当错)")
check("staffing 缺失 → 0 命中",
      len([i for i in _check_numbers("定员293人。", {"staffing": []}) if i.get("field") == "定员"]), 0)

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
