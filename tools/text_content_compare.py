#!/usr/bin/env python3
"""纯文字内容对比器 (格式无关)
原则: 撇掉一切格式(样式/表格/标题层级/编号), 只比"文字内容"
产出:
  1. 逐节文字 diff (原报告 vs 生成报告) — 用了哪些词/数字, 缺了哪些
  2. 数字集合对比 (数字是最硬的硬事实)
  3. 专业名词/物质集合对比
  4. 句子级覆盖: 原报告每句 → 生成报告是否有效覆盖
用法: python tools/text_content_compare.py --orig X.docx --gen Y.docx [--out out.json]
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import docx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
_ENVF = Path.home() / ".hermes" / ".env"
if not DEEPSEEK_KEY and _ENVF.exists():
    for _l in _ENVF.read_text().splitlines():
        if _l.startswith("DEEPSEEK_API_KEY="):
            DEEPSEEK_KEY = _l.split("=", 1)[1].strip()
LLM_URL = os.environ.get("LLM_BASE_URL", "https://workbuddy2api.henryai.top/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4.1-flash")


def raw_text(path: str, include_tables: bool = True) -> list[str]:
    """纯文字提取: 段落 + 表格单元格文字 (撇掉样式/层级, 只留文字)
    返回按文档顺序的文字块列表"""
    d = docx.Document(path)
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
    out = []
    for el in d.element.body:
        if el.tag == qn("w:p"):
            t = Paragraph(el, d).text.strip()
            if t:
                out.append(t)
        elif el.tag == qn("w:tbl") and include_tables:
            tb = Table(el, d)
            for r in tb.rows:
                cells = [c.text.strip().replace("\n", " ") for c in r.cells]
                # 去重合并单元格
                ded = []
                for c in cells:
                    if not ded or ded[-1] != c:
                        ded.append(c)
                line = " | ".join(x for x in ded if x)
                if line:
                    out.append("[表] " + line)
    return out


def split_sections(lines: list[str]) -> dict:
    """按章节号切分 (文字层, 不管样式)
    长兴原报告: '1.1 项目背景' / 生成: '1.1  项目背景' — 归一空格后匹配"""
    secs = {}
    cur = "_head"
    secs[cur] = []
    for t in lines:
        m = re.match(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s{1,3}(\S.{0,40})$", t)
        if m and not t.startswith("表"):
            cur = m.group(1)
            secs.setdefault(cur, [])
            secs[cur].append(t)  # 标题也留着(标题文字也是内容)
        else:
            if cur not in secs:
                secs[cur] = []
            secs[cur].append(t)
    return secs


def nums_of(text: str) -> set:
    """提取数字事实 (含单位, 归一化): 12000吨 / 4100万美元 / 19.5% """
    out = set()
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(万吨|吨|万元|亿美元|万美元|美元|%|℃|mg/m3|mg/m³|dB|dB\(A\)|m/s|次|人|台|套|张|个|年|月|日|h|min|kV|V|Pa|Hz|Lux|lx|ppm|kg|m3|m³|m2|m²)?", text):
        v, u = m.group(1), m.group(2) or ""
        if len(v) >= 2 or u:  # 单数字无单位忽略(序号噪声)
            out.add(f"{v}{u}")
    return out


def terms_of(text: str) -> set:
    """提取专业名词: 化学品/标准号/危害因素/设备词
    - 标准号: GB/GBZ/GB-T 数字
    - 化学品: 含常见化学字尾 (烯/烷/醇/酸/酯/酮/醛/胺/苯/醚/酚/酐)
    - 危害因素: 粉尘/噪声/高温/毒物名
    """
    out = set()
    for m in re.finditer(r"(GBZ?/T?\s?\d{3,5}(?:[—\-]\d{4})?)", text):
        out.add(re.sub(r"\s", "", m.group(1)))
    # 化学品名 (2-8字, 含化学字)
    for m in re.finditer(r"[\u4e00-\u9fa5A-Za-z0-9\-]{2,10}(?:烯|烷|醇|酸|酯|酮|醛|胺|苯|醚|酚|酐|炔|肼|腈|硫|磷|氯|钠|钾|钙|锌|铝|锰|铬|镍|镉|铅|汞|锡)", text):
        w = m.group(0)
        if 2 <= len(w) <= 12:
            out.add(w)
    # 危害/工序术语
    for m in re.finditer(r"[\u4e00-\u9fa5]{2,10}(?:粉尘|烟尘|噪声|高温|振动|辐射|中毒|窒息|灼伤|工频电场|照明|风速|上锁|盲板|监护)", text):
        out.add(m.group(0))
    return out


def llm_compare(sec: str, orig_txt: str, gen_txt: str) -> dict:
    """纯文字逐节比对: 原报告文字 vs 生成报告文字 (都无格式)"""
    prompt = f"""你是职业卫生评价报告审核专家。下面给出【原报告】和【生成报告】同一节的**纯文字内容**(已去掉格式/表格线/编号)。

