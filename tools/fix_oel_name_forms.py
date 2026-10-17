#!/usr/bin/env python3
"""官方层"名字形状"一次性修复: 主名+子条目粘连 / 名单元格截断 / note 词内断词 (#37)

缺陷 (2026-10-17 重新取证, 下面每个数都是直接测得)
    `oel_limit.factor_name` 里有两组行, 是解析 GBZ 2.1—2019 表1 时把**一个名单元格里的
    主名+子条目**按空格拆错了留下的产物:

      rid=1399/1400  factor_name = '镍及其无机化合物(按Ni 计) 金属镍与难溶性镍化合'
                     conditions  = '物' / '可溶性镍化合物'         (子条目被劈成 '物' + 半截)
      rid=1631/1632  factor_name = '人造矿物纤维绝热棉粉尘（玻'    (左括号不配平)
                     conditions  = '人造矿物纤维绝热棉粉尘（玻' / '璃棉、矿渣棉、岩棉）'

    权威文本 (tools/fix_oel_subscripts.authority(), 即 PDF 表1 的 span 顺序文本):
      镍 (序号 221)  名单元格 = '镍及其无机化合物(按Ni计) 金属镍与难溶性镍化合物 可溶性镍化合物'
                     同行限值 = '1 0.5'      同行备注 = 'G1（镍化合物），敏 G2B（金属和合金）'
      纤维 (序号 29) 名单元格 = '人造矿物纤维绝热棉粉尘（玻璃棉、矿渣棉、岩棉）' (单一全名)
                     同行限值 = '5 1 f/mL'

纸面后果 —— **纠正上一版写在本文件里的错误论断**
    上一版说「残词 '金属镍与难溶性镍化合' 原样印在成品里, 三份导出 25/24/6 次」。那是拿残词当
    **子串**去数, 而正确的全名 '金属镍与难溶性镍化合物' 本身就含那一段字符。改用负向断言
    `金属镍与难溶性镍化合(?!物)` 复扫 data/report_*.docx → 残词 **0 次** (68 份), 全名 608 次;
    '镍及其无机' 在成品里也 **0 次**。项目侧 56 行客户数据: 全名 69 次、残词 **0 次**、
    '镍及其无机' 9 次 —— 但那 9 次都在 4b16f075ad 的 LLM 正文里 (裸主名 '镍及其无机化合物',
    不带 '(按Ni计)' 后缀), 不是任何 detections/限值查找键 (复算见 --proof)。
    真正上了纸的是**假陈述** (实测 data/report_c8c7ff0a4d.docx):
      接触限值表 表32 行26 = ['金属镍与难溶性镍化合物', '—','—','—','—', '未制定职业接触限值']
      表41 行28            = ['28', '金属镍与难溶性镍化合物', '—','—','—']
    根因链条: 官方层 factor_name 不是标准里的任何一个条目 → 用子条目名 `WHERE factor_name=?`
    查不到行 → 限值四列落 '—' 并写「未制定职业接触限值」, 而标准 221 行明明给了 1 与 0.5。

修到哪一步、剩下谁
    本工具只做**官方层那一层**: 把权威文本放回它本来的列 —— 主名进 factor_name, 子条目名进
    conditions。这个形状不是我定的, 是库里既有的同族约定 (钒 1248/1249、钼 1389/1390、
    铅 1404/1405、铜 1479/1480、铝尘 1618/1619、麻尘 1620-1622、石棉 1639/1640 全是
    主名 + conditions 装子名; conditions 非空实测 105 行, 其中 22 行是这种子条目形状)。
    修完之后 `WHERE factor_name='金属镍与难溶性镍化合物'` **仍然查不到** —— 那是读侧匹配的事
    (#51/#41, web/section_filler 的名字解析), 由 detrows 那条线负责, 本批不越界改读路径。

不动的两处, 以及为什么 (有据不动, 不是"以前没报所以是对的")
    · `health_effect.factor` rid=279 = '镍及其无机化合物(按Ni计)金属镍与难溶性镍化合物可溶性镍化合物'
      —— 该表 357 行一律存"整格去空格", 用同一把尺子实测 357/357 绿, 279 符合本表约定;
      而 '镍及其无机' 在 68 份成品里 0 次, 项目侧只有 9 次裸主名散文 (不是查询键)
      → 改成主名只会破坏本表约定, 没有实测收益。登记为 #37-b (低优先, 待真出现读者再动)。
    · 跨表同步 (hazard_factor_alias.oel_name / material_dictionary.oel_factor /
      hazard_toxicity.factor_name) —— 实测这三张表里 **0 行**含 '镍及其无机' 或 '绝热棉'
      (镍的别名链只有 羰基镍: alias rid=750 / toxicity rid=94), 所以 #36 那套跨表同步在这里
      是僵尸规则。删掉并在原处留下测量结果, 免得下次照抄。

规矩 (与 tools/fix_oel_subscripts.py 同一套)
  · 新值**不手写**: 由权威整格按空格切分得到, 并校验「主名 + 各子条目 = 整格」这条配对不变式;
  · 限值配对也要有据: 库里这几行的 value 必须按顺序构成同一行限值单元格 token 的子序列,
    且那个限值单元格必须唯一 —— 不唯一就整批不执行 (证不出顺序就不许装);
  · 默认 dry-run; `--apply` 才写, 且只写本地库 (进生产需用户单独批准);
  · 写前原值落盘 (DATA_DIR, 认 OHS_DATA_DIR), 写完逐格读回 + 行数不变
    (oel_limit 638 / health_effect 357), 任一不过整体 ROLLBACK;
  · 反向对表: 每条规则要么命中、要么点名 (已是目标值/现值漂移/行不存在), 不静默;
  · 权威 PDF 取不到 → 短路成一条点名, 不许产出 N 条"查无此名"。

用法:
    OHS_GBZ21_PDF=acquire/raw/nhc_gbz/pdfs/GBZ_2.1_2019.pdf .venv/bin/python tools/fix_oel_name_forms.py --proof
    ... 同上 (去掉 --proof) 预演 / 加 --apply 写本地库
"""
import os
import re
import sys
import json
import time
import glob
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.pdf_cells import nospace                       # noqa: E402  单一产地: 去空格口径
from knowledge.paths import DB_PATH, DATA_DIR                 # noqa: E402  (数据目录唯一产地, 认 OHS_DATA_DIR)
from tools.fix_oel_subscripts import (GBZ21_PAGES, SOURCE,    # noqa: E402
                                      authority, row_map)

