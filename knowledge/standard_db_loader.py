"""标准库构建器 — 评价依据标准库 (10.2.1 报告引用清单+现行状态)

数据源:
  1. nhc catalog (acquire/raw/nhc_gbz/all_catalog.jsonl) — GBZ/WS/T 全部
     含 standardcode/title/pubtime/sstime/url (现行状态直接可得)
  2. std.samr (gbQueryPage API) — GB/GB/T 国家标准的 现行/废止/即将实施
  3. GBZ/T 196—2025 第2章 规范性引用文件 — 报告评价依据骨架 (26个标准)

输出: data/ohs.db → standard_db (标准号/名称/类型/状态/替代/实施期/评价依据标记)
      → standard_ref (GBZ/T 196 引用清单: 标准号/名称/用途/报告章节)

用途: 10.2.1 评价依据清单 (自动生成+时效核验, 不引过期版)
用法: python3 -m knowledge.standard_db_loader
"""
import json
import re
import sqlite3
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from acquire.base import PoliteFetcher  # noqa: E402

DB = Path(__file__).resolve().parent.parent / "data/ohs.db"
CATALOG = Path(__file__).resolve().parent.parent / "acquire/raw/nhc_gbz/all_catalog.jsonl"

# GBZ/T 196—2025 第2章 规范性引用文件 (标准号→名称)
REF_196 = [
    ("GB 5083", "生产设备安全卫生设计总则"),
    ("GB/T 12801", "生产过程安全卫生要求总则"),
    ("GB/T 16758", "排风罩的分类及技术条件"),
    ("GB/T 18664", "呼吸防护用品的选择、使用与维护"),
    ("GB/T 23466", "护听器的选择指南"),
    ("GB/T 38144", "眼面部防护应急喷淋和洗眼设备"),
    ("GB 39800", "个体防护装备配备规范"),
    ("GB 50019", "工业建筑供暖通风与空气调节设计规范"),
    ("GB 50033", "建筑采光设计标准"),
    ("GB/T 50034", "建筑照明设计标准"),
    ("GB 50073", "洁净厂房设计规范"),
    ("GB/T 50087", "工业企业噪声控制设计规范"),
    ("GB 50187", "工业企业总平面设计规范"),
    ("GBZ 1", "工业企业设计卫生标准"),
    ("GBZ 2.1", "工作场所有害因素职业接触限值 第1部分:化学有害因素"),
    ("GBZ 2.2", "工作场所有害因素职业接触限值 第2部分:物理因素"),
    ("GBZ 158", "工作场所职业病危害警示标识"),
    ("GBZ 159", "工作场所空气中有害物质监测的采样规范"),
    ("GBZ 188", "职业健康监护技术规范"),
    ("GBZ/T 194", "工作场所防止职业中毒卫生工程防护措施规范"),
    ("GBZ/T 195", "有机溶剂作业场所个人职业病防护用品使用规范"),
    ("GBZ/T 205", "密闭空间作业职业危害防护规范"),
    ("GBZ/T 223", "工作场所有毒气体检测报警装置设置规范"),
    ("GBZ/T 224", "职业卫生名词术语"),
    ("GBZ/T 225", "用人单位职业病防治指南"),
    ("GBZ 230", "职业性接触毒物危害程度分级"),
    ("GBZ/T 298", "工作场所化学有害因素职业健康风险评估技术导则"),
]


def _norm(code: str) -> str:
    """标准号归一化: 全角—→- 去空格"""
    return re.sub(r"[—–]", "-", code).replace(" ", "").upper()


def resolve_current(conn, code_no_year: str) -> dict | None:
    """无年份引用 → 现行版 (GBZ 188 → GBZ 188-2025; 排除已废止)
    前缀精确: GBZ 1 匹配 GBZ1- 不匹配 GBZ19-; GB 39800 匹配 GB39800.1- 总则"""
    nc = _norm(code_no_year)
    rows = conn.execute(
        "SELECT code, name, state, effdate FROM standard_db "
        "WHERE code LIKE ? OR code LIKE ? "
        "ORDER BY code DESC",
        (nc + "-%", nc + ".%")).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT code, name, state, effdate FROM standard_db "
            "WHERE code LIKE ? ORDER BY code DESC", (nc + "%",)).fetchall()
    if not rows:
        return None
    # 目标: 主版本 (GB 39800 → .1 总则; GBZ 1 → GBZ1; 排除 .2/.3... 分部分)
    core = nc
    best = None
    for code, name, state, effdate in rows:
        # 分部分排除: 引用无 .N 时, 选 .1 (总则) 或 无分部分
        if "." in code and "." not in core:
            part = code.split(".")[-1]
            if not part.startswith(("1-", "1.")):
                continue
        if "废止" in state or "已废止" in name:
            continue
        if best is None:
            best = (code, name, state, effdate)
    return {"code": best[0], "name": best[1], "state": best[2], "effdate": best[3]} if best else None


