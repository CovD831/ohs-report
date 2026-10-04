"""迁移: 把定式章节 (FIXED_TEXTS 有键的节) 的 section_states 置为 fixed + 换成定式文本

背景 (skill: 导出优先级):
  word_export 读 section_states[sn] 的规则 = 「state=="generated" → 用库里的 text;
  否则 → 用 llm_draft._fixed_text」。所以**只在源码里加 FIXED_TEXTS 不够** ——
  库里若已落盘 state="generated" 的旧文本, 定式永远不生效。

本脚本: 对每个项目, 凡 section_states[sn].state=="generated" 且 sn ∈ FIXED_TEXTS 键,
  用 _fixed_text(sn, project) 现算文本覆盖, 并把 state 置 "fixed"。
幂等: 重复跑结果相同 (已是 fixed 的跳过)。
用法: python tools/migrate_section_states_to_fixed.py [--apply]
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "ohs.db"


def main() -> int:
    apply = "--apply" in sys.argv
    refresh = "--refresh" in sys.argv   # 强制用源码定式重算 (源码改了但库里是 fixed 时用)
    from web.llm_draft import FIXED_TEXTS, _fixed_text

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, data FROM project").fetchall()
    total = 0
    for r in rows:
        pid = r["id"]
        try:
            d = json.loads(r["data"])
        except Exception:
            continue
        ss = d.get("section_states") or {}
        pd_ = dict(d)
        pd_["id"] = pid
        changed = 0
        for sn in list(ss.keys()):
            if sn not in FIXED_TEXTS:
                continue
            cur = ss.get(sn) or {}
            if not refresh and cur.get("state") != "generated":
                continue          # 已是 fixed/manual → 不动 (幂等)
            txt = _fixed_text(sn, pd_)
            if not txt:
                continue
            if refresh and txt == cur.get("text"):
                continue          # 文本未变 → 不写
            ss[sn] = {"state": "fixed", "text": txt}
            changed += 1
        if changed:
            print(f"  {pid}: {changed} 节 → fixed")
            total += changed
            if apply:
                d["section_states"] = ss
                conn.execute("UPDATE project SET data=? WHERE id=?",
                             (json.dumps(d, ensure_ascii=False), pid))
    if apply and total:
        conn.commit()
    conn.close()
    if not total:
        print("(无待处理)")
    print(f"{'✅ 已落库' if apply else '[DRY-RUN]'} 共 {total} 节; {'加 --apply 生效' if not apply else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
