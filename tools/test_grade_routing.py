#!/usr/bin/env python3
"""作业分级路由守卫 (任务 #39) —— GBZ/T 229 的"部分"必须由数据里的类别字段决定

钉三件事:
  1) **粉尘作业绝不拿 229.2 的依据**。旧路由是 `"高温" in 因子名` → `d.get("sio2")` →
     else 化学物, 于是 hazard_factor.category='粉尘' 而没填 sio2 的检测行 100% 落进
     grade_chemical, 成品「关键控制点表」(表10.1-1) 给滑石粉尘/聚乙烯粉尘/炭黑粉尘/电焊烟尘
     印 "G=WD×WB×WL (229.2 式4/表5)" —— 粉尘分级是 **229.1**, 229.2 是化学物 (引错标准部分)。
     噪声行同样被当化学物算出 G=2.x 印成"Ⅰ级轻度危害作业" (噪声是 229.4)。
  2) **类别缺失/未知不得静默按化学物分档**, 必须显式降级并在"依据"列写明原因。
  3) **229.1 的分档常数是 2025 版原文**: WM 1/2/**4**/**6**、WB 的分界是 B≤1 (不是 0.5)、
     表1 的 M=50 属 10-50 档 (旧写法 n<50 把 50 错分进 50-80, WM 2→4 凭空升一档)。

一手来源 (本轮用 knowledge/pdf_cells 按 span 原样顺序逐格核对; **不用** find_tables().extract()
的基线排序 —— 那正是 #36 修掉的"化学式下标被挤到单元格末尾"的坑):
  acquire/raw/nhc_gbz/pdfs/GBZ_T_229.1_2025.pdf
    第4页 表1 游离二氧化硅含量的权重数(WM)取值: r1 ['M＜10','1'] r2 ['10≤M≤50','2']
        r3 ['50＜M≤80','4'] r4 ['M＞80','6']
    第4页 表2 生产性粉尘职业接触限值比值的权重数(WB)取值: r1 ['B≤1','0'] r2 ['B＞1','B']
    第4-5页 表3 体力劳动强度权重数(WL): ['Ⅰ（轻）','1.0'] ['Ⅱ（中）','1.5'] ['Ⅲ（重）','2.0']
        + 第5页续表首行 ['Ⅳ（极重）','2.5']
    第5页 表4 生产性粉尘作业分级: ['G=0 且B≤0.5','0 级（相对无害作业）']
        ['G=0 且B＞0.5','Ⅰ级（行动水平作业）'] ['0＜G≤6','Ⅱ级（轻度危害作业）']
        ['6＜G≤16','Ⅲ级（中度危害作业）'] ['G＞16','Ⅳ级（高度危害作业）']
    第2页 前言: "GBZ/T 229 分为以下4 个部分：——第1部分：生产性粉尘；——第2部分：化学物；
        ——第3部分：高温作业；——第4部分：噪声" ← 放射性/生物/其他"无对应部分"的判据出处
  acquire/raw/nhc_gbz/pdfs/GBZ_T_229.2_2025.pdf
    第4页 表1 (WD) 1/2/4/8 · 第4页 表2 (WB) B≤0.5→0, B＞0.5→B · 第5页 表3 (WL) 1.0/1.5/2.0/2.5
    第6页 式(4) G=WD×WB×WL · 第8页 表5: G=0→0级 / 0＜G≤6→Ⅰ / 6＜G≤24→Ⅱ / G＞24→Ⅲ
        + 表5 注「若B＞1 则为Ⅱ级（中度危害作业）或以上作业」
    → 229.2 这一支**与原文一致**, 本轮不改动它的数值, 只守住"谁能进这一支"。

⚠ 本文件不许点名具体物质当判据 (照 tools/test_toxicity_cell.py 的教训: 对本地样本过拟合的
  守卫 = 没有守卫)。A 段用 `代号甲粉尘` 这类合成因子名 + 合成库, 一行官方数据都不读;
  B 段只按**类别字段的取值**对真库里真实存在的行断言普适性质; C 段配"必须能红"的变异用例。
用法: .venv/bin/python tools/test_grade_routing.py    (退出码 0 = 全绿)
"""
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import knowledge.grade_engine as ge                      # noqa: E402
import knowledge.project_assess as pa                    # noqa: E402

n_pass = n_fail = n_skip = 0


def check(label, got, want):
    global n_pass, n_fail
    if got == want:
        n_pass += 1
        print(f"  ✓ {label}")
    else:
        n_fail += 1
        print(f"  ✗ {label}\n      实际: {got!r}\n      期望: {want!r}")


def skip(label, why):
    global n_skip
    n_skip += 1
    print(f"  – {label} 跳过 ({why})")


