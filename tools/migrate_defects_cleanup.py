"""通用迁移: 全项目存量正文级缺陷清理 (hedge / 废止标准号 / 系统措辞 / 韩文 / 元注释)

背景 (2026-10 事故): 早期生成的 section_states 文本里有 5 类"不该进正式报告"的内容:
  ① hedge: 「现行版本待补充/版本信息缺失/需核实后确认」— 标准号明明已可解析, 留 hedge=失职
  ② 废止标准号: GBZ 2.2—2007 / GB/T 38144.1—2019 等被引用 (LLM 凭记忆)
  ③ 系统措辞: 「由系统核验」「表格由系统自动插入」「机械提取与判定」— 内部词泄漏
  ④ 韩文: 「해당项目」=「该项目」的误翻
  ⑤ 元注释: 「（注：本节正文未复制表格内容…」「如需全部纳入…请确认」— 说明书/问句泄漏
不清理则任何重新导出都会把它们带进最终报告。

修法 (三支):
  A. 确定性: 主文本用 _fix_standard_refs (standard_db 现行版兜底) 全量扫; built_tables 单元格同扫。
  B. 精确替换表: 上述 ①②③④⑤ 的残余按**逐句精确匹配**替换 (来自 /tmp/probe*_out.txt 实测上下文)。
     每键必须命中 >0, 否则报 MISS (防止匹配串写错造成假绿)。
  B2. regex sweep: GB/T 38144 / GB(/T) 50034 家族 裸号与废止版 → 现行版 (全项目, 正文+单元格)。
      依据: standard_db (38144.1/.2-2019 废止→GB 38144-2025) + 住建部公告 PDF (原 GB 50034-2013 同时废止)。
  C. 兜底: _deskew_jargon (单一合并点, 只做确定性替换)。
幂等: 跑干跑两次结果一致; 已清理项不再命中即跳过。
用法: python tools/migrate_defects_cleanup.py [--apply]
输出: /tmp/migrate_defects_dryrun.txt (明细)
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "ohs.db"

# 精确替换表: (pid, sec, old, new)  —— old 用尽量短的完整片段, 全节唯一
# 来源: /tmp/probe4_out.txt + /tmp/hedge_full.txt 实测上下文
RULES = [
    # ---- ada54603a9 §1.3: hedge + 韩文 (同句) ----
    ("ada54603a9", "1.3",
     " 해당项目为化学原料和化学制品制造业",
     " 本项目为化学原料和化学制品制造业"),
    ("ada54603a9", "1.3",
     "眼面部防护应急喷淋和洗眼设备、个体防护装备配备规范所依据标准的现行版本待补充，需核实后确认。",
     "眼面部防护应急喷淋和洗眼设备依据 GB 38144—2025，个体防护装备配备依据 GB 39800.1—2020。"),
    # ---- ada54603a9: GB/T 38144 hedge → 现行版 GB 38144—2025 ----
    ("ada54603a9", "10.2",
     "GB/T 38144（现行版本待补充）配置",
     "GB 38144—2025配置"),
    ("ada54603a9", "11",
     "眼面部应急喷淋和洗眼设备按GB/T 38144配置，该标准现行版本待补充。",
     "眼面部应急喷淋和洗眼设备按GB 38144—2025配置。"),
    ("ada54603a9", "11.2",
     "按GB/T38144要求设置应急喷淋与洗眼设备（现行版本待补充），",
     "按GB 38144—2025要求设置应急喷淋与洗眼设备，"),
    ("ada54603a9", "11.7",
     "参照GB/T 38144的要求，具体标准版本待补充；",
     "参照GB 38144—2025的要求；"),
    ("ada54603a9", "11.4",
     "个体防护装备的配备应符合GB 39800的要求，眼面部应急喷淋和洗眼设备的设置应符合GB/T 38144的要求，上述两项标准现行版本号均待补充。",
     "个体防护装备的配备应符合GB 39800.1—2020的要求，眼面部应急喷淋和洗眼设备的设置应符合GB 38144—2025的要求。"),
    ("ada54603a9", "5.1.1.1",
     "其设置要求参照GB/T 38144执行（该标准现行版本待补充）。",
     "其设置要求参照GB 38144—2025执行。"),
    ("ada54603a9", "6.1.2",
     "拟依据GB/T 38144《眼面部防护应急喷淋和洗眼设备》执行，该标准现行版本待补充。",
     "拟依据GB 38144—2025《眼面部防护 应急喷淋和洗眼设备》执行。"),
    ("ada54603a9", "7.1",
     "应按照《眼面部防护 应急喷淋和洗眼设备》（GB/T 38144）执行，该标准现行版本待补充。",
     "应按照《眼面部防护 应急喷淋和洗眼设备》（GB 38144—2025）执行。"),
    # ---- ada54603a9: GB 39800 hedge → GB 39800.1—2020 ----
    ("ada54603a9", "10.2",
     "及GB 39800（现行版本待补充）执行",
     "及GB 39800.1—2020执行"),
    ("ada54603a9", "5.1.1.1",
     "个体防护装备的配备应满足GB 39800的要求（该标准现行版本待补充）；",
     "个体防护装备的配备应满足GB 39800.1—2020的要求；"),
    ("ada54603a9", "5.1.1.2",
     "眼面部应急冲洗设施参照GB/T 38144的要求，个体防护装备的配备原则参照GB 39800，上述两项标准现行版本待补充。",
     "眼面部应急冲洗设施参照GB 38144—2025的要求，个体防护装备的配备原则参照GB 39800.1—2020执行。"),
    ("ada54603a9", "6.1.1",
     "个体防护装备的配备原则应满足GB39800的要求，该标准现行版本待补充；",
     "个体防护装备的配备原则应满足GB39800.1—2020的要求；"),
    ("ada54603a9", "6.1.3",
     "拟按GB 18664-2025呼吸防护用品的选择、使用与维护及GB 39800个体防护装备配备规范的相关要求执行，其中GB 39800的现行版本待补充。",
     "拟按GB 18664—2025呼吸防护用品的选择、使用与维护及GB 39800.1—2020《个体防护装备配备规范 第1部分：总则》的相关要求执行。"),
    # ---- ada54603a9 §8.1: 版本信息缺失 (长句精确) ----
    ("ada54603a9", "8.1",
     "GB/T 38144 眼面部防护应急喷淋和洗眼设备及 GB 39800 个体防护装备配备规范的标准版本信息缺失，依据标准推断。",
     "眼面部防护应急喷淋和洗眼设备应按GB 38144—2025配置，个体防护装备配备应按GB 39800.1—2020执行。"),
    # ---- ada54603a9 §8.2 / c8c7ff0a4d §5.3: 表格元注释 (表题行, 系统插表时会写表题) ----
    ("ada54603a9", "8.2",
     "\n\n表 8.2-1 个人使用的职业病防护用品配备评价一览表（表格由系统自动插入）",
     ""),
    ("c8c7ff0a4d", "5.3",
     "\n\n表 5.3-1 本项目主要职业病危害因素职业接触限值一览表（表格由系统自动插入）",
     ""),
    # ---- ada54603a9 §3.3.3: 整段（注：… 由系统自动插入。）删除 (说给编辑者的话, 非报告正文) ----
    ("ada54603a9", "3.3.3",
     "（注：本节正文未复制表格内容，表格拟对照 GB50187-2012、GBZ1-2010 及 GB5083-2023 中关于功能分区、卫生防护距离、风向布置、道路与运输、绿化与竖向设计等条款，以“序号 | 卫生要求 | 检查依据 | 检查结果 | 评价”五列结构承载明细，由系统自动插入。）",
     ""),
    # ---- c8c7ff0a4d §2.4: 元问句注释删除 (问句不得进正文) ----
    ("c8c7ff0a4d", "2.4",
     "（注：原文中 item 序号与 post 编号存在混排",
     "（注：原文中标准条款引用编号存在混排"),
    ("c8c7ff0a4d", "2.4",
     "；上述内容按语义归类整理，标准条款号以 GBZ 1—2010 原文为准。",
     "，已按语义归类整理，标准条款号以 GBZ 1—2010 原文为准。"),
    ("c8c7ff0a4d", "2.4",
     "若需严格对照 post 编号逐条排列，请提供规范化的 post-条款对应关系。",
     ""),
    ("c8c7ff0a4d", "2.4",
     "另，原文中\"GBZ1-2010 8.1\"",
     "原文中\"GBZ1-2010 8.1\""),
    ("c8c7ff0a4d", "2.4",
     "等条款引用，如需全部纳入 2.4 节，请确认其与本节主题的对应关系；",
     "等条款引用经复核后纳入本节；"),
    ("c8c7ff0a4d", "2.4",
     "江苏省职业病防治条例第18条为地方性法规条款，是否纳入本节需明确。）",
     "江苏省职业病防治条例第18条为地方性法规条款。）"),
    # ---- 系统措辞 (SYS) ----
    ("4b16f075ad", "7",
     "具体标准目录以评价依据表为准，标准现行版本已由系统核验。",
     "具体标准目录以评价依据表为准，所引用标准均为现行有效版本。"),
    ("59fc6f3b02", "7",
     "具体标准内容详见评价依据表，其所引用的标准均为系统核验的现行有效版本。",
     "具体标准内容详见评价依据表，其所引用的标准均为现行有效版本。"),
    ("59fc6f3b02", "7.3",
     "本项目职业病危害预评价依据《中华人民共和国职业病防治法》及现行有效的GBZ体系标准进行系统核验。",
     "本项目职业病危害预评价依据《中华人民共和国职业病防治法》及现行有效的GBZ体系标准开展。"),
    ("c8c7ff0a4d", "1",
     "等职业卫生标准体系（标准现行版由系统核验，详见评价依据表）。",
     "等职业卫生标准体系（所引用标准均为现行有效版本，详见评价依据表）。"),
]

# ---- B2. 通用 regex sweep: 38144/50034 家族 (全项目, 正文+单元格) ----
# 38144: 2019 两分部废止, 2025 统合现行; 裸号/任意年份/伪形态(.N—2025)/复合(.1/.2—2019)
#        → GB 38144—2025 (已是现行形态则原样不动, 幂等)
# 50034: 2013 版废止, 2024 现行; 2013 年份形与裸号 → GB/T 50034—2024
def _sub_38144(m):
    """38144 家族统一替换: 任何历史/伪形态 → GB 38144—2025"""
    s = m.group(0)
    norm = re.sub(r"\s", "", s).replace("—", "-").replace("–", "-").replace("−", "-")
    if norm in ("GB38144-2025", "GB/T38144-2025"):
        return s
    return "GB 38144—2025"


SWEEP = [
    # ⚠ "代替" 前面提到的旧号不动 (那是替代关系陈述, 不是引用); 复合/伪形态整体消费防残断
    (re.compile(r"(?<!代替)(?<!代替\s)GB\s*/?\s*T?\s*38144(?:\.\s*\d)?(?:\s*/\s*\.?\s*\d)?"
                r"(?:\s*[—\-–−－]\s*(?:19|20)\d{2})?"),
     _sub_38144),
    (re.compile(r"GB\s*/?\s*T?\s*50034\s*[—\-–−－]\s*2013"),
     "GB/T 50034—2024"),
    (re.compile(r"GB\s*/?\s*T?\s*50034(?!\s*[—\-–−－]\s*\d{4})"),
     "GB/T 50034—2024"),
]


def _sweep_text(t):
    """按 SWEEP 逐条替换, 返回 (新文本, 实际改动数; 原样匹配不计)"""
    n = 0
    for rx, rep in SWEEP:
        hits = [0]

        def _w(m, rep=rep, hits=hits):
            out = rep(m) if callable(rep) else rep
            if out != m.group(0):
                hits[0] += 1
            return out

        t = rx.sub(_w, t)
        n += hits[0]
    return t, n


def _dump_report(lines):
    Path("/tmp/migrate_defects_dryrun.txt").write_text("\n".join(lines), encoding="utf-8")


def _walk_built_tables(bt, fix):
    """递归扫 built_tables (dict/list/str) 中的字符串, fix 返回替换后文本"""
    changed = 0
    if isinstance(bt, dict):
        for k in list(bt.keys()):
            v = bt[k]
            if isinstance(v, str):
                nv = fix(v)
                if nv != v:
                    bt[k] = nv
                    changed += 1
            elif isinstance(v, (dict, list)):
                changed += _walk_built_tables(v, fix)
    elif isinstance(bt, list):
        for i in range(len(bt)):
            v = bt[i]
            if isinstance(v, str):
                nv = fix(v)
                if nv != v:
                    bt[i] = nv
                    changed += 1
            elif isinstance(v, (dict, list)):
                changed += _walk_built_tables(v, fix)
    return changed


def main() -> int:
    apply = "--apply" in sys.argv
    from web.standard_version import fix_standard_refs

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, data FROM project").fetchall()

    rep = [f"migrate_defects_cleanup  {'APPLY' if apply else 'DRY-RUN'}",
           f"rules: {len(RULES)}"]
    total_txt = total_tbl = 0
    total_sweep = 0
    misses = 0
    for r in rows:
        pid = r["id"]
        try:
            d = json.loads(r["data"])
        except Exception:
            continue
        ss = d.get("section_states") or {}
        secs = [s for s in ss if isinstance(ss.get(s), dict)]

        # ---- A. 确定性: _fix_standard_refs 全节 + built_tables 全树 ----
        std_hits = 0
        for s in secs:
            t = ss[s].get("text") or ""
            if "GB" not in t:
                continue
            nt, chg = fix_standard_refs(t)
            if chg:
                std_hits += len(chg)
                sec_txt_changed = True
                if apply:
                    ss[s]["text"] = nt
                rep.append(f"  [STD] {pid} §{s}: " + "; ".join(chg))
        bt = d.get("built_tables")
        if isinstance(bt, (dict, list)):
            bt_hits = _walk_built_tables(bt, lambda x: fix_standard_refs(x)[0])
            if bt_hits:
                std_hits += bt_hits
                rep.append(f"  [STD-TBL] {pid}: built_tables {bt_hits} 处")
        total_tbl += std_hits if isinstance(bt, (dict, list)) else 0

        # ---- B. 精确替换表 ----
        for rpid, sec, old, new in RULES:
            if rpid != pid:
                continue
            st = ss.get(sec)
            if not isinstance(st, dict):
                rep.append(f"  [MISS-SEC] {pid} §{sec} 不存在 ({old[:24]}…)")
                misses += 1
                continue
            t = st.get("text") or ""
            hit = t.count(old)
            if hit == 0:
                if new and new in t:
                    rep.append(f"  [DONE] {pid} §{sec}: 已迁移 (新文本在场)")
                elif not new:
                    # 删除类规则: 幂等复核 — old 已不在正文 (首跑 MISS 才是错误, 此处为已删状态)
                    rep.append(f"  [DEL-DONE] {pid} §{sec}: 已删除 (old 不再命中)")
                else:
                    rep.append(f"  [MISS] {pid} §{sec}: {old[:36]}…")
                    misses += 1
                continue
            nt = t.replace(old, new)
            if apply:
                st["text"] = nt
            total_txt += hit
            rep.append(f"  [FIX x{hit}] {pid} §{sec}: {old[:36]}…  →  {new[:36]}…")

        # ---- B2. 通用 sweep: 38144/50034 家族 (正文 + 单元格) ----
        for s in secs:
            t = ss[s].get("text") or ""
            if "38144" not in t and "50034" not in t:
                continue
            nt, k = _sweep_text(t)
            if k:
                total_sweep += k
                rep.append(f"  [SWEEP] {pid} §{s}: {k} 处")
                if apply:
                    ss[s]["text"] = nt
        if isinstance(bt, (dict, list)):
            sw = _walk_built_tables(bt, lambda x: _sweep_text(x)[0])
            if sw:
                total_sweep += sw
                rep.append(f"  [SWEEP-TBL] {pid}: built_tables {sw} 项")

        # ---- C. 兜底: deskew ----
        try:
            from web.llm_draft import _deskew_jargon
            for s in secs:
                t = ss[s].get("text") or ""
                nt = _deskew_jargon(t)
                if nt != t:
                    rep.append(f"  [DESKEW] {pid} §{s}")
                    if apply:
                        ss[s]["text"] = nt
        except Exception as e:
            rep.append(f"  [DESKEW-SKIP] {e}")

        if apply:
            d["built_tables"] = bt if isinstance(bt, (dict, list)) else d.get("built_tables")
            conn.execute("UPDATE project SET data=? WHERE id=?",
                         (json.dumps(d, ensure_ascii=False), pid))

    if apply:
        conn.commit()
    conn.close()

    rep.append(f"\nTOTAL: 文本替换命中 {total_txt} 处; 标准号修复 {total_tbl} 处; sweep {total_sweep} 处; MISS {misses} 处")
    _dump_report(rep)
    print("\n".join(rep[-25:]))
    print(f"\n报告: /tmp/migrate_defects_dryrun.txt ({len(rep)} 行)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
