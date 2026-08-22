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
        return "暂不支持该章节(可扩展)"
    return _llm(prompt)


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "demo-cx"
    sec = sys.argv[2] if len(sys.argv) > 2 else "10.2.3"
    print(f"=== LLM 成文: {sec} ===")
    print(draft_section(pid, sec))
