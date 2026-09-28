#!/usr/bin/env python3
"""
报告逐章内容对比器 — 原报告 vs 生成报告 (每章 LLM 差异分析)
====================================================
用法: python tools/compare_chapters.py --gen gen.docx --orig orig.docx [--out out.json]
输出: 每章问题清单 {章: [原报告有而生成缺/生成有而原报告无/数据不一致/质量差异]}
"""
import argparse, json, os, re, sys
from pathlib import Path
import docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
if not DEEPSEEK_KEY:
    _envf = Path.home() / ".hermes" / ".env"
    if _envf.exists():
        for line in _envf.read_text().splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                DEEPSEEK_KEY = line.split("=", 1)[1].strip()
# provider 统一: workbuddy2api 中转 (勿改回官方直连 — 全项目单一口径)
DEEPSEEK_URL = os.environ.get(
    "LLM_BASE_URL", "https://workbuddy2api.henryai.top/v1/chat/completions")
DEEPSEEK_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4.1-flash")


def extract_sections(path):
    d = docx.Document(path)
    out, cur, tables = [], None, []
    for el in d.element.body:
        if el.tag == qn("w:p"):
            p = Paragraph(el, d)
            t = p.text.strip()
            if not t:
                continue
            m = re.match(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\s+(.+)$", t)
            if m and len(m.group(2)) < 45:
                if cur:
                    cur["tables"] = tables
                    out.append(cur)
                    tables = []
                cur = {"id": m.group(1), "title": m.group(2), "text": [], "tables": []}
                continue
            if cur:
                cur["text"].append(t)
        elif el.tag == qn("w:tbl"):
            tb = Table(el, d)
            tables.append([[c.text.strip().replace("\n", " ") for c in r.cells] for r in tb.rows])
    if cur:
        cur["tables"] = tables
        out.append(cur)
    return out


def call_llm(prompt, key):
    import urllib.request
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps({"model": DEEPSEEK_MODEL, "messages": [
            {"role": "user", "content": prompt}], "temperature": 0.2}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        d = json.loads(resp.read())
    return d["choices"][0]["message"]["content"]


COMPARE_PROMPT = """你是职业卫生预评价报告专家。对比同一章节在【原报告(专家编写)】与【生成报告(系统生成)】中的内容差异。
只报**有实际意义的差异**，按类别输出：
- 内容缺失: 原报告该章有的关键内容/数据, 生成报告没有 (注明具体)
- 内容冗余: 生成报告有但原报告没有的重复/无关内容
- 数据不一致: 同一数据的数值/口径在两篇中不同 (注明两边值)
- 质量差异: 生成报告该章表述不专业/空泛/套话/逻辑问题
- 信息补充: 生成报告有而原报告没有的合理内容 (简短)

【原报告 - {sec} {title}】
{orig}

【生成报告 - {sec} {title}】
{gen}

输出 JSON: {{"missing": [...], "redundant": [...], "data_mismatch": [...], "quality": [...], "added": [...]}}
每项一句话, 具体到数据/名词。无差异返回空数组。不要输出其他文字。
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen", required=True)
    ap.add_argument("--orig", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--max-chars", type=int, default=2500, help="每章截断字符")
    ap.add_argument("--sections", default="", help="逗号分隔的章节ID, 不传=全部")
    args = ap.parse_args()

    gen_secs = {s["id"]: s for s in extract_sections(args.gen)}
    orig_secs = {s["id"]: s for s in extract_sections(args.orig)}
    ids = args.sections.split(",") if args.sections else sorted(set(gen_secs) & set(orig_secs))

    if not DEEPSEEK_KEY:
        print("❌ 无 DEEPSEEK_KEY"); sys.exit(1)

    # ===== 结构性缺失检测: 原报告目录全集 vs 生成报告目录 =====
    # (盲区修复: 之前只比"两边都有的节" — 原报告有而生成侧没有的节永远不会报差异)
    # 只比 1-2 级标题 (3级以下目录差异多为编号风格)
    def toplevel(secs, max_depth=2):
        return {sid: s["title"] for sid, s in secs.items() if sid.count(".") < max_depth}
    gen_top, orig_top = toplevel(gen_secs), toplevel(orig_secs)
    # 排除目录区重复 (生成报告目录区+正文区双标题, id 集合相同)
    struct_missing = sorted(set(orig_top) - set(gen_top), key=lambda x: [int(p) for p in x.split(".") if p.isdigit()])
    if struct_missing:
        print(f"\n⚠️ 结构性缺失 {len(struct_missing)} 节 (原报告有, 生成报告未生成):")
        for sid in struct_missing:
            print(f"   ❌ [{sid}] {orig_top[sid]}")
    # 表格种类对照: 原报告表名/表头 vs 生成报告 (1-2级节聚合)
    def table_heads(secs, top):
        heads = {}
        for sid, s in secs.items():
            if sid.count(".") >= 2:
                continue
            for tb in (s.get("tables") or []):
                if tb and tb[0]:
                    h = " | ".join(str(c)[:8] for c in tb[0][:5])
                    heads.setdefault(h, sid)
        return heads
    gen_tbl, orig_tbl = table_heads(gen_secs, gen_top), table_heads(orig_secs, orig_top)
    tbl_missing = {h: sid for h, sid in orig_tbl.items() if h not in gen_tbl}
    if tbl_missing:
        print(f"\n⚠️ 表格种类缺失 {len(tbl_missing)} 张 (原报告有, 生成报告无同表头表):")
        for h, sid in list(tbl_missing.items())[:10]:
            print(f"   ❌ [{sid}] 表头: {h[:50]}")
    struct_result = {"structural_missing_sections": struct_missing,
                     "structural_missing_tables": {h: sid for h, sid in tbl_missing.items()}}

    results = {}
    for sec in ids:
        if sec not in gen_secs or sec not in orig_secs:
            results[sec] = {"title": "(仅单边存在)", "note": "只在一份报告中存在, 跳过逐节对比",
                            "missing": [], "redundant": [], "data_mismatch": [], "quality": [], "added": []}
            print(f"[{sec}] 仅单边存在, 跳过")
            continue
        g, o = gen_secs[sec], orig_secs[sec]
        # 正文 + 表格 (表格数据是核心内容, 不喂会误判'表题悬空')
        g_txt = "\n".join(g["text"])[:args.max_chars]
        o_txt = "\n".join(o["text"])[:args.max_chars]
        for tb in (g.get("tables") or [])[:3]:
            g_txt += "\n[表] " + " | ".join((row[0] if row else "") for row in tb[:8])
        for tb in (o.get("tables") or [])[:3]:
            o_txt += "\n[表] " + " | ".join((row[0] if row else "") for row in tb[:8])
        prompt = COMPARE_PROMPT.format(sec=sec, title=o["title"], orig=o_txt, gen=g_txt)
        try:
            raw = call_llm(prompt, DEEPSEEK_KEY)
            m = re.search(r"\{.*\}", raw, re.S)
            d = json.loads(m.group(0)) if m else {"raw": raw[:200]}
        except Exception as e:
            d = {"error": str(e)}
        results[sec] = {"title": o["title"], **d}
        n = sum(len(v) for k, v in d.items() if isinstance(v, list))
        print(f"[{sec}] {o['title'][:25]}: {n} 项差异")

    if args.out:
        results["_structural"] = struct_result
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1))
        print(f"✅ 已保存 {args.out} (含结构性缺失检测)")
    else:
        print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
