"""LLM 成文器 — 把机械结果组织成报告叙述 (三层架构的第③层)

原理:
  机械(已有): 识别/判定/分级 → 结构化数据
  LLM(本模块): 把结构化数据+知识库写成章节叙述文字
    输入: 项目概况(设备/物料/检测/判定) + 知识库(限值/健康效应/条款)
    约束: 数据来自机械结果, LLM不编造数值; 语言组织=报告惯用叙述
  人工(后续): 审核草稿/修改

用法: python3 -m web.llm_draft demo-cx [--section 10.2.3]
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
        "temperature": 0.3,  # 低温度: 数据一致性
    }).encode(), headers={"Authorization": f"Bearer {key}",
                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"]


def build_1023_prompt(project: dict, assess: dict) -> str:
    """10.2.3 工程概况叙述 prompt (数据全来自机械结果)"""
    chain = assess.get("industry_chain") or {}
    hazards = [h["factor"] for h in assess.get("hazards", [])]
    eqs = project.get("equipment", [])[:15]
    return f"""你是职业病危害预评价报告撰写专家。请根据以下结构化数据撰写 10.2.3 建设项目工程分析的正式叙述段落。

【项目信息】项目名称: {project.get('name', '')}
【行业链】{chain.get('full', '')}
【主要设备】{"; ".join(eqs)}
【主要危害因素】{"; ".join(hazards)}

要求:
1. 用正式报告语言叙述 (不是表格), 分小段
2. 不得编造数值/数据, 只用上面给的信息
3. 从工程角度描述: 项目概况→产品方案→主要装置→工艺流程→设备设施→主要职业病危害产生环节
4. 300-500字"""


def build_1025_prompt(project: dict, assess: dict) -> str:
    """10.2.5 危害分析叙述 prompt"""
    hz = []
    for h in assess.get("hazards", [])[:10]:
        hz.append(f"{h['factor']} (来源: {';'.join(h.get('sources', [])[:2])})")
    return f"""你是职业卫生评价专家。根据以下识别结果撰写 10.2.5.1 职业病危害因素识别分析的叙述。

【识别结果】{"; ".join(hz)}

要求:
1. 按产生环节叙述危害因素 (工艺过程/设备/原辅材料)
2. 只使用给定因素, 不添加新因素
3. 正式报告语言, 250-400字
4. 说明各种危害的接触岗位和可能影响"""


def build_prompt(pid: str, sec: str, project: dict, assess: dict) -> str:
    """按章节构建 prompt (数据全来自机械结果)"""
    chain = assess.get("industry_chain") or {}
    risk = assess.get("industry_risk") or {}
    hazards = [h["factor"] for h in assess.get("hazards", [])]
    eqs = project.get("equipment", [])[:15]
    dets = project.get("detections", [])
    det_str = "; ".join(f"{d['factor']}={d.get('ctwa')}" for d in dets[:10])
    # 通用信息块
    info = f"""【项目名称】{project.get('name', '')}
【行业链】{chain.get('full', '')}
【主要危害因素】{"、".join(hazards[:10])}
【主要设备】{"; ".join(eqs[:10])}
【检测数据】{det_str or "无"}
【风险分类】{risk.get('level', '')} ({risk.get('name', '')})"""

    prompts = {
        "10.2.1": f"""撰写 10.2.1 总论部分（评价目的、评价依据、评价范围、评价方法）的叙述。

{info}

要求:
1. 说明评价依据包括职业病防治法及 GBZ 标准体系（共 27 项，详见评价依据表）
2. 评价范围=项目投产运行期间主要职业病危害因素
3. 正式报告语言，200-300字""",
        "10.2.4": f"""撰写 10.2.4 类比企业（项目）调查与分析部分的叙述（9 要素可比性结论）。

{info}

要求:
1. 按 GBZ/T 196 附录C 九要素（自然环境/原料/规模/定员/制度/工艺/设备/防护/管理）说明可比性
2. 类比项目与拟建项目的相似性结论（可比/基本可比/不可比）
3. 正式报告语言，200-300字""",
        "10.2.6": f"""撰写 10.2.6 职业病防护设施分析与评价的叙述。

