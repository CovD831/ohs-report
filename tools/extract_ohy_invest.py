#!/usr/bin/env python3
"""从「原有项目现状评价报告」提取职业病防治经费列表 (表11.11-1 / 表67)

目标: data/ohy_invest.json — 供 section_filler 建「职业病防治经费表」(表9.2-1)
数据源: 材料包 C3_原有项目/*现状评价*.docx
红线: 只提取原文表格有的行; 提取不到留空 — 绝不编造金额。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAT = ROOT / "data" / "materials"
OUT = ROOT / "data" / "ohy_invest.json"

KEY_ROW = re.compile(r"职业卫生管理机构组织工作经费|职业病防护设施费用|防治经费")


def _num(s: str) -> float | None:
    m = re.search(r"[\d.]+", str(s or ""))
    return float(m.group()) if m else None


def extract_one(path: Path) -> dict | None:
    try:
        from docx import Document
    except Exception:
        return None
    doc = Document(str(path))
    for tb in doc.tables:
        rows = [[c.text.strip() for c in r.cells] for r in tb.rows]
        flat = "\n".join("\n".join(r) for r in rows)
        if not re.search(r"职业卫生管理机构组织工作经费|职业病防护设施费用", flat):
            continue
        out = []
        _n = 0
        for r in rows:
            if len(r) < 2:
                continue
            name = r[1] if len(r) > 1 else ""
            amt = _num(r[2]) if len(r) > 2 else None
            if not name or amt is None:
                continue
            if "总计" in name or "合计" in name:
                out.append(["", name, amt])
            else:
                _n += 1
                out.append([str(_n), name, amt])
        if out:
            return {"cols": ["序号", "项目", "投资（万元）"], "rows": out,
                    "src": str(path.relative_to(ROOT))}
    return None


def main() -> int:
    write = "--write" in sys.argv
    result: dict[str, dict] = {}
    if MAT.exists():
        for pid_dir in sorted(MAT.iterdir()):
            if not pid_dir.is_dir():
                continue
            for f in sorted(pid_dir.rglob("*.docx")):
                hit = None
                try:
                    hit = extract_one(f)
                except Exception:
                    hit = None
                if hit:
                    result[pid_dir.name] = hit
                    print(f"  {pid_dir.name}: {len(hit['rows'])} 行 ← {f.name}")
                    break
    if not result:
        print("(无命中)")
    if write:
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {OUT.relative_to(ROOT)} ({len(result)} 项目)")
    else:
        print("[DRY-RUN] 加 --write 生效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
