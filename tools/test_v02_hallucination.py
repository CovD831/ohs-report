#!/usr/bin/env python3
"""V-02 词表锚定幻觉检测回归 (2026-10-14 重写)

旧实现: 建了 allowed 集却整段丢弃, 只查 4 个黑名单词 → 幻觉检测形同装饰。
新实现: 官方危害因素词表(740 词)扫正文, 命中且项目无源才报; 精度优先三道豁免。
用法: .venv/bin/python tools/test_v02_hallucination.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from web.app import _get_project_data  # noqa: E402
from web.validators import _check_factors, _load_factor_vocab, _sourced_factors  # noqa: E402

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


PID = "ada54603a9"
P = _get_project_data(PID)
terms, pat = _load_factor_vocab()
print("### 词表")
check("词表可加载且规模 >=500", len(terms) >= 500, True)
check("含 砷及其化合物 (GBZ 2.1 常见剧毒, 本项目无源)", "砷及其化合物" in terms, True)
check("类别词「振动」不入词表", "振动" in terms, False)
# 生产实测暴露的一类误报 (d4c2b5450c 的 5.2.2): 物理/放射性因素是**作业类别**不是投料
# 物质, 天然不进物料清单 → 按 hazard_factor.category 数据驱动排除
check("物理因素类别词按库内 category 排除 (工频电磁场)", "工频电磁场" in terms, False)
check("同上 (紫外线/低温/微波)",
      [w for w in ("紫外线", "低温", "微波") if w in terms], [])
check("化学因素仍保留在词表 (苯乙烯类)", any("苯乙烯" in t for t in terms), True)
check("ASCII 逗号物质名保持完整", "n,n-二甲基苯胺" in terms or "n，n-二甲基苯胺" in terms, True)
check("词表无带右括号尾巴的脏词元", [t for t in terms if "）" in t or ")" in t], [])

allowed = _sourced_factors(P)
print("\n### 有源集")
check("苯乙烯有源 (物料清单)", "苯乙烯" in allowed, True)
check("砷及其化合物无源", "砷及其化合物" in allowed, False)
check("柴油有源 (虽不在物料清单, 但材料原文出现)", "柴油" in allowed, True)

print("\n### 真阳性必须报")
h = _check_factors("本项目主要危害因素为砷及其化合物和甲醇。", P, None)
check("无源剧毒物质被报", [x["found"] for x in h if x["found"] == "砷及其化合物"] != [], True)
check("报的级别是 warn (不阻断生成)", h[0]["level"], "warn")
h2 = _check_factors("作业场所存在硫酸二甲酯浓度超标风险。", P, None)
check("历史幻觉词仍报 error",
      [x["level"] for x in h2 if x["found"] == "硫酸二甲酯"], ["error"])

print("\n### 四类误报必须静默")
check("有源物质不误报", _check_factors("苯乙烯、甲醇、甲苯危害程度评价。", P, None), [])
check("类比检测真因子不误报 (聚乙烯粉尘)",
      [x for x in _check_factors("聚乙烯粉尘接触水平低于限值。", P, None)
       if x["found"] == "聚乙烯粉尘"], [])
check("施工期语境豁免 (水泥粉尘)",
      [x for x in _check_factors("施工期间作业人员可能接触水泥粉尘、电焊烟尘。", P, None)
       if x["found"] == "水泥粉尘"], [])
check("法规条文引用豁免 (石棉粉尘)",
      [x for x in _check_factors("《职业病防治条例》第二十一条：应当对在具有矽尘、石棉粉尘"
                                 "和高毒物品等严重危害因素岗位加强监测。", P, None)
       if x["found"] == "石棉粉尘"], [])
check("类别词不误报 (防噪声振动)",
      _check_factors("拟采取防尘、防毒、防噪声振动及防高温设施。", P, None), [])
check("物理因素类别列举不误报 (生产 d4c2b5450c/5.2.2 原句)",
      _check_factors("本项目物理因素主要来源于生产设备运行及公用工程配套，涉及噪声、高温、"
                     "低温、工频电磁场及紫外线等类别；依据GBZ 2.2—2007 判定。", P, None), [])

print("\n### 防自证洗白: 只出现在正文/骨架里的词不得算有源")
p2 = dict(P)
p2["section_states"] = {"x": {"text": "砷及其化合物"}}
p2["built_tables"] = {"t": {"rows": [["砷及其化合物"]]}}
check("section_states/built_tables 不参与有源判定", "砷及其化合物" in _sourced_factors(p2), False)
check("被这样洗白后仍能报出来",
      [x["found"] for x in _check_factors("砷及其化合物接触水平超标。", p2, None)],
      ["砷及其化合物"])

print("\n### 真实语料零误报 (4 项目全节扫描)")
total = 0
scanned = 0
detail = []
for pid in ["ada54603a9", "c8c7ff0a4d", "2863a36451", "57ffa2b3b0"]:
    d = _get_project_data(pid)
    for sec, st in (d.get("section_states") or {}).items():
        text = (st or {}).get("text") or ""
        if len(text) < 30:
            continue
        scanned += 1
        for i in _check_factors(text, d, None):
            total += 1
            detail.append(f"{pid}/{sec} {i['found']}({i['level']})")
check(f"历史正文 {scanned} 节扫描: 命中 0 处 (481 命中经三道豁免归零)", detail, [])

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
