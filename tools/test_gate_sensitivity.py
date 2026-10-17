#!/usr/bin/env python3
"""S0 门禁: **证明内容级指纹"能红"** (2026-10-15)

判据不是"脚本跑通了", 而是"该报警的时候它报警"。本测试全程自建临时 docx,
**不读 data/ohs.db、不碰官方数据层** —— 与既有范式一致 (合成决策表, 不许具名样本)。

每条变异同时给出两个结果, 这才是它真正的意义:
  · 旧门 (p0_metrics 的聚合计数) 看不看得见 → 记为 BLIND / SEES
  · 新门 (export_fingerprint.digest) 看不看得见 → 必须 SEE
若某条新门看不见, 本测试直接红。

用法: .venv/bin/python tools/test_gate_sensitivity.py   (退出码 0 = 全绿)
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from docx import Document  # noqa: E402

from tools.export_fingerprint import PIDS, diff, digest, texts_from_docx  # noqa: E402

n_pass = n_fail = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got}\n      期望: {want}")


def counters(path: Path) -> dict:
    """复刻旧门口径 (tools/p0_metrics.py:56-59) —— 只留长度与计数, 不留文字"""
    paras, cells, rows = texts_from_docx(path)
    return {
        "bytes": path.stat().st_size,
        "tables": len(rows),
        "table_rows": sum(rows),                 # ⚠ 求和: 一表+1 一表-1 可互相抵掉
        "body_chars": sum(len(p) for p in paras),
        "table_chars": sum(len(c[3]) for c in cells),
        "pending_cells": sum(p.count("待补充") for p in paras) + sum(c[3].count("待补充") for c in cells),
    }


def make_doc(path: Path, bold: bool = False) -> None:
    d = Document()
    d.add_paragraph("1.1 项目背景")
    d.add_paragraph("本项目职业接触限值依据 GBZ 2.1—2007 执行。")   # 废止版写法
    d.add_paragraph("未获取检测数据，待补充")
    t = d.add_table(rows=3, cols=3)
    for ci, h in enumerate(["因素", "限值", "单位"]):
        t.cell(0, ci).text = h
    t.cell(1, 0).text = "苯"
    t.cell(1, 1).text = "6"          # PC-TWA 数值 (单字符, 变异成等长的 "9")
    t.cell(1, 2).text = "mg/m³"
    t.cell(2, 0).text = "甲苯"
    t.cell(2, 1).text = "50"
    t.cell(2, 2).text = "mg/m³"
    p2 = d.add_paragraph("第二张表见下")
    if bold:
        for r in p2.runs or [p2.add_run("")]:
            r.bold = True
    t2 = d.add_table(rows=2, cols=2)
    t2.cell(0, 0).text = "场景"
    t2.cell(0, 1).text = "处置"
    t2.cell(1, 0).text = "泄漏"
    t2.cell(1, 1).text = "撤离路线见附图"
    d.save(str(path))


def edit(src: Path, dst: Path, fn) -> Path:
    d = Document(str(src))
    fn(d)
    d.save(str(dst))
    return dst


def cmp(new: dict, base: dict, old_new: dict, old_base: dict) -> tuple[str, str]:
    """返回 (旧门结论, 新门结论)"""
    old = "BLIND" if old_new == old_base else "SEES"
    same = all(new.get(k) == base.get(k) for k in
               ("para_hashes", "cell_hashes", "std_codes", "rows_per_table", "chars", "pending_cells"))
    return old, ("BLIND" if same else "SEES")


tmp = Path(tempfile.mkdtemp(prefix="gate_sens_"))
base_p = tmp / "base.docx"
make_doc(base_p)
base = digest(base_p)
base_old = counters(base_p)

print("=== 0. 前置: 指纹与旧门在「无改动」上必须一致 ===")
again = tmp / "again.docx"
make_doc(again)
check("同内容二次生成 → 指纹一致 (顺序稳定, 无随机/日期扰动)", digest(again) == {**base, "docx": again.name}, True)
check("同内容二次生成 → 旧门也一致 (作为对照基线)", counters(again) == base_old, True)

print("\n=== 1. 数值被改 (限值 6 → 9, 刻意等长) ===")


def _swap_number(d):
    d.tables[0].cell(1, 1).text = "9"


m1 = edit(base_p, tmp / "m1.docx", _swap_number)
new1, old1 = digest(m1), counters(m1)
o, n = cmp(new1, base, old1, base_old)
check("旧门: 等长数值改动应当看不见 (说明它为什么不够)", o, "BLIND")
check("新门: 数值改动必须红", n, "SEES")
changed = [list(c[:3]) for c in new1["cell_hashes"]
           if c != next(b for b in base["cell_hashes"] if b[:3] == c[:3])]
check("新门: 能定位到具体格 (表0 行1 列1)", changed, [[0, 1, 1]])

print("\n=== 2. 标准号换版 (GBZ 2.1—2007 → GBZ 2.1—2019, 只动年份) ===")


def _swap_std(d):
    for p in d.paragraphs:
        if "GBZ 2.1—2007" in p.text:
            p.text = p.text.replace("GBZ 2.1—2007", "GBZ 2.1—2019")


m2 = edit(base_p, tmp / "m2.docx", _swap_std)
new2, old2 = digest(m2), counters(m2)
o, n = cmp(new2, base, old2, base_old)
# 旧门这里**不作断言**: 它是否"看见"取决于 docx 压缩尺寸有没有碰巧变 (§1 就是没变)。
# 把"偶然能看见"当证据正是本次重构要拆掉的习惯 —— 见 §1 的 BLIND 断言为准。
print(f"  ⓘ 旧门对本条: {o} (不可依赖: 同一类改动在 §1 里它就 BLIND)")
check("新门: 必须红", n, "SEES")
check("新门: std_codes 集合真的变了", new2["std_codes"] != base["std_codes"], True)
check("新门: 旧号从集合消失", "GBZ2.1-2007" in base["std_codes"] and "GBZ2.1-2007" not in new2["std_codes"], True)
check("新门: 现行号进入集合", "GBZ2.1-2019" in new2["std_codes"], True)

print("\n=== 3. 删一行 ===")
m3 = edit(base_p, tmp / "m3.docx", lambda d: d.tables[0]._tbl.remove(d.tables[0].rows[2]._tr))
new3, old3 = digest(m3), counters(m3)
o, n = cmp(new3, base, old3, base_old)
check("旧门: 看得见 (table_rows 求和会变)", o, "SEES")
check("新门: 必须红", n, "SEES")
check("新门: 用 rows_per_table 定位到是第 0 张表 3→2", new3["rows_per_table"][0] == 2 and base["rows_per_table"][0] == 3, True)

print("\n=== 4. 一表 +1 行 / 另一表 −1 行 (求和门的盲区) ===")


def _shift_rows(d):
    d.tables[0].add_row()
    d.tables[1]._tbl.remove(d.tables[1].rows[-1]._tr)


m4 = edit(base_p, tmp / "m4.docx", _shift_rows)
new4, old4 = digest(m4), counters(m4)
check("旧门: table_rows 求和不变 → 结构性抵销被放过",
      old4["table_rows"] == base_old["table_rows"], True)
check("新门: rows_per_table 分别记账必红", new4["rows_per_table"] != base["rows_per_table"], True)

print("\n=== 5. 只改格式不改文字 (加粗) → 两个门都不许红 ===")
fmt_p = tmp / "fmt.docx"
make_doc(fmt_p, bold=True)
new5, old5 = digest(fmt_p), counters(fmt_p)
check("指纹与文字无关: 加粗不该触发", (new5["para_hashes"], new5["cell_hashes"]) == (base["para_hashes"], base["cell_hashes"]), True)
check("但 docx 字节确实变了 (证明指纹不是字节哈希, 否则日间噪声会淹死它)",
      fmt_p.stat().st_size != base_p.stat().st_size, True)

print("\n=== 6. 反向对照: 指纹必须能\"红\"到能被 diff 函数报出来 ===")
lines = diff(new1, base, False, None)
check("diff(m1, golden) 返回非空差异行", bool(lines), True)
check("diff(未改副本, golden) 返回空", diff(digest(again), base, False, None), [])
check("差异行里含「单元格内容变化」字样", any("单元格内容变化" in l for l in lines), True)

print("\n=== 7. 交付物一致性 (防「改了名单忘了另一边」) ===")
pm = (ROOT / "tools" / "p0_metrics.py").read_text(encoding="utf-8")
ids_in_p0 = re.findall(r'"([0-9a-f]{10})"', pm.split("PIDS = [")[1].split("]")[0])
check("本门的项目名单与 p0_metrics 同步", PIDS, ids_in_p0)
door_src = (ROOT / "tools" / "export_fingerprint.py").read_text(encoding="utf-8")
# 原来这里是字符串级假断言 ("UPDATE " not in door_src and '.execute("INSERT' not in src):
# 它查不出 oel.connect() 的 executescript(DDL)、查不出 ALTER TABLE、也查不出 WAL 副作用,
# 而它保护的对象当时正开着可写连接跑 DDL —— 假绿了一整轮 (S1c 计划 W6)。
# 换成 tools/chain_audit.py 那把尺子: 验收链取数函数里不得有写侧工厂/_conn/写类 SQL。
from tools.chain_audit import chain_write_sites  # noqa: E402
check("指纹链的取数函数不写库 (判据与 test_acceptance_readonly 同源)",
      chain_write_sites(ROOT), [])
check("指纹模块里已不再出现写侧工厂调用", "connect()" in door_src and "connect_ro()" not in door_src, False)

sys.stdout.flush()
print(f"\n=== 结果: {n_pass} 绿 / {n_fail} 红 ===")
sys.exit(1 if n_fail else 0)
