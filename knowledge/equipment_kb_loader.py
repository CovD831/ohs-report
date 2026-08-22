"""设备知识库 (P2) — 岗位工序规则 + 设备物料桥 + 物料危害词典

原理: 识别引擎不应手工枚举"设备→危害" (无穷), 而应以物料为枢纽:
  岗位/工序 (专家规则) → 危害因素
  设备 → 承载物料 (设备清单) → OEL表CAS匹配 → 危害因素

数据来源 (旧报告种子, 全部提取入库供人工核对):
  A. 浦发热电表3/38: 生产单元|工序|岗位|设备密闭|物料|产生环节|主要危害因素 (12行, 专家标注)
  B. 长兴表17: 设备名称|数量|内部物料 (315行, 设备物料桥)
  C. GBZ 2.1 表1 CAS (oel_limit已有, 物料名→CAS→危害匹配的锚点)

输出: data/ohs.db → work_unit_rule (岗位工序规则) + equipment_material (设备物料桥)
       verified=0 全部待人工核对 (P2 原则: 机械自动+人工把关)
用法: python3 -m knowledge.equipment_kb_loader
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402

LONGXING = Path.home() / "Desktop/law/职业病危害预评价报告/长兴--预评（备案稿7-31）_extracted.json"
PUFA = Path.home() / "Desktop/law/职业病危害预评价报告/预评新-  浦发热电（备案稿）_extracted.json"
SRC_PUFA = "浦发热电(2017)表3/38"
SRC_LONG = "长兴(2025)表17"

DDL = """
CREATE TABLE IF NOT EXISTS work_unit_rule (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  unit         TEXT,               -- 生产单元 (垃圾焚烧/汽轮机发电...)
  process      TEXT,               -- 工序 (垃圾焚烧/水处理...)
  post         TEXT,               -- 岗位 (垃圾焚烧岗位...)
  enclosed     TEXT,               -- 设备密闭 (密闭/敞开式/-)
  material     TEXT,               -- 物料或中间产物 (垃圾/蒸汽/氢氧化钙...)
  source_note  TEXT,               -- 产生环节说明 (原始文本)
  factors      TEXT NOT NULL,      -- 主要职业病危害因素 (用;分隔: 高温、噪声、一氧化碳...)
  source       TEXT NOT NULL,
  verified     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS equipment_material (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  equipment    TEXT NOT NULL,      -- 设备名称 (纯化槽/酯化釜/汽轮机...)
  quantity     TEXT,               -- 数量
  material     TEXT,               -- 内部物料 (环己烷/甲基环己烷...)
  line         TEXT,               -- 生产线/车间归属 (特殊单体R27&R28生产线)
  source       TEXT NOT NULL,
  verified     INTEGER DEFAULT 0
);
"""


def extract_pufa() -> list[dict]:
    """表3 岗位工序规则 (12行专家标注)"""
    d = json.loads(PUFA.read_text(encoding="utf-8"))
    target = None
    for t in d.get("tables", []):
        h = " ".join(t.get("headers", []))
        if "职业病危害因素" in h and "生产单元" in h:
            target = t
            break
    rows = []
    if not target:
        return rows
    for r in target.get("rows", []):
        if len(r) < 7:
            continue
        factors = " ".join(str(x) for x in r[6:]).replace(" ", "")
        # 危害因素列表: 用顿号/逗号切分 (保留组合)
        fx = re.split(r"[、,，]", factors)
        fx = [f.strip() for f in fx if f.strip()]
        rows.append({
            "unit": str(r[0]).strip(), "process": str(r[1]).strip(),
            "post": str(r[2]).strip(), "enclosed": str(r[3]).strip(),
            "material": str(r[4]).strip() if len(r) > 4 else "",
            "source_note": " ".join(str(x) for x in r[5:6]).strip(),
            "factors": ";".join(fx),
            "source": SRC_PUFA,
        })
    return rows


def extract_longxing() -> list[dict]:
    """表17 设备物料桥 (315行)"""
    d = json.loads(LONGXING.read_text(encoding="utf-8"))
    target = None
    for t in d.get("tables", []):
        h = " ".join(t.get("headers", []))
        if "设备名称" in h and "内部物料" in h:
            target = t
            break
    rows = []
    if not target:
        return rows
    line = ""
    for r in target.get("rows", []):
        if len(r) < 9:
            continue
        name = str(r[1]).strip()
        # 生产线标题行 (整行同文本, 无数量)
        if all(str(x).strip() == name for x in r[:9]):
            line = name
            continue
        if not name or name in ("序号", "设备名称"):
            continue
        mat = " / ".join(str(x) for x in r[8:9]) if len(r) > 8 else ""
        # 清理换行/表格单元格残留 (find_tables 多行单元格)
        mat = re.sub(r"[\n\r]+", " ", mat)
        mat = re.sub(r" {2,}", " ", mat)
        rows.append({
            "equipment": name, "quantity": str(r[5]).strip() if len(r) > 5 else "",
            "material": mat.strip(), "line": line, "source": SRC_LONG,
        })
    return rows


def main():
    rows_p = extract_pufa()
    rows_l = extract_longxing()
    print(f"浦发热电 岗位工序规则: {len(rows_p)} 条")
    print(f"长兴 设备物料桥: {len(rows_l)} 条")
    conn = connect()
    conn.executescript(DDL)
    conn.execute("DELETE FROM work_unit_rule WHERE source=?", (SRC_PUFA,))
    conn.execute("DELETE FROM equipment_material WHERE source=?", (SRC_LONG,))
    conn.executemany("""
        INSERT OR REPLACE INTO work_unit_rule
        (unit, process, post, enclosed, material, source_note, factors, source, verified)
        VALUES (:unit, :process, :post, :enclosed, :material, :source_note, :factors, :source, 0)
    """, rows_p)
    conn.executemany("""
        INSERT OR REPLACE INTO equipment_material
        (equipment, quantity, material, line, source, verified)
        VALUES (:equipment, :quantity, :material, :line, :source, 0)
    """, rows_l)
    conn.commit()
    print("--- 抽查 ---")
    for r in conn.execute("SELECT unit, process, factors FROM work_unit_rule LIMIT 4"):
        print(f"  规则: {r[0]} / {r[1]} → {r[2][:40]}")
    for r in conn.execute(
            "SELECT equipment, material FROM equipment_material WHERE equipment LIKE '%釜%' LIMIT 3"):
        print(f"  设备: {r[0]} → {r[1][:40]}")
    conn.close()


if __name__ == "__main__":
    main()
