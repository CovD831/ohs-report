"""体检: fixed_texts_data.json 的 std_text 清单是否全部现行有效

背景 (2026-10): 清单是**硬编码**的, 标准一换版就过期 (实测 GBZ 2.2-2007 留在清单里
很久没人发现)。硬编码不可避免, 但要能**自动发现过期**, 而不是靠人肉 review。

用法:
    .venv/bin/python tools/check_fixed_std_text.py          # 体检并列出问题
    .venv/bin/python tools/check_fixed_std_text.py --fix    # 自动替换为现行版

判定来源: standard_db (state='废止' → 问题; 有现行后继 → 给出应改版本)
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.standard_version import _base_code, _norm, resolve_current  # noqa: E402

JSON_P = ROOT / "web" / "fixed_texts_data.json"
DB_P = ROOT / "data" / "ohs.db"

# 清单条目形态: 《名称》(GB XXXX-YYYY) 或 （GB XXXX-YYYY）或裸 GB XXXX-YYYY
_RE_CODE = re.compile(r"[（(]\s*((?:GB|GBZ|WS|HG|HGT)[A-Z/T]*\s*\d+(?:\.\d+)?(?:\s*[—\-–]\s*(?:19|20)\d{2})?)\s*[)）]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="把废止标准号替换为现行版")
    args = ap.parse_args()

    doc = json.loads(JSON_P.read_text(encoding="utf-8"))
    text = doc.get("std_text", "") or ""
    if not text:
        print("std_text 为空")
        return 1

    conn = sqlite3.connect(DB_P)
    stale, unknown, fixed = [], [], []
    new_text = text
    for m in _RE_CODE.finditer(text):
        raw = m.group(1).strip()
        norm = _norm(raw)
        hit = None
        for r in conn.execute("SELECT code, name, state FROM standard_db"):
            if re.sub(r"[\s—–−]", "", (r[0] or "")).replace("－", "-").upper() == norm:
                hit = r
                break
        if hit is None:
            unknown.append(raw)
            continue
        if (hit[2] or "") == "废止":
            cur = resolve_current(conn, hit[0]) or {}
            cur_code = cur.get("code", "")
            if cur_code and (cur.get("state") or "") not in ("废止", "已废止"):
                stale.append((raw, hit[0], cur_code))
                if args.fix:
                    new_text = new_text.replace(raw, cur_code)
                    fixed.append((hit[0], cur_code))
            else:
                stale.append((raw, hit[0], ""))

    print(f"清单条目(含标准号): {len(_RE_CODE.findall(text))}")
    print()
    if stale:
        print(f"=== 已废止 {len(stale)} 条 ===")
        for raw, was, cur in stale:
            print(f"  ❌ {raw:<28} 库里={was:<20} 现行={'（库缺，需人工核实）' if not cur else cur}")
    else:
        print("=== 无废止标准 ✅ ===")
    if unknown:
        print()
        print(f"=== 库中查不到 {len(unknown)} 条 (多为行业标准/库覆盖不全) ===")
        for u in unknown:
            print(f"  ⚠ {u}")

    if args.fix and fixed:
        doc["std_text"] = new_text
        JSON_P.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print()
        print("=== 已替换 ===")
        for was, cur in fixed:
            print(f"  {was} → {cur}")
    elif args.fix:
        print()
        print("无可自动替换项 (需现行版在库中且已标废止)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
