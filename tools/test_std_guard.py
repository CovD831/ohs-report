"""单测: 标准号守卫 web/std_guard.py — 产物不得引用已废止标准号

背景(用户红线): LLM 不得碰确定性内容。标准号有 standard_db 权威值, 但 LLM
会凭空补年份/引旧版(实测: 产物出现 GB 23466—2009, 现行是 GB 23466-2025)。

本测试覆盖三层:
  1. 归一化 _norm — 横线变体/空格/大小写必须归一 (否则漏检)
  2. superseded_map — 建「旧→新」映射的方向正确 (⚠ replace_of 语义=本版代替了谁,
     曾写反 → 测试锁死)
  3. scan_superseded — 真 DB 命中/不误报 + 合成黑名单边界

运行: python tools/test_std_guard.py
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from web.std_guard import _norm, superseded_map, scan_superseded  # noqa: E402

ok = 0
fail = 0


def check(desc, got, want):
    global ok, fail
    good = got == want
    ok += good
    fail += (not good)
    print(f"  {'✓' if good else '✗'} {desc}")
    if not good:
        print(f"      期望={want!r}  实得={got!r}")


# ---------- 1. 归一化 ----------
print("### 1. 标准号归一化 _norm")
NORM = [
    ("GB/T 23466-2009", "GB/T23466-2009"),
    ("GB/T23466-2009", "GB/T23466-2009"),
    ("gb/t 23466—2009", "GB/T23466-2009"),      # 破折号变体
    ("GB/T 23466–2009", "GB/T23466-2009"),      # en-dash
    ("GB/T 23466－2009", "GB/T23466-2009"),     # 全角减号
    ("GBZ/T 300.100—2018", "GBZ/T300.100-2018"),
    ("", ""),
    (None, ""),
]
for src, want in NORM:
    check(f"_norm({src!r})", _norm(src), want)

# ---------- 2. 映射方向 ----------
print("\n### 2. superseded_map 方向 (replace_of = 本版代替了谁)")
_c = sqlite3.connect(":memory:")
_c.execute("CREATE TABLE standard_db (code TEXT, name TEXT, state TEXT, replace_of TEXT)")
_c.executemany("INSERT INTO standard_db VALUES (?,?,?,?)", [
    ("GB 23466-2025", "听力防护装备的选择、使用和维护", "现行", "GB/T 23466-2009"),
    ("GB/T 23466-2009", "护听器的选择指南", "废止", ""),
    ("GB 11111-2020", "某现行标准", "现行", ""),          # 无替代关系
    ("GB 22222-1990", "某废止标准", "废止", ""),          # 废止但无 replace_of
])
_m = superseded_map(_c)
check("废止号进黑名单", "GB/T23466-2009" in _m, True)
check("旧→新 方向正确", _m.get("GB/T23466-2009"), "GB23466-2025")
check("无 replace_of 的废止号也在(替代号空)", _m.get("GB22222-1990"), "")
check("★现行号不得进黑名单", "GB23466-2025" in _m, False)
check("无关现行号不在", "GB11111-2020" in _m, False)

# ---------- 3. 扫描 ----------
print("\n### 3. scan_superseded 扫描")

# 3a. 合成: 命中 + 不误报 + 上下文
TEXT = (
    "本项目依据《工业企业设计卫生标准》GBZ 1-2010 与"
    "《护听器的选择指南》GB/T 23466-2009 进行评价; "
    "同时参考 GB/T 23466—2009 的另一处引用。"
    "现行标准 GB 23466-2025 亦已纳入。"
    "另引 GB 50011-2010、GBZ 2.1-2019 —— 均现行, 不得报。"
)
res = scan_superseded(TEXT, _c)
check("★命中 1 个废止号(去重)", len(res), 1)
if res:
    check("命中号归一正确", res[0]["norm"], "GB/T23466-2009")
    check("给出替代号", res[0]["suggest"], "GB23466-2025")
    check("上下文非空", bool(res[0]["ctx"]), True)
check("★现行 GB 23466-2025 不报", any(r["norm"] == "GB23466-2025" for r in res), False)

# 3b. 干净文本 → 0
check("干净文本 → 0 命中",
      scan_superseded("依据 GBZ 1-2010、GB 50011-2010 评价。", _c), [])

# 3c. 空文本 / None
check("空文本 → 0", scan_superseded("", _c), [])
check("None → 0", scan_superseded(None, _c), [])

# 3d. 无年份的标准号 → 不报 (守卫只认带年份的确定引用, 避免误报)
check("无年份号不报(GB/T 23466)",
      scan_superseded("依据 GB/T 23466 选择护听器。", _c), [])

# 3e. 空黑名单 → 0 (不崩)
_c2 = sqlite3.connect(":memory:")
_c2.execute("CREATE TABLE standard_db (code TEXT, name TEXT, state TEXT, replace_of TEXT)")
check("空库 → 0 命中且不崩",
      scan_superseded("GB/T 23466-2009", _c2), [])

# ---------- 4. 真实 DB (若存在) ----------
print("\n### 4. 真实 standard_db")
db = ROOT / "data" / "ohs.db"
if db.exists():
    rc = sqlite3.connect(db)
    rm = superseded_map(rc)
    check("真实库黑名单非空", len(rm) > 0, True)
    check("★23466 旧版在真实黑名单",
          rm.get("GB/T23466-2009"), "GB23466-2025")
    # 真稿回归: 旧稿(长兴)应命中, 新稿(新泰)应 0 命中
    rec = Path("/tmp/rec16_conv")
    if rec.is_dir():
        for fn, want_zero in [("长兴.docx", False), ("新泰.docx", False)]:
            p = None
            for cand in rec.glob("*.docx"):
                if fn.replace(".docx", "") in cand.name:
                    p = cand
                    break
            if p:
                from web.std_guard import _read_any
                hits = scan_superseded(_read_any(str(p)), rc)
                print(f"    {p.name}: {len(hits)} 处废止引用 {[h['norm'] for h in hits]}")
    rc.close()
else:
    print("    (无 data/ohs.db, 跳过)")

print()
print(f"通过 {ok} / {ok + fail}")
if fail:
    print(f"✗ {fail} 项失败")
sys.exit(1 if fail else 0)