# ---- 期望表 (抄自上面的一手 PDF 逐格核对, 不 from 被测代码 —— 否则改坏判据就测不出来) ----
WM_EXPECT = {"<10": 1, "10-50": 2, "50-80": 4, ">80": 6}          # 229.1—2025 第4页 表1 r1-r4
# 类别字段值 → 229 的哪一部分 (物理因素写成"允许集合", 二级判据是接触量的量纲)
EXPECT_PART = {"粉尘": ge.PART_DUST,
               "化学因素": ge.PART_CHEM,
               "化学有害因素": ge.PART_CHEM,
               "放射性因素": None, "生物因素": None, "其他因素": None}
PHYS_PART_ALLOWED = (ge.PART_HEAT, ge.PART_NOISE, None)
ALL_PARTS = (ge.PART_DUST, ge.PART_CHEM, ge.PART_HEAT, ge.PART_NOISE)


def routing_violations(entries):
    """普适不变式 (只按**类别字段**判, 不认物质名): 输出的部分/依据列必须由类别决定。

    返回违反项列表 —— 同一个函数既跑真库 (B3) 也跑变异用例 (C1/C2/C3), 判据只有一份:
    给判据另写一遍比对逻辑, 测的就不是那条判据能不能红了。
    """
    bad = []
    for e in entries:
        cat, part = e.get("category"), e.get("part")
        note, lvl = e.get("note") or "", e.get("level")
        if lvl is None and not note.strip():
            bad.append(f"类别={cat}: 既无级别也无原因 (静默)")
        if cat in EXPECT_PART:
            want = EXPECT_PART[cat]
            if part != want:
                bad.append(f"类别={cat}: 部分={part!r} 应为 {want!r}")
            if cat == "粉尘" and ge.PART_CHEM in note:
                bad.append(f"类别=粉尘: 依据列出现 229.2 的式子 ({note[:36]}…)")
            if cat in ("化学因素", "化学有害因素") and any(
                    p in note for p in (ge.PART_DUST, ge.PART_HEAT, ge.PART_NOISE)):
                bad.append(f"类别={cat}: 依据列串进别的部分 ({note[:36]}…)")
            if cat in ("放射性因素", "生物因素", "其他因素") and any(p in note for p in ALL_PARTS):
                bad.append(f"类别={cat}: 无对应部分却带了 229 的具体部分 ({note[:36]}…)")
        elif cat == "物理因素":
            if part not in PHYS_PART_ALLOWED:
                bad.append(f"类别=物理因素: 部分={part!r} 不在允许集合 {PHYS_PART_ALLOWED}")
            if ge.PART_CHEM in note:
                bad.append(f"类别=物理因素: 依据列被写成化学物 229.2 ({note[:36]}…)")
            # 物理因素的二级期望 (标准事实: 229.3 分的是 WBGT 指数℃, 229.4 分的是 dB(A) 等效声级)
            dtxt = " ".join(str(x) for x in (e.get("dims") or ())).lower()
            want_p = ge.PART_HEAT if "wbgt" in dtxt else (
                ge.PART_NOISE if "db" in dtxt else None)
            if part != want_p:
                bad.append(f"类别=物理因素 量纲={e.get('dims')}: 部分={part!r} 应为 {want_p!r}")
        else:                                    # 类别字段为空 / 库里查无此因子
            if ge.PART_CHEM in note or part is not None:
                bad.append(f"类别={cat!r}: 未归类却按某一支分了级 ({note[:36]}…)")
            if "未归类" not in note:
                bad.append(f"类别={cat!r}: 未归类必须写明'未归类', 实际={note[:36]!r}")
    return bad


# ============ A. 真值表: 纯函数 + 合成库, 一行官方数据都不读 ============
print("### A1. plan_grade: 类别字段 → 229 的哪一部分 (真值表)")
for cat, want in (("粉尘", ("dust", ge.PART_DUST)),
                  ("化学因素", ("chemical", ge.PART_CHEM)),
                  ("化学有害因素", ("chemical", ge.PART_CHEM)),
                  ("放射性因素", (None, None)),
                  ("生物因素", (None, None)),
                  ("其他因素", (None, None))):
    p = ge.plan_grade(cat, ())
    check(f"{cat} → engine={want[0]} part={want[1]}", (p["engine"], p["part"]), want)
    if want[0] is None:
        check(f"{cat} 不参与分级必须写明原因", bool(p["reason"]), True)

print("\n### A2. plan_grade: 物理因素的二级判据 = 接触限值的**量纲** (不是因素名清单)")
for dims, want in ((("WBGT指数|℃",), ("heat", ge.PART_HEAT)),
                   (("LEX,8h|dB(A)",), ("noise", ge.PART_NOISE)),
                   (("声压级峰值|dB(A)",), ("noise", ge.PART_NOISE)),
                   (("4h等能量频率计权振动加速度|m/s²",), (None, None)),
                   (("电场强度|kV/m",), (None, None)),
                   ((), (None, None))):
    p = ge.plan_grade("物理因素", dims)
    check(f"{dims[0] if dims else '<无量纲>'} → engine={want[0]}", (p["engine"], p["part"]), want)
    if want[0] is None:
        check("  该量纲不参与分级必须写明原因", bool(p["reason"]), True)
