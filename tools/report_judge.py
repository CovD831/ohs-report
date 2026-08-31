#!/usr/bin/env python3
"""
报告 BenchMark — 非确定性层 (LLM Judge)
================================================
对生成的预评价报告做主观维度评审 (通用, 每份报告可跑):
  G1 主题契合: 每章讲该章的事 (不串章/不重复)
  G2 数据严谨: 数据引用有据, 不编造数字
  G3 逻辑连贯: 章节内部逻辑自洽, 结论与正文一致
  G4 文风规范: 无套话/AI腔, 符合报告体
  G5 覆盖度: 关键内容要素齐全 (对应11章标准)

用法:
  python tools/report_judge.py --docx path/to/report.docx [--reference path/to/原报告.docx] [--provider deepseek]
输出: JSON (每维度 1-5 分 + 意见 + 问题清单)
"""
from __future__ import annotations
import argparse, json, sys, os, re
from pathlib import Path

# 从环境读 API key (复用 Hermes 的 DeepSeek key, 绝不硬编码)
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
if not DEEPSEEK_KEY:
    # 回退: Hermes .env (本地开发/评测环境)
    _envf = Path.home() / ".hermes" / ".env"
    if _envf.exists():
        for ln in _envf.read_text().splitlines():
            if ln.startswith("DEEPSEEK_API_KEY="):
                DEEPSEEK_KEY = ln.split("=", 1)[1].strip()
                break
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"


def _llm(prompt: str, system: str, provider: str = "deepseek") -> str:
    import urllib.request
    body = json.dumps({
        "model": "deepseek-v4-flash-vision-exp",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "temperature": 0.2, "max_tokens": 2000,
    }).encode()
    req = urllib.request.Request(DEEPSEEK_URL, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_KEY}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"]


def extract_docx_text(path: str, max_chars: int = 60000) -> str:
    import docx
    d = docx.Document(path)
    out = []
    for p in d.paragraphs:
        t = p.text.strip()
        if t:
            out.append(t)
    txt = "\n".join(out)
    # 按章采样: 每章取前 4500 字符 — 全章覆盖 (修复: 整体截断6万字符 → 6-11章不可见
    # → Judge 误报'6-11章空白')
    if len(txt) > max_chars:
        import re as _re
        chs = []
        # 找章标题行 ("数字 标题")
        idxs = [(i, t) for i, t in enumerate(out)
                if _re.match(r"^\d{1,2}\s+\S{2,20}$", t)]
        per_ch = max_chars // max(1, len(idxs) or 1)
        for k, (i, t) in enumerate(idxs):
            end = idxs[k + 1][0] if k + 1 < len(idxs) else len(out)
            block = out[i:end]
            take = 0
            for line in block:
                if take >= per_ch:
                    break
                chs.append(line)
                take += len(line)
        txt = "\n".join(chs)
    return txt[:max_chars]


JUDGE_PROMPT = """你是职业卫生评价报告的质量评审专家。请评审以下生成的「建设项目职业病危害预评价报告」片段。

【评审须知】
- 现行标准版本(勿误判为虚构/过时): GBZ/T 196—2025(预评价导则), GBZ 2.1—2019(化学有害因素接触限值),
  GBZ 2.2—2007(物理因素接触限值), GBZ 1—2010(工业企业设计卫生标准), GB 39800.1—2020(个体防护装备)
- "表x.x 相关数据表" 等占位段落是 Word 表格标题的正常形态, 表格本体即随后的数据表, 不是"空表"
- 报告中"风险类别为严重/待补充/依据标准推断"为行业惯用表述, 少量出现属正常, 大量重复才扣分

【报告片段】
{content}

【评审维度】(每项 1-5 分, 5=最优)
G1 主题契合: 每章讲该章的事, 不串章(如3.5工艺分析里写选址/投资), 不重复叙述
G2 数据严谨: 数据/数值有据可查, 不编造 (如检测值/限值/投资额/容量), 缺数据标注"待补充"
G3 逻辑连贯: 章节内部逻辑自洽, 结论与正文数据一致
G4 文风规范: 无套话(AI腔/空泛), 符合预评价报告体 (专业·实证·克制)
G5 覆盖度: 每章覆盖该章应具备的要素

【输出格式】严格 JSON:
{{
  "g1_topic_fit": {{"score": N, "comment": "..."}},
  "g2_data_rigor": {{"score": N, "comment": "..."}},
  "g3_logic": {{"score": N, "comment": "..."}},
  "g4_style": {{"score": N, "comment": "..."}},
  "g5_coverage": {{"score": N, "comment": "..."}},
  "key_concerns": ["问题1", "问题2", ...],
  "overall_comment": "总体评价"
}}
不要输出 JSON 以外的任何文字。"""


def main():
    ap = argparse.ArgumentParser(description="报告 LLM Judge 评测")
    ap.add_argument("--docx", required=True)
    ap.add_argument("--provider", default="deepseek")
    args = ap.parse_args()

    if not DEEPSEEK_KEY:
        print("❌ 环境变量 DEEPSEEK_API_KEY 未设置", file=sys.stderr)
        sys.exit(1)

    content = extract_docx_text(args.docx)
    if len(content) < 500:
        print("❌ 报告文本过短, 无法评审", file=sys.stderr)
        sys.exit(1)

    # 分段评审 (报告长, 分2-3段各评一轮, 再汇总) — 简化: 一次性评审(报告文本已截断)
    prompt = JUDGE_PROMPT.format(content=content)
    resp = _llm(prompt, "你是严谨的职业卫生评价报告评审专家, 只输出JSON。", args.provider)
    # 提取 JSON (容忍 markdown 包裹)
    m = re.search(r"\{.*\}", resp, re.S)
    if m:
        try:
            judge = json.loads(m.group(0))
        except Exception:
            judge = {"raw": resp}
    else:
        judge = {"raw": resp}
    # 汇总
    dims = ["g1_topic_fit", "g2_data_rigor", "g3_logic", "g4_style", "g5_coverage"]
    scores = {}
    for dim in dims:
        v = judge.get(dim) or {}
        scores[dim] = v.get("score", 0) if isinstance(v, dict) else v
    avg = sum(scores.values()) / len(scores) if scores else 0
    judge["scores"] = scores
    judge["total_judge"] = round(avg, 2)
    print(json.dumps(judge, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
