import sqlite3, sys, os
# 建 aux_unit_hazard 表 (辅助/公辅评价单元 → 典型危害因素, GBZ/T196"辅助设施/公用工程单元" + 跨行业一致)
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'ohs.db')
conn = sqlite3.connect(DB)
conn.execute("""CREATE TABLE IF NOT EXISTS aux_unit_hazard(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit TEXT NOT NULL,          -- 辅助单元名
    keywords TEXT,               -- 匹配关键词(设备/设施名, 逗号分隔)
    factors TEXT,                -- 典型危害因素(逗号分隔)
    source TEXT,                 -- 依据
    verified INTEGER DEFAULT 0
)""")

ROWS = [
    # (unit, keywords, factors, source)
    ("供配电系统", "配电,变配电,配电室,高压,变压器,发电机房", "工频电场", "GBZ/T196辅助设施+行业常识"),
    ("锅炉房/供热", "锅炉,余热锅炉,供热,蒸汽,导热油,加热炉", "高温,噪声,一氧化碳", "GBZ/T196公用工程+行业常识"),
    ("空压机站", "空压机,压缩空气,空压站", "噪声", "GBZ/T196公用工程+行业常识"),
    ("化验室/分析", "化验,分析,实验室,品控,检测室", "二甲苯,甲醇,乙醇,硫酸,氢氧化钠,噪声", "GBZ/T196辅助设施+行业常识"),
    ("废水处理", "废水,污水处理,生化,污水池", "硫化氢,氨,噪声", "GBZ/T196公用工程+GBZ2.1/2.2"),
    ("废气处理", "废气,废气处理,活性炭,脱硫,脱硝,除尘", "活性炭粉尘,氢氧化钙粉尘,二氧化硫,噪声", "GBZ/T196公用工程+行业常识"),
    ("储罐区", "储罐,罐区,储槽,储运,原料罐,成品罐,卸车,卸料", "苯系物,挥发性有机溶剂,噪声", "GBZ/T196仓储区+行业常识"),
    ("检维修", "检维修,维修,检修,电焊,焊接,焊工,机修", "电焊烟尘,锰及其化合物,臭氧,紫外辐射,噪声", "GBZ/T196辅助设施+行业常识"),
    ("仓库/仓储", "仓库,仓储,料库,原料库,成品库,堆场", "粉尘", "GBZ/T196仓储区+行业常识"),
    ("冷却塔/循环水", "冷却塔,循环水,冷却水", "噪声", "GBZ/T196公用工程+GBZ2.2"),
    ("冷冻站", "冷冻,制冷,冷库,氨制冷", "噪声,氨", "GBZ/T196公用工程+行业常识"),
]
for unit, kw, fac, src in ROWS:
    conn.execute("INSERT OR IGNORE INTO aux_unit_hazard(unit, keywords, factors, source) VALUES(?,?,?,?)",
                 (unit, kw, fac, src))
conn.commit()
print("aux_unit_hazard 建表完成, 行数:", conn.execute("SELECT COUNT(*) FROM aux_unit_hazard").fetchone()[0])
print("表定义:", [c[1] for c in conn.execute("PRAGMA table_info(aux_unit_hazard)")])
conn.close()
