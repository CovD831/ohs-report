#!/usr/bin/env python3
"""导出**内容级**指纹 —— 重构/改数据前后的主验收门 (2026-10-15, S0)

为什么需要它: `tools/p0_metrics.py` 的"逐指标 diff"只比聚合计数 (bytes/tables/
table_rows/body_chars/table_chars/pending_cells, 见 :57-62), **一个单元格的文字都不存**。
实测后果: 限值从 "8" 改成 "9 mg/m³"、依据标准号被换成废止版, 前后逐项指标完全一致 ——
用它来证明"这一批改动没影响成品"是循环论证。本模块把同一批导出产物按
**段落 / 逐格 / 标准号集合** 摘要化, 差异可定位到 (表序, 行, 列)。

口径与既有基线保持一致的细节:
  - 文档顺序按 body 元素走 (段落与表交错), 不用 d.tables 的平铺序;
  - 合并单元格**不去重** (与 p0_metrics:46-47 同口径: 逐 cell 出现即计, 可复现);
  - 哈希 = sha1(单元格/段落文本 strip 后), 不做内部空白归一 —— 竖排表的换行是内容;
  - **不存原文**, 只存哈希: golden 进公开仓库也不泄漏项目/企业名称。
    差异需要看字面时, 用 --show-text 现算现打。

用法:
  .venv/bin/python tools/export_fingerprint.py --write            # 建/更新 golden
  .venv/bin/python tools/export_fingerprint.py --check            # 比对 (退出码 0=一致)
  可选 --pids a,b  --show-text  --docx-out DIR
"""
import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

GOLDEN_DIR = ROOT / "tools" / "golden"
# 与 p0_metrics.py:15 同一组 4 项目 (p0_metrics 是脚本带模块级副作用, 不可 import,
# 故此名单**必须人工同步**; test_gate_sensitivity 会比对两处数字防漂移)
PIDS = ["ada54603a9", "c8c7ff0a4d", "2863a36451", "57ffa2b3b0"]

from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.table import Table  # noqa: E402
from docx.text.paragraph import Paragraph  # noqa: E402

from web.standard_version import STD_REF_PAT, ref_code  # noqa: E402
# ⚠ 导出相关的重依赖 (web.app / knowledge.project_assess / tools.export_e2e / web.word_export)
#   **故意放在 export() 内部 import**: test_gate_sensitivity 只用本模块的纯函数
#   (texts_from_docx / digest / diff), 不该为了做门禁把 web.app 的导入副作用
#   (_cleanup_expired / init_tasks) 拖进容器里的守卫步骤。

H = lambda s: hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]  # noqa: E731


def texts_from_docx(path: Path) -> tuple[list[str], list[tuple[int, int, int, str]], list[int]]:
    """按文档顺序取 (段落文本, [(表序,行,列,文本)], 每表行数)

    只负责取字, 不负责判 —— 判由 digest/mutate 侧做, 这样测试可以直接构造输入。
    """
    d = Document(str(path))
    paras: list[str] = []
    cells: list[tuple[int, int, int, str]] = []
    rows_per_table: list[int] = []
    ti = 0
    for el in d.element.body:
        if el.tag == qn("w:p"):
            t = Paragraph(el, d).text.strip()
            if t:
                paras.append(t)
        elif el.tag == qn("w:tbl"):
            tb = Table(el, d)
            rows_per_table.append(len(tb.rows))
            for ri, r in enumerate(tb.rows):
                for ci, c in enumerate(r.cells):
                    s = c.text.strip()
                    if s:
                        cells.append((ti, ri, ci, s))
            ti += 1
    return paras, cells, rows_per_table


def std_codes(paras: list[str], cells: list[tuple]) -> list[str]:
    """正文里出现的全部标准号 (规范化后排序) —— 废止号进正文必红"""
    seen = set()
    for txt in list(paras) + [c[3] for c in cells]:
        for m in STD_REF_PAT.finditer(txt):
            seen.add(ref_code(m))
    return sorted(seen)


def digest(path: Path) -> dict:
    paras, cells, rows_per_table = texts_from_docx(path)
    return {
        "docx": path.name,
        "n_paras": len(paras),
        "n_tables": len(rows_per_table),
        "rows_per_table": rows_per_table,          # 每表分别记账, 不许求和 (修聚合门"一表+1 一表-1 可抵"的洞)
        "chars": sum(len(p) for p in paras) + sum(len(c[3]) for c in cells),
        "pending_cells": sum(p.count("待补充") for p in paras) + sum(c[3].count("待补充") for c in cells),
        "std_codes": std_codes(paras, cells),
        # 哈希层: 顺序即身份, 差异可直接定位
        "para_hashes": [H(p) for p in paras],
        "cell_hashes": [[ti, ri, ci, H(s)] for ti, ri, ci, s in cells],
    }


