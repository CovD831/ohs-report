"""量化: 长兴缓存是否同样存在"索引说有因素但精读没产出"的静默丢页?

新泰已证实: 52 漏读页中 5 页真有数据(70 条) → 静默丢弃。
长兴(生产主测试项目) 104 索引页 / 32 产出页 → 72 页待查。
只读诊断, 不写缓存。
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
sys.path.insert(0, "/Users/abaaba/Projects/ohs-report/web")

import vision_extract as V  # noqa: E402

CACHE = Path("/Users/abaaba/Projects/ohs-report/data/vision_cache/32e58e26097c8e58.json")
PDF = Path("/Users/abaaba/Desktop/长兴合成树脂/材料包_生成报告用/05_类比检测_职业卫生检测2025.pdf")
OUT = Path("/tmp/cx_missed_reprobe.json")

vd = json.loads(CACHE.read_text())
pages = vd["index"]["pages"]
dets = vd["detections"]

idx_f = sorted(p["page"] for p in pages
               if str(p.get("factor") or "").strip() not in ("", "无"))
read = sorted({d.get("_page") for d in dets if d.get("_page")})
missed = [p for p in idx_f if p not in read]

print(f"长兴: 索引判有因素 {len(idx_f)} 页 | 产出 {len(read)} 页 | 漏 {len(missed)} 页")
print(f"PDF: {PDF}")
print(f"存在: {PDF.exists()}")
if not PDF.exists():
    raise SystemExit("PDF 不在, 无法复跑")

recs = []
t0 = time.time()
tot = 0.0
for i, pno in enumerate(missed, 1):
    png = V._render(PDF, pno - 1, 180)
    err, raw = "", ""
    try:
        raw, c = V._ask(png, V._EXTRACT_ASK, max_tokens=2000)
        tot += c
    except Exception as e:
        err = f"{type(e).__name__}: {e}"

    parsed_ok, tt, items = False, "", []
    if not err:
        t = raw.strip()
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                d = json.loads(m.group(0))
                parsed_ok = True
                tt = str(d.get("table_type") or "").strip().upper()[:1]
                items = d.get("items") or []
            except Exception:
                parsed_ok = False
    trunc = bool(raw) and raw.count("{") > raw.count("}")
    nf = sum(1 for it in items
             if isinstance(it, dict) and str(it.get("factor") or "").strip())
    recs.append({"page": pno, "err": err, "parsed_ok": parsed_ok, "table_type": tt,
                 "n_items": len(items), "n_factor": nf, "raw_len": len(raw),
                 "truncated_suspect": trunc, "credit": c if not err else 0.0,
                 "first_factors": [str(it.get("factor"))[:60] for it in items[:3]
                                   if isinstance(it, dict)]})
    flag = "★有数据" if nf else ("✗空" if parsed_ok else "‼失败")
    print(f"  [{i:2}/{len(missed)}] p{pno:3} {flag} items={len(items):3} "
          f"有factor={nf:3} raw={len(raw):5} {'截断?' if trunc else ''} {err[:45]}",
          flush=True)

OUT.write_text(json.dumps(recs, ensure_ascii=False, indent=2))
has = [r for r in recs if r["n_factor"]]
fail = [r for r in recs if r["err"] or not r["parsed_ok"]]
print()
print("=" * 78)
print(f"汇总: 重跑 {len(recs)} 页 ({time.time()-t0:.0f}s, credit {tot:.2f})")
print(f"  ★真有数据: {len(has)} 页 → 可找回 {sum(r['n_factor'] for r in has)} 条")
print(f"  ‼异常/解析失败: {len(fail)} 页 {[r['page'] for r in fail]}")
print(f"  有数据页: {[r['page'] for r in has]}")
print(f"  明细 → {OUT}")