PDF = os.environ.get("OHS_GBZ21_PDF") or os.path.join(
    ROOT, "acquire", "raw", "nhc_gbz", "pdfs", "GBZ_2.1_2019.pdf")

# ── 权威整格 (逐字取自 PDF 表1; 段次由 split_cell 派生, 不是我手工编号) ─────────────────────
NICK_CELL = "镍及其无机化合物(按Ni计) 金属镍与难溶性镍化合物 可溶性镍化合物"
NICK_PAIR = [(1399, "1"), (1400, "0.5")]        # (rowid, 库里这一行的 value) —— 顺序须与整格子条目一致
WOOL_CELL = "人造矿物纤维绝热棉粉尘（玻璃棉、矿渣棉、岩棉）"
WOOL_PAIR = [(1631, "5"), (1632, "1")]

EXPECTED_ROWS = {"oel_limit": 638, "health_effect": 357}   # 2026-10-17 实测; 改名不许动行数
EXPECTED_DOCX = 68                                        # data/report_*.docx 份数 (纸面口径的分母)


def split_cell(cell):
    """权威名单元格 → [主名, 子条目…] (PDF 里子条目就是靠空格分列的)"""
    return [t for t in cell.split(" ") if t]


def load_authority():
    if not os.path.exists(PDF):
        return None, None, [f"一手 PDF 取不到: {PDF} —— 本工具不动任何数据 "
                            "(读不到源头必须短路成一条点名, 不许报 N 条「查无此名」)"]
    try:
        texts, _ev = authority(PDF, GBZ21_PAGES)
        rmap = row_map(PDF, GBZ21_PAGES)
    except Exception as exc:                     # authority/row_map 会因解析形状不对主动抛错
        return None, None, [f"权威文本解析失败: {exc}"]
    miss = [c for c in (NICK_CELL, WOOL_CELL) if nospace(c) not in texts]
    if miss:
        return None, None, [f"权威整格在 PDF 页段 {GBZ21_PAGES.start + 1}-{GBZ21_PAGES.stop} "
                            f"里找不到: {miss} —— 整批不执行"]
    return texts, rmap, []