check("手传振动(m/s²) 绝不能被派给 229.3/229.4 (229 只有粉尘/化学物/高温/噪声四部分)",
      ge.plan_grade("物理因素", ("4h等能量频率计权振动加速度|m/s²",))["part"], None)

print("\n### A3. plan_grade: 类别缺失/未知/写了半个词 → 一律不分级 + 原因 (禁止静默按化学物)")
for cat in ("", "  ", "粉尘作业", "化学", "化学物", None, "瞎写的类别"):
    p = ge.plan_grade(cat, ("PC-TWA|mg/m3",))
    check(f"类别={cat!r} → 三支点都不许进", (p["engine"], p["part"], bool(p["reason"])),
          (None, None, True))

print("\n### A4. grade_dust: 式(1) + 表1 + 表2 + 表4 的算术")
for band, wm in WM_EXPECT.items():
    check(f"WM({band}) == {wm} (229.1—2025 表1 r1-r4)", ge.grade_dust(band, 2.0, "Ⅰ")["wm"], wm)
for b, want_wb in ((0.4, 0), (0.5, 0), (0.8, 0), (1.0, 0), (1.2, 1.2)):
    check(f"B={b} → WB={want_wb} (表2 的分界是 1, 不是化学物的 0.5)",
          ge.grade_dust("<10", b, "Ⅰ")["wb"], want_wb)
check("G=0 且 B≤0.5 → 0 级 相对无害作业 (表4 r1)",
      (ge.grade_dust("<10", 0.4, "Ⅰ")["level"], ge.grade_dust("<10", 0.4, "Ⅰ")["name"]),
      ("0", "相对无害作业"))
check("G=0 且 0.5<B≤1 → Ⅰ级 行动水平作业 (表4 r2; 旧 WB 写法下这行是死分支)",
      (ge.grade_dust("<10", 0.8, "Ⅰ")["level"], ge.grade_dust("<10", 0.8, "Ⅰ")["name"]),
      ("Ⅰ", "行动水平作业"))
check("0<G≤6 → Ⅱ级 轻度危害作业 (表4 r3)", ge.grade_dust("<10", 4.0, "Ⅰ")["level"], "Ⅱ")
check("G=6 边界仍属 Ⅱ级", ge.grade_dust("<10", 6.0, "Ⅰ")["level"], "Ⅱ")
check("6<G≤16 → Ⅲ级 中度危害作业 (表4 r4)", ge.grade_dust("10-50", 4.0, "Ⅰ")["level"], "Ⅲ")
check("G>16 → Ⅳ级 高度危害作业 (表4 r5)", ge.grade_dust(">80", 4.0, "Ⅳ")["level"], "Ⅳ")
check("分档键取不到 → None (旧实现兜到 WM=4: 既不是原表的档位含义, 也不是'不知道')",
      ge.grade_dust(None, 0.8, "Ⅱ"), None)
check("粉尘行的依据列必须是 229.1 (不能是 229.2 的式子)",
      ge.grade_dust("<10", 2.0, "Ⅰ")["note"], "G=WM×WB×WL (229.1 式1/表1/表4)")

print("\n### A5. grade_chemical: 229.2—2025 的数值原样保留 (本轮只改路由, 不动这一支的算法)")
check("WB 分界仍是 B≤0.5→0 (229.2 表2)", ge.grade_chemical("中度危害", 0.4, "Ⅰ")["wb"], 0)
check("WD 表1 轻度危害=1", ge.grade_chemical("轻度危害", 2.0, "Ⅰ")["wd"], 1)
check("WD 表1 极度危害=8", ge.grade_chemical("极度危害", 2.0, "Ⅰ")["wd"], 8)
check("表5 6<G≤24 → Ⅱ级", ge.grade_chemical("中度危害", 4.0, "Ⅰ")["level"], "Ⅱ")
check("表5 G>24 → Ⅲ级", ge.grade_chemical("极度危害", 4.0, "Ⅰ")["level"], "Ⅲ")
check("表5 注: B>1 把 Ⅰ级抬到 Ⅱ级", ge.grade_chemical("轻度危害", 1.2, "Ⅰ")["level"], "Ⅱ")
check("化学物行的依据列仍是 229.2 式4/表5",
      ge.grade_chemical("中度危害", 0.8, "Ⅰ")["note"], "G=WD×WB×WL (229.2 式4/表5)")

print("\n### A6. _sio2_band: 表1 的边界是左开右闭 (旧写法 n<50 把 M=50 错分进 50-80)")
for v, want in ((3.39, "<10"), (9.99, "<10"), (10, "10-50"), (50, "10-50"),
                (50.001, "50-80"), (80, "50-80"), (80.001, ">80"), (100, ">80"),
                ("<10", "<10"), (">80", ">80"), ("＜10", "<10"), ("＜10%", "<10"),
                ("3.39%", "<10"), ("≥10%", None), ("≤10", None),
                (None, None), ("", None), ("未测", None)):
    check(f"M={v!r} → {want}", pa._sio2_band(v), want)

