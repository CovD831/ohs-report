"""职业卫生管理制度规则 (10.2.9) — GBZ/T 225 全十二项制度检查

依据: GBZ/T 225—2010《用人单位职业病防治指南》第4/5章 (文本层OK, 条款已核实)
  4.1 组织机构与规章制度 | 4.2 前期预防 | 4.3 材料和设备管理
  4.4 工作场所管理 | 4.5 危害因素监测 | 4.6 履行告知 | 4.7 防护设施和PPE
  4.8 职业健康监护 | 4.9 应急救援 | 4.10 职业卫生培训 | 4.11 报告病人
  5.5~5.6 档案管理
GBZ 158—2003: 警示标识 (危害因素→警示标识类别, 扫描件视觉直读)

10.2.9 报告检查点 (对应GBZ/T 196 10.2.9):
  管理机构与人员/防治计划/管理制度和操作规程/职业卫生档案/健康监护✓/
  危害因素监测检测/应急预案✓/职业卫生培训/告知/警示标识/经费概算/工伤保险

输出: data/ohs.db → management_rule (制度项/要求/标准/条款/检查点/报告要求)
用法: python3 -m knowledge.management_rule_loader
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

# 12项制度 → 检查点 (GBZ/T 225 条款 + GBZ 158 警示标识)
RULES = [
    # 1. 管理机构与人员
    ("管理机构与人员", "设立职业卫生管理机构, 配备专(兼)职职业卫生专业人员", "GBZ/T 225—2010", "4.1"),
    ("管理机构与人员", "企业负责人/管理者了解并支持职业病防治工作", "GBZ/T 225—2010", "4.1"),
    # 2. 防治计划
    ("防治计划", "制定职业病防治计划和实施方案", "GBZ/T 225—2010", "4.1"),
    ("防治计划", "依法参加工伤保险", "GBZ/T 225—2010", "4.1"),
    # 3. 管理制度和操作规程
    ("管理制度", "建立、健全职业卫生管理制度和操作规程", "GBZ/T 225—2010", "4.1"),
    ("管理制度", "在可能产生职业病危害设备的醒目位置设警示标识和中文警示说明", "GBZ/T 225—2010", "4.3"),
    # 4. 警示标识 (GBZ 158)
    ("警示标识", "有害因素→警示标识: 粉尘/毒物(警示标识+说明)/噪声(耳塞标识)/放射(三电离)", "GBZ 158—2003", "全部"),
    ("警示标识", "产生职业病危害的作业场所醒目位置设置警示标识, 说明产生危害/后果/防护", "GBZ 158—2003", "3"),
    # 5. 职业病告知
    ("告知", "履行职业病危害告知义务: 合同/岗前培训/作业场所中危害告知", "GBZ/T 225—2010", "4.6"),
    ("告知", "有毒物品包装应有明显的警示标识和中文警示说明", "GBZ/T 225—2010", "4.3"),
    # 6. 危害因素监测检测
    ("监测检测", "工作场所职业病危害因素浓度或强度符合国家职业卫生标准", "GBZ/T 225—2010", "4.4"),
    ("监测检测", "委托检测机构定期开展职业病危害因素监测与检测", "GBZ/T 225—2010", "4.5"),
    # 7. 职业卫生培训
    ("培训", "负责人/管理人员/劳动者 职业卫生培训 (上岗前+在岗定期)", "GBZ/T 225—2010", "4.10"),
    # 8. 档案管理
    ("档案", "职业卫生档案: 监测评价档案/PPE档案/健康监护档案等", "GBZ/T 225—2010", "5.5/5.6"),
    ("档案", "职业病危害事故应急救援预案", "GBZ/T 225—2010", "4.9"),
    # 9. 经费概算
    ("经费概算", "职业病防治经费: 防护设施/检测设备/应急设施/PPE/岗前体检/培训/评价 及比例", "GBZ/T 196—2025", "10.2.9"),
    # 10. 报告病人
    ("报告病人", "及时向卫生行政部门报告职业病病人", "GBZ/T 225—2010", "4.11"),
]


def main():
    conn = connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS management_rule (
          id        INTEGER PRIMARY KEY AUTOINCREMENT,
          category  TEXT NOT NULL,   -- 制度类别 (管理机构/防治计划/警示标识...)
          require   TEXT NOT NULL,   -- 要求/检查点
          std_code  TEXT NOT NULL,   -- 依据标准
          clause    TEXT,            -- 条款号
          source    TEXT NOT NULL,
          verified  INTEGER DEFAULT 0
        )
    """)
    conn.execute("DELETE FROM management_rule WHERE source='GBZ/T225+GBZ158+GBZ/T196'")
    conn.executemany("""
        INSERT INTO management_rule (category, require, std_code, clause, source, verified)
        VALUES (?, ?, ?, ?, 'GBZ/T225+GBZ158+GBZ/T196', 0)
    """, [(c, r, s, cl) for c, r, s, cl in RULES])
    conn.commit()
    print(f"入库 management_rule: {len(RULES)} 条 (10.2.9 十二项管理制度)")
    # 按类别统计
    for r in conn.execute("SELECT category, COUNT(*) FROM management_rule GROUP BY category"):
        print(f"  {r[0]}: {r[1]} 条")
    conn.close()


if __name__ == "__main__":
    main()
