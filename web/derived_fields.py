"""派生字段提取 — 补齐真实报告 1.1 基本情况里我们缺失的两个字段

调研依据 (8份真实报告 1.1 基本情况 频次):
  辐射源项  6/8 — 我们完全没有; 且与"是否生成电离辐射章节"直接相关
  工作制度  7/8 — 我们没有; 真实报告里是聚合量
                  (如"劳动定员10人, 年工作日300天, 单班制, 每班8h, 年工作2400h")

两者都是**纯规则**导出, 不经 LLM (符合用户"认可能计算的绝不生成"原则)。
"""
from __future__ import annotations

import re

# ============ 辐射源项 ============
# 判据与 web/conditional_sections.py 的"防电离辐射设施"保持一致 (同源, 勿各写一份)
RADIATION_KEYWORDS = [
    "X射线", "X-Ray", "Xray", "x-ray", "放射源", "探伤", "加速器", "中子", "辐照",
    "CT机", "γ", "γ射线", "钴-60", "钴60", "铯-137", "铯137", "铱-192", "铱192",
    "密封源", "放射性", "电离辐射", "同位素", "射线检测", "面密度检测仪",
]
# 明确"无"的表述 (真实报告多为 "辐射源项：无")
_NONE_RE = re.compile(r"^\s*[无沒有]|不涉及|无辐射")


def _scan_texts(project: dict) -> list[str]:
    """收集用于扫描的文本 (设备名优先 — 辐射源是设备属性)"""
    texts = []
    for k in ("equipment", "equipment_detail", "process_text", "protection", "facilities"):
        v = project.get(k)
        if isinstance(v, str):
            texts.append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, dict):
                    texts.append(" ".join(str(vv) for vv in x.values() if vv))
                elif x:
                    texts.append(str(x))
    return texts


def extract_radiation(project: dict) -> str:
    """辐射源项: 命中设备名则列出, 否则 '无'

    返回真实报告同款形态: "X-Ray测试机、正负极面密度检测仪" 或 "无"
    """
    hits: list[str] = []
    for t in _scan_texts(project):
        for kw in RADIATION_KEYWORDS:
            if kw in t:
                # 取设备名本身 (截到合理长度), 避免塞整段文本
                nm = t.strip()[:24]
                if nm and nm not in hits:
                    hits.append(nm)
                break
    if not hits:
        return "无"
    # 设备条目里常有整行拼接, 尽量取前 4 项
    return "、".join(hits[:4]) + ("等" if len(hits) > 4 else "")


def has_radiation(project: dict) -> bool:
    """是否有电离辐射源项 (供 conditional_sections 复用, 避免两处判据漂移)"""
    return not _NONE_RE.match(extract_radiation(project))


# ============ 工作制度 ============
_WORKDAYS_RE = re.compile(r"(\d{2,3})\s*(?:个)?\s*(?:工作)?日|年工作日\s*[:：]?\s*(\d{2,3})")
_HOURS_PER_SHIFT_RE = re.compile(r"每班\s*(?:工作)?\s*(\d+(?:\.\d+)?)\s*(?:个)?\s*(?:小时|h|H)")
_ANNUAL_HOURS_RE = re.compile(r"年(?:工作)?(?:时间|时数|工时)\s*[:：]?\s*(\d{3,5})\s*(?:小时|h|H)?")
_SHIFT_WORDS = [
    ("四班三运转", "四班三运转"), ("三班两倒", "三班两倒"),
    ("四班", "四班制"), ("三班", "三班制"), ("两班", "两班制"), ("二班", "两班制"),
    ("单班", "单班制"), ("常白班", "常白班"), ("白班", "常白班"),
]


def _from_shifts(project: dict) -> str:
    """从 shifts 表规则聚合班制"""
    sf = project.get("shifts") or []
    sysms: list[str] = []
    for x in sf:
        if isinstance(x, dict):
            s = str(x.get("system") or x.get("班制") or "").strip()
            if s and s not in sysms and s != "—":
                sysms.append(s)
    return "、".join(sysms[:3])


def _from_text(project: dict) -> tuple[str, str, str]:
    """从自由文本 (process_text / 材料原文) 抽 工作日 / 每班时长 / 年工时"""
    blob = " ".join(
        str(project.get(k) or "") for k in ("process_text", "work_system_text", "staffing_text")
    )
    wd = ""
    m = _WORKDAYS_RE.search(blob)
    if m:
        wd = (m.group(1) or m.group(2) or "").strip()
    hps = ""
    m = _HOURS_PER_SHIFT_RE.search(blob)
    if m:
        hps = m.group(1)
    ah = ""
    m = _ANNUAL_HOURS_RE.search(blob)
    if m:
        ah = m.group(1)
    return wd, hps, ah


def extract_work_system(project: dict) -> str:
    """工作制度: 聚合成年人话 (对标真实报告 "定员10人, 年工作日300天, 单班制")

    纯规则, 只做加总与拼接; 材料没给的项不编造。
    """
    parts: list[str] = []

    # 定员
    staffs = project.get("staffing") or []
    total = 0
    for x in staffs:
        if isinstance(x, dict):
            m = re.search(r"\d+", str(x.get("count") or ""))
            if m:
                total += int(m.group(0))
    if total:
        parts.append(f"劳动定员{total}人")

    # 班制
    sysm = _from_shifts(project)
    if not sysm:
        blob = str(project.get("process_text") or "")
        for kw, canon in _SHIFT_WORDS:
            if kw in blob:
                sysm = canon
                break
    if sysm:
        parts.append(sysm)

    # 工作日 / 每班时长 / 年工时
    wd, hps, ah = _from_text(project)
    if wd:
        parts.append(f"年工作日{wd}天")
    if hps:
        parts.append(f"每班{hps}小时")
    if ah:
        parts.append(f"年工作{ah}小时")
    elif wd and hps:
        # 可算则算 (认可能计算的绝不生成), 但只在两项都拿到时
        try:
            parts.append(f"年工作{int(wd) * int(float(hps))}小时")
        except ValueError:
            pass

    if not parts:
        return ""
    return "，".join(parts)


# ============ 统一入口 ============
def derive_fields(project: dict) -> dict:
    """产出派生字段 → 并入 project data (导入时调用)"""
    return {
        "radiation": extract_radiation(project),
        "work_system": extract_work_system(project),
    }


if __name__ == "__main__":
    import json
    import sqlite3
    import sys

    sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
    c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
    c.row_factory = sqlite3.Row
    for pid in [r[0] for r in c.execute("SELECT id FROM project LIMIT 40")][:6]:
        d = json.loads(c.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()[0])
        if not d.get("equipment"):
            continue
        f = derive_fields(d)
        print(f"--- {pid} | {str(d.get('company') or d.get('name') or '')[:24]}")
        print(f"    radiation   = {f['radiation']!r}")
        print(f"    work_system = {f['work_system']!r}")
