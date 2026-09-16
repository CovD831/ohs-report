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


# ===== 历史数据修补: "系统标准库提升"行 check_point 曾错装为条款号 (与 clause 同值) =====
# 正确语义: check_point = 该条对应的符合性检查要点短句 (参照正常行风格: 措施要点分号分隔)
# 幂等: 只修 check_point=clause 的行, 按 measure 前20字匹配 (id 在 DELETE/重插后会漂移, 不能按 id)
_CHECKPOINT_FIX = {
    "排风罩罩口吸风气流应保证罩口平均控制": "罩口平均控制风速实测达标; 柜式罩柜面风速0.25~0.5m/s",
    "罩型选择：应优先采用密闭罩；不能密闭": "优先密闭罩; 外部罩靠近散发源且气流从人员侧流向有害物侧",
    "风管风速：除尘管道内风速应保证粉尘不沉": "除尘/排毒风管设计风速达标; 粉尘不沉积、毒物不短路",
    "系统布置：同一排风系统所排有害物混合后": "混合后燃爆/腐蚀/增毒的排风已分设系统",
    "无毒代毒：生产工艺应优先采用无毒或低毒": "原辅材料低毒化替代落实; 设备密闭化/管道化/自动化",
    "隔离布置：产生职业病危害因素的工序应与": "危害工序与无害工序隔开; 隔离操作室+观察窗设置",
    "危害因数选型：应根据危害因数(浓度/限值)": "按危害因数选型正确: ≤10过滤式, >10或不明供气式",
    "面罩指定防护因数：半面罩APF=10、全面罩": "所选呼吸防护装备APF大于实际危害因数",
    "过滤元件更换：过滤式呼吸防护装备应按更": "过滤元件按周期更换; 失效征兆立即更换撤离",
    "首次使用某型号密合型面罩时应进行适合性": "密合型面罩首次适合性检验; 每次佩戴气密性检查",
    "应急呼吸器配置：可能发生急性中毒的场所": "正压自给式空气呼吸器配置到位且定期检查维护",
    "使用培训：使用呼吸防护用品前应经专门培": "使用前专门培训且记录; 覆盖佩戴/维护/失效识别",
}


def fix_checkpoints() -> int:
    """修补 check_point 错装为条款号的行 (幂等, 按 measure 前缀匹配)"""
    conn = connect()
    rows = conn.execute(
        "SELECT id, measure FROM protection_rule "
        "WHERE check_point=clause AND source LIKE '系统标准库提升%'").fetchall()
    n = 0
    for rid, measure in rows:
        for pre, cp in _CHECKPOINT_FIX.items():
            if (measure or "").startswith(pre[:20]):
                conn.execute("UPDATE protection_rule SET check_point=? WHERE id=?", (cp, rid))
                n += 1
                break
        else:
            print(f"  ⚠ 未匹配 measure: id={rid} {measure[:30]}")
    conn.commit()
    conn.close()
    return n


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
    # 历史修补: "系统标准库提升"行 check_point 错装条款号 → 检查点描述
    fixed = fix_checkpoints()
    print(f"修补 check_point: {fixed} 条")


if __name__ == "__main__":
    main()
