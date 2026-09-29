"""定性: 表型B 被丢弃是否真的造成信息损失?

前提: 新泰有两套表
  A. 表型A 逐因素浓度 (105 条, 保留)  ← "每个因素测了多少"
  B. 表型B 岗位×多因素 (55 条, 丢弃)   ← "每个岗位接触哪些因素"

若 A 已覆盖 B 提到的因素 → B 丢弃**不损失浓度信息**,
B 唯一独有的是"岗位↔因素"的映射关系 (识别信息)。
"""
import json
from pathlib import Path

d = json.loads(Path("/Users/abaaba/Projects/ohs-report/data/vision_cache/"
                    "a66517be268181e6.json").read_text())
dets = d["detections"]

A = [x for x in dets if x.get("table_type") == "A"]
B = [x for x in dets if x.get("table_type") == "B"]


def norm(s):
    return str(s or "").replace("（", "(").replace("）", ")").replace(" ", "").strip()


# A 的因素集合
A_fac = {}
for x in A:
    f = norm(x.get("factor"))
    if f and (x.get("ctwa") or x.get("cstel") or x.get("results")):
        A_fac.setdefault(f, []).append(x)

# B 提到的因素集合
B_fac = set()
for x in B:
    for f in str(x.get("factor") or "").replace(",", "、").split("、"):
        nf = norm(f)
        if nf:
            B_fac.add(nf)

print(f"表型A 有浓度的因素: {len(A_fac)} 个")
print(f"  {sorted(A_fac)[:14]}")
print()
print(f"表型B 提到的因素: {len(B_fac)} 个")
print(f"  {sorted(B_fac)}")
print()

# 关键: B 提到但 A 没测的
missing = B_fac - set(A_fac)
covered = B_fac & set(A_fac)
print(f"B 提到且 A 已测: {len(covered)}")
print(f"B 提到但 A 未测: {len(missing)}")
print(f"  {sorted(missing)}")
print()
print("=== 判定 ===")
if not missing:
    print("  B 丢弃**不损失浓度信息** (A 已全覆盖); B 独有的是'岗位↔因素'映射")
else:
    print(f"  ⚠ B 独有 {len(missing)} 个因素无浓度 → 这些是'识别出但未测'")
    print("     按真实报告惯例应标'未检测'或在识别表体现, 而非编造浓度")

print()
print("=== B 记录里的岗位样本 (这是 B 独有的信息) ===")
for x in B[:5]:
    print(f"  {str(x.get('sampling_point'))[:46]:48} → {str(x.get('factor'))[:40]}")
