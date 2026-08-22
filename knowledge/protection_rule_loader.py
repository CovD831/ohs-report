"""防护设施规则 (10.2.6) — GBZ/T 194 防毒措施 + 检查表

依据 (GBZ/T 194—2007, 视觉直读确认):
  4.1.1 总平面布置: 产生毒物车间居夏季最小频率风向上风侧
  4.1.2 严重污染+尚无有效控制技术 → 远离居住区
  4.1.4 厂区道路/消防通道 宽度不宜小于1.2m
  4.2.1 毒物易漏设备: 可发生毒物泄漏的设备应设有隔离措施
  4.2.2 散发毒物设备布置同一建筑物内 → 毒性大与小应隔开布置
  4.3.2 车间内温度条件: 毒物散发场所 厂房高≥3.2m(一般), 人均面积≥15m² 人均容积≥10m³
  4.3.3 产生毒物场所最好多层建筑, 底层布置抽气管道/过滤/通风设备
  4.4.4 一氧化碳工作场所: 监测+安装报警器, 加强密闭环通风

检查表规则 (危害→防护设施类型→检查点):
  防毒: 密闭化 → 局部排风(排风罩 GB/T 16758) → 全面通风 → 报警器(GBZ/T 223)
  防尘: 湿式作业 → 密闭 → 除尘器 → 局部排风
  防噪: 隔声罩/隔声间/消声器 (GB/T 50087)
  防高温: 隔热/通风/空调 (GBZ 1 7.1)
  防辐射: 屏蔽/距离/时间 (GBZ 2.2)

输出: data/ohs.db → protection_rule (危害类别/措施/标准/条款/检查点)
用法: python3 -m knowledge.protection_rule_loader
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

# 防护设施规则 (类别 → 措施序列 → 依据标准/条款)
RULES = [
    # 防毒 (GBZ/T 194)
    ("化学毒物", "密闭化", "GBZ/T 194—2007", "4.2.2", "产生毒物设备应密闭化; 同一建筑内毒性与无毒性设备隔开"),
    ("化学毒物", "局部排风", "GBZ/T 194—2007", "3.8", "排毒系统: 密闭、隔离、通风排毒等技术措施"),
    ("化学毒物", "全面通风", "GBZ/T 194—2007", "4.3.2", "厂房高度≥3.2m; 人均面积≥15m²; 人均容积≥10m³"),
    ("化学毒物", "毒物报警", "GBZ/T 223—2009", "全部", "有毒气体检测报警装置设置 (GBZ/T 223)"),
    ("化学毒物", "应急洗眼", "GB/T 38144.1—2019", "全部", "眼面部防护应急喷淋和洗眼设备 (GB/T 38144)"),
    # 防尘
    ("粉尘", "湿式作业", "GBZ 1—2010", "7.1.2", "粉尘作业场所应采用湿式作业或干式除尘"),
    ("粉尘", "密闭除尘", "GBZ 1—2010", "7.1.2", "除尘系统应采用密闭、除尘器"),
    ("粉尘", "局部排风", "GB/T 16758—2008", "全部", "排风罩的分类及技术条件 (GB/T 16758)"),
    # 防噪声
    ("噪声", "隔声降噪", "GB/T 50087—2013", "全部", "工业企业噪声控制设计规范 (隔声罩/隔声间)"),
    ("噪声", "消声器", "GB/T 50087—2013", "全部", "通风管道/风管消声"),
    # 防高温
    ("高温", "隔热通风", "GBZ 1—2010", "7.1.1", "高温作业场所: 隔热、自然通风/机械通风"),
    # 防振动
    ("振动", "减振隔振", "GBZ/T 194—2007", "4.2", "振动设备基础减振"),
    # 防辐射
    ("辐射", "屏蔽", "GBZ 2.2 App", "表6", "微波/电磁场 屏蔽防护"),
]


def main():
    conn = connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS protection_rule (
          id       INTEGER PRIMARY KEY AUTOINCREMENT,
          hazard_category TEXT NOT NULL,  -- 化学毒物/粉尘/噪声/高温/振动/辐射
          measure  TEXT NOT NULL,         -- 设施措施 (密闭化/局部排风...)
          std_code TEXT NOT NULL,         -- 依据标准
          clause   TEXT,                  -- 条款号 (4.2.2 / 3.8)
          check_point TEXT NOT NULL,      -- 检查点描述
          source   TEXT NOT NULL,
          verified INTEGER DEFAULT 0
        )
    """)
    conn.execute("DELETE FROM protection_rule WHERE source='GBZ/T194+GBZ1+GB/T50087'")
    conn.executemany("""
        INSERT INTO protection_rule (hazard_category, measure, std_code, clause,
                                     check_point, source, verified)
        VALUES (?, ?, ?, ?, ?, 'GBZ/T194+GBZ1+GB/T50087', 0)
    """, [(c, m, s, cl, cp) for c, m, s, cl, cp in RULES])
    conn.commit()
    print(f"入库 protection_rule: {len(RULES)} 条 (各类别→措施→标准条款)")
    for r in conn.execute("SELECT hazard_category, measure, std_code, clause FROM protection_rule LIMIT 10"):
        print(f"  {r[0]:6s} {r[1]:8s} | {r[2]} {r[3]}")
    conn.close()


if __name__ == "__main__":
    main()