{info}

要求:
1. 说明拟采取的防护设施：化学毒物密闭化+局部排风（GBZ/T 194）、粉尘湿式作业、噪声隔声（GB/T 50087）、高温隔热通风
2. 各防护设施的标准依据（详见防护设施检查表13项）
3. 正式报告语言，200-300字""",
        "10.2.7": f"""撰写 10.2.7 应急救援措施的分析与评价的叙述。

{info}

要求:
1. 说明应急预案、应急喷淋洗眼（GB/T 38144）、密闭空间作业规范（GBZ/T 205 氧18-22%/可燃<10%LEL）
2. 应急救援设施及演练要求
3. 正式报告语言，200-300字""",
        "10.2.8": f"""撰写 10.2.8 个人使用的职业病防护用品的分析与评价的叙述。

{info}

要求:
1. 按危害类别说明配备：化学→防毒面具/化学防护服（GB 39800.1—2020）、粉尘→KN95、噪声→耳塞/耳罩
2. 说明选择依据和佩戴要求
3. 正式报告语言，200-300字""",
        "10.2.9": f"""撰写 10.2.9 职业卫生管理的分析与评价的叙述。

{info}

要求:
1. 说明健康监护安排（GBZ 188—2025，173条规则）、管理制度（GBZ/T 225—2010 十二项检查）
2. 管理机构、培训、档案、告知等要求
3. 正式报告语言，200-300字""",
        "10.2.10": f"""撰写 10.2.10 职业病危害关键控制点分析的叙述。

{info}

要求:
1. 说明主要危害因素及其控制级别（化学/粉尘/噪声/高温）
2. 关键控制点=危害程度×接触人数×防护水平
3. 正式报告语言，200-300字""",
        "10.2.12": f"""撰写 10.2.12 结论与建议的叙述。

{info}

要求:
1. 明确职业病危害类别（严重/一般）及依据
2. 总结存在的主要问题
3. 明确采取补充建议后的可行性
4. 正式报告语言，250-350字""",
        "10.2.2": f"""撰写 10.2.2 现有企业概况（改扩建项目）的叙述。

{info}

要求:
1. 说明现有企业建厂时间、所属行业、企业规模、职工/生产/接触人数（待用户填写）
2. 说明现有职业病危害现状（因子种类/分布/接触水平）
3. 说明职业卫生机构、管理制度、健康监护、职业病发病情况（待用户填写）
4. 正式报告语言，200-300字""",
        "10.2.11": f"""撰写 10.2.11 职业病防治措施的补充建议（问题→针对性建议）的叙述。

{info}

要求:
1. 针对存在的问题（超标/分级高/制度/防护/应急缺项）逐条提出建议
2. 建议措施引用标准（GBZ 2.1/GBZ/T 194/GBZ/T 225/GBZ 188）
3. 建议内容具体可执行（自动化/密闭化/通风/个体防护/监护）
4. 正式报告语言，250-400字""",
    }
    return prompts.get(sec, "")


def draft_section(pid: str, sec: str) -> str:
    """生成某章 LLM 草稿"""
    p = get_project(pid)
    conn = connect()
    project = {"name": p["name"], "industry": p["data"].get("industry", ""),
               "equipment": p["data"].get("equipment", []),
               "detections": p["data"].get("detections", []),
               "process_text": p["data"].get("process_text", "")}
    assess = assess_project(conn, project)
    conn.close()
    if sec == "10.2.3":
        prompt = build_1023_prompt(project, assess)
    elif sec == "10.2.5":
        prompt = build_1025_prompt(project, assess)
    else:
        prompt = build_prompt(pid, sec, project, assess)
    if not prompt:
        return "暂不支持该章节(可扩展)"
    return _llm(prompt)


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "demo-cx"
    sec = sys.argv[2] if len(sys.argv) > 2 else "10.2.3"
    print(f"=== LLM 成文: {sec} ===")
    print(draft_section(pid, sec))
