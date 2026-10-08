#!/usr/bin/env python3
"""迁移: 裸编号假表题/假引用清理 (ada 3.1.3 实测)

背景 (2026-10 事故):
  LLM 在 3.1.3 节尾写了两处"裸表号"内容:
    ① 正文: "...其建筑规模、层数及火灾危险类别等情况汇总见表1。"  (裸 表1, 无横杠)
    ② 假表题行: "表1 建设项目建构筑物及环境相关情况汇总表" (与挂载的真实表体不符,
       该节实际挂的是气象表; 建构筑物表在建 3.7.1/表3.7-1)
  裸表号违反系统表号体系 (表N-M 带横杠); 且表题-表体不一致误导阅读。

修法 (源头精确替换; 导出护栏双保险见 word_export._is_llm_table_title /
     _fix_dangling_table_refs 的裸引用分支):
  - 引用改为指向真实所在: "汇总详见3.7.1节表3.7-1。"
  - 假表题行整行删除

幂等: 替换后不再命中 → 二跑 0 变更。
用法: python tools/migrate_bare_table_refs.py [--apply]
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
DB = ROOT / "data" / "ohs.db"

# (pid, sec, old, new) — old 必须全节唯一且命中; MISS 即报错 (防假绿)
RULES = [
    ("ada54603a9", "3.1.3",
     "其建筑规模、层数及火灾危险类别等情况汇总见表1。",
     "其建筑规模、层数及火灾危险类别等情况汇总详见3.7.1节表3.7-1。"),
    ("ada54603a9", "3.1.3",
     "\n\n表1 建设项目建构筑物及环境相关情况汇总表",
     ""),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    by_pid: dict[str, dict] = {}

    n_hit = n_miss = n_done = 0
    for pid, sec, old, new in RULES:
        if pid not in by_pid:
            row = c.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
            by_pid[pid] = json.loads(row["data"]) if row else {}
        d = by_pid[pid]
        ss = d.get("section_states") or {}
        v = ss.get(sec)
        txt = (v.get("text") or "") if isinstance(v, dict) else ""
        if old not in txt:
            if new and new in txt:      # 已迁移过 (new 已在位) → 幂等跳过
                print(f"DONE  [{pid} §{sec}] 已迁移")
                n_done += 1
                continue
            if not new and old not in txt and "表1 建设项目建构筑物及环境" not in txt:
                print(f"DONE  [{pid} §{sec}] 已迁移 (待删行不存在)")
                n_done += 1
                continue
            print(f"MISS  [{pid} §{sec}] 未命中: {old[:44]!r}")
            n_miss += 1
            continue
        if not isinstance(v, dict):
            print(f"MISS  [{pid} §{sec}] 节不存在")
            n_miss += 1
            continue
        v["text"] = txt.replace(old, new, 1)
        print(f"HIT   [{pid} §{sec}] {old[:40]!r} → {new[:40]!r}")
        n_hit += 1

    if n_miss:
        print(f"\n✗ 有 {n_miss} 条未命中, 中止不写 (修规则或确认已迁移)")
        return 1

    if args.apply:
        for pid, d in by_pid.items():
            c.execute("UPDATE project SET data=? WHERE id=?",
                      (json.dumps(d, ensure_ascii=False), pid))
        c.commit()
        print(f"\n✅ 已写入 {len(by_pid)} 个项目 ({n_hit} 处)")
    else:
        print(f"\n(预演) {n_hit} 处待改 — 加 --apply 落盘")
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
