"""投影层效果对比: 同一章节, 投影前(原始清单) vs 投影后(关键字段)

这是本次改造的核心验收 —— 用真实报告形态核对投影输出。
"""
import json
import sqlite3
import sys

sys.path.insert(0, "/Users/abaaba/Projects/ohs-report")
from web.field_projection import render_for_prompt  # noqa: E402
from web.structure_data import get_chapter_info  # noqa: E402

# 先注入派生字段 (radiation/work_system) 再投影
from web.derived_fields import derive_fields  # noqa: E402

c = sqlite3.connect("/Users/abaaba/Projects/ohs-report/data/ohs.db")
c.row_factory = sqlite3.Row

pid = sys.argv[1] if len(sys.argv) > 1 else "c8c7ff0a4d"
d = json.loads(c.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()["data"])
d.update({k: v for k, v in derive_fields(d).items() if v})

SECTIONS = ["1.1", "1", "2.1", "3.4.2", "3.6.1", "5.1.1.2"]
for sec in SECTIONS:
    proj = render_for_prompt(sec, d, {})
    whole = get_chapter_info(sec, d, {})
    print("=" * 78)
    print(f"### 章节 {sec}")
    print(f"    投影层是否命中: {'是' if proj else '否 (回退旧逻辑)'}")
    print(f"    get_chapter_info 输出长度: {len(whole)} 字符")
    print()
    print(whole[:1100])
    print()