print("\n### A7. _band_from_labels: 只认标准自己写进名字的标注, 跨档/无标注一律不猜")
check("＜10% 标注 → 唯一档 <10", pa._band_from_labels(["代号甲粉尘（游离SiO2 含量＜10%）"])[0], "<10")
check("＜10% 全角写法 (库里 GBZ 2.1 的原样) 也认",
      pa._band_from_labels(["代号甲粉尘（游离SiO2含量＜10 %）"])[0], "<10")
check("≥10% 跨 10-50/50-80/>80 三档 → 不许挑一档",
      pa._band_from_labels(["代号丙粉尘（游离SiO2含量≥10%）"])[0], None)
check("没有任何标注 → None (而不是默认最低档或最坏档)",
      pa._band_from_labels(["代号乙粉尘"])[0], None)
check("两条标注指向不同档 → None (不自裁)",
      pa._band_from_labels(["代号丁粉尘（游离SiO2含量＜10%）",
                            "代号丁粉尘（游离SiO2含量＞80%）"])[0], None)
check("标注写法认全半角/带空格 (标准原文两种都有)",
      [pa._band_from_labels([n])[0] for n in
       ("代号戊粉尘（游离SiO2 含量＜10%）", "代号戊粉尘（游离SiO2含量<10 %）",
        "代号戊粉尘（游离SiO2 含量＜10 %）")], ["<10", "<10", "<10"])

# ---- 合成库跑 grade_detections: 与生产同一条代码路径, 但一行官方数据都不读 ----
SYN_HAZ = ("CREATE TABLE hazard_factor (id INTEGER PRIMARY KEY, category TEXT, seq INTEGER,"
           " name TEXT, cas TEXT, note TEXT, source TEXT, verified INTEGER)")
SYN_OEL = ("CREATE TABLE oel_limit (factor_name TEXT, factor_type TEXT, cas TEXT,"
           " english_name TEXT, oel_type TEXT, value TEXT, unit TEXT, conditions TEXT,"
           " note TEXT, source_standard TEXT, verified INTEGER)")
# (类别字段值, 因子名) —— 名字全是"代号X", 判据在类别和量纲上, 不在名字上
SYN_FACTORS = (("粉尘", "代号甲粉尘"), ("粉尘", "代号乙粉尘"), ("化学因素", "代号丙"),
               ("物理因素", "代号丁"), ("物理因素", "代号戊"), ("物理因素", "代号己"),
               ("物理因素", "代号癸"), ("放射性因素", "代号庚"), ("生物因素", "代号辛"),
               ("其他因素", "代号壬"))
SYN_LIMITS = (("代号甲粉尘（游离SiO2 含量＜10%）", "粉尘", "PC-TWA(总尘)", "4", "mg/m3"),
              ("代号乙粉尘", "粉尘", "PC-TWA(总尘)", "8", "mg/m3"),
              ("代号丙", "化学有害因素", "PC-TWA", "50", "mg/m3"),
              ("代号丁", "物理因素", "WBGT指数", "30", "℃"),
              ("代号癸", "物理因素", "WBGT指数", "30", "℃"),
              ("代号戊", "物理因素", "LEX,8h", "85", "dB(A)"),
              ("代号己", "物理因素", "4h等能量频率计权振动加速度", "5", "m/s²"),
              # 限值表里 factor_type 为空的一行: 类别字段查无, 但**算得出接触比值** ——
              # 这样 C3 的变异体(未归类静默按化学物分档)会真的产出一条 229.2 的依据, 判据才测得到
              ("查无此因子", None, "PC-TWA", "50", "mg/m3"))
# 每个因子的检测行 (代号甲粉尘故意先给一行定不了级、再给一行定得出 → 阳性对照 _put_grade 的取舍)
SYN_ROWS = [{"factor": "代号甲粉尘", "ctwa": None},
            {"factor": "代号甲粉尘", "ctwa": "0.8"},
            {"factor": "代号乙粉尘", "ctwa": "0.8"},
            {"factor": "代号丙", "ctwa": "30"},
            {"factor": "代号丁", "value": "31.5", "rate": "60%", "labor": "Ⅱ"},
            {"factor": "代号癸"},
            {"factor": "代号戊", "ctwa": "86"},
            {"factor": "代号己", "value": "6"},
            {"factor": "代号庚"},
            {"factor": "代号辛"},
            {"factor": "代号壬"},
            {"factor": "查无此因子", "ctwa": "30"}]