任务: 逐条列出**生成报告缺少的实际内容**(不是格式差异, 不是措辞差异, 是"信息点缺失")。

判定标准(严格):
- 算缺失: 原报告写了某个具体事实/数据/措施/要求, 生成报告**完全没有对应信息**(如原报告写"作业前30min内分析, 可放宽至60min", 生成只写"作业前分析"→缺放宽条件)
- 不算缺失: 表述顺序不同/详略不同/措辞不同/生成报告写得更细
- 算数字缺失: 原报告有的具体数值(含单位), 生成报告没有或不一致 — 标注两个值
- 算术语缺失: 原报告列出的物质名/危害因素名/设备名/标准号, 生成报告未出现

【原报告 - {sec}】
{orig_txt[:4000]}

【生成报告 - {sec}】
{gen_txt[:4000]}

输出 JSON(只输出 JSON):
{{"missing_points": ["信息点缺失, 具体到内容", ...], "number_diff": ["原X vs 生成Y", ...], "term_missing": ["术语/物质/标准号", ...], "gen_extra": ["生成报告有而原报告无的实质内容(简)", ...]}}
无缺失返回空数组。"""
    req = urllib.request.Request(
        LLM_URL,
        data=json.dumps({"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                         "temperature": 0}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {DEEPSEEK_KEY}"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = json.loads(resp.read())["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {"raw": raw[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig", required=True)
    ap.add_argument("--gen", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--sections", default="")
    ap.add_argument("--no-llm", action="store_true", help="只做规则对比(数字/术语), 不调LLM")
    args = ap.parse_args()

    orig_lines = raw_text(args.orig)
    gen_lines = raw_text(args.gen)
    O, G = split_sections(orig_lines), split_sections(gen_lines)
    ids = args.sections.split(",") if args.sections else sorted(
        set(O) & set(G), key=lambda x: [int(p) if p.isdigit() else 0 for p in x.split(".")])

    results = {}
    tot = {"missing_points": 0, "number_diff": 0, "term_missing": 0, "gen_extra": 0}
    for sec in ids:
        if sec == "_head":
            continue
        o_txt = "\n".join(O.get(sec, []))
        g_txt = "\n".join(G.get(sec, []))
        # 规则层: 数字 + 术语 (格式无关的硬事实)
        o_nums, g_nums = nums_of(o_txt), nums_of(g_txt)
        o_terms, g_terms = terms_of(o_txt), terms_of(g_txt)
        miss_nums = sorted(o_nums - g_nums)
        miss_terms = sorted(o_terms - g_terms)
        rec = {"orig_chars": len(o_txt), "gen_chars": len(g_txt),
               "rule_missing_numbers": miss_nums, "rule_missing_terms": miss_terms}
        if not args.no_llm and DEEPSEEK_KEY and len(o_txt) > 60:
            try:
                rec.update(llm_compare(sec, o_txt, g_txt))
            except Exception as e:
                rec["llm_error"] = str(e)[:100]
        results[sec] = rec
        n_miss = len(rec.get("missing_points", []))
        tot["missing_points"] += n_miss
        tot["number_diff"] += len(rec.get("number_diff", []))
        tot["term_missing"] += len(rec.get("term_missing", []))
        tot["gen_extra"] += len(rec.get("gen_extra", []))
        print(f"[{sec}] {len(o_txt)}→{len(g_txt)}字 | 缺信息{n_miss} 数字差{len(rec.get('number_diff',[]))} "
              f"术语缺{len(miss_terms)}", flush=True)

    results["_summary"] = tot
    out = args.out or "/tmp/text_content_cmp.json"
    Path(out).write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\n=== 合计 ===\n缺信息点 {tot['missing_points']} | 数字差异 {tot['number_diff']} | "
          f"术语缺失 {tot['term_missing']} | 生成多余 {tot['gen_extra']}\n已保存 {out}")


if __name__ == "__main__":
    main()
