"""评价依据标准: 无年份号 → 现行版 解析 (通用, 不写死年份)

背景 (2026-10 事故):
    报告"评价依据表"列的是 GBZ/T 196-2025 第2章规范性引用清单, 形式为**无年份号**
    (如 'GBZ 188', 'GBZ/T 196' —— 规范写法: 无年份 = 引用最新版)。
    旧实现把无年份号直接给 LLM, LLM 凭训练记忆**自己补年份**, 补成了废止版:
        GBZ 188   → LLM 写 GBZ 188-2014  ❌ (现行是 GBZ 188-2025)
        GBZ/T 196 → LLM 写 GBZ/T 196-2007 ❌ (现行是 GBZ/T 196-2025)
    而 standard_db 里**已经有**现行版记录 —— 只是从没被用来给 LLM 定版。

修法 (确定性查表, 不靠 LLM):
    对每个无年份标准号, 从 standard_db 取**未废止的最新版本** → 得到"应写版本",
    作为"标准版本基准"喂进 prompt; LLM 照抄而非自补。

用法:
    from web.standard_version import resolve_all_refs
    refs = resolve_all_refs(conn)   # [{ref_code, name, current_code, state}, ...]
"""
from __future__ import annotations

import re
import sqlite3

_RE_YEAR = re.compile(r"(?:19|20)\d{2}")


def _norm(s: str) -> str:
    """归一化: 去空白 + 全角连字符转半角 + 大写"""
    s = re.sub(r"[\s\u3000]", "", s or "").upper()
    return re.sub(r"[—–−－]", "-", s)


def _base_code(code: str) -> str:
    """标准身份键: 去年份 + 去推荐性标记(/T) + 去空白

    为什么去 /T: 同一标准的**推荐性↔强制性会换版时改变** —
        报告依据表写 'GB/T 12801' (2008 版是推荐性),
        现行版 'GB 12801-2025' 已改为**强制性**(不再带 /T)。
    若把 /T 当身份一部分, 就永远匹配不上现行版, 只能解析到废止的旧版。
    GBZ/T → GBZ 同理。
    """
    s = re.sub(r"[\s\u3000]", "", code or "").upper()
    s = re.sub(r"[—–−－]", "-", s)
    s = re.sub(r"-(?:19|20)\d{2}$", "", s)      # 去年份
    s = s.replace("GBZ/T", "GBZ").replace("GB/T", "GB")
    return s


def _year(code: str) -> int:
    """标准号年份 = 最后一个 4 位数 (GB17017-2010 的 17017 是编号, 不是年份)"""
    m = _RE_YEAR.findall(code or "")
    return int(m[-1]) if m else 0


def resolve_current(conn: sqlite3.Connection, ref_code: str) -> dict | None:
    """无年份/任意标准号 → 库中**未废止的最新版**记录

    返回 {code, name, state} 或 None (库中无该标准号)
    """
    base = _base_code(ref_code)
    if not base:
        return None
    rows = conn.execute("SELECT code, name, state FROM standard_db").fetchall()
    same = [r for r in rows if _base_code(r[0]) == base]
    if not same:
        return None
    # 优先未废止的, 再按年份降序
    alive = [r for r in same if (r[2] or "") not in ("废止", "已废止")]
    pool = alive or same
    pool.sort(key=lambda r: _year(r[0]), reverse=True)
    c, n, s = pool[0]
    return {"code": c, "name": n, "state": s, "deprecated_only": not alive}


def resolve_all_refs(conn: sqlite3.Connection) -> list[dict]:
    """standard_ref 全表 → 附现行版解析 (报告"评价依据表"用)

    返回 [{ref_code, name, current_code, state, note}, ...]
      ref_code    : 报告里的写法 (无年份, 如 'GBZ 188')
      current_code: 应写的完整号 (如 'GBZ188-2025')
      note        : 未收录/仅有废止版 等异常说明
    """
    out = []
    for code, name in conn.execute("SELECT code, name FROM standard_ref ORDER BY id"):
        cur = resolve_current(conn, code)
        rec = {"ref_code": code, "name": name,
               "current_code": cur["code"] if cur else "",
               "state": cur["state"] if cur else "",
               "note": ""}
        if cur is None:
            rec["note"] = "标准库未收录, 无法判定现行版 → 待补充"
        elif cur.get("deprecated_only"):
            rec["note"] = "库中仅有废止版, 现行版缺失 → 待补充"
        elif (cur["state"] or "") == "废止":
            rec["note"] = "解析到的仍是废止版 → 待核实"
        out.append(rec)
    return out


if __name__ == "__main__":
    from pathlib import Path
    db = Path(__file__).resolve().parent.parent / "data" / "ohs.db"
    conn = sqlite3.connect(str(db))
    refs = resolve_all_refs(conn)
    ok = sum(1 for r in refs if r["current_code"] and not r["note"])
    print(f"standard_ref {len(refs)} 项, 已解析出现行版 {ok} 项\n")
    for r in refs:
        mark = "✅" if not r["note"] else "⚠"
        print(f"  {mark} {r['ref_code']:<14} → {r['current_code'] or '—':<20} {r['note']}")