def export(pid: str, out_dir: Path) -> Path:
    """与 p0_metrics.py:88-94 同一套取数/导出, 逐行对齐 (口径变了 = 门失效)"""
    from knowledge.oel import connect
    from knowledge.project_assess import assess_project
    from tools.export_e2e import build_project
    from web.projects_db import get_project_data as _get_project_data
    from web.word_export import export_docx

    conn = connect()
    try:
        data = _get_project_data(pid)
        project = build_project(pid)
        assess = assess_project(conn, project)
        assess["_project_data"] = dict(project)
        out = out_dir / f"report_{pid}_fp.docx"
        export_docx(project, assess, out, section_states=data.get("section_states"))
        return out
    finally:
        conn.close()


def diff(got: dict, want: dict, show_text: bool, src: Path | None) -> list[str]:
    """结构化比对; 返回人类可读的差异行 (空 = 一致)"""
    out = []
    for k in ("n_paras", "n_tables", "chars", "pending_cells"):
        if got[k] != want[k]:
            out.append(f"{k}: {want[k]} → {got[k]}")
    if got["rows_per_table"] != want["rows_per_table"]:
        w, g = want["rows_per_table"], got["rows_per_table"]
        for i in range(max(len(w), len(g))):
            a, b = (w[i] if i < len(w) else "无表"), (g[i] if i < len(g) else "无表")
            if a != b:
                out.append(f"表#{i} 行数: {a} → {b}")
    if got["std_codes"] != want["std_codes"]:
        lost = set(want["std_codes"]) - set(got["std_codes"])
        new = set(got["std_codes"]) - set(want["std_codes"])
        if lost:
            out.append(f"标准号消失 {len(lost)} 个: {sorted(lost)[:8]}")
        if new:
            out.append(f"标准号新增 {len(new)} 个: {sorted(new)[:8]}")
    for label, key in (("段落", "para_hashes"), ("单元格", "cell_hashes")):
        a, b = want[key], got[key]
        bad = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
        if len(a) != len(b):
            out.append(f"{label}数: {len(a)} → {len(b)}")
        if bad:
            out.append(f"{label}内容变化 {len(bad)} 处, 首个 index={bad[0]}")
            if show_text and src is not None:
                paras, cells, _ = texts_from_docx(src)
                if key == "para_hashes":
                    i = bad[0]
                    print(f"      段落#{i} 新: {paras[i][:90]!r}")
                else:
                    ti, ri, ci, s = cells[bad[0]]
                    print(f"      表#{ti} 行{ri} 列{ci} 新: {s[:90]!r}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true", help="导出并写 golden")
    g.add_argument("--check", action="store_true", help="导出并与 golden 比对")
    ap.add_argument("--pids", default=",".join(PIDS))
    ap.add_argument("--show-text", action="store_true", help="差异处现算并打印文字 (不落盘)")
    ap.add_argument("--docx-out", default="/tmp/fp_docx")
    args = ap.parse_args()

    pids = [p for p in args.pids.split(",") if p]
    out_dir = Path(args.docx_out)
    out_dir.mkdir(parents=True, exist_ok=True)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    rc = 0

    for pid in pids:
        docx_path = export(pid, out_dir)
        got = digest(docx_path)
        gp = GOLDEN_DIR / f"{pid}.fp.json.gz"
        print(f"\n=== {pid}  段{got['n_paras']} 表{got['n_tables']} "
              f"格{len(got['cell_hashes'])} 字{got['chars']} 待补充{got['pending_cells']} "
              f"标准号{len(got['std_codes'])} ===")
        if args.write:
            gp.write_bytes(gzip.compress(json.dumps(got, ensure_ascii=False).encode("utf-8")))
            print(f"  → golden 已写 {gp.relative_to(ROOT)} ({gp.stat().st_size} B)")
            continue
        if not gp.exists():
            print(f"  ✗ 无 golden ({gp.relative_to(ROOT)}), 先 --write")
            rc = 1
            continue
        want = json.loads(gzip.decompress(gp.read_bytes()).decode("utf-8"))
        d = diff(got, want, args.show_text, docx_path)
        if d:
            rc = 1
            print("  ✗ 与 golden 有差异:")
            for line in d[:20]:
                print("     - " + line)
            if len(d) > 20:
                print(f"     … 共 {len(d)} 类差异")
        else:
            print("  ✓ 与 golden 逐段/逐格/标准号 一致")
    return rc


if __name__ == "__main__":
    sys.exit(main())
