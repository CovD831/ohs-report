"""standard_db 数据归一化: 修 code 字段污染 + name/state 矛盾

三个已知质量问题 (2026-10 查证):
  ① code 字段混入「代替 XXX」/ 全角空格 / 中文括号
     例: 'GBZ2.2-2007代替GBZ2-2002' → 应为 'GBZ2.2-2007', 被替代者另记
         'WS216-2008　代替　WS216-2001' → 'WS216-2008'
         '（GBZ2.1-2019）第1号修改单' → 保留(它是修改单, 非标准本体)
  ② name 里已明写「（xxxx年x月x日起废止）」「（已废止）」「（已被代替）」
     但 state 仍是 '实施中' → 按 name 的显式声明纠正 state
  ③ 被替代的标准**根本不在库里** (如 GBZ2.2-2019)
     → 本脚本不造行(凭记忆造标准=编造), 只报告缺口

原则: 只做**可由现有字段确定的**修正; 不确定的一律转人工列出, 不猜。
     改动前 dry-run 全量打印, --apply 才写库。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge.oel import connect  # noqa: E402

# code 里「代替 XXX」的切分: 全角空格/普通空格/直接相连
_RE_CODE_REPLACE = re.compile(r"^(.+?)[\s\u3000]*代替[\s\u3000]*(.+)$")
# 标准号本体形态 (用于从 name 里回捞真正的 code)
_RE_CODE_BODY = re.compile(r"^[A-Za-z/\s\.\-]*\d+(?:\.\d+)?[\-—–−]\d{4}$")
# name 里的废止声明
_RE_ABOLISHED = re.compile(r"(已废止|已被代替|自\s*\d{4}\s*年\s*\d{1,2}\s*月?\s*\d{0,2}\s*日?起?废止"
                           r"|\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*废止)")


def _clean_code(raw: str) -> tuple[str, str | None]:
    """返回 (干净code, 被替代者code或None)"""
    s = (raw or "").strip()
    m = _RE_CODE_REPLACE.match(s)
    if m:
        head, tail = m.group(1).strip(), m.group(2).strip()
        return re.sub(r"[\s\u3000]", "", head), re.sub(r"[\s\u3000]", "", tail)
    return s, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = connect()

    rows = list(conn.execute("SELECT id, code, name, state, replace_of FROM standard_db"))

    code_fix: list[tuple] = []      # (id, old, new)
    state_fix: list[tuple] = []     # (id, code, old, new, 依据)
    for_backfill: list[tuple] = []  # (id, clean_code, replaced)

    for rid, code, name, state, replace_of in rows:
        new_code, replaced = _clean_code(code or "")
        if new_code != (code or ""):
            code_fix.append((rid, code, new_code))
        if replaced and not (replace_of or "").strip():
            for_backfill.append((rid, new_code, replaced))
        # name 显式声明废止 → state 纠正
        if state in ("实施中", "现行") and _RE_ABOLISHED.search(name or ""):
            state_fix.append((rid, new_code, state, "废止", name[:50]))

    print(f"=== ① code 去污 ({len(code_fix)} 条) ===")
    for rid, old, new in code_fix[:25]:
        print(f"   {old!r}\n     → {new!r}")
    if len(code_fix) > 25:
        print(f"   ... 共 {len(code_fix)} 条")
    print(f"\n=== ② state 按 name 声明纠正 ({len(state_fix)} 条) ===")
    for rid, code, old, new, why in state_fix:
        print(f"   {code:<24} {old} → {new}   [{why}]")
    print(f"\n=== ③ 回填 replace_of ({len(for_backfill)} 条) ===")

    if not args.apply:
        print("\n[dry-run] 未写库。加 --apply 执行。")
        return 0

    for rid, old, new in code_fix:
        conn.execute("UPDATE standard_db SET code=? WHERE id=?", (new, rid))
    for rid, code, old, new, why in state_fix:
        conn.execute("UPDATE standard_db SET state=? WHERE id=?", (new, rid))
    for rid, code, replaced in for_backfill:
        conn.execute("UPDATE standard_db SET replace_of=? WHERE id=?", (replaced, rid))
    conn.commit()
    print(f"\n✅ 已写库: code {len(code_fix)} / state {len(state_fix)} / replace_of {len(for_backfill)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
