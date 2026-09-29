"""单测: 检测结果表"判定"列三态 — 禁止无条件写"合格"

背景: section_filler 原实现写死 "合格" (2 处)。
      视觉提取修复后 表型B(岗位×多因素) 记录进入 detections, 它们**没有测得值**,
      若仍写死"合格" = 为未检测的因素断言合格 = 编造结论。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from web.section_filler import _det_verdict  # noqa: E402

CASES = [
    # (记录, 期望判定, 说明)
    ({"judgement": "符合", "ctwa": "0.5"},        "符合",   "材料自带'符合'"),
    ({"judgement": "合格", "ctwa": "0.5"},        "符合",   "材料自带'合格' → 标准化"),
    ({"judgement": "不合格", "ctwa": "9.9"},      "不符合", "材料自带'不合格'"),
    ({"judgement": "不符合", "ctwa": "9.9"},      "不符合", "材料自带'不符合'"),
    ({"judgement": "", "ctwa": "0.5"},            "符合",   "有测得值 → 已判过 (ctwa 按定义是浓度)"),
    ({"judgement": "", "results": ["<0.3"], "table_type": "A"},
     "符合", "★表型A 的 results 是逐样检测值 → 可判"),
    ({"judgement": "", "results": ["0.5"], "table_type": "B"},
     "—",     "★★表型B 的 results 是'采样时间(小时)' → 不得判合格"),
    ({"judgement": "", "results": ["2"], "table_type": ""},
     "—",     "★★表型缺省/未知 → 保守不判"),
    ({"judgement": "", "results": [], "table_type": "A"}, "—", "表型A 但空 results → 待补充"),
    ({"judgement": "", "ctwa": ""},               "—",      "★无值无判定 → 待补充 (原会写成'合格')"),
    ({"judgement": "", "ctwa": "", "results": []}, "—",     "★空 results → 待补充"),
    ({"judgement": "浓度或强度相对稳定"},          "浓度或强度相对稳定", "材料原话保留"),
    ({"judgement": "", "ctwa": "—"},              "—",      "占位符'—'不算值"),
]

ok = 0
for rec, want, desc in CASES:
    got = _det_verdict(rec)
    good = got == want
    ok += good
    mark = "✓" if good else "✗"
    print(f"  {mark} {desc}")
    if not good:
        print(f"      期望={want!r} 实得={got!r}  记录={rec}")

print()
print(f"判定三态: 通过 {ok}/{len(CASES)}")
# 关键红线: 绝不为无值记录断言合格
bad = [r for r, w, _ in CASES if w == "—" and _det_verdict(r) == "符合"]
print("红线检查 (无值不得判'符合'):", "✓ 通过" if not bad else f"✗ {bad}")
sys.exit(0 if ok == len(CASES) and not bad else 1)