def synth_db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE standard_db (code TEXT, name TEXT, state TEXT, replace_of TEXT)")
    c.execute("INSERT INTO standard_db VALUES('GBZ2.1-2019','工作场所有害因素职业接触限值',"
              "'实施中','')")
    c.execute(SYN_HAZ)
    c.execute(SYN_OEL)
    for i, (cat, name) in enumerate(SYN_FACTORS):
        c.execute("INSERT INTO hazard_factor(category,seq,name,source) VALUES(?,?,?,?)",
                  (cat, i, name, "合成"))
    for nm, ft, ot, v, u in SYN_LIMITS:
        c.execute("INSERT INTO oel_limit(factor_name,factor_type,oel_type,value,unit,"
                  "source_standard) VALUES(?,?,?,?,?,?)", (nm, ft, ot, v, u, "GBZ 2.1—2019"))
    c.commit()
    return c


def no_M_dust_violations(entries):
    """判据: 这批合成行里的 代号乙粉尘 在库里**拿不到 M** (既无实测、目录名也无单一标注),
    所以只许"未定级 + 原因"。拿到级别 = 把"不知道"当成某一档 → 违反。"""
    return [(e["factor"], e["level"], e.get("sio2_band")) for e in entries
            if e.get("category") == "粉尘" and e["factor"] == "代号乙粉尘"
            and e.get("level") is not None]


try:
    print("\n### A8. grade_detections 打在合成库上 (生产同一条路径, 零官方数据)")
    sc = synth_db()
    out = pa.grade_detections(sc, SYN_ROWS)
    byf = {g["factor"]: g for g in out}
    check("合成库: 目录名自带＜10%标注的粉尘 → 229.1 且定出级 (旧路由给的是 '—' 或 229.2)",
          (byf["代号甲粉尘"]["part"], byf["代号甲粉尘"]["level"]), (ge.PART_DUST, "0"))
    check("合成库: 同因子先未定级后定级 → 定级行不被未定级行抹掉 (下游按因子后写覆盖)",
          byf["代号甲粉尘"]["level"] is not None, True)
    check("合成库: 拿不到 M 的粉尘行 → 未定级 + 原因点名 229.1 表1, 且不出现 229.2",
          (byf["代号乙粉尘"]["level"], ge.PART_DUST in byf["代号乙粉尘"]["note"],
           "229.2" in byf["代号乙粉尘"]["note"]), (None, True, False))
    check("合成库: 化学因素行 → 229.2", (byf["代号丙"]["part"], byf["代号丙"]["level"]),
          (ge.PART_CHEM, "Ⅰ"))
    check("合成库: WBGT 有实测值 → 229.3 定级",
          (byf["代号丁"]["part"], byf["代号丁"]["level"], "229.3" in byf["代号丁"]["note"]),
          (ge.PART_HEAT, "Ⅲ", True))
    check("合成库: WBGT 无实测值 → 未定级 + 原因 (旧实现拿默认 30℃/100% 造出 Ⅲ级)",
          (byf["代号癸"]["level"], "229.3" in byf["代号癸"]["note"],
           byf["代号癸"].get("name")), (None, True, None))
    check("合成库: dB(A) → 路由到 229.4 但不造表 (未定级 + 原因)",
          (byf["代号戊"]["part"], byf["代号戊"]["level"], bool(byf["代号戊"]["note"])),
          (ge.PART_NOISE, None, True))
    check("合成库: m/s² 无对应部分 → 不分级 + 原因",
          (byf["代号己"]["part"], byf["代号己"]["level"], bool(byf["代号己"]["note"])),
          (None, None, True))
    for fac in ("代号庚", "代号辛", "代号壬"):
        g = byf[fac]
        check(f"合成库: {g['category']} ({fac}) → 明确不参与 + 原因, 不拿任何一支的式子",
              (g["part"], g["level"], bool(g["note"]), ge.PART_CHEM in g["note"]),
              (None, None, True, False))
    check("合成库: 类别查无 → 写明未归类, 依据列没有 229.2 的式子",
          ("未归类" in byf["查无此因子"]["note"], ge.PART_CHEM in byf["查无此因子"]["note"]),
          (True, False))
    check("合成库: 全部输出行过一遍不变式 routing_violations", routing_violations(out), [])
    check("合成库: 拿不到 M 的粉尘行没有级别 (no_M_dust 判据基线绿)",
          no_M_dust_violations(out), [])
    sc.close()
except Exception as e:                                    # noqa: BLE001
    check("A 段不抛异常", f"抛出 {type(e).__name__}: {e}", "无异常")

# ============ B. 真库不变式: 只对库里真实存在的行断言普适性质 ============
from knowledge.oel import OfficialSchemaMissing, connect_ro   # noqa: E402
from knowledge.paths import DB_PATH                            # noqa: E402

print(f"\n### B. 真库不变式 (DB={DB_PATH})")
try:
    conn = connect_ro()
except (sqlite3.Error, OfficialSchemaMissing) as e:
    print(f"  ✗ 官方数据层不可读, 而 B 段必须跑在它上面 ({type(e).__name__}: {e})")
    sys.exit(1)

