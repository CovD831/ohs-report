"""章节状态迁移 — 旧10.2.x键 → 新1-9键

迁移映射 (旧引擎章节 → 报告章节):
  10.2.1 → 7 (评价要点) | 10.2.3 → 8 (工程分析) | 10.2.5 → 2 (识别评价)
  10.2.6 → 3 (防护) | 10.2.10 → 4? (控制点归综合) | 10.2.11 → 5 (补充建议)
  10.2.12 → 6 (结论) | 10.2.3.7 → 4 (综合) | 10.2.4 → 9 (类比)
用法: python3 -m web.migrate_states <pid>
"""
import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "ohs.db"

# 旧 → 新 (关联性: 哪个旧章的文本最接近哪个新章)
MAP = {
    "10.2.1": "7", "10.2.2": "1", "10.2.3": "8", "10.2.3.7": "4",
    "10.2.3.8": "4", "10.2.4": "9", "10.2.5": "2", "10.2.6": "3",
    "10.2.7": "3", "10.2.8": "3", "10.2.9": "4", "10.2.10": "4",
    "10.2.11": "5", "10.2.12": "6",
}


def migrate(pid: str) -> dict:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
    if not r:
        return {"error": "not found"}
    data = json.loads(r["data"])
    old_states = data.get("section_states", {})
    new_states = {}
    moved = 0
    for old_sec, st in old_states.items():
        new_sec = MAP.get(old_sec)
        if not new_sec:
            continue
        text = st.get("text", "")
        # 合并策略: 同目标章的多个旧章文本拼接 (如4=10.2.3.7+10.2.9+10.2.10)
        if new_sec in new_states:
            existing = new_states[new_sec]["text"]
            new_states[new_sec] = {"state": "generated",
                                   "text": existing + "\n\n" + text}
        else:
            new_states[new_sec] = {"state": st.get("state", "generated"), "text": text}
        moved += 1
    data["section_states"] = new_states
    conn.execute("UPDATE project SET data=? WHERE id=?",
                 (json.dumps(data, ensure_ascii=False), pid))
    conn.commit()
    conn.close()
    return {"ok": True, "moved": moved, "sections": list(new_states.keys())}


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "demo-cx"
    print(migrate(pid))