def load_nhc_catalog() -> dict[str, dict]:
    """nhc catalog → {norm_code: {title, pubtime, sstime, url}}"""
    out = {}
    for ln in CATALOG.open(encoding="utf-8"):
        it = json.loads(ln)
        code = (it.get("standardcode") or "").strip()
        if not code:
            continue
        out[_norm(code)] = {
            "title": it.get("title", ""), "pubtime": it.get("pubtime", ""),
            "sstime": it.get("sstime", ""), "url": it.get("url", ""),
        }
    return out


def load_samr(codes: list[str]) -> dict[str, dict]:
    """std.samr API → {norm_code: {title, state}} (GB/GB/T 状态)"""
    f = PoliteFetcher("samr_std_db")
    out = {}
    for code in codes:
        if not code.startswith("GB"):
            continue
        try:
            r = f.get_json("https://std.samr.gov.cn/gb/search/gbQueryPage?searchText="
                           + urllib.parse.quote(code))
            rows = (r.get("data") or r).get("rows", [])
            for it in rows:
                c = (it.get("C_STD_CODE") or "").replace("<sacinfo>", "").replace("</sacinfo>", "")
                state = it.get("STATE", "")
                name = it.get("C_C_NAME", "")
                if re.search(r"GB", c):
                    out[_norm(c)] = {"title": name, "state": state}
        except Exception:
            continue
    return out


def main():
    conn = sqlite3.connect(str(DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS standard_db (
          id       INTEGER PRIMARY KEY AUTOINCREMENT,
          code     TEXT NOT NULL,   -- 标准号 (GBZ 2.1-2019)
          name     TEXT,            -- 名称
          state    TEXT,            -- 现行/废止/即将实施/已废止
          pubdate  TEXT,            -- 发布日期
          effdate  TEXT,            -- 实施日期
          replace_of TEXT,          -- 代替 (GBZ 188-2014)
          url      TEXT,
          source   TEXT NOT NULL,   -- nhc_catalog | samr
          verified INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS standard_ref (
          id     INTEGER PRIMARY KEY AUTOINCREMENT,
          code   TEXT NOT NULL,     -- 标准号 (无年份: GBZ 2.1)
          name   TEXT NOT NULL,     -- 名称
          clause TEXT,              -- 引用条款 (GBZ/T 196 2)
          purpose TEXT,             -- 报告用途 (评价依据)
          source TEXT NOT NULL
        )
    """)
    # 1) 评价依据骨架
    conn.execute("DELETE FROM standard_ref WHERE source='GBZ/T 196-2025 2章'")
    conn.executemany("INSERT OR REPLACE INTO standard_ref (code,name,purpose,source) VALUES (?,?,?,?)",
                     [(c, n, "评价依据(规范性引用)", "GBZ/T 196-2025 2章") for c, n in REF_196])
    # 2) nhc catalog → standard_db
    nhc = load_nhc_catalog()
    conn.execute("DELETE FROM standard_db WHERE source='nhc_catalog'")
    n = 0
    for code, it in nhc.items():
        # 状态推断: 有sstime=实施 (含年份); 无sstime=待实施/废止
        state = "实施中" if it["sstime"] else "待实施"
        conn.execute("""
            INSERT OR REPLACE INTO standard_db (code, name, state, pubdate, effdate, url, source)
            VALUES (?, ?, ?, ?, ?, ?, 'nhc_catalog')
        """, (code, it["title"], state, it["pubtime"], it["sstime"], it["url"]))
        n += 1
    print(f"nhc catalog 入库: {n} 条")
    # 2.5) std.samr 补 GB/GB/T (评价依据里的 GB 系列)
    gb_refs = [c for c, _ in REF_196 if c.startswith("GB")]
    samr = load_samr(gb_refs)
    print(f"std.samr 获取 GB 系列: {len(samr)} 条")
    for code, it in samr.items():
        conn.execute("""
            INSERT OR REPLACE INTO standard_db (code, name, state, source)
            VALUES (?, ?, ?, 'samr')
        """, (code, it.get("title", ""), it.get("state", "")))
    # 3) 报告评价依据的现行状态 (无年份引用 → 现行版)
    print("\n=== 评价依据标准现行版 (解析无年份→现行) ===")
    for c, name in REF_196:
        cur = resolve_current(conn, c)
        if cur:
            print(f"  {c:12s} → 现行: {cur['code']:22s} {cur['name'][:24]:26s} [{cur['state']}]")
        else:
            print(f"  {c:12s} ⚠️ 无现行记录")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
