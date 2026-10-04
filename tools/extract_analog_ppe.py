#!/usr/bin/env python3
"""提取「类比企业(现有企业)个人防护用品配备表」 —— 4.2.4 的数据源

真稿 9.2.4 类比企业个体使用职业病危害防护用品的配备与使用 含 表9.2-4
(岗位|配置的职业病防护用品|品牌/型号|单位|数量|发放频率)。

本项目为扩建项目 —— 类比企业即**企业现有生产装置**, 其 PPE 配备的权威源
= C3 现状评价报告 表60 (序号|个人防护用品名称|规格型号|岗位|配备数量|更换周期)。

输出 data/analog_ppe.json:
  { pid: {"rows":[{name,model,post,count,circle}], "n":12} }

用法:
  python tools/extract_analog_ppe.py            # 预览
  python tools/extract_analog_ppe.py --write    # 落盘
"""
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
MAT = ROOT / "data" / "materials"
OUT = ROOT / "data" / "analog_ppe.json"

HEAD = ["序号", "个人防护用品名称", "规格型号", "岗位", "配备数量", "更换周期"]


def _norm(s: str) -> str:
    return "".join((s or "").split())


def find_ppe_table(docx_path: str):
    """在现状评价 docx 里定位 PPE 配备表 (表头含 个人防护用品名称+配备数量+更换周期)。"""
    import docx

    d = docx.Document(docx_path)
    for t in d.tables:
        hdr = [_norm(c.text) for c in t.rows[0].cells]
        if "个人防护用品名称" in hdr and "配备数量" in hdr and "更换周期" in hdr:
            return t
    return None


def parse_rows(t) -> list[dict]:
    rows = []
    for r in t.rows[1:]:
        cells = [c.text.strip().replace("\n", "") for c in r.cells]
        if len(cells) < 6:
            cells += [""] * (6 - len(cells))
        _, name, model, post, count, circle = cells[:6]
        if not name:
            continue
        rows.append({
            "name": name,
            "model": model or "/",
            "post": post,
            "count": count,
            "circle": circle,
        })
    return rows


def main():
    write = "--write" in sys.argv
    out = {}
    if not MAT.exists():
        print("无 materials 目录")
        return
    for pid in sorted(os.listdir(MAT)):
        d = MAT / pid / "C3_原有项目"
        if not d.exists():
            continue
        cand = list(d.glob("*现状评价*.docx"))
        if not cand:
            continue
        t = find_ppe_table(str(cand[0]))
        if t is None:
            print(f"{pid}: 未找到 PPE 表")
            continue
        rows = parse_rows(t)
        out[pid] = {"rows": rows, "n": len(rows)}
        print(f"{pid}: {len(rows)} 行")
        for r in rows:
            print(f"   {r['name']} | {r['model']} | {r['post']} | {r['count']} | {r['circle']}")
    if write and out:
        if OUT.exists():
            old = json.loads(OUT.read_text(encoding="utf-8"))
            old.update(out)
            out = old
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"✅ 已写 {OUT} ({len(out)} 项目)")


if __name__ == "__main__":
    main()