def limits_cell_for(cell, pairs, rmap):
    """同一行里"其空格切分能按顺序包住这几行 value"的单元格; 返回列表 (调用方要求恰有 1 个)"""
    seq = [p[1] for p in pairs]
    out = []
    for c in sorted(rmap.get(nospace(cell), set())):
        tk = [t for t in c.split(" ") if t]
        i = 0
        for t in tk:
            if i < len(seq) and seq[i] == t:
                i += 1
        if i == len(seq):
            out.append(c)
    return out


def check_pairing(rmap, conn):
    """配对不变式: ① 整格切出的子条目数 == 预期 ② 限值单元格唯一 ③ 库里 value 仍是取证时那个"""
    errs = []
    for cell, pairs, nsub in ((NICK_CELL, NICK_PAIR, 2), (WOOL_CELL, WOOL_PAIR, 0)):
        toks = split_cell(cell)
        if len(toks) - 1 != nsub:
            errs.append(f"  {cell!r} 切出 {len(toks) - 1} 个子条目, 与预期 {nsub} 不符 —— 不许按猜的装")
            continue
        cands = limits_cell_for(cell, pairs, rmap)
        if len(cands) != 1:
            errs.append(f"  {cell!r} 同行的限值单元格不唯一 ({len(cands)} 个: {cands}) —— "
                        "证不出「这几行的限值与整格的子条目顺序对得上」, 整批不执行")
        for rid, val in pairs:
            got = conn.execute("SELECT value FROM oel_limit WHERE rowid=?", (rid,)).fetchone()
            if got is None:
                errs.append(f"  oel_limit rid={rid} 不存在")
            elif (got[0] or "") != val:
                errs.append(f"  oel_limit rid={rid} 的 value 是 {got[0]!r} 而非取证时的 {val!r} "
                            "—— 配对前提变了, 本批不动它")
    return errs


# (表, 列, rowid, 旧值指纹, 新值来源, 一手依据)
# 新值来源: ("cell", 整格, 段次) / ("fixed", 逐字串) / ("clear",) = 残文, 清成 NULL
REPAIRS = [
    ("oel_limit", "factor_name", 1399, "镍及其无机化合物(按Ni 计) 金属镍与难溶性镍化合",
     ("cell", NICK_CELL, 0), "权威名单元格首段 = 主名 (表1 序号221); 子条目进 conditions, "
     "与钒/钼/铅/铜/铝尘/麻尘/石棉同一约定 (实测 22 行这种形状)"),
    ("oel_limit", "conditions", 1399, "物",
     ("cell", NICK_CELL, 1), "权威名单元格第二段; 与 1399.value=1 按同行限值单元格 '1 0.5' 的顺序配对"),
    ("oel_limit", "factor_name", 1400, "镍及其无机化合物(按Ni 计) 金属镍与难溶性镍化合",
     ("cell", NICK_CELL, 0), "同上 (主名两行共用)"),
    ("oel_limit", "conditions", 1400, "可溶性镍化合物",
     ("cell", NICK_CELL, 2), "权威名单元格第三段; 与 1400.value=0.5 配对 —— 值本已正确, 这条是幂等校验"),
    ("oel_limit", "factor_name", 1631, "人造矿物纤维绝热棉粉尘（玻",
     ("cell", WOOL_CELL, 0), "权威名单元格整格 (表1 序号29), 单一全名; 两行共用"),
    ("oel_limit", "conditions", 1631, "人造矿物纤维绝热棉粉尘（玻",
     ("clear",), "残文 = factor_name 的复读; 整格只有一个名字, 权威里没有子条目 → 清成 NULL。"
     "⚠ 不新造「质量浓度/纤维浓度」当子标签: 那六个字在权威里出现在**健康效应列** "
     "(「质量浓度：皮肤和眼刺激 纤维浓度：呼吸道不良健康效应」), 不在名单元格 —— 写进 conditions 属推断"),
    ("oel_limit", "factor_name", 1632, "人造矿物纤维绝热棉粉尘（玻",
     ("cell", WOOL_CELL, 0), "同上"),
    ("oel_limit", "conditions", 1632, "璃棉、矿渣棉、岩棉）",
     ("clear",), "残文已并入 factor_name"),
    # note 的词内断词: #40 那一族里唯一该动的。诚实口径: 实测 68 份成品里 '镍化合 物' **0 次**
    # (这几份没把该因子的备注引到纸上), 所以它是"迟早上纸"的存量缺陷而不是已上纸的缺陷;
    # 动它的依据是新值逐字等于同一行的权威备注单元格, 不是"我猜它会印出来"。
    ("oel_limit", "note", 1399, "G1（镍化合 物），敏 G2B（金属 和合金）",
     ("fixed", "G1（镍化合物），敏 G2B（金属和合金）"), "同一行的备注单元格逐字 (row_map 同行绑定)"),
    ("oel_limit", "note", 1400, "G1（镍化合 物），敏 G2B（金属 和合金）",
     ("fixed", "G1（镍化合物），敏 G2B（金属和合金）"), "同上"),
]


