"""LLM 成文器 — 通用化 prompt 体系 (不写死项目数据)

原则:
  prompt = 通用结构要求 (任何项目适用) + 动态数据 (从项目/知识库注入)
  禁止: 写死某项目的具体数值/设备/工序 (换项目即错)
  动态数据注入: info 块 (项目名/行业/危害/设备/检测+OEL限值/原料)
  通用结构: 每个章节的"写法角度" (标准要求的角度, 跨项目通用)

数据来源 (全部动态):
  info.检测数据: 检测值 + 系统查 OEL 限值 (不做人工对照)
  info.原料: 原辅材料清单 (A2a 提取)
  info.工序: 工艺说明 (A2b 提取)
"""
import json
import os
import sys
from pathlib import Path

import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge.project_assess import assess_project  # noqa: E402
from knowledge.oel import connect  # noqa: E402
from web.projects_db import get_project  # noqa: E402

BASE = "https://api.deepseek.com/v1/chat/completions"


def _llm(prompt: str, system: str = "你是职业卫生评价专家, 撰写正式的职业病危害预评价报告文字。") -> str:
    """调用 DeepSeek"""
    env = Path("/Users/abaaba/.hermes/.env")
    key = ""
    for ln in env.read_text().splitlines():
        if ln.startswith("DEEPSEEK_API_KEY="):
            key = ln.split("=", 1)[1].strip()
            break
    req = urllib.request.Request(BASE, data=json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": 0.3,
    }).encode(), headers={"Authorization": f"Bearer {key}",
                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"]


def _build_info(project: dict, assess: dict) -> str:
    """动态信息块: 项目数据 + 知识库限值 (通用)"""
    conn = connect()
    chain = assess.get("industry_chain") or {}
    risk = assess.get("industry_risk") or {}
    hazards = assess.get("hazards", [])
    eqs = project.get("equipment", [])[:20]

    # 危害因素: 因子+来源
    hz_str = "; ".join(f"{h['factor']}({'、'.join(h.get('sources', [])[:2])})" for h in hazards[:15]) or "无"

    # 检测数据: 值 + OEL 限值 (系统查询, 不写死)
    det_parts = []
    for d in project.get("detections", [])[:15]:
        fname = d.get("factor", "")
        val = d.get("ctwa")
        limit = None
        for r in conn.execute(
                "SELECT oel_type, value, unit FROM oel_limit WHERE factor_name LIKE ? LIMIT 1",
                (fname + "%",)):
            limit = f"{r[0]}={r[1]}{r[2]}"
            break
        det_parts.append(f"{fname}={val} (限值:{limit or '查GBZ 2.1'})")
    det_str = "; ".join(det_parts) or "无"

    # 工序 (从工艺文本)
    procs = [ln.strip().lstrip("- ") for ln in (project.get("process_text") or "").splitlines()
             if ln.startswith("- 车间")][:12]
    proc_str = "\n".join(procs) or "无工艺文本"

    # 设备 (推断工艺水平)
    eq_str = "、".join(str(e).split("|")[0] for e in eqs[:12]) or "无"

    # 原料 (A2a)
    mats = []
    f = Path(__file__).resolve().parent.parent / "data" / "materials" / "A2a_原辅材料清单.csv"
    if f.exists():
        import csv
        with open(f, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                n = (r.get("名称") or "").strip()
                if n:
                    mats.append(n)
    mat_str = "、".join(mats[:15]) or "无"
    conn.close()

    return f"""【项目名称】{project.get('name', '')}
【行业链】{chain.get('full', '')}
【危害因素】{hz_str}
【设备】{eq_str}
【工序】{proc_str}
【原辅材料】{mat_str}
【检测数据(含OEL限值, 系统查询)】{det_str}
【风险分类】{risk.get('level', '')}({risk.get('name', '')})"""


# ===== 通用章节 prompt (结构=标准角度, 数据=info动态) =====
def _design_base() -> str:
    """标准措施条款摘要 (系统查询, 供建议类prompt)"""
    conn = connect()
    lines = []
    for r in conn.execute("SELECT check_point, std_code, clause FROM protection_rule"):
        lines.append(f"- [{r[1]} {r[2]}] {r[0][:40]}")
    for r in conn.execute("SELECT require, std_code, clause FROM management_rule"):
        lines.append(f"- [{r[1]} {r[2]}] {r[0][:40]}")
    conn.close()
    return "\n".join(l for l in lines if l)[:1500]


def build_section_prompt(sec: str, info: str) -> str:
    """按章节返回通用 prompt (无写死数据, 结构跨项目)"""
    DESIGN_BASE = _design_base()
    prompts = {
        "10.2.1": f"""撰写 10.2.1 总论的叙述。

{info}

要求:
1. 评价目的: 识别预测拟建项目职业病危害, 提出防治措施
2. 评价依据: 职业病防治法+GBZ标准体系(详见评价依据表, 标准现行版由系统核验)
3. 评价范围: 投产运行期间主要危害因素及防治
4. 评价方法: 工程分析法/类比法/检查表法
5. 正式报告语言, 250-350字""",

        "10.2.2": f"""撰写 10.2.2 现有企业概况(改扩建项目)的叙述。

{info}

要求:
1. 现有企业建厂时间/行业/规模/人数(字段表见采集表, 数据待用户填)
2. 现有职业病危害现状概述
3. 职业卫生机构/制度/监护现状
4. 正式报告语言, 200-300字""",

        "10.2.3.7": f"""撰写 10.2.3.7 建筑卫生学分析与评价的叙述。

{info}

要求 (通用角度):
1. 选址与总体布局: 产生危害车间布置/功能分区 (GBZ 1—2010 5.1/5.2.1)
2. 建筑卫生学: 车间卫生特征等级/采光(GB/T 50033)/照度(GB/T 50034-2024)
3. 辅助用室: 浴室/更衣室/盥洗 配置 (GBZ 1—2010 表10/表11 卫生特征1-4级)
4. 暖通: 自然通风/机械通风 (GB 50019)
5. 结合本项目实际危害因素判断适用条款
6. 正式报告语言, 300-450字""",

        "10.2.4": f"""撰写 10.2.4 类比企业(项目)调查与分析。

{info}

要求:
1. 类比项目选择原则 (同行业/同原料/同工艺优先)
2. 九要素可比性: 自然环境/原料/规模/定员/制度/工艺/设备/防护/管理 (附录C)
3. 类比结论: 可比/基本可比/不可比 及依据
4. 正式报告语言, 250-350字""",

        "10.2.10": f"""撰写 10.2.10 职业病危害关键控制点分析的叙述。

{info}

要求 (通用: 岗位×危害×措施):
1. 按岗位(或工序)列关键控制因子 (从【工序】【危害因素】判断)
2. 每个控制点: 控制级别(★★★/★★/★) + 控制措施(密闭化/通风/个体防护/监护)
3. 关键控制点判定: 危害程度×接触人数×防护水平
4. 正式报告语言, 350-550字""",

        "10.2.11": f"""撰写 10.2.11 职业病防治措施的补充建议。
相关标准措施条款（系统从标准库查询，建议必须引用这些条款）:
{DESIGN_BASE}

{info}

要求:
1. 每条建议引用上述标准条款（标准号+条款号）
2. 六项通用建议: "三同时"/监测检测/健康监护/警示标识/培训/经费
3. 结合【检测数据】超标或接近限值的因素重点建议
4. 建议具体可执行（措施+依据）
5. 正式报告语言, 400-600字""",

        "10.2.12": f"""撰写 10.2.12 结论与建议的叙述。

{info}

要求:
1. 职业病危害类别判定(严重/一般)及依据
2. 存在主要问题总结
3. 可行性结论 (采取补充建议后)
4. 正式报告语言, 250-350字""",
    }
    return prompts.get(sec, "")


def build_1023_prompt(project: dict, assess: dict, info: str) -> str:
    """10.2.3 工程分析 (通用结构, 数据动态)"""
    chain = assess.get("industry_chain") or {}
    hazards = [h["factor"] for h in assess.get("hazards", [])]
    eqs = project.get("equipment", [])[:25]
    return f"""你是职业病危害预评价报告撰写专家。撰写 10.2.3 建设项目工程分析的正式叙述。

{info}

要求 (通用结构, 数据从【设备】【工序】【原辅材料】动态取):
1. 基本情况: 项目名称/性质/生产规模/地点
2. 工程内容: 主要生产装置/辅助公用工程
3. 生产工艺流程: 按【工序】逐段简述
4. 主要职业病危害产生环节 (设备/工序→危害)
5. 不得编造数据, 只用信息块内容
6. 400-600字"""


def build_1025_prompt(assess: dict, info: str) -> str:
    """10.2.5 危害分析 (通用三角度, 数据动态)"""
    return f"""你是职业卫生评价专家。撰写 10.2.5 职业病危害因素识别及危害程度分析的叙述。

{info}

要求 (通用结构):
1. 生产工艺过程识别: 按【工序】逐段 → 化学因素 (从【危害因素】取)
2. 生产环境识别: 照明/微气候/通风不良 (如项目有)
3. 劳动过程识别: 体力劳动强度/人机工效 (如项目有)
4. 施工过程识别: 基坑/高处/焊接 (如项目有)
5. 健康影响: 各因素可能导致的职业病
6. 只写数据支持的内容, 不编造; 500-800字"""


def build_1026_prompt(info: str) -> str:
    """10.2.6 防护设施 (通用四层结构, 数据从检测信息动态度评价)"""
    return f"""撰写 10.2.6 职业病防护设施分析与评价的叙述。

{info}

要求 (通用四层结构):
1. 【防毒设施】:
   - 工艺控制: 密闭化/管道化/自动化程度 (从【设备】【工序】判断)
   - 局部排风: 排毒点设排风罩(GB/T 16758)/排毒系统(GBZ/T 194 3.8)
   - 物料防护: 设备密闭/投料控制
2. 【防尘设施】: 湿式作业/除尘(GBZ 1—2010 7.1.2), 投料防逸散 (如项目有粉尘)
3. 【防噪声设施】: 隔声/消声/减振(GB/T 50087) (如项目有高噪设备)
4. 【防高温设施】: 隔热/通风/降温 (如项目有热源)
5. 【效果评价】: 用【检测数据(含OEL限值)】对照 GBZ 2.1 限值定量判断
   (每个有检测值的因素: 数值对比限值→低于OEL则措施有效)
6. 只写信息块支撑的内容(设备/检测/工序), 不编造项目特性
7. 正式报告语言, 500-800字"""


def draft_section(pid: str, sec: str) -> str:
    """生成某章 LLM 草稿 (通用prompt体系)"""
    p = get_project(pid)
    conn = connect()
    project = {"name": p["name"], "industry": p["data"].get("industry", ""),
               "equipment": p["data"].get("equipment", []),
               "detections": p["data"].get("detections", []),
               "process_text": p["data"].get("process_text", "")}
    assess = assess_project(conn, project)
    conn.close()
    info = _build_info(project, assess)
    if sec == "10.2.3":
        prompt = build_1023_prompt(project, assess, info)
    elif sec == "10.2.5":
        prompt = build_1025_prompt(assess, info)
    elif sec == "10.2.6":
        prompt = build_1026_prompt(info)
    else:
        prompt = build_section_prompt(sec, info)
    if not prompt:
        return "暂不支持该章节(可扩展)"
    return _llm(prompt)


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "demo-cx"
    sec = sys.argv[2] if len(sys.argv) > 2 else "10.2.6"
    print(f"=== LLM 成文: {sec} (通用prompt) ===")
    print(draft_section(pid, sec))
