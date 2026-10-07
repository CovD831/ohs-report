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

    ⚠ 部分号保留: 'GB 39800.1' / 'GB/T 38144.1' 的分部号 (".1"/".2") **属于身份**,
    不得剥除 — 否则 'GB 39800' (总称) 会错配到 'GB39800.10' (机械分部) 或
    永远匹配不上任何分部 (2026-10 事故: 12 处 '现行版本待补充' 即此因)。
    """
    s = re.sub(r"[\s\u3000]", "", code or "").upper()
    s = re.sub(r"[—–−－]", "-", s)
    s = re.sub(r"-(?:19|20)\d{2}$", "", s)      # 去年份
    s = s.replace("GBZ/T", "GBZ").replace("GB/T", "GB")
    return s


def _part_no(base: str) -> int | None:
    """取分部号: 'GB39800.2' → 2; 无分部 → None"""
    m = re.match(r"^[A-Z]+\d+\.(\d+)$", base)
    return int(m.group(1)) if m else None


def _family(base: str) -> str:
    """族键: 'GB39800.2' → 'GB39800' (用于找不到分部时的同族回退)"""
    m = re.match(r"^([A-Z]+\d+)", base or "")
    return m.group(1) if m else (base or "")


def _year(code: str) -> int:
    """标准号年份 = 最后一个 4 位数 (GB17017-2010 的 17017 是编号, 不是年份)"""
    m = _RE_YEAR.findall(code or "")
    return int(m[-1]) if m else 0


def resolve_current(conn: sqlite3.Connection, ref_code: str) -> dict | None:
    """无年份/任意标准号 → 库中**未废止的最新版**记录

    返回 {code, name, state, part_fallback} 或 None (库中无该标准号)

    分部处理 (2026-10):
      - 引用带分部号 ('GB/T 38144.1') → 精确匹配同分部
      - 引用无分部 ('GB 39800', 报告依据表常见总称) → 库里只有 'GB39800.1/.2/...'
        → 取**第1部分(总则/基础分部)**, 并在 part_fallback 标注,
        由调用方在提示块说明 "总称→第1部分" (不写 "待补充" — 库里有权威版本)。
    """
    base = _base_code(ref_code)
    if not base:
        return None
    rows = conn.execute("SELECT code, name, state, replace_of FROM standard_db").fetchall()
    same = [r for r in rows if _base_code(r[0]) == base]
    part_fallback = None
    if not same and _part_no(base) is None:
        # 无分部号的引用 → 同族第1部分 兜底 ('GB39800' → 'GB39800.1')
        fam = _family(base) + ".1"
        cand = [r for r in rows if _base_code(r[0]) == fam]
        if cand:
            same, part_fallback = cand, "总称→第1部分"
    if not same:
        return None
    # 优先未废止的, 再按年份降序
    alive = [r for r in same if (r[2] or "") not in ("废止", "已废止")]
    if not alive and _part_no(base) is not None:
        # 分部引用但同分部全部废止 → 族(无分部)兜底: 「换版时分部合并回主版」情形
        # (GB/T 38144.1-2019 废止 → GB 38144-2025 合并版, 2026-10 取证)
        fam = _family(base)                       # 'GB38144.1' → 'GB38144'
        cand = [r for r in rows if _base_code(r[0]) == fam]
        alive_cand = [r for r in cand if (r[2] or "") not in ("废止", "已废止")]
        if alive_cand:
            same, part_fallback = cand, "分部→合并主版"
            alive = alive_cand
    if not alive:
        # 同族全部废止 → 跨族代替兜底: 沿 replace_of **反向**找现行代替者
        # (实例: GB/T 11651-2008 被 GB 39800.1-2020 全部代替 — SAMR 详情页取证, 2026-10)
        # ⚠ 分隔符不能含 '/': 'GB/T 11651-2008' 会被 `/` 切成 'T 11651-2008'。
        hit = []
        for r in rows:
            for _tok in re.split(r"[,，;；]+", (r[3] or "")):
                if _tok.strip() and _base_code(_tok.strip()) == base:
                    hit.append(r)
                    break
        alive_hit = [r for r in hit if (r[2] or "") not in ("废止", "已废止")]
        if alive_hit:
            same, part_fallback = hit, "被代替→现行版"
            alive = alive_hit
    pool = alive or same
    pool.sort(key=lambda r: _year(r[0]), reverse=True)
    c, n, s = pool[0][0], pool[0][1], pool[0][2]
    return {"code": c, "name": n, "state": s, "deprecated_only": not alive,
            "part_fallback": part_fallback}


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


def fix_standard_refs(text: str, conn: sqlite3.Connection | None = None) -> tuple[str, list[str]]:
    """把 text 中**废止/无效**标准号替换为现行版 (确定性, 不靠 LLM)

    单一产地: 原 web/app.py `_fix_standard_refs` 迁入 (2026-10) — 避免 tools/ 或
    迁移脚本 import web.app 时触发其副作用 (_cleanup_expired / init_tasks)。

    事故 (2026-10): LLM 凭记忆引废止版 (GBZ 2.2—2007 / GBZ 188—2014)。
    已通过"标准版本基准"从源头减少, 但 LLM 仍可能偶发写错; 此处做确定性兜底替换:
    用 standard_db 把文中的 废止标准号 → 其现行版标准号。

    返回 (新文本, 改动说明列表); 无改动原样返回。conn=None 时自开连接 (只读用途)。
    """
    if not text:
        return text, []
    own = False
    if conn is None:
        try:
            from pathlib import Path as _P
            db = _P(__file__).resolve().parent.parent / "data" / "ohs.db"
            if not db.exists():
                return text, []
            conn = sqlite3.connect(str(db))
            own = True
        except Exception:
            return text, []

    # 文档中出现形态: GBZ 2.2—2007 / GBZ2.2-2007 / GB/T 18664-2002 / GB/T18664—2002第4.2条 ...
    # ⚠ 不能用 \b 收尾: 中文字符在 \w 内, '2002第' 之间无 \b → 紧跟中文的标准号会漏匹配。
    #   改用 (?![0-9A-Za-z]) 排除字母数字 (中文/标点可跟随)。
    re_ref = re.compile(r"(?<![0-9A-Za-z])(GB(?:/T|/Z)?|GBZ(?:/T)?)\s*(\d+(?:\.\d+)?)"
                        r"\s*[—\-–−－]\s*((?:19|20)\d{2})(?![0-9A-Za-z])")
    changes: list[str] = []
    # 状态表 (基准): (base, 年份) → state。
    # ⚠ 2026-10 修: 旧查询对 'GB 30077-2013' 这类带空格的库记录永远匹配不上 → 废止版漏替换。
    # 再修: 用 _base_code(去 /T、去年份) + 年份做键 —— 'GB/T 50034-2013' 与库中
    # 'GB 50034-2013' 归一后同为 ('GB50034', 2013), 消除 /T 与空格变体差异。
    st_map: dict[tuple, str] = {}
    for _c, _s in conn.execute("SELECT code, state FROM standard_db"):
        st_map.setdefault((_base_code(_c), _year(_c)), _s or "")

    def _sub(m):
        prefix, num, yr = m.group(1), m.group(2), m.group(3)
        full = f"{prefix}{num}-{yr}"
        try:
            state = st_map.get((_base_code(full), int(yr)), "")
            # 按全号匹配不到时再按原样查一次
            if not state:
                row = conn.execute("SELECT state FROM standard_db WHERE code=?", (full,)).fetchone()
                state = (row[0] if row else None) or ""
            if state not in ("废止", "已废止"):
                return m.group(0)
            cur = resolve_current(conn, full)
            if not cur or not cur.get("code"):
                return m.group(0)
            if (cur.get("state") or "") in ("废止", "已废止"):
                return m.group(0)          # 解析到的仍是废止版 → 不动
            new_code = cur["code"]
            if _year(new_code) <= int(yr):
                return m.group(0)          # 没更新 → 不动
            # 保留原文书写风格: 若有"前缀 编号"的空格则保留, 破折号形态保留
            spaced = bool(re.match(r"^[A-Za-z/]+\s+\d", m.group(0)))
            dash = "—" if "—" in m.group(0) else "-"
            # 前缀与编号取**现行版自身**(不能用 _base_code, 它抹掉了 /T 标记)
            m2 = re.match(r"^(GBZ/T|GB/T|GBZ|GB/Z|GB)\s*(\d+(?:\.\d+)?)", new_code)
            if not m2:
                return m.group(0)
            pfx, rest = m2.group(1), m2.group(2)
            yr_new = new_code[new_code.rfind("-") + 1:]
            new_full = f"{pfx} {rest}{dash}{yr_new}" if spaced else f"{pfx}{rest}{dash}{yr_new}"
            changes.append(f"{m.group(0)} → {new_full}")
            return new_full
        except Exception:
            return m.group(0)

    out = re_ref.sub(_sub, text)
    if own:
        conn.close()
    return out, changes


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