def new_value(src):
    if src[0] == "cell":
        return split_cell(src[1])[src[2]]
    if src[0] == "fixed":
        return src[1]
    return None


def entry_hit(nv, cells):
    """nv 能否在某个权威整格里对应到**一个条目** (前后是空格或格边); 空格差异不算差异"""
    s = nospace(nv)
    if not s:
        return True
    pat = re.compile(r"\s*".join(re.escape(ch) for ch in s))
    for cell in cells:
        for m in pat.finditer(cell):
            if ((m.start() == 0 or cell[m.start() - 1] == " ")
                    and (m.end() == len(cell) or cell[m.end()] == " ")):
                return True
    return False


def check_authority(texts):
    cells = list(dict.fromkeys(texts.values()))
    return [f"  {tab}.{col} rid={rid} 的新值 {new_value(src)!r} 在权威整格里对不上任何条目 → 拒绝"
            for tab, col, rid, _old, src, _why in REPAIRS
            if new_value(src) is not None and not entry_hit(new_value(src), cells)]


def plan(conn):
    can, hold = [], []
    for tab, col, rid, old, src, why in REPAIRS:
        nv = new_value(src)
        cur = conn.execute(f"SELECT {col} FROM {tab} WHERE rowid=?", (rid,)).fetchone()
        if cur is None:
            hold.append(f"  {tab} rid={rid} 不存在 (行被删过? 不动)")
            continue
        got = cur[0]
        if got == nv or (nv is None and got in (None, "")):
            hold.append(f"  {tab}.{col} rid={rid} 已是目标值 (幂等, 本批不动)")
            continue
        if got != old:
            hold.append(f"  {tab}.{col} rid={rid} 现值 {got!r} ≠ 取证时 {old!r} —— 中途被改过, 不动")
            continue
        can.append((tab, col, rid, old, nv, why))
    return can, hold


def paper_proof():
    """纸面口径: 用负向断言数, 不用子串数 (上一版就栽在这儿)"""
    files = sorted(glob.glob(str(DATA_DIR / "report_*.docx")))     # DATA_DIR 是路径唯一产地, 认 OHS_DATA_DIR
    from tools.export_fingerprint import texts_from_docx
    txt = "\n".join("\n".join(texts_from_docx(Path(f))[0]) for f in files)
    return {
        "docx份数": len(files),
        "残词(?!物)": len(re.findall(r"金属镍与难溶性镍化合(?!物)", txt)),
        "全名": len(re.findall("金属镍与难溶性镍化合物", txt)),
        "镍及其无机": len(re.findall("镍及其无机", txt)),
        "note断词": len(re.findall("镍化合 物", txt)),
    }


