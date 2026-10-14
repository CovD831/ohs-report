"""守卫: 标准号的"引用形态尺子"只能有一把 (knowledge.standard_version.STD_REF_PAT)

背景 (2026-10-14 实测): 仓库里曾有 6 份各写一遍的标准号归一化/匹配正则
  (standard_version 改写器 / validators V-05 / std_guard / table_skeleton / 两个入库脚本)。
  裂开方式是**一条护栏看得见、另一条看不见**: 全角"－"写法改写器认得 → 正文被静默改掉,
  而 V-05 的分隔符集里没有它 → 该报警的不报。table_skeleton 那份更隐蔽:
  它 SQL 侧只去空格不去连接号, 库里 'GBZ 230—2010' 这种写法永远查不中 → 废止骨架刷不动。

本文件钉两件事:
  A. 行为一致性: 对同一标准号的多种书写变体, "被改写"与"被 V-05 报警"必须同真同假
     (取样全部来自 standard_db, 不点名任何物质/标准号 → 本地与生产容器都能跑)。
  B. 单一产地: 消费方不得自带标准号正则 (静态源码检查, 防止将来又长出一份)。
用法: python tools/test_std_ref_parity.py
"""
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.oel import connect                                       # noqa: E402
import web.std_guard                                                    # noqa: E402
import knowledge.standard_version as sv                                 # noqa: E402
import web.table_skeleton                                               # noqa: E402
import web.validators                                                   # noqa: E402
from knowledge.standard_version import fix_standard_refs, is_superseded       # noqa: E402
from web.validators import _check_standards                             # noqa: E402

n_pass = n_fail = 0


def check(label, cond, detail=""):
    global n_pass, n_fail
    if cond:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label} {detail}")


# ---------- B. 单一产地 (静态) ----------
print("### B. 判据单一产地: 消费方不得自带标准号正则")
check("std_guard._ART 就是 STD_REF_PAT", web.std_guard._ART is sv.STD_REF_PAT, True)
check("std_guard._norm 就是 norm_code", web.std_guard._norm is sv.norm_code, True)

# 源码里不得再出现"GB...数字...连接号...年份"形态的自建正则 (允许引用 STD_REF_PAT)
_SELF_RE = re.compile(r"re\.compile\([^)]*GB|re\.findall\([^)]*GB|_\w+\s*=\s*lambda[^=]*连接号")
for mod, fn in ((web.validators, web.validators._check_standards),
                (sv, sv.fix_standard_refs),
                (web.table_skeleton, None)):
    src = inspect.getsource(fn) if fn else inspect.getsource(mod)
    hits = [ln.strip()[:60] for ln in src.splitlines() if _SELF_RE.search(ln)]
    check(f"{mod.__name__} 无自建标准号正则", not hits, hits)
    if fn and "STD_REF_PAT" not in src and "STD_REF_PAT" not in inspect.getsource(mod):
        check(f"{mod.__name__} 引用了 STD_REF_PAT", False, "没找到共享尺子")

check("STD_SEPS 覆盖 半角/en/em/minus/全角",
      all(ch in sv.STD_SEPS for ch in "-–—−－"), sv.STD_SEPS)

# norm_code / ref_code 的纯函数行为 (不碰库)
check("norm_code('gbz/t 181－2006')", sv.norm_code("gbz/t 181－2006"), "GBZ/T181-2006")
check("norm_code 去全角空格", sv.norm_code("GBZ\u30002.1—2019"), "GBZ2.1-2019")
m = sv.STD_REF_PAT.search("按 GBZ/T 196—2025 编制")
check("ref_code 命中→归一", sv.ref_code(m) if m else None, "GBZ/T196-2025")

# ---------- A. 行为一致性 (取样自 standard_db, 数据无关点名) ----------
print("\n### A. 同一标准号的各种写法: 改写与报警必须同真同假")
conn = connect()
rows = conn.execute("SELECT code, state FROM standard_db").fetchall()
# 取样: 库里能查到且"已废止"或"state 漏标但被代替"的号 —— 两类都必须被两条护栏认出来
targets = []
for code, state in rows:
    if not code or not re.search(r"(?:19|20)\d{2}$", code):
        continue
    if (state or "").strip() in ("废止", "已废止") or is_superseded(conn, code):
        targets.append(code)
    if len(targets) >= 6:
        break
check("取到可比对的旧版号", len(targets) >= 3, f"仅 {len(targets)} 条")

SPELLINGS = [
    ("{p} {n}—{y}", "em dash + 空格"),
    ("{p} {n}-{y}", "半角 + 空格"),
    ("{p}{n}-{y}", "半角无空格"),
    ("{p}{n}－{y}", "全角无空格"),
    ("{p}{n}−{y}", "minus sign"),
    ("{p} {n} － {y}", "全角带空格"),
]

def split_code(code):
    m = re.match(r"^(GBZ?|WS|HG|AQ|JB|DB)(?:\s*/\s*(T|Z))?(\d+(?:\.\d+)?)[—–\-－−]((?:19|20)\d{2})$",
                 sv.norm_code(code))
    return m.groups() if m else None

mismatch = []
tested = 0
for code in targets:
    parts = split_code(code)
    if not parts:
        continue
    p, t, num, yr = parts
    prefix = p + (f"/{t}" if t else "")
    for tmpl, label in SPELLINGS:
        text = tmpl.format(p=prefix, n=num, y=yr) + " 执行。"
        _, changed = fix_standard_refs(text, conn)
        flagged = bool(_check_standards(text))
        tested += 1
        if bool(changed) != flagged:
            mismatch.append((text, f"改写={bool(changed)} V-05={flagged} ({label})"))
check(f"{tested} 组写法(6 种变体 × {len(targets)} 个旧版号)判定一致",
      not mismatch, mismatch[:4])

# 现行版号: 两条护栏都不许动 (防过度替换/误报)
cur_codes = [c for c, s in rows if (s or "").strip() not in ("废止", "已废止")
             and not is_superseded(conn, c) and re.search(r"(?:19|20)\d{2}$", c or "")][:8]
false_pos = []
for code in cur_codes:
    parts = split_code(code)
    if not parts:
        continue
    p, t, num, yr = parts
    text = f"{p}{'/' + t if t else ''} {num}—{yr} 为准。"
    _, changed = fix_standard_refs(text, conn)
    flagged = _check_standards(text)
    if changed or any(i.get("level") == "error" for i in flagged):
        false_pos.append((text, changed, [i.get("rule_id") for i in flagged]))
check(f"{len(cur_codes)} 个现行版号不被误改/误报", not false_pos, false_pos[:3])
conn.close()

print(f"\n通过 {n_pass} / 失败 {n_fail}")
sys.exit(1 if n_fail else 0)
