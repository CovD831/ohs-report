"""决定性量化: 索引说有因素但精读**没产出**的页, 到底有多少真有数据?

背景(已证实): 新泰缓存标称 read_pages=63, 实际只有 11 页产出记录。
重新精读抽样 8 页: p30/44/45/46 **有数据**(噪声83.8 / 照度404lx / 合格率9/9),
                    p26/54/90/93 确实为空(方法/说明页)。
→ 说明**索引过报** + **精读静默丢页** 两个问题叠加。

本脚本: 对全部"漏读页"重新精读, 逐页记录
   - _ask 是否抛异常 (原代码 `except: continue` 静默跳过)
   - _parse_json 是否解析失败 (原代码静默返回 {"items":[]})
   - items 条数 / 原始响应长度 / 是否疑似 max_tokens 截断

只读诊断, **不写缓存**。
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
sys.path.insert(0, "/Users/abaaba/Projects/ohs-report/web")

import vision_extract as V  # noqa: E402
from factor_clean import clean_factor_name  # noqa: E402

CACHE = Path("/Users/abaaba/Projects/ohs-report/data/vision_cache/a66517be268181e6.json")
PDF = Path("/Users/abaaba/Desktop/新泰预评价/材料包_生成报告用/05_类比检测_2025职业病危害因素检测报告.pdf")
OUT = Path("/tmp/q2_missed_reprobe.json")

vd = json.loads(CACHE.read_text())
pages = vd["index"]["pages"]
dets = vd["detections"]

idx_f = sorted(p["page"] for p in pages
               if str(p.get("factor") or "").strip() not in ("", "无"))
read = sorted({d.get("_page") for d in dets if d.get("_page")})
missed = [p for p in idx_f if p not in read]

print(f"索引判有因素 {len(idx_f)} 页 | 实际产出 {len(read)} 页 | 漏 {len(missed)} 页")
print(f"重跑这 {len(missed)} 页 (只读, 不写缓存)…\n")

recs = []
t0 = time.time()
tot_credit = 0.0
for i, pno in enumerate(missed, 1):
    png = V._render(PDF, pno - 1, 180)
    err = ""
    raw = ""
    try:
        raw, c = V._ask(png, V._EXTRACT_ASK, max_tokens=2000)
        tot_credit += c
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        c = 0.0

    parsed_ok = False
    tt, items = "", []
    if not err:
        # 复刻 _parse_json 的判据, 但记录是否真的解析成功
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

    # 疑似截断: 有 { 但没有闭合的 }
    trunc = bool(raw) and raw.count("{") > raw.count("}")

    n_factor = sum(1 for it in items
                   if isinstance(it, dict) and str(it.get("factor") or "").strip())
    recs.append({"page": pno, "err": err, "parsed_ok": parsed_ok, "table_type": tt,
                 "n_items": len(items), "n_factor": n_factor,
                 "raw_len": len(raw), "truncated_suspect": trunc,
                 "credit": c,
                 "first_factors": [str(it.get("factor"))[:60] for it in items[:3]
                                   if isinstance(it, dict)]})
    flag = "★有数据" if n_factor else ("✗空" if parsed_ok else "‼解析失败/异常")
    print(f"  [{i:2}/{len(missed)}] p{pno:3} {flag}  表型={tt or '?':1} "
          f"items={len(items):3} 有factor={n_factor:3} raw={len(raw):5} "
          f"{'截断?' if trunc else ''} {err[:50]}")

OUT.write_text(json.dumps(recs, ensure_ascii=False, indent=2))
el = time.time() - t0

has = [r for r in recs if r["n_factor"]]
empty = [r for r in recs if not r["err"] and r["parsed_ok"] and not r["n_factor"]]
fail = [r for r in recs if r["err"] or not r["parsed_ok"]]

print()
print("=" * 78)
print("汇总")
print("=" * 78)
print(f"  重跑页数        : {len(recs)}  ({el:.0f}s, credit {tot_credit:.2f})")
print(f"  ★ 真有数据      : {len(has)} 页  → 原精读**静默丢弃**")
print(f"  ✗ 确实为空      : {len(empty)} 页  → 索引过报, 正常")
print(f"  ‼ 异常/解析失败 : {len(fail)} 页")
print()
if has:
    print("  有数据的页 (原丢失):")
    tot_items = sum(r["n_factor"] for r in has)
    print(f"    {[r['page'] for r in has]}")
    print(f"    → 可找回 {tot_items} 条 factor 记录")
print()
print(f"  明细 → {OUT}")