print("\n### B1. 路由表必须覆盖类别字段的**全部取值域** (新增类别值要当场发现)")
hf_cats = sorted({r[0] for r in conn.execute("SELECT category FROM hazard_factor").fetchall()})
check(f"hazard_factor.category 取值域 {hf_cats} 全部在 CATEGORY_ROUTING 里",
      sorted(set(hf_cats) - set(ge.CATEGORY_ROUTING)), [])
ft_cats = sorted({r[0] for r in conn.execute("SELECT factor_type FROM oel_limit").fetchall()})
check(f"oel_limit.factor_type 取值域 {ft_cats} 全部在 CATEGORY_ROUTING 里",
      sorted(set(ft_cats) - set(ge.CATEGORY_ROUTING)), [])
check("类别取值域不许为空 (空=官方层没装, 下面的不变式全是空转)",
      (len(hf_cats) > 0, len(ft_cats) > 0), (True, True))

print("\n### B2. 解析器不许跑偏: 目录里登记过的名字必须解析回**它自己登记的那些类别之一**")
cats_by_name: dict = {}
for cat, name in conn.execute("SELECT category, name FROM hazard_factor").fetchall():
    cats_by_name.setdefault(name, set()).add(cat)
drift = []
for name, cats in cats_by_name.items():
    got = pa._catalog_of(conn, name)
    if got[0] not in cats:          # 同名多类别是目录本身的形状 (如某条目既在化学又在放射性),
        drift.append((sorted(cats), name, got[0], got[2]))   # 判据只要求"解析回登记过的类别之一"
check(f"{len(cats_by_name)} 个登记名 → _catalog_of 解析回登记的类别之一 (不跨类串味)",
      drift[:5], [])
_multi = sorted(n for n, v in cats_by_name.items() if len(v) > 1)
print(f"    (目录里同名多类别的条目: {_multi or '无'} —— 判据按「解析回登记的类别之一」写, "
      f"两种形状都不会误报)")

print("\n### B3. 真库里每个项目的每一行检测数据: 逐行跑 grade_detections + 同一条不变式")
all_entries, proj_n, row_n = [], 0, 0
for pid, data in conn.execute("SELECT id, data FROM project").fetchall():
    try:
        dets = (json.loads(data) or {}).get("detections") or []
    except (ValueError, TypeError):
        continue
    if not dets:
        continue
    proj_n += 1
    uniq, seen = [], set()
    for d in dets:
        key = (str(d.get("factor") or ""), str(d.get("ctwa")), str(d.get("sio2")),
               str(d.get("value")))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(d)
    row_n += len(uniq)
    all_entries.extend(pa.grade_detections(conn, uniq))
# ⚠ 这一段跑在**项目业务数据**上: 容器里 (GUARD_TESTS) 生产库可能一个项目都没有,
#   或者项目的 detections 全是空 —— 那不是分级引擎的失效。2026-10-14 的教训
#   (test_toxicity_cell 硬读本地才有的行, 容器里 `row[0]` TypeError) 同款, 所以这里
#   **具名跳过**而不是断言"必须有输出"; 真库没数据时, 路由口径由 A1–A8 的合成用例兜住。
if proj_n == 0:
    cats_seen = []          # 跳过分支也要有值: B3 之后的类别兜底账不许变成 NameError
    skip("B3 整段 (逐行不变式)",
         "本环境真库没有带 detections 行的项目 —— 不代表代码错, 也不代表测过了; "
         "路由口径由 A1/A2/A3 的合成真值表兜住, 类别取值域是否为空由上一条 空转探测 判")
else:
    check(f"{proj_n} 个项目 / {row_n} 行检测行跑通且有输出", len(all_entries) > 0, True)
    check("不变式: 部分/依据列由类别决定 —— 粉尘拿不到 229.2, 物理拿不到 229.2, 未归类拿不到任何一支",
          routing_violations(all_entries)[:5], [])
    cats_seen = sorted({str(e.get("category")) for e in all_entries})
    check(f"真库检测行覆盖到的类别取值 {cats_seen} 全部被路由表认识",
          sorted({c for c in cats_seen if c not in ge.CATEGORY_ROUTING and c != "None"}), [])

print("\n### B4. 真库里定出级的粉尘行: WM 只能是表1 的四档值, 依据列只能是 229.1")
dust_graded = [e for e in all_entries
               if e.get("category") == "粉尘" and e.get("level") is not None]
check(f"{len(dust_graded)} 行粉尘定级结果的 wm ∈ {{1,2,4,6}} (229.1—2025 表1)",
      sorted({e.get("wm") for e in dust_graded} - set(WM_EXPECT.values())), [])
check("同上: 分档键只能是表1 的四档",
      sorted({e.get("sio2_band") for e in dust_graded} - set(WM_EXPECT)), [])
check("同上: 依据列全部是 229.1 (一行都不许带 229.2)",
      [e["factor"] for e in dust_graded if ge.PART_CHEM in e.get("note", "")], [])
check("同上: WB 的分界按表2 —— B≤1 的粉尘行必须 G=0",
      [e["factor"] for e in dust_graded if e.get("wb", 0) and e.get("wb", 0) <= 1], [])
