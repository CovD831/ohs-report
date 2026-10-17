"""PDF 表格单元格的**下标感知**取文本 (唯一产地)

为什么要有这个模块 (2026-10-16, #36 取证):
  `oel_limit` 里有一批化学名的下标数字被甩到了名字末尾:
      库里  '滑石粉尘（游离SiO 含量＜10%） 2'      标准  '滑石粉尘（游离SiO2 含量＜10%）'
      库里  '锰及其无机化合物（按 MnO 计） 2'      标准  '锰及其无机化合物（按MnO2 计）'
      库里  '碲化铋（按Bi 2Te 计） 3'              标准  '碲化铋（按Bi2Te3 计）'
  根因不是 OCR 读错了字, 而是 **PyMuPDF 的 `find_tables().extract()` 按基线排序**:
  下标 '2' 的字号 6.0pt、基线 468.5, 正文 9.0pt、基线 466.9 —— 基线更低就被排到最后,
  于是字符对了、**位置错了**。这类错不会以"乱码"的形式出现, 只会让 `factor_name=?` 查不到。

  同一页同一个单元格, 按 **span 顺序** (而非基线顺序) 拼, 得到的就是标准里的样子。
  本模块把"怎么拼"和"凭什么说它是下标"收在一处, 供三件事共用:
    · tools/fix_oel_subscripts.py  —— 存量迁移 (旧值→PDF 权威值)
    · tools/test_oel_subscripts.py —— 判据 (库里的名字必须等于 PDF 权威文本)
    · knowledge/oel_parser.py      —— 产地 (将来重跑解析就该拿到正确名字)

判据 (全部来自 PDF 自己的排版, 不是任何物质清单):
  某 span 是**下标** ⟺ 它的字号 < 本单元格主字号 × SUB_SIZE_RATIO
                     且 它的基线 > 本单元格主基线 + BASE_DROP
  两个阈值都取自本轮实测分布: 正文 9.0pt/基线 466.9, 下标 6.0pt(有的 4.6pt)/基线 468.5
  → 比值 0.67 与 0.51 都远低于 0.8, 基线差 1.6 远大于 0.4。留这个余量是为了"上标不误入":
  上标同样小, 但基线**更高**, 由 BASE_DROP 的方向排除。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

SUB_SIZE_RATIO = 0.8     # 字号 < 主字号×0.8 才算"小字"
BASE_DROP = 0.4          # 且基线低于主基线 0.4pt 以上才算"下标" (上标基线更高 → 被这条排除)

WHITESPACE = re.compile(r"\s+")
DIGITS_TAIL = re.compile(r"\s\d{1,2}\s*$")     # 『末尾孤立数字』形状 (本轮缺陷的可观察形状)


def _dominant(spans):
    """单元格内 (主字号, 主基线) = 按字符数加权的最多值 —— 不看"第一个 span", 表头/单位行会骗人"""
    sw, bw = Counter(), Counter()
    for s in spans:
        for _ in s.get("text", ""):
            sw[s["size"]] += 1
            bw[round(s["origin"][1], 1)] += 1
    if not sw:
        return None, None
    return sw.most_common(1)[0][0], bw.most_common(1)[0][0]


def spans_to_text(spans):
    """一行 span → 文本, 下标紧贴前一个字符 (不补空格)。返回 (文本, 下标证据列表)

    ⚠ 不在这里 strip: 单元格是**多行**的, "换行处到底有没有空格"由 PDF 自己说 ——
    实测页9 'Tellurium and Compounds (except ' 这个 span 就带尾随空格, 而下一行是 'H2Te), as Te';
    若在这里 strip 再把行间拼成 '', 就会造出 'exceptH2Te' 这种**新的**错 (本轮复验抓到并已修)。
    """
    ms, mb = _dominant(spans)
    out, ev = [], []
    for s in spans:
        t = s.get("text", "")
        if not t:
            continue
        if ms and s["size"] < ms * SUB_SIZE_RATIO and round(s["origin"][1], 1) > mb + BASE_DROP:
            ev.append({"text": t, "size": s["size"], "dom_size": ms,
                       "origin": round(s["origin"][1], 1), "dom_origin": mb})
        out.append(t)                          # 下标与普通 span 都是原样接上
    return "".join(out), ev


def cell_text(page, rect):
    """表格单元格 → (按 span 顺序拼的文本, 下标证据)。
    与 `find_tables().extract()` 的差别只在**排序键**: 这里按行内 span 顺序 + 行间按出现顺序,
    extract() 按基线 → 小字号下标会被挤到整个 cell 的最后。
    行间直接拼接 (空白由 PDF 自己的 span 决定), 最后统一压缩空白 —— 中文换行不补空格,
    拉丁文的词间空格因为写在 span 里所以自然保留。
    """
    import pymupdf
    d = page.get_text("dict", clip=pymupdf.Rect(rect))
    parts, ev = [], []
    for b in d["blocks"]:
        for ln in b.get("lines", []):
            t, e = spans_to_text(ln.get("spans", []))
            if t:
                parts.append(t)
                ev.extend(e)
    return WHITESPACE.sub(" ", "".join(parts)).strip(), ev


def authority(pdf_path, page_range, *, min_cells=100):
    """PDF 指定页段的**权威单元格文本集合** → (texts, evidence_by_text)
    texts: {去空白后的文本: 原样文本}  ——  去空白是匹配键, 因为 PDF 里同一名字可能有排版空格差异;
    evidence_by_text: {原样文本: [下标证据]}  ——  只有真带下标证据的 cell 才能作为"改名字"的依据。
    读不到 PDF / 解析出的单元格太少 → **抛错点名**, 不返回空集 (空集=「标准里没这些名字」,
    会让任何逐条比对都变成"全部找不到", 而那是取景错不是数据错)。
    """
    import pymupdf
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(f"一手来源不在: {p} —— 本判据/迁移的数据源就是这份标准 PDF, "
                                f"读不到时必须短路成一条点名, 不许报 N 条「查无此名」")
    doc = pymupdf.open(str(p))
    texts, ev = {}, {}
    try:
        for pno in page_range:
            page = doc[pno]
            for tab in page.find_tables().tables:
                for rect in tab.cells:
                    if not rect:
                        continue
                    t, e = cell_text(page, rect)
                    if not t:
                        continue
                    texts.setdefault(nospace(t), t)
                    if e:
                        prev = ev.get(t, [])
                        ev[t] = prev + [x for x in e if x not in prev]
    finally:
        doc.close()
    if len(texts) < min_cells:
        raise RuntimeError(f"从 {p.name} 只解析出 {len(texts)} 个单元格 (<{min_cells}) "
                           "—— 解析形状不对, 判据不能在这种前提下跑")
    return texts, ev


def nospace(s):
    return re.sub(r"\s+", "", s or "")


def row_map(pdf_path, page_range, min_rows=200):
    """{单元格文本(去空白): 同一行里其它单元格的文本集合}

    为什么需要它 (2026-10-16 对抗审查复验): 只校验"名字是不是标准里存在的一个名字",
    挡不住**错绑** —— 把 id=1623 (煤尘, 限值 4) 的名字改成 '谷物粉尘（游离SiO2含量＜10%）'
    (标准里另一个真名, 限值 6), 形状判据与"名字存在性"判据**全都绿**, 而限值已经错了。
    这条尺子把"名字"与"同一行的限值"绑回标准里的排版关系: 迁移前要求
    `库里这一行的 value` 出现在 `目标名字所在行的单元格集合` 里, 否则不动并点名。
    """
    import pymupdf
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(f"一手来源不在: {p} —— 同行绑定判据读不到源头, 必须短路成一条点名")
    doc = pymupdf.open(str(p))
    out = {}
    try:
        for pno in page_range:
            page = doc[pno]
            for tab in page.find_tables().tables:
                for row in tab.rows:
                    texts = []
                    for c in row.cells:
                        if not c:
                            texts.append("")
                            continue
                        t, _e = cell_text(page, c)
                        texts.append(t)
                    for i, t in enumerate(texts):
                        if not t:
                            continue
                        bucket = out.setdefault(nospace(t), set())
                        bucket.update(x for j, x in enumerate(texts) if j != i and x)
    finally:
        doc.close()
    if len(out) < min_rows:
        raise RuntimeError(f"同行绑定表只解析出 {len(out)} 行 (<{min_rows}) —— 排版形状不对, 不能跑")
    return out


def value_in_row(value, row_texts):
    """库里的一个限值字符串是否出现在标准同一行的单元格集合里 (允许 '5' 出现在 '5 (皮)' 这类单元格里)"""
    if value is None:
        return True                       # 库里这格没有值 → 没有可绑的东西, 不参与这条判定
    v = nospace(str(value))
    if not v:
        return True
    return any(v in nospace(t) for t in row_texts)


def digit_bag(s):
    """数字多重集 (排序后的字符串) —— 用来卡"只许挪位置, 不许改数字"。
    为什么需要它: 用「去掉数字与空白」的骨架当身份会把 '1-硝基丙烷' 和 '2-硝基丙烷'
    判成同一个名字 (本轮实测的歧义就这一对) —— 骨架必须**保留数字内容**, 只忽略它的位置。
    """
    return "".join(sorted(re.findall(r"\d", s or "")))
