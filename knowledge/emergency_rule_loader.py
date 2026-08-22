"""应急救援措施规则 (10.2.7) — GBZ/T 205 密闭空间 + GB/T 38144 应急洗眼

来源: GBZ/T 205—2007 视觉直读 (扫描件, 条款已核实)
  6.1 安全作业操作规程: 6.1.2.1 氧含量18%~22%正常
  6.1.2.2 可燃气体<爆炸下限10% (油轮船舶<1%)
  6.1.2.3 有毒气体浓度须低于GBZ 2.1 (超限须机械通风+个体防护)
  6.1.5 救援人员须经培训考核合格
  3.16 缺氧环境 氧<18% | 3.17 富氧环境 氧>22%
GB/T 38144.1—2019: 应急喷淋和洗眼设备 (化学品接触应急)

10.2.7 报告要求: 应急救援组织/预案/设施(应急柜/喷淋洗眼)/监护
输出: data/ohs.db → emergency_rule (场景/指标/限值/标准/条款/检查点)
用法: python3 -m knowledge.emergency_rule_loader
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

# 应急规则 (场景 → 指标/限值 → 标准条款)
RULES = [
    ("密闭空间作业", "氧气含量", "18%~22% 正常; 缺氧<18% 须机械通风", "GBZ/T 205—2007", "6.1.2.1/3.16"),
    ("密闭空间作业", "可燃气体", "低于爆炸下限10%; 油轮船舶检修<1%", "GBZ/T 205—2007", "6.1.2.2"),
    ("密闭空间作业", "有毒气体", "低于GBZ 2.1限值; 超限须机械通风+GB/T 18664呼吸防护", "GBZ/T 205—2007", "6.1.2.3"),
    ("密闭空间作业", "救援人员", "须经培训考核合格; 不得盲目施救", "GBZ/T 205—2007", "6.1.5"),
    ("密闭空间作业", "作业条件", "通风设备/个体防护/检测/照明/通讯/应急设备 齐全", "GBZ/T 205—2007", "6.1.1"),
    ("化学毒物接触", "应急喷淋洗眼", "化学品溅射场所 设应急喷淋和洗眼设备", "GB/T 38144.1—2019", "全部"),
    ("化学毒物接触", "应急洗眼", "眼面部防护应急喷淋和洗眼设备安装要求", "GB/T 38144.1—2019", "全部"),
    ("职业中毒应急", "报警装置", "有毒气体检测报警装置 (有毒作业场所)", "GBZ/T 223—2009", "全部"),
    ("职业中毒应急", "应急救援组织", "用人单位应提供应急救援保障 (GBZ/T 205 4.1.8)", "GBZ/T 205—2007", "4.1.8"),
    ("高温中暑应急", "防暑降温", "高温作业场所 防暑降温措施 (GBZ 2.2表8 WBGT)", "GBZ 2.2—2007", "表8"),
    ("急性中毒应急", "应急预案", "可能急性中毒的建设项目应设应急救援预案", "GBZ/T 196—2025", "10.2.7"),
    ("密闭空间失控", "中止救援", "密闭空间不符合安全条件时, 立即撤出/中止作业", "GBZ/T 205—2007", "4.2.3"),
]


def main():
    conn = connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS emergency_rule (
          id        INTEGER PRIMARY KEY AUTOINCREMENT,
          scenario  TEXT NOT NULL,   -- 场景 (密闭空间作业/化学毒物接触...)
          item      TEXT NOT NULL,   -- 检查项 (氧气含量/可燃气体...)
          require   TEXT NOT NULL,   -- 要求/限值 (18%~22%...)
          std_code  TEXT NOT NULL,   -- 标准
          clause    TEXT,            -- 条款号
          source    TEXT NOT NULL,
          verified  INTEGER DEFAULT 0
        )
    """)
    conn.execute("DELETE FROM emergency_rule WHERE source='GBZ/T205+GB/T38144+GBZ/T223'")
    conn.executemany("""
        INSERT INTO emergency_rule (scenario, item, require, std_code, clause, source, verified)
        VALUES (?, ?, ?, ?, ?, 'GBZ/T205+GB/T38144+GBZ/T223', 0)
    """, [(s, i, r, st, c) for s, i, r, st, c in RULES])
    conn.commit()
    print(f"入库 emergency_rule: {len(RULES)} 条 (10.2.7 应急救援)")
    for r in conn.execute("SELECT scenario, item, require, std_code, clause FROM emergency_rule LIMIT 8"):
        print(f"  {r[0]:10s} {r[1]:8s} | {r[2][:28]:30s} | {r[3]} {r[4]}")
    conn.close()


if __name__ == "__main__":
    main()
