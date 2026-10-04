"""迁移: 修正 section_states 正文里的**悬空表号引用** (章号平移遗留)

背景:
  报告章号平移后 (旧 5.x 检测/建筑卫生学 → 现 4.4/3.7/3.8/5.2/10.1), LLM 生成的正文
  仍写旧表号 (如「见表5.3-1」), 而这些表号在现行报告里不存在 → **悬空引用**
  (违背用户红线「表编号逐张对齐」)。2026-10 实测 ada54603a9 有 55 处。

修法 (确定性, 零 LLM):
  按「旧表号 → 新表号」映射替换; 映射依据 = 内容主题一致 (检测结果/建筑卫生学/辅助用室/
  物理因素/关键控制点/建构筑物/选址)。无法确定映射的 → 改写为「详见相应章节表格」
  (不臆造表号, 符合 prompt 5f 的兜底要求)。

幂等: 重复跑结果相同 (替换后字符串不再命中旧表号)。
用法: python tools/migrate_fix_table_refs.py [--apply]
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ohs.db"

# 旧表号 → 新表号 (按内容主题一一对应; 章号平移遗留)
REMAP = {
    "表5.3-1": "表4.4-2",   # 化学毒物检测结果 (接触水平/限值对照)
    "表5.3-2": "表4.4-3",   # 物理因素检测结果
    "表5.3-3": "表4.4-3",   # 物理因素限值 → 检测结果表
    "表5.4-1": "表3.8-3",   # 辅助用室检查 (原 5.4 结构 → 现 3.8)
    "表5.2-1": "表3.2-2",   # 选址评价
    "表5.2-2": "表5.2-2",   # 物理因素对人体健康影响 (已对)
    "表5.1-1": "表3.7-1",   # 建构筑物 → 3.7
    "表6.2-2": "表6.2-1",
    "表6.2-3": "表6.2-1",
    "表6.2-4": "表6.2-1",
    "表2.2-1": "",          # 无对应表 → 改写为文字指代
}
PAT = re.compile(r"(见|详见|参见|如表|见表|详见表)(表\d+(?:\.\d+)*-\d+)")


def main() -> int:
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    total = 0
    for r in conn.execute("SELECT id, data FROM project").fetchall():
        pid = r["id"]
        try:
            d = json.loads(r["data"])
        except Exception:
            continue
        ss = d.get("section_states") or {}
        n = 0
        for sn, v in ss.items():
            t = (v or {}).get("text") or ""
            if not t:
                continue
            new = t
            for old, nw in REMAP.items():
                if old not in new:
                    continue
                if nw and nw != old:
                    new = new.replace(old, nw)
                    n += 1
                elif not nw:
                    # 无对应表 → 改文字指代 (保留动词, 去掉表号)
                    new = re.sub(r"(见|详见|参见|如表|见表)" + re.escape(old),
                                 r"\1相应章节表格", new)
                    n += 1
            if new != t:
                v["text"] = new
        if n:
            print(f"  {pid}: {n} 处表号引用修正")
            total += n
            if apply:
                d["section_states"] = ss
                conn.execute("UPDATE project SET data=? WHERE id=?",
                             (json.dumps(d, ensure_ascii=False), pid))
    if apply and total:
        conn.commit()
    conn.close()
    print(f"{'✅ 已落库' if apply else '[DRY-RUN]'} 共 {total} 处;{' 加 --apply 生效' if not apply else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
