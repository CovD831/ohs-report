"""解析 mem.gov.cn《危险化学品分类信息表》(GHS) → ghs_class 表

数据源:
  GBZ/T 230—2025 附录 B.1 官方指定源 = mem.gov.cn 危化品目录实施指南附件
  https://www.mem.gov.cn/gk/gwgg/xgxywj/wxhxp_228/201509/t20150902_232638.shtml
  (附件 W020171030616953518195.doc, .doc => textutil 转 txt)

用途与边界:
  ✅ 归档官方 GHS 危险性类别 (与 GBZ/T 230 表B.1 同源可比)
  ✅ 仅作**二元判定**输入: 剧毒 / 致癌性 / 皮肤腐蚀 等
  ❌ **不用于计算 THI** — 实测覆盖不足(多数物质 8 项仅 1~2 项有数据),
     按标准注6兜底会系统性虚高(氨算得 Ⅳ级 但真稿为 Ⅲ级)。
     危害程度取值仍以真实报告取证为准。

CLI:
  python tools/load_ghs_class.py --txt /tmp/thi/ghs_raw.txt          # 预演
  python tools/load_ghs_class.py --txt /tmp/thi/ghs_raw.txt --apply  # 写入
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "ohs.db"

DDL = """
CREATE TABLE IF NOT EXISTS ghs_class (
    id          INTEGER PRIMARY KEY,
    seq         INTEGER,
    name        TEXT NOT NULL,
    alias       TEXT,
    english     TEXT,
    cas         TEXT,
    hazard_raw  TEXT,
    remark      TEXT,
    is_highly_toxic INTEGER DEFAULT 0,
    is_carcinogen   INTEGER DEFAULT 0,
    source      TEXT,
    verified    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_ghs_cas  ON ghs_class(cas);
CREATE INDEX IF NOT EXISTS ix_ghs_name ON ghs_class(name);
"""

SOURCE = "mem.gov.cn:危化品分类信息表(2015版实施指南附录)"

# 表头字段名(用于跳过)
HEADER = ["附件", "危险化学品分类信息表", "序号", "品名", "别名", "英文名", "CAS号", "危险性类别", "备注"]
# 危险性类别行特征: 含 "类别N" 或 是已知危险性短语
HAZ_HINT = re.compile(
    r"类别\s*\d|易燃|爆炸|氧化|加压气体|自燃|遇水|腐蚀|刺激|致敏|"
    r"生殖|致癌|靶器官|吸入危害|危害水生|急性毒性|退敏|有机过氧化物|"
    r"自反应|发火|金属腐蚀"
)


def parse(txt: str) -> list[dict]:
    """按「序号」切块解析。字段顺序: 序号|品名|别名|英文名|CAS|危险性类别…|备注"""
    lines = [l.strip() for l in txt.split("\n")]
    # 找数据起点: 表头 "备注" 之后首个纯数字序号行
    start = 0
    for i, l in enumerate(lines):
        if l == "备注":
            start = i + 1
            break

    recs: list[dict] = []
    cur: dict | None = None
    for l in lines[start:]:
        if re.fullmatch(r"\d{1,5}", l):
            # 新记录开始
            if cur and cur.get("name"):
                recs.append(cur)
            cur = {"seq": int(l), "hazards": [], "remark": []}
            continue
        if cur is None:
            continue
        # ⚠ 空行**不能跳过**: 别名/英文名常为空(如 硫化氢 的别名为空行),
        #   跳过会让后续字段整体前移 → CAS 列存进危险性类别(实测踩到)。
        #   仍走「按序占位」逻辑, 空串占位即可。
        if "name" not in cur:
            if not l:
                continue  # 序号后的首个空行忽略
            cur["name"] = l
        elif "alias" not in cur:
            cur["alias"] = l
        elif "english" not in cur:
            cur["english"] = l
        elif "cas" not in cur:
            cur["cas"] = l
        else:
            if HAZ_HINT.search(l):
                cur["hazards"].append(l)
            elif l:
                cur["remark"].append(l)
    if cur and cur.get("name"):
        recs.append(cur)
    return recs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--txt", required=True, help="textutil 转换后的 GHS 分类表 txt")
    ap.add_argument("--apply", action="store_true", help="写入数据库(默认预演)")
    a = ap.parse_args()

    txt = Path(a.txt).read_text(encoding="utf-8", errors="ignore")
    recs = parse(txt)
    print(f"解析记录: {len(recs)}")

    # 统计
    n_cas = sum(1 for r in recs if r.get("cas", "").count("-") >= 1)
    n_haz = sum(1 for r in recs if r["hazards"])
    n_carc = sum(1 for r in recs if any("致癌" in h for h in r["hazards"]))
    n_tox = sum(1 for r in recs if any("剧毒" in x for x in r["remark"]))
    print(f"  含 CAS: {n_cas} | 含危险性类别: {n_haz} | 含致癌性: {n_carc} | 剧毒: {n_tox}")

    print("\n样例(氨):")
    for r in recs:
        if r.get("name") == "氨":
            print(f"  CAS={r['cas']} 类别={r['hazards']}")
            break

    if not a.apply:
        print("\n[预演] 未写入。加 --apply 落库。")
        return 0

    conn = sqlite3.connect(DB)
    # 逐条执行: DDL 里的 -- 行注释会把多行语句切断, executescript 会语法错
    for stmt in [s for s in DDL.split(";") if s.strip()]:
        conn.execute(stmt)
    conn.execute("DELETE FROM ghs_class")
    rows = []
    for r in recs:
        rows.append((
            r.get("seq"), r["name"], r.get("alias", ""), r.get("english", ""),
            r.get("cas", ""), "\n".join(r["hazards"]), "；".join(r["remark"]),
            1 if any("剧毒" in x for x in r["remark"]) else 0,
            1 if any("致癌" in h for h in r["hazards"]) else 0,
            SOURCE, 0,
        ))
    conn.executemany(
        "INSERT INTO ghs_class(seq,name,alias,english,cas,hazard_raw,remark,"
        "is_highly_toxic,is_carcinogen,source,verified) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM ghs_class").fetchone()[0]
    print(f"\n✓ 写入 ghs_class: {total} 条")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
