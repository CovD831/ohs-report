"""诊断: 26 张 await_assess 表 有多少能真填上

跑真项目完整评估, 逐张表核对"评估链路是否接通"。
不用 LLM (纯规则路径), 所以快且可重复。
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = next((p for p in (Path("/app"), Path(__file__).resolve().parent.parent)
             if (p / "web" / "app.py").exists()), Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else "c8c7ff0a4d"
    conn = sqlite3.connect(str(ROOT / "data" / "ohs.db"))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
    data = json.loads(row["data"])

    from knowledge.project_assess import assess_project
    from knowledge.oel import connect as oel_connect
    oel_conn = oel_connect()
    a = assess_project(oel_conn, data)

    print("=" * 76)
    print(f"项目 {pid} 评估结果")
    print(f"  hazards      {len(a['hazards']):3} 个")
    print(f"  judgements   {len(a['judgements']):3} 个")
    print(f"  grades       {len(a['grades']):3} 个")
    print(f"  ppe          {len(a['ppe']):3} 个")
    print(f"  level_summary {len(a['level_summary'])} 类")

    # physical 因素是否进入 hazards
    phys = {"噪声", "高温", "工频电场", "振动", "紫外辐射", "WBART"}
    hz = [h.get("factor", "") for h in a["hazards"]]
    phys_in = [f for f in hz if any(p in f for p in phys)]
    print(f"\n  hazards 里物理因素: {phys_in or '★ 无 (设备59台却无噪声/高温)'}")

    # 逐张 await_assess 表核对数据源
    from web.table_skeleton import build_skeletons
    from web.derived_fields import derive_fields
    data.update({k: v for k, v in derive_fields(data).items() if v})
    sk = build_skeletons(data)
    aa = [n for n, t in sk.items() if t.get("status") == "await_assess"]

    print(f"\n{'=' * 76}")
    print(f"await_assess 表 {len(aa)} 张 — 逐个核对评估链路数据源\n")
    print(f"{'表名':22} {'数据源':28} 可填?")
    print("-" * 76)

    # 数据源可用性判定 (纯规则: 看输入侧有没有东西)
    has = {
        "hazards": bool(a["hazards"]),
        "judgements": bool(a["judgements"]),
        "grades": bool(a["grades"]),
        "ppe": bool(a["ppe"]),
        "protection": bool(data.get("protection")),
        "management": bool(data.get("management")),
        "analogous": bool(data.get("analogous_test") or data.get("hazard_grid")),
        "detections": bool(data.get("detections")),
        "buildings": bool(data.get("buildings")),
        "aux": bool(a["level_summary"]),
    }
    MAP = {
        "危害因素识别表": ("hazards", has["hazards"]),
        "健康影响表": ("hazards+health_effect库", has["hazards"]),
        "物理因素健康影响表": ("★ 需物理因素(缺)", bool(phys_in)),
        "关键控制点表": ("judgements", has["judgements"]),
        "接触限值表": ("hazards.oel", any(h.get("oel") for h in a["hazards"])),
        "噪声接触限值表": ("oel_limit标准库(GBZ2.2)", True),
        "高温接触限值表": ("oel_limit标准库(GBZ2.2)", True),
        "室内空气质量标准表": ("标准库(直出)", True),
        "类比可比性表": ("analogous_test", has["analogous"]),
        "类比PPE配备表": ("ppe", has["ppe"]),
        "类比PPE有效性表": ("ppe", has["ppe"]),
        "类比工作日写实表": ("★ 需类比写实材料", "写实" in str(data)),
        "劳动强度分级表": ("grades", has["grades"]),
        "卫生特征分级表": ("GBZ1标准库", True),
        "周边环境表": ("★ 需周边环境材料", "周边" in str(data)),
        "应急物资清单": ("emergency_supplies", bool(data.get("emergency_supplies"))),
        "选址检查表": ("GBZ1标准库", True),
        "总体布局检查表": ("GBZ1标准库", True),
        "建筑卫生学检查表": ("GBZ1标准库", True),
        "辅助用室检查表": ("GBZ1标准库", True),
        "辅助用室设置表": ("辅助用室材料", bool(data.get("buildings"))),
        "工艺检查表": ("GBZ1标准库", True),
        "设备布局检查表": ("GBZ1标准库", True),
        "管理制度检查表": ("management", has["management"]),
        "防护设施检查表": ("protection", has["protection"]),
        "应急救援检查表": ("GBZ1标准库", True),
        "PPE配备表": ("ppe", has["ppe"]),
        "PPE拟配置检查表": ("ppe", has["ppe"]),
    }
    ok = 0
    for n in aa:
        src, can = MAP.get(n, ("未登记", False))
        if can:
            ok += 1
        print(f"{n:22} {src:28} {'✓' if can else '✗'}")
    print("-" * 76)
    print(f"可填 {ok}/{len(aa)}  不可填 {len(aa) - ok}")


if __name__ == "__main__":
    main()