if not dust_graded:
    skip("粉尘定级行核对", "本环境真库没有可定级的粉尘行 —— 该口径由 A4/A8 的合成用例兜住")

print("\n### B5. 不许静默: 每一行要么有级, 要么依据列写明原因")
silent = [(e["factor"], e.get("category")) for e in all_entries
          if e.get("level") is None and not (e.get("note") or "").strip()]
check(f"{len(all_entries)} 行输出里没有静默空行", silent[:5], [])
unclassified = [e for e in all_entries if not e.get("category")]
check(f"{len(unclassified)} 行未归类的全部写明'未归类' (且没有级别)",
      [(e["factor"], e["level"], "未归类" in (e.get("note") or "")) for e in unclassified
       if e["level"] is not None or "未归类" not in (e.get("note") or "")], [])

print("\n### B6. 229.2 这一支没被本轮改动带走 (化学物行的式子/级别原样)")
chem = [e for e in all_entries if e.get("category") in ("化学因素", "化学有害因素")
        and e.get("level") is not None]
check(f"{len(chem)} 行化学物定级结果的依据列仍是 229.2 式(4)/表5",
      [e["factor"] for e in chem if "WD×WB×WL" not in e.get("note", "")
       and "表5注" not in e.get("note", "")][:5], [])

conn.close()

# ============ C. 变异: 判据必须先证明**能红** ============
print("\n### C. 变异用例 (改坏什么 → 必须红在哪; 每条都跑生产同一条路径)")
sc = synth_db()
base = pa.grade_detections(sc, SYN_ROWS)
check("C0 基线: 合成库当前是绿的", routing_violations(base), [])


def with_routing(mutate, rows):
    """临时改坏 grade_engine 的路由表 → 跑同一条 grade_detections → 还原"""
    saved = dict(ge.CATEGORY_ROUTING)
    try:
        mutate(ge.CATEGORY_ROUTING)
        return pa.grade_detections(sc, rows)
    finally:
        ge.CATEGORY_ROUTING.clear()
        ge.CATEGORY_ROUTING.update(saved)


def m_dust_to_chem(reg):
    reg["粉尘"] = ("chemical", ge.PART_CHEM, "")


out = with_routing(m_dust_to_chem, SYN_ROWS)
v = routing_violations(out)
check("C1 变异『粉尘类别接到化学物那一支 (=旧 bug 的形状)』→ 必须红", bool(v), True)
print("      红在哪: " + " | ".join(v[:2]))
check("C1 还原后回到绿 (证明红的是判据, 不是环境)", routing_violations(
    pa.grade_detections(sc, SYN_ROWS)), [])


def m_phys_to_chem(reg):
    reg["物理因素"] = ("chemical", ge.PART_CHEM, "")


v = routing_violations(with_routing(m_phys_to_chem, SYN_ROWS))
check("C2 变异『物理因素整体按化学物分档』→ 必须红", bool(v), True)
print("      红在哪: " + " | ".join(v[:2]))


def plan_grade_old_style(category, dims=()):
    """变异体: 复刻旧路由的兜底 —— 认不出的类别一律交给化学物那一支"""
    eng, part, reason = ge.CATEGORY_ROUTING.get(str(category or "").strip(),
                                                ("chemical", ge.PART_CHEM, ""))
    return {"engine": eng, "part": part, "reason": reason}


_saved_pg = pa.plan_grade
pa.plan_grade = plan_grade_old_style
try:
    out = pa.grade_detections(sc, SYN_ROWS)
finally:
    pa.plan_grade = _saved_pg
v = [x for x in routing_violations(out) if "未归类" in x]
check("C3 变异『类别缺失/未知静默落回化学物』→ 必须逐条点名未归类的行", bool(v), True)
print("      红在哪: " + " | ".join(v[:2]))
check("C3 还原后回到绿", routing_violations(pa.grade_detections(sc, SYN_ROWS)), [])

print("\n### C4. 变异『WM 抄回 2010 版 3/4』与『WB 分界抄回 0.5』→ 必须红")


def wm_violations():
    """判据: WM_TABLE 必须逐格等于 229.1—2025 第4页表1 的 1/2/4/6"""
    return [(k, ge.WM_TABLE.get(k), v) for k, v in WM_EXPECT.items() if ge.WM_TABLE.get(k) != v]


check("C4a 基线: WM_TABLE 与表1 一致", wm_violations(), [])
ge.WM_TABLE.update({"50-80": 3, ">80": 4})            # 上一版抄的 2010 数值
check("C4a 变异『50＜M≤80→3、M＞80→4』→ 必须点名这两格",
      wm_violations(), [("50-80", 3, 4), (">80", 4, 6)])
ge.WM_TABLE.update(WM_EXPECT)
check("C4a 还原后回到绿", wm_violations(), [])


