"""完整导出验收: 36 张骨架表里, 导出的 docx 实际出现哪些表

关卡: 不看代码猜测, 直接打开导出的 docx 数表格 + 抓表题,
      与骨架名单对账 → 得出"哪些表真产出了 / 哪些丢了"。
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = next((p for p in (Path("/app"), Path(__file__).resolve().parent.parent)
             if (p / "web" / "app.py").exists()), Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))

import docx  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402

# 骨架表名 → 报告表题的关键词 (表题用报告体措辞, 不含骨架内部名)
# 例: 骨架"班制定员表" → 表题"表3.1-2 本项目生产岗位定员表"
# ⚠ 首版直接用骨架名去 match 表题 → 假报 17/19 缺失 (表题措辞不同)
CAP_HINT = {
    "班制定员表": ["定员"], "项目概况表": ["经济技术指标", "概况"],
    "原辅材料表": ["原辅材料"], "产品产量表": ["产品方案", "产品产量"],
    "设备明细表": ["设备设施"], "建构筑物表": ["建构筑物", "建（构）筑物"],
    "类比危害分布表": ["类比企业", "危害因素识别"],
    "气象因素表": ["气象"], "选址检查表": ["选址"],
    "总体布局检查表": ["总体布局"], "工艺检查表": ["工艺"],
    "设备布局检查表": ["设备及布局", "设备布局"],
    "建筑卫生学检查表": ["卫生学检查"], "卫生特征分级表": ["分级"],
    "辅助用室设置表": ["辅助用室设置"], "辅助用室检查表": ["辅助用室检查"],
    "噪声接触限值表": ["噪声"], "高温接触限值表": ["高温"],
    "防护设施检查表": ["防护设施"], "应急救援检查表": ["应急"],
    "危害因素识别表": ["危害因素"], "健康影响表": ["健康影响"],
    "接触限值表": ["接触限值"], "关键控制点表": ["关键控制点"],
    "类比可比性表": ["可比性"], "劳动强度分级表": ["劳动强度"],
    "类比PPE配备表": ["类比企业", "PPE"], "类比PPE有效性表": ["有效性"],
    "PPE配备表": ["个体防护", "PPE"], "PPE拟配置检查表": ["拟配置"],
    "管理制度检查表": ["管理"], "室内空气质量标准表": ["空气质量"],
    "类比工作日写实表": ["写实"], "应急物资清单": ["应急"],
}


def extract_table_captions(doc) -> list[str]:
    """抓表题 (形如 '表3.1-1 xxx') — 表题通常在表前一段"""
    caps = []
    for p in doc.paragraphs:
        t = p.text.strip()
        m = re.match(r"^表\s*([\d.]+[-—]\d+)\s*(.*)$", t)
        if m:
            caps.append(t[:60])
    return caps


def table_shapes(doc) -> list[tuple[int, int]]:
    return [(len(t.rows), len(t.columns)) for t in doc.tables]


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else "c8c7ff0a4d"
    conn = sqlite3.connect(str(ROOT / "data" / "ohs.db"))
    conn.row_factory = sqlite3.Row
    data = json.loads(conn.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()["data"])

    from web.derived_fields import derive_fields
    from web.table_skeleton import build_skeletons
    from web.external_tables import fill_one
    from web.word_export import export_docx

    data.update({k: v for k, v in derive_fields(data).items() if v})
    sk = build_skeletons(data)
    for n in list(sk):
        if sk[n].get("status") == "filling":
            t = fill_one(n, data)
            if t:
                sk[n] = t
    data["built_tables"] = sk

    try:
        from knowledge.project_assess import assess_project
        from knowledge.oel import connect as oc
        assess = assess_project(oc(), data)
    except Exception as e:
        assess = {}
        print("assess err:", e)

    out = Path("/tmp/verify_export.docx")
    export_docx({"id": pid, "data": data, "name": data.get("name") or "test"}, assess, out)
    doc = docx.Document(str(out))

    caps = extract_table_captions(doc)
    shapes = table_shapes(doc)
    print("=" * 78)
    print(f"导出: {out}  ({out.stat().st_size} bytes)")
    print(f"  段落 {len(doc.paragraphs)} | 表格 {len(doc.tables)} | 表题 {len(caps)}")
    print()

    # 骨架里有行的表 (应该被导出)
    sk_with_rows = {n: len(t.get("rows") or []) for n, t in sk.items() if t.get("rows")}
    print(f"骨架表(有行) {len(sk_with_rows)} 张:")
    cap_blob = "\n".join(caps)
    missing = []
    for n, cnt in sorted(sk_with_rows.items()):
        hints = CAP_HINT.get(n, [n])
        found = any(h in cap_blob for h in hints)
        if not found:
            missing.append((n, cnt))
        print(f"  {'✓' if found else '✗'} {n:20} 骨架{cnt:3}行")
    print()
    print(f"骨架有行但导出未见表题的: {len(missing)}")
    for n, c in missing:
        print(f"   ✗ {n} ({c} 行)")

    print()
    print("=" * 78)
    print(f"导出 docx 里的表题 (共 {len(caps)}):")
    for c in caps:
        print("   ", c)
    print()
    print(f"表格形状 (行×列) 前 20:")
    for i, s in enumerate(shapes[:20], 1):
        print(f"   {i:2}. {s[0]}行 × {s[1]}列")


if __name__ == "__main__":
    main()
