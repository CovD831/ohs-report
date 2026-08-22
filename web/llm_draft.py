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
    """10.2.3 工程概况叙述 prompt (对齐原报告1.1-1.3 子结构)"""
    chain = assess.get("industry_chain") or {}
    hazards = [h["factor"] for h in assess.get("hazards", [])]
    eqs = project.get("equipment", [])[:25]
    return f"""你是职业病危害预评价报告撰写专家。请根据以下结构化数据撰写 10.2.3 建设项目工程分析的正式叙述段落。

【项目信息】项目名称: {project.get('name', '')}
【行业链】{chain.get('full', '')}
【主要设备】{"; ".join(eqs)}
【主要危害因素】{"; ".join(hazards)}

要求（对齐原报告1.1-1.3 子结构）:
1. 1.1 基本情况: 项目名称/性质/生产规模/拟建地点/周边环境
2. 1.2 工程内容: 1.2.x 主要生产装置/辅助及公用工程/环保设施
3. 1.3 生产工艺流程分节: 酯化→纯化→洗涤→溶剂回收 各工序简述
4. 主要职业病危害产生环节 (设备/工序→危害因素)
5. 不得编造数值/数据, 只用给定信息
6. 400-600字"""


def build_1025_prompt(project: dict, assess: dict) -> str:
    """10.2.5 危害分析叙述 prompt (三角度识别, 对齐原报告2.1.1-2.1.4)"""
    hz = []
    for h in assess.get("hazards", [])[:10]:
        hz.append(f"{h['factor']} (来源: {';'.join(h.get('sources', [])[:2])})")
    return f"""你是职业卫生评价专家。根据以下识别结果撰写 10.2.5 职业病危害因素识别及危害程度分析的叙述。

【识别结果】{"; ".join(hz)}

要求（三角度识别, 对齐原报告2.1.1-2.1.4结构）:
1. 生产工艺过程识别: 酯化/纯化/洗涤/溶剂回收等工序→化学因素
2. 生产环境识别: 工作环境照明、微气候、通风不良等
3. 劳动过程识别: 体力劳动强度(Ⅰ-Ⅳ)、人机工效(姿势/搬运)
4. 建设施工过程识别: 基坑/高处/焊接/粉尘噪声(如适用)
5. 健康影响: 各因素可能导致的职业病(GBZ 188 对应)
6. 正式报告语言，400-600字"""


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

要求（措施+效果评价两角度, 对齐原报告3.1.x结构）:
1. 拟采取防护设施: 化学毒物密闭化+局部排风(GBZ/T 194 4.2.2/3.8)、粉尘湿式作业、
   噪声隔声罩/减振(GB/T 50087)、高温隔热+通风(GBZ 1 7.1.1)
2. 防护设施效果评价: 结合类比检测(环己烷<1/异丙醇<0.7/苯乙烯<1.7均达标→
   防护措施可使化学物浓度低于OEL)、噪声降至≤85dB(A)、高温WBGT≤28℃
3. 各设施的标准条款
4. 正式报告语言，350-500字""",
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
        "10.2.3.7": f"""撰写 10.2.3.7 建筑卫生学分析与评价的叙述。

{info}

要求（按原文要求角度）:
1. 选址与总体布局符合性: 产生职业病危害的车间布置、功能分区（GBZ 1—2010 5.1/5.2.1）
2. 建筑卫生学: 车间卫生特征等级（1-4级）、采光（GB/T 50033）、照明照度（GB/T 50034-2024 表5.4.1）
3. 辅助用室配置: 浴室/更衣室/盥洗（GBZ 1—2010 表10/表11, 1级3人/2级6人/3级9人/4级12人）
4. 暖通空调: 自然通风/机械通风（GB 50019—2015）
5. 正式报告语言，300-400字""",
        "10.2.10": f"""撰写 10.2.10 职业病危害关键控制点分析的叙述。

{info}

要求（按岗位×危害×措施角度, 对齐原报告11.x结构）:
1. 按岗位(工序)列关键控制因子 (如酯化加料岗位→甲苯/环己烷等)
2. 每个控制点说明控制级别(★★★/★★/★)和控制措施(密闭化/排风/个体防护/监护)
3. 关键控制点=危害程度×接触人数×防护水平 综合判定
4. 正式报告语言，350-500字""",
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
        "10.2.11": f"""撰写 10.2.11 职业病防治措施的补充建议的叙述。

{info}

要求（六项分项建议, 对齐原报告5.1-5.6结构）:
1. "三同时"落实: 防护设施与主体工程同时设计施工投产
2. 职业病危害监测检测: 定期委托检测机构检测(GBZ/T 225 4.5)
3. 职业健康监护: 上岗前/在岗/离岗/应急检查(GBZ 188—2025)
4. 警示标识: 危害岗位设警示标识+中文说明(GBZ 158—2003)
5. 职业卫生培训: 负责人/管理人员/劳动者(GBZ/T 225 4.10)
6. 职业病防治经费: 防护设施/检测/应急/PPE/体检/培训与台账(10.2.9)
7. 正式报告语言，400-600字""",
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
