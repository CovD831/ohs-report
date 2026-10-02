#!/usr/bin/env python3
"""本项目危害因素 → GBZ 188-2025 检查节 映射（11.5 职业健康监护表）

## 数据源与红线
- 节号**全部来自** `tools/extract_health_exam.py` 对
  `acquire/raw/nhc_gbz/pdfs/GBZ_188_2025.pdf` 原文的提取产物
  `data/gbz188_health_exam.json`，**不是凭记忆写的**（§⑲ 教训：
  上一版映射靠记忆写节号 → 丙酮/苯乙烯/乙酸乙酯/苯/硫酸/氢氧化钠/锰/紫外
  的节号**全错**，且其中若干因素标准里根本没有对应节）。
- **物质身份必须确定，禁止模糊/LIKE 匹配**（§⑰ 教训）。
- 无对应节的因素 → **不编造**，进 `UNCOVERED`，由正文诚实说明其体检依据。

## 覆盖边界（诚实标注）
GBZ 188-2025 第 5~7 章共 93 节，**只覆盖部分物质**。本项目检测到的下列因素
**标准无独立节**（已逐节核对全 93 节标题）：

    丙酮、乙酸乙酯、乙二醇、丙二醇、二乙二醇、苯乙烯、氢氧化钠、硫酸及三氧化硫、
    锑及其化合物、金属镍与难溶性镍化合物、三氧化铬、氮氧化物(有)、臭氧、甲醛、
    工频电场、照度、石蜡 …

→ 这些**不生成行**（与真稿一致：真稿表也只有 8 行，未列这些）。
"""
from __future__ import annotations
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# 节号 → 因素名（从提取产物实测得到，逐条可核对）
#   本项目侧因素名(写进真稿行名用) : GBZ188 节号
FACTOR_TO_SECTION: dict[str, str] = {
    # ── 粉尘 → 6.x（按**致尘肺病机制**分节, 不是一股脑归 6.7）──────────
    # 真稿「其他粉尘」一行 = 6.4 其他致尘肺病的无机粉尘（炭黑/石墨/滑石/云母…）
    "其他粉尘": "6.4",
    "滑石粉尘": "6.4", "滑石粉尘（呼尘）": "6.4", "滑石粉尘(呼尘)": "6.4",
    "炭黑粉尘": "6.4", "沉淀SiO2（白炭黑）": "6.4",
    "电焊烟尘": "6.4",
    # 矽尘 = 6.1 游离二氧化硅粉尘（结晶型, 含量≥10%）
    "游离二氧化硅": "6.1", "矽尘": "6.1", "矽尘（石英粉）": "6.1",
    # 有机粉尘（树脂/聚乙烯）= 6.6 有机粉尘
    "其他粉尘(树脂)": "6.6", "其他粉尘（树脂）": "6.6",
    "其他粉尘(环氧树脂)": "6.6", "其他粉尘（环氧树脂）": "6.6",
    "其他粉尘（颜料粉尘）": "6.6", "聚乙烯粉尘": "6.6",
    # 煤尘 = 6.2；石棉 = 6.3
    "煤尘": "6.2",
    "石棉粉尘": "6.3",
    # ── 化学因素（节号=提取产物实测值）──────────────────────
    "甲醇": "5.22",             # 5.22 甲醇
    "甲苯": "5.58",             # 5.58 甲苯（二甲苯参照执行）
    "二甲苯": "5.58",
    "邻苯二甲酸酐": "5.55",      # 5.55 酸雾或酸酐
    "马来酸酐": "5.55",
    "异氰酸酯类": "5.56",        # 5.56 致喘物（分类项 a) 异氰酸酯类）
    # ── 物理因素（GBZ188 第7章，节号实测）─────────────────────
    "噪声": "7.1",               # 7.1 噪声
    "高温": "7.3",               # 7.3 高温
    "紫外光": "7.5",             # 7.5 紫外辐射（紫外线）
    "紫外辐射(电焊弧光)": "7.5",
}

# 无 GBZ188 对应节的本项目因素（不生成行，正文诚实说明）
UNCOVERED = [
    "丙酮", "乙酸乙酯", "2-丁酮", "乙二醇", "丙二醇", "二乙二醇",
    "苯乙烯", "氢氧化钠", "硫酸及三氧化硫", "锑及其化合物",
    "金属镍与难溶性镍化合物", "三氧化铬", "臭氧", "甲醛",
    "工频电场", "照度", "石蜡", "甲基丙烯酸",
]

# 真稿表行名（用于生成时保持与真稿一致的显示名）
ROW_LABELS: dict[str, str] = {
    "6.4": "其他粉尘",
    "6.1": "矽尘（游离二氧化硅粉尘）",
    "6.6": "有机粉尘（树脂、聚乙烯等）",
    "5.22": "甲醇",
    "5.58": "甲苯、二甲苯",
    "5.55": "酸雾或酸酐（邻苯二甲酸酐、马来酸酐等）",
    "5.56": "异氰酸酯类、过氧化苯甲酰、过氧化二月桂酰、N,N-二乙基乙胺",
    "7.1": "噪声",
    "7.3": "高温",
    "7.5": "紫外辐射（电焊弧光）",
}


