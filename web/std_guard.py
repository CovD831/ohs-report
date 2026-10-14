"""标准号引用守卫 — 产物文本里出现「已废止标准号」时报警

用户红线: LLM 不得碰确定性内容。标准号是确定性数据(standard_db 有权威值),
但 LLM 会凭空补年份/引用旧版(实测: 产物中出现 GB 23466—2009, 而现行是 GB 23466-2025)。

策略:
  - 从 standard_db 读 state='废止' 的 (code, name) 构成黑名单
  - 扫描给定文本, 命中废止号 → 收集 (标准号, 建议替代号, 上下文)
  - 不改写文本(改写属生成端职责), 只做**校验 + 报告** → 供质检/回归调用

用法:
  from web.std_guard import scan_superseded
  issues = scan_superseded(text, conn)     # -> [{code, suggest, ctx}, ...]
  python -m web.std_guard <docx|txt> ...   # CLI: 打印报告, 有命中则 exit 1
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

# 标准号形态与归一化 = 单一产地 web.standard_version (2026-10-14 收口)
#   此处原有一份自己的 _ART/_norm: 分隔符集少一个 U+2212、前缀写成 '/?\s*T?'
#   (会把 'GB T 123' 这类非法写法也吞进来), 与改写器/V-05 三把尺子不一致。
from web.standard_version import STD_REF_PAT as _ART, norm_code as _norm, ref_code  # noqa: E402


def superseded_map(conn: sqlite3.Connection) -> dict[str, str]:
    """{废止号归一: 替代号} — 替代号从 replace_of 反查, 无则空串

    ⚠ replace_of 语义 = 「本版代替了谁」。所以要建「旧→新」映射,
      应遍历**现行**行的 replace_of, 而非读废止行的 replace_of。
    """
    out: dict[str, str] = {}
    # 主流: 现行行的 replace_of 指向被它替代的旧版
    for code, repl in conn.execute(
        "SELECT code, replace_of FROM standard_db "
        "WHERE replace_of IS NOT NULL AND replace_of!=''"
    ):
        out[_norm(repl)] = _norm(code)
    # 兜底: 废止行本身也进黑名单(替代号未知 → 空串)
    for (code,) in conn.execute("SELECT code FROM standard_db WHERE state='废止'"):
        out.setdefault(_norm(code), "")
    return out


def scan_superseded(text: str, conn: sqlite3.Connection) -> list[dict]:
    """扫描文本里引用的已废止标准号

    返回 [{code, suggest, ctx}, ...]（去重, 按首次出现序）
    """
    bad = superseded_map(conn)
    if not bad:
        return []

    found: dict[str, dict] = {}
    for m in _ART.finditer(text or ""):
        full = ref_code(m)
        if full in bad:
            if full not in found:
                s = max(0, m.start() - 40)
                e = min(len(text), m.end() + 40)
                found[full] = {
                    "code": m.group(0).strip(),
                    "norm": full,
                    "suggest": bad[full],
                    "ctx": text[s:e].replace("\n", " "),
                }
    return list(found.values())


# ---------- CLI ----------
def _read_any(path: str) -> str:
    p = Path(path)
    if p.suffix.lower() == ".docx":
        import zipfile

        z = zipfile.ZipFile(p)
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
        return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
    return p.read_text(encoding="utf-8", errors="ignore")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    db = "data/ohs.db"
    conn = sqlite3.connect(db)
    total = 0
    for path in argv:
        text = _read_any(path)
        issues = scan_superseded(text, conn)
        print(f"### {path}  ({len(text)} chars)")
        if not issues:
            print("    ✓ 未引用已废止标准号")
        for it in issues:
            total += 1
            sug = it["suggest"] or "?"
            print(f"    ✗ {it['code']}  → 应引 {sug}")
            print(f"       …{it['ctx']}…")
    print()
    print(f"{'✗ 发现 ' + str(total) + ' 处已废止引用' if total else '✓ 全部通过'}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
