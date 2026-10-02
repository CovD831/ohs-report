"""从 standard_db.name 的「代替 XXX」推导被替代标准的 state → 废止

背景 (2026-10 事故):
    长兴报告正文引用了 GBZ/T 196—2007《建设项目职业病危害预评价技术导则》,
    该标准已被 GBZ/T196-2025 代替, 但库里 state='实施中' → 系统无法判定其废止,
    正文引废止标准无人拦。

根因:
    standard_db.replace_of 字段基本没填 (仅 2 条), 「代替 XXX」信息埋在 name 括号里。
    如: code='GBZ/T196-2025', name='建设项目职业病危害预评价技术标准（代替GBZ/T 196—2007）'

做法 (确定性规则, 不靠 LLM):
    1. 解析 name 中「代替 <标准号>」→ 定位被替代标准
    2. 匹配标准号时**归一化**(去空格/去/、破折号统一) 后再比对, 避免格式差异漏配
    3. 被替代标准 state: 实施中 → 废止 (只改仍标"实施中"的; 已"废止"不动)
    4. 回填 replace_of 字段 (让替代关系显式化, 供校验层/前端使用)

安全:
    - 只做 state 翻转与 replace_of 回填, 不删任何行
    - --dry-run 默认, 需 --apply 才写库
    - 改前打印全部影响行, 人工可核
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge.oel import connect  # noqa: E402

# 「代替 XXX」的多种写法: (代替GBZ/T 196—2007) / 代替 WS 196—2001 / （代替 GB/T 23466-2009）
_RE_REPLACE = re.compile(r"代替\s*([A-Za-z][A-Za-z/\s\.]*\d+[\-—–]\d{4})")


def _norm(code: str) -> str:
    """标准号归一化: 去空格、统一破折号、大小写统一 → 用于比对

    例: 'GBZ/T 196—2007' / 'GBZ/T196-2007' / 'gbz/t196–2007' → 'GBZ/T196-2007'
    """
    s = (code or "").strip().upper()
    s = re.sub(r"[\s]", "", s)
    s = re.sub(r"[—–−]", "-", s)
    return s


def _year(code: str) -> int:
    """取标准号里的**年份** —— 最后一个 4 位数

    ⚠ 不能取第一个数字组: 'GB17017-2010' 的第一组是编号 17017 → 会误取 1701。
    年份恒在末尾 '-YYYY'。
    """
    m = re.findall(r"(\d{4})", code or "")
    return int(m[-1]) if m else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写库 (默认 dry-run)")
    args = ap.parse_args()

    conn = connect()
    rows = list(conn.execute("SELECT id, code, name, state, replace_of FROM standard_db"))
    by_norm: dict[str, tuple] = {}
    for r in rows:
        by_norm.setdefault(_norm(r[1]), r)

    to_deprecate: list[tuple] = []      # (id, code, name, old_state, replaced_by)
    to_backfill: list[tuple] = []       # (id, code, replace_of)
    suspicious: list[tuple] = []        # 年份方向可疑 → 转人工

    for rid, code, name, state, replace_of in rows:
        m = _RE_REPLACE.search(name or "")
        if not m:
            continue
        old_code = m.group(1)
        target = by_norm.get(_norm(old_code))
        # 回填 replace_of (显式记录"本文件代替谁")
        if not (replace_of or "").strip():
            to_backfill.append((rid, code, old_code))
        if not target:
            continue
        tid, tcode, tname, tstate, _ = target
        # ⚠ 方向守卫: 新版年份必须 > 被替代版年份; 否则说明 name 写反了或解析错,
        #    **不得**据此把新标准标成废止 (实测 GBZ1-2010 的 name 里嵌着"GBZ1-2002"字样)
        if _year(code) and _year(old_code) and _year(code) <= _year(old_code):
            suspicious.append((code, old_code, tstate))
            continue
        if tstate in ("实施中", "现行"):
            to_deprecate.append((tid, tcode, tname, tstate, code))

    print(f"\n=== ⚠ 年份方向可疑, 转人工 ({len(suspicious)} 条) ===")
    for code, old, st in suspicious:
        print(f"  {code} → 声称代替 {old} (但年份不更晚)  [{st}]")
    print(f"\n=== 将标为「废止」({len(to_deprecate)} 条) ===")
    for tid, tcode, tname, tstate, by in sorted(to_deprecate, key=lambda x: x[1]):
        print(f"  {tcode:<22} {tstate} → 废止   (被 {by} 代替)  {tname[:44]}")
    print(f"\n=== 将回填 replace_of ({len(to_backfill)} 条, 前 10) ===")
    for rid, code, old in to_backfill[:10]:
        print(f"  {code:<22} replace_of = {old}")

    if not args.apply:
        print("\n[dry-run] 未写库。加 --apply 执行。")
        return 0

    for tid, tcode, tname, tstate, by in to_deprecate:
        conn.execute("UPDATE standard_db SET state='废止' WHERE id=?", (tid,))
    for rid, code, old in to_backfill:
        conn.execute("UPDATE standard_db SET replace_of=? WHERE id=?", (old, rid))
    conn.commit()
    print(f"\n✅ 已写库: {len(to_deprecate)} 条标废止, {len(to_backfill)} 条回填 replace_of")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