def load_sections() -> dict:
    """提取产物是 list[{factor, code, category, pre, on, cycle, off}] → 按 code 建索引"""
    p = os.path.join(_ROOT, "data", "health_exam.json")
    with open(p, encoding="utf-8") as f:
        rows = json.load(f)
    return {r["code"]: r for r in rows if r.get("code")}


def verify_mapping() -> dict:
    """校验: 每个节号必须在提取产物里存在, 且有检查内容（防手滑/防记忆写错）"""
    secs = load_sections()
    ok, bad = [], []
    for fac, sn in FACTOR_TO_SECTION.items():
        if sn not in secs:
            bad.append({"factor": fac, "section": sn, "reason": f"节 {sn} 不在产物中"})
        elif not (secs[sn].get("pre") or secs[sn].get("on")):
            bad.append({"factor": fac, "section": sn, "reason": f"节 {sn} 无检查内容"})
        else:
            ok.append((fac, sn))
    # 反向: 真稿 8 行对应的节是否都能生成
    rows = sorted(set(FACTOR_TO_SECTION.values()))
    return {"ok": ok, "bad": bad, "row_sections": rows}


def _tidy(s: str) -> str:
    """清提取残渣: 连续分号/多余括号冒号/首尾标点

    ⚠ 只做确定性字符清洗, 不改语义、不补内容。
    """
    if not s:
        return s
    s = s.replace("（按", "按").replace("按GBZ/T229.1）：", "按GBZ/T229.1：")
    s = re.sub(r"[；;]{2,}", "；", s)          # ；；→；
    s = re.sub(r"[：:][；;]", "：", s)          # "：；"→"：" (冒号后残留分号)
    s = re.sub(r"[；;][：:]", "：", s)          # "；："→"："
    s = re.sub(r"^[；;：:，,、]+", "", s)       # 去首标点
    s = re.sub(r"[；;：:，,、\s]+$", "", s)     # 去尾标点
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_health_exam_rows(pd: dict) -> list:
    """→ 体检项目与周期表 行 (照真稿 表5.1-1 的 6 列口径)

    口径来源: 真稿表按「体检项目相同」把本项目因素归并成行, 行名列出本项目实际
    接触的具体物质; 检查内容/周期取自 GBZ 188-2025 对应节原文。

    规则(全部确定性, 无 LLM):
      ① 本项目危害因素(取 assess.hazards / hazard_grid / detections 三源并集, 非白名单)
      ② 因素 → 节 (FACTOR_TO_SECTION, 节号实测)
      ③ 同一节的因素合并成一行; 行名用 ROW_LABELS
      ④ 岗中为空时填「同岗前」(原文只有应急检查的节, 如甲醇/甲苯)
      ⑤ 周期空 →「—」(诚实标注, 不编旧版周期)

    ⚠ 不编造: 因素无对应节 → 不生成行(见 UNCOVERED)。
    """
    import json as _json
    import os as _os

    secs = load_sections()
    facs: set[str] = set()
    for src in ("hazards", "hazard_grid", "detections"):
        for it in (pd.get(src) or []):
            if isinstance(it, dict):
                f = str(it.get("factor") or it.get("name") or "").strip()
            else:
                f = str(it).strip()
            if f:
                facs.add(f)
    # 归并: 节 → [因素...]
    by_sec: dict[str, list[str]] = {}
    for f in facs:
        sn = FACTOR_TO_SECTION.get(f)
        if sn:
            by_sec.setdefault(sn, []).append(f)
    rows = []
    for sn in sorted(by_sec):
        s = secs.get(sn) or {}
        label = ROW_LABELS.get(sn) or s.get("factor") or sn
        pre = _tidy(s.get("pre", "")) or "—"
        on = _tidy(s.get("on", "")) or "同岗前"
        cyc = _tidy(s.get("cycle", "")) or "—"
        off = _tidy(s.get("off", "")) or "同岗中"
        rows.append([label, "岗前、岗中、离岗", pre, on, cyc, off])
    return rows


if __name__ == "__main__":
    r = verify_mapping()
    print(f"✅ 可用映射 {len(r['ok'])} 条 → {len(set(s for _, s in r['ok']))} 个节")
    by_sec: dict[str, list[str]] = {}
    for f, s in r["ok"]:
        by_sec.setdefault(s, []).append(f)
    for s in sorted(by_sec):
        lbl = ROW_LABELS.get(s, "")
        print(f"   {s:6} [{lbl[:22]:22}] ← {'、'.join(by_sec[s][:5])}")
    if r["bad"]:
        print(f"\n🔴 可疑映射 {len(r['bad'])} 条:")
        for b in r["bad"]:
            print(f"   {b['factor']:24} → {b['section']:6} {b['reason']}")
    print(f"\n⚠ 标准无对应节(不生成行) {len(UNCOVERED)} 个因素")