def dust_wb_violations(impl):
    """判据: 0.5<B≤1 时 229.1 表2 给 WB=0 → G=0 → 表4 第2行 Ⅰ级(行动水平作业)。
    抄了 229.2 的 0.5 分界就会把这段算成 WB=B>0 → Ⅱ级, 并让表4 第2行成了死分支。"""
    bad = []
    for b in (0.6, 0.8, 1.0):
        r = impl("10-50", b, "Ⅰ")
        got = None if r is None else (r["wb"], r["g"], r["level"])
        if got != (0, 0.0, "Ⅰ"):
            bad.append((b, got))
    return bad


check("C4b 基线: grade_dust 在 0.5<B≤1 上给 WB=0/G=0/Ⅰ级", dust_wb_violations(ge.grade_dust), [])


def grade_dust_old_wb(band, b, labor):
    """变异体: 只把 WB 的分界退回 0.5 (化学物那一支的形状), 其余照 229.1 原表"""
    wm = WM_EXPECT[band]
    wb = 0 if b <= 0.5 else b
    g = wm * wb * ge.WL_TABLE.get(labor, 1.5)
    lvl = ("0" if (g == 0 and b <= 0.5) else
           "Ⅰ" if g == 0 else "Ⅱ" if g <= 6 else "Ⅲ" if g <= 16 else "Ⅳ")
    return {"wb": wb, "g": round(g, 2), "level": lvl}


check("C4b 变异『WB 分界抄成 0.5』→ 同一判据必须点名 B=0.6/0.8/1.0 三处",
      [x[0] for x in dust_wb_violations(grade_dust_old_wb)], [0.6, 0.8, 1.0])

print("\n### C5. 变异『表1 边界写成 n<50』→ 必须红在 A6 那句真值表上")


def band_violations(fn):
    """判据: 表1 r2 是 10≤M≤50 (含 50), r3 是 50＜M≤80 (不含 50)"""
    want = {10: "10-50", 50: "10-50", 50.001: "50-80", 80: "50-80", 80.001: ">80", 9: "<10"}
    return [(m, want[m], fn(m)) for m in want if fn(m) != want[m]]


check("C5 基线: _sio2_band 的边界与表1 一致", band_violations(pa._sio2_band), [])


def _sio2_band_old(v):
    """变异体: 上一版实现 (n<50 当 10-50) —— M=50 被错分进 50-80, WM 2→4 凭空升一档"""
    n = float(v)
    if n < 10:
        return "<10"
    if n < 50:
        return "10-50"
    if n <= 80:
        return "50-80"
    return ">80"


check("C5 变异『n<50 当 10-50』→ 同一判据必须点名 M=50 这一格",
      band_violations(_sio2_band_old), [(50, "10-50", "50-80")])

print("\n### C6. 变异『拿不到 M 就兜一档 WM』→ 必须红: 没实测 M 的粉尘行会凭空拿到级别")
FIXTURE = [{"factor": "代号乙粉尘", "ctwa": "0.8"}]     # 库里这一条: 无实测 M, 名字也无标注
check("C6 基线: 拿不到 M 的粉尘行未定级 (routing_violations 也绿)",
      (no_M_dust_violations(pa.grade_detections(sc, FIXTURE)), routing_violations(
          pa.grade_detections(sc, FIXTURE))), ([], []))
_saved_bfl = pa._band_from_labels
pa._band_from_labels = lambda names: ("<10", "兜底按最低档")     # 变异体: 不知道就兜 M＜10
try:
    mut = pa.grade_detections(sc, FIXTURE)
finally:
    pa._band_from_labels = _saved_bfl
check("C6 变异『band=None 兜最低档』→ 判据必须点名这条行",
      no_M_dust_violations(mut), [("代号乙粉尘", "0", "<10")])
check("C6 还原: 同一条输入回到未定级 + 原因", no_M_dust_violations(
    pa.grade_detections(sc, FIXTURE)), [])

print("\n### C7. 变异『物理因素的二级判据被摘掉 (WBGT/dB 不再认)』→ 必须红")
_saved_dr = tuple(ge.DIMENSION_ROUTING)
ge.DIMENSION_ROUTING = ()
try:
    out = pa.grade_detections(sc, [{"factor": "代号丁", "value": "31.5", "rate": "60%"},
                                   {"factor": "代号戊", "ctwa": "86"}])
finally:
    ge.DIMENSION_ROUTING = _saved_dr
v = routing_violations(out)
check("C7 变异后 229.3/229.4 两支都掉了 → 判据必须点名两行", len(v), 2)
print("      红在哪: " + " | ".join(v))
check("C7 还原后回到绿", routing_violations(pa.grade_detections(
    sc, [{"factor": "代号丁", "value": "31.5", "rate": "60%"},
         {"factor": "代号戊", "ctwa": "86"}])), [])
sc.close()

print(f"\n通过 {n_pass} / 失败 {n_fail} / 跳过 {n_skip}")
sys.exit(1 if n_fail else 0)