def main() -> int:
    mode = "--apply" if "--apply" in sys.argv else ("--proof" if "--proof" in sys.argv else "dry")
    texts, rmap, errs = load_authority()
    if errs:
        for e in errs:
            print("❌", e)
        return 1
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    bad = check_pairing(rmap, conn) + check_authority(texts)
    if bad:
        print("❌ 取证自检没过, 整批不执行:")
        for b in bad:
            print(b)
        conn.close()
        return 1

    if mode == "--proof":
        print(f"辖区标准 = {SOURCE} / 权威单元格 {len(texts)} 个 / 同行绑定 {len(rmap)} 个名字 "
              f"(源 {os.path.basename(PDF)} 页 {GBZ21_PAGES.start + 1}-{GBZ21_PAGES.stop})")
        for cell, pairs in ((NICK_CELL, NICK_PAIR), (WOOL_CELL, WOOL_PAIR)):
            print(f"  名单元格 = {cell!r}")
            print(f"    切分     = {split_cell(cell)}")
            print(f"    限值格   = {limits_cell_for(cell, pairs, rmap)} (要求恰 1 个)")
            print(f"    同行     = {sorted(rmap.get(nospace(cell), ()))}")
            for rid, val in pairs:
                got = conn.execute("SELECT factor_name,conditions,value,unit FROM oel_limit "
                                   "WHERE rowid=?", (rid,)).fetchone()
                print(f"    rid={rid} 现值 = {got}  (配对要求 value={val!r})")
        pp = paper_proof()
        print(f"  纸面口径: {pp}")
        if pp["docx份数"] != EXPECTED_DOCX:
            print(f"  ⓘ 成品份数与取证时不同 ({EXPECTED_DOCX} → {pp['docx份数']}), 纸面数字要重读")
        blob = "".join(x[0] or "" for x in conn.execute("SELECT data FROM project").fetchall())
        npj = conn.execute("SELECT count(*) FROM project").fetchone()[0]
        print(f"  项目侧 {npj} 行: 全名 {blob.count('金属镍与难溶性镍化合物')} 次 / "
              f"残词 {len(re.findall(r'金属镍与难溶性镍化合(?!物)', blob))} 次 / "
              f"镍及其无机 {blob.count('镍及其无机')} 次")
        conn.close()
        return 0

    # 预演走**只读**连接 (上面那把 mode=ro), 只有 --apply 才允许出现可写连接 ——
    # 否则"只开可写连接没改数据"就会在数据目录留下 -wal/-shm, 把部署演练的"目录不许新增条目"
    # 判据搞成环境函数 (见 quick_deploy 的排除条款与 tools/chain_audit.py 的静态接线判据)。
    can, hold = plan(conn)
    print(f"官方层可改 {len(can)} 格; 不动 {len(hold)} 项")
    for tab, col, rid, old, nv, why in can:
        print(f"  [{tab}.{col}] rid={rid}  {old!r}\n      → {nv!r}   依据: {why}")
    for h in hold:
        print("  ⓘ", h)
    if mode != "--apply":
        print("\n(预演模式 — 未写库; 加 --apply 才写入)")
        conn.close()
        return 0

    conn.close()
    conn = sqlite3.connect(str(DB_PATH))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    before = [{"table": t, "column": c, "rowid": r, "old": o} for t, c, r, o, _n, _w in can]
    bpath = DATA_DIR / f"oel_name_forms_before_{stamp}.json"
    bpath.write_text(json.dumps(before, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  ⓘ 写前原值已存 {bpath.name}")

    n = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for tab, col, rid, old, nv, _why in can:
            cur = conn.execute(f"UPDATE {tab} SET {col}=? WHERE rowid=? AND {col} IS ?",
                               (nv, rid, old))
            if cur.rowcount != 1:
                raise RuntimeError(f"{tab}.{col} rid={rid} affected={cur.rowcount} (期望 1)")
            n += 1
        for tab, col, rid, _old, nv, _why in can:          # 写完逐格读回
            got = conn.execute(f"SELECT {col} FROM {tab} WHERE rowid=?", (rid,)).fetchone()[0]
            if got != nv:
                raise RuntimeError(f"读回不一致 {tab}.{col} rid={rid}: 期望 {nv!r} 实得 {got!r}")
        cnt = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in EXPECTED_ROWS}
        for k, v in EXPECTED_ROWS.items():
            if cnt[k] != v:
                raise RuntimeError(f"{k} 行数从 {v} 变成 {cnt[k]} —— 名字修复不许动行数")
        conn.execute("COMMIT")
        print(f"✅ 已写 {n} 格并逐格读回核对 (行数 {cnt})")
    except BaseException as exc:
        conn.execute("ROLLBACK")
        print(f"⏹ 已回滚: {exc}")
        conn.close()
        return 1
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
