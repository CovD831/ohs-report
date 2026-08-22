"""工艺描述层物质提取器 — 扩展识别输入域

背景: 报告 10.2.5.1 识别要求 原辅材料/生产工艺/生产设备 三来源
      管线已有: 设备→物料 (equipment_material), 工序规则 (work_unit_rule)
      缺: 工艺描述段落 → 物质 (碳酸钠/三羟甲基丙烷/1,6-己二醇...)

原理: 段落文本扫描 → OEL 库物质名 (子串, 防止漏)+危化品目录名 → 物质清单
      与设备层结果合并 (去重), 标注来源(工艺描述/设备)

边界: OEL 无的物质 (三羟甲基丙烷/聚乙二醇/二丙二醇...) 仍识别为'危害提示'
      但 needs_test=False (无限值不判定, 只提示) — 符合'能计算的绝不生成'

用法: python3 -m knowledge.process_material_extractor
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402


def extract_from_text(conn, text: str) -> list[str]:
    """从文本提取物质名 (OEL库词典 + 危化品目录, 精确名优先避免子串误配)"""
    # OEL 词典: 按名称长度降序, 长名优先匹配 (聚乙二醇>乙二醇)
    oel_names = [r[0] for r in conn.execute(
        "SELECT DISTINCT factor_name FROM oel_limit ORDER BY LENGTH(factor_name) DESC")]
    found = []
    for name in oel_names:
        if name and name in text and name not in found:
            found.append(name)
    # 危险化学品名 (太长限前100, 精确名匹配)
    hz_names = [r[0] for r in conn.execute(
        "SELECT DISTINCT name FROM hazchem_item ORDER BY LENGTH(name) DESC LIMIT 200")]
    for name in hz_names:
        if name and name in text and name not in found:
            found.append(name)
    return found


def extract_from_report(conn, report_json_path: str) -> tuple[list[dict], list[str]]:
    """从报告 extracted JSON 提取工艺描述物质"""
    d = json.load(open(report_json_path, encoding="utf-8"))
    paragraphs = []
    for p in d.get("paragraphs", []):
        t = p.get("text", "") if isinstance(p, dict) else str(p)
        if len(t) > 30:
            paragraphs.append(t)
    text = "\n".join(paragraphs)
    names = extract_from_text(conn, text)
    # 每个物质查 OEL/CAS/危化品
    out = []
    for n in names:
        oel = conn.execute("SELECT oel_type, value, unit FROM oel_limit "
                           "WHERE factor_name=? LIMIT 3", (n,)).fetchall()
        cas = conn.execute("SELECT cas FROM oel_limit WHERE factor_name=? "
                           "AND cas IS NOT NULL LIMIT 1", (n,)).fetchone()
        hz = conn.execute("SELECT name, is_toxic FROM hazchem_item WHERE name=? "
                          "LIMIT 1", (n,)).fetchone()
        out.append({
            "material": n,
            "has_oel": bool(oel),
            "oel": [{"type": r[0], "value": r[1], "unit": r[2]} for r in oel],
            "cas": cas[0] if cas else None,
            "hazchem": hz[0] if hz else None,
            "toxic": bool(hz[1]) if hz else False,
        })
    return out, names


if __name__ == "__main__":
    conn = connect()
    report = "/Users/abaaba/Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json"
    items, names = extract_from_report(conn, report)
    print(f"工艺描述识别物质: {len(names)} 个")
    print("\n=== 物质 → OEL/危化品 (前25) ===")
    for it in items[:25]:
        oel = ("OEL:" + "、".join(f"{x['type']}={x['value']}{x['unit']}" for x in it["oel"][:2])) if it["has_oel"] else "无OEL"
        hz = ""
        if it["hazchem"]:
            hz = f" ⚠️危化品[{it['hazchem']}]" + ("·剧毒" if it["toxic"] else "")
        print(f"  {it['material']:24s} {oel}{hz}")
    # 汇总
    with_oel = sum(1 for i in items if i["has_oel"])
    no_oel = len(items) - with_oel
    print(f"\n有OEL: {with_oel} | 无OEL(仅提示): {no_oel}")
    conn.close()
