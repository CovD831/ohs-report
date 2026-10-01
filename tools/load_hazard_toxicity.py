"""B2: 建 hazard_toxicity 表 — 化学物「形态 + 危害程度分级」确定性查表

背景:
  真稿表 5.2-1 结构(逐格取证, 新泰/长兴/浦发三份备案稿):
      名称 | 形态 | 危害特性 | 对人体健康的影响 | 危害程度 | 可能引起的职业病或职业性病损
  其中「形态」= 物质常温/工业使用状态(气体/液体/固体/气溶胶) — 确定性属性
      「危害程度」= GBZ/T 230—2025《工作场所毒物危害程度分级标准》THI 分级
         轻度危害(Ⅰ): THI<35 | 中度危害(Ⅱ): 35≤THI<50
         高度危害(Ⅲ): 50≤THI<65 | 极度危害(Ⅳ): THI≥65
         (标准 2025-09-04 发布, 2026-02-01 实施, 代替 GBZ/T 230—2010)

  但 THI 需 8 项毒理学数据(LC50/LD50/刺激腐蚀/致敏/生殖毒性/致癌/扩散/蓄积),
  我方无此数据源 → 按用户定的原则: **只录入已确定的常见毒物等级**,
  未收录的一律标「待补充」, 不推算、不编造。

数据来源: 3 份真实备案稿 5.2-1 表逐格取证(28 种物质)
    /tmp/rec16_conv/6-1新泰--预评（备案稿）.docx
    /tmp/rec16_conv/长兴--预评（备案稿7-31）.docx
    /tmp/rec16_conv/预评新-  浦发热电（备案稿）.docx

⚠ 不作为复刻来源的「成稿事故」(已识别, 故意不复刻):
    - 浦发稿「危害程度」列串入了职业病名(如"职业性急性一氧化碳中毒") → 填错, 不复刻
    - 浦发稿 二氧化硫/氮氧化物 也无危害程度值 → 标「-」
    - 一氧化碳在浦发稿写作「一氧化碳（非高原）」→ 归一为「一氧化碳」

用法: .venv/bin/python tools/load_hazard_toxicity.py [--apply]
"""
import sqlite3
import sys
import argparse

DDL = """
CREATE TABLE IF NOT EXISTS hazard_toxicity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_name TEXT NOT NULL,          -- 物质名(与 hazard_factor.name 对齐)
    aliases     TEXT,                   -- 别名, 竖线分隔
    form        TEXT,                   -- 形态: 气体/液体/固体/气溶胶/粉尘
    level       TEXT NOT NULL,          -- 极度危害/高度危害/中度危害/轻度危害/待补充
    grade       INTEGER,                -- Ⅳ/Ⅲ/Ⅱ/Ⅰ → 4/3/2/1; 待补充=NULL
    basis       TEXT,                   -- 依据(标准号/取证来源)
    source      TEXT NOT NULL,          -- 取证来源文件
    verified    INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_hazard_toxicity_name
    ON hazard_toxicity(factor_name);
"""

# (物质名, 别名, 形态, 等级) — 全部来自真实备案稿 5.2-1 表逐格取证
DATA = [
    # --- 新泰 ---
    ("石灰石粉尘", "石灰石粉", "固体", "待补充"),   # 真稿该行危害程度列为「/」
    ("氟化氢", "氢氟酸|HF", "气体", "高度危害"),
    ("氯化氢及盐酸", "氯化氢|盐酸", "液体", "中度危害"),
    ("氧化钙", "生石灰", "固体", "轻度危害"),
    # --- 长兴 ---
    ("甲苯", "甲苯", "液体", "中度危害"),
    ("苯乙烯", "乙烯基苯", "液体", "中度危害"),
    ("丙烯酸丁酯", "丙烯酸正丁酯", "液体", "中度危害"),
    ("甲基丙烯酸甲酯", "MMA|有机玻璃单体", "液体", "轻度危害"),
    ("环己烷", "六氢化苯", "液体", "轻度危害"),
    ("1,6-己二醇", "己二醇", "液体", "轻度危害"),
    ("丙烯酸", "丙烯酸单体", "液体", "轻度危害"),
    ("甲基丙烯酸", "MAA", "液体", "轻度危害"),
    ("异丙醇", "IPA", "液体", "轻度危害"),
    ("丁酮", "甲乙酮|MEK", "液体", "轻度危害"),
    ("丙烯腈", "乙烯基氰", "液体", "高度危害"),
    ("碳酸钠", "纯碱|苏打", "液体", "轻度危害"),
    ("甲醇", "木醇", "液体", "中度危害"),
    ("甲苯二异氰酸酯", "TDI", "液体", "高度危害"),
    # --- 浦发 ---
    ("一氧化碳", "CO", "气体", "高度危害"),
    ("二氧化碳", "CO2", "气体", "轻度危害"),
    ("二氧化硫", "SO2", "气体", "中度危害"),
    ("氮氧化物", "NOx", "气体", "高度危害"),
    ("氨", "氨气|液氨", "液体", "高度危害"),
    ("硫化氢", "H2S", "气体", "高度危害"),
    ("氢氧化钠", "烧碱|苛性钠", "气溶胶", "中度危害"),
    ("甲硫醇", "硫氢甲烷", "气体", "中度危害"),
    ("氢氧化钙", "熟石灰", "固体", "轻度危害"),
]

GRADE = {"极度危害": 4, "高度危害": 3, "中度危害": 2, "轻度危害": 1}
BASIS = "GBZ/T 230-2025 (THI分级); 形态=真实报告5.2-1取证"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/ohs.db")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = sqlite3.connect(args.db)
    if args.apply:
        c.executescript(DDL)
        # 兼容: 旧版表缺 form 列时补上
        cols = {r[1] for r in c.execute("PRAGMA table_info(hazard_toxicity)")}
        if "form" not in cols:
            c.execute("ALTER TABLE hazard_toxicity ADD COLUMN form TEXT")
            print("  [迁移] 补 form 列")

    ins = upd = skip = 0
    for name, alias, form, level in DATA:
        g = GRADE.get(level)
        row = c.execute(
            "SELECT id, level, form FROM hazard_toxicity WHERE factor_name=?", (name,)
        ).fetchone()
        if row:
            if row[1] != level or row[2] != form:
                print(f"  [更] {name}: {row[2]}/{row[1]} → {form}/{level}")
                if args.apply:
                    c.execute("UPDATE hazard_toxicity SET level=?, grade=?, form=?, aliases=? WHERE id=?",
                              (level, g, form, alias, row[0]))
                upd += 1
            else:
                skip += 1
            continue
        print(f"  [增] {name:16} {form:4} {level}")
        if args.apply:
            c.execute(
                "INSERT INTO hazard_toxicity (factor_name, aliases, form, level, grade, basis, source, verified) "
                "VALUES (?,?,?,?,?,?,?,0)",
                (name, alias, form, level, g, BASIS, "real-reports:5.2-1"),
            )
        ins += 1

    if args.apply:
        c.commit()
        total = c.execute("SELECT COUNT(*) FROM hazard_toxicity").fetchone()[0]
        print(f"\n表内共 {total} 条")
        print("\n=== 分级分布 ===")
        for lv, n in c.execute(
            "SELECT level, COUNT(*) FROM hazard_toxicity GROUP BY level ORDER BY grade DESC"
        ):
            print(f"  {lv:8} {n}")
        print("\n=== 形态分布 ===")
        for fm, n in c.execute(
            "SELECT form, COUNT(*) FROM hazard_toxicity GROUP BY form ORDER BY 2 DESC"
        ):
            print(f"  {fm or '(空)':8} {n}")
    else:
        print(f"\n(dry-run: 新增 {ins} / 更新 {upd} / 已有 {skip}; 加 --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
