#!/usr/bin/env python3
"""任务 #53: 找出**已生成正文**里仍写着废止版号的句子, 逐处判"能不能只改号"

默认只读 (`--scan`), **本工具当前拒绝写入** —— 用户口径是"逐处读上下文再决定",
所以这一步只交付"每处 + 原句 + 判类 + 理由", 要写必须先看过清单再解除 ALLOW_WRITE。

为什么这是"内容"而不是"标签"问题 (2026-10-17 实测)
    #54 把 5 行判废止之后, `resolve_current` 会把新写的引用发到 2025; 但**已经存进库的正文**
    仍写着旧号: 本机 1 处 + 生产 16 处, 全在 `project.data.section_states.<节>.text`
    (我们自己起草的正文, 不是甲方手填的字段 —— 键名从数据派生, 见 owned_keys())。
    直接字符串替换 2010→2025 = 只改标签, 是 #17 定过的红线。原因是 229.1 那支**级数变了**:
    2010 版四级, 2025 版五级 (0 / Ⅰ行动水平 / Ⅱ轻度 / Ⅲ中度 / Ⅳ高度) ——
    "Ⅰ级轻度危害作业"在 2010 下是对的、在 2025 下是错的; 把号一改, 句子就变成假陈述。

判类怎么派生 (不含物质名/项目号清单)
    1) 每个新版 PDF 的**分级级名**由正文自己给出: 用 `分为…级…（0级）…（Ⅰ级）…` 这类句子
       在 PDF 里去空格逐字解析出「罗马数字 → 级名」表 (2025 版那五份是文本层干净的,
       2010 版扫描件 OCR 糊 → 所以只做**单边判据**: 句子与 2025 表矛盾 = 旧版内容, 不猜 2010 表);
    2) 句子改写目标 = **反向找**: 谁 `state='废止'` 且**另一行**的 `replace_of` 指到它 (按身份键),
       那一行就是新版 (见 stale_targets; 本目录里 replace_of 两种方向都有用法, 不能猜);
    3) A 类 (可只改号)   : 句子里没有任何「级名/罗马数字」断言、没有级别结论、也没抄旧书名,
                            只是把该号当依据引用;
       T 类 (号+书名一起改): 2025 这一批**标准名也换了** —— 『工作场所职业病危害作业分级 第N部分…』
                            →『职业病危害作业分级标准 第N部分…』(229.3 还把"高温"写成"高温作业"),
                            句中抄着旧书名时只换号 = 新号配旧名的错引 → 新名从目录 name 取;
       L 类 (含级别结论, 要重算): 句子里印着一个级别值 (『判定为0级，即相对无害作业』『…为Ⅱ级』),
                            换版改的正是算这个值的判据 (229.1 级数 4→5、表2 WB 台阶取消、
                            229.2 改了分级依据与表2 B 赋值) → **级名一致也放行不了**, 只能重算;
       B 类 (内容也是旧的): 句子里出现了与新版级名表**矛盾**的配对 (例: 新版里"轻度"属 Ⅱ级,
                            句中写"Ⅰ级…轻度") 或写了新版不存在的级名 ("重度危害作业");
       C 类 (交人工)     : 判据取不到 / 级名表只解析出一部分 / 级名不在新版表里 → 不自动改。
    4) 判据看不见什么 (写在这里, 别当全绿): 现在判三维 (级名配对 / 级别结论 / 旧书名耦合),
       仍**不判**阈值数字、表号 (229.1 表4→表5 那种换编号)、采样与检测方法这些内容 ——
       句子里若只是"按 229.1 的查表法确定分级指数"这类引用, 会落进 A 类,
       所以 A 类落地前还要人过一遍; 另外 OCR 糊的旧版级名表**推不出来**, 单边判据可能放过
       "恰好也符合新版表"的旧句子。
       ⚠ 2026-10-17 现场补的一条: 级名的罗马数字**两套排版都见过** —— 229.1/229.2 用 Unicode
       Ⅰ/Ⅱ/Ⅲ, 而 229.3—2025 的 4.3.1 整句写拉丁 (I级)(II级)。第一版只认 Unicode 那一套,
       229.3 照样"解析成功"却整档丢了 Ⅰ=轻度危害作业 (打印出来 3 对, 没人看得出少了一档),
       于是新版下本应成立的断言会被降级 C。现在正则两套都收, 并加**完整性对表**:
       标准自己声明"分为N级" ↔ 实际解析出的配对数不等 → 该族标记 partial, 命中一律 C, 不许放行 A。

用法:
    .venv/bin/python tools/rewrite_stale_std_citations.py --scan            # 只读扫描 (默认)
    .venv/bin/python tools/rewrite_stale_std_citations.py --scan --pid X    # 只看某个项目
    .venv/bin/python tools/rewrite_stale_std_citations.py --selftest        # 分类器能不能分开新旧句子
    .venv/bin/python tools/rewrite_stale_std_citations.py --apply           # 只写 A/T 两类 (需 ALLOW_WRITE)
    .venv/bin/python tools/rewrite_stale_std_citations.py --rollback <json> # 用写前原值整段还原
"""
import os
import re
import sys
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from knowledge.paths import DB_PATH, DATA_DIR                    # noqa: E402
from knowledge.standard_version import (norm_code, _base_code, _year,   # noqa: E402
                                        STD_SEPS, _SEP_CLS)

RAW = os.environ.get("OHS_GBZ21_RAW_DIR") or os.path.join(ROOT, "acquire", "raw", "nhc_gbz", "pdfs")

# 只列**新版 PDF 文件**; 号、级名、配对全部从 PDF 正文解析 (不手写)
NEW_EDITION_PDFS = ("GBZ_T_229.1_2025.pdf", "GBZ_T_229.2_2025.pdf", "GBZ_T_229.3_2025.pdf",
                    "GBZ_T_192.1_2025.pdf", "GBZT_192.2_2025.pdf")

ROMAN = ("Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ")
CN_NUM = {"一": "Ⅰ", "二": "Ⅱ", "三": "Ⅲ", "四": "Ⅳ", "五": "Ⅴ"}
# 成品里同一级既见过 Unicode 罗马数字也见过拉丁字母 (LLM 起草的正文写 "I级"、标准原文写 "Ⅰ级"),
# 只认一种 = 把一批真实断言当"没有断言"放行进 A 类。匹配串在 classify() 里按长度降序排好。
LATIN = {"IV": "Ⅳ", "VI": "Ⅵ", "IX": "Ⅸ", "III": "Ⅲ", "II": "Ⅱ", "V": "Ⅴ", "I": "Ⅰ"}
# 2026-10-17 窗口四: 用户看过 §1/§6 那份逐处清单后批准 "A 改号 + T 号名一起改 (推荐)",
# L 类那句明判"不改文本, 走分级重算"。写入范围因此**只放行 A/T**; L/B/C/D 由代码挡住,
# 不靠人记规矩 (见 apply_writes 里的 want = {"A","T"})。
ALLOW_WRITE = True


def to_roman(tok):
    return LATIN.get(tok.upper(), CN_NUM.get(tok, tok))


_CN_CNT = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}


def _n_cnt(tok):
    """标准正文『分为N级』里的 N → 整数 (中文数字与阿拉伯数字两种排版都见过)"""
    return _CN_CNT.get(tok, None) if not tok.isdigit() else int(tok)


def ns(s):
    return re.sub(r"[\s\u3000]+", "", str(s or ""))


def _nsd(s):
    """\u6bd4\u53f7\u7528: \u53bb\u7a7a\u767d + \u4e09\u79cd\u7834\u6298\u53f7\u5f52\u4e00\u6210\u534a\u89d2 (\u4e0e tools/retire_2025_batch.py \u540c\u4e00\u53e3\u5f84)"""
    return ns(s).replace("\u2014", "-").replace("\u2013", "-")


def pdf_text(path, pages=None):
    import pymupdf
    d = pymupdf.open(path)
    try:
        idx = range(len(d)) if pages is None else range(min(pages, len(d)))
        return "".join(d[i].get_text() for i in idx)
    finally:
        d.close()


# 级名的罗马数字在标准正文里有**两套排版**: 229.1/229.2 用 Unicode Ⅰ/Ⅱ/Ⅲ, 而 229.3—2025 的
# 4.3.1 那句写的是拉丁 "轻度危害作业（I级）、中度危害作业（II级）…"。只认 Unicode 那一套时
# 实测 229.3 的表解析出 3 对 (Ⅱ中度/Ⅲ重度/Ⅳ极重度, 来自别处排版), **漏掉 Ⅰ=轻度** ——
# 于是"Ⅰ级轻度危害作业"这种在新版下其实成立的断言会被降级成 C 交人工 (方向安全但不是判据)。
# 多位在前防 'II' 被 'I' 抢先; 归一交给 to_roman(), 与 classify() 里同一把尺子。
_ROMAN_ALT = "IV|III|II|I|V|Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ|Ⅸ|0"


def _roman_map(tok):
    return to_roman(tok)


def level_table(path):
    """从新版正文解析「罗马数字 ↔ 级名」。两个方向都试, 因为标准自己的写法不止一种排版:
        A 型 (229.1/229.2): '分为五级：相对无害作业（0级）、行动水平作业（Ⅰ级）…'  → 名（罗马级）
        B 型 (229.3 高温)  : 正文与表里写 'Ⅱ级（中度危害作业）'                    → 罗马级（名）
       返回 dict; **两个方向都解析不到配对时返回 {"by_roman": {}, "names": set(), ...}** ——
       那是"这本标准没有级别词汇" (192 是测定方法), 与"PDF 取不到判据"是两回事,
       前者可以让 A 类通过, 后者必须降级 C。"""
    t = ns(pdf_text(path))
    pairs = {}
    # A 型
    for name, roman in re.findall(rf"([\u4e00-\u9fa5]{{2,12}}作业)[（(]({_ROMAN_ALT})级[）)]", t):
        pairs.setdefault(_roman_map(roman), set()).add(re.sub(r"^[和、，]", "", name))
    # B 型
    for roman, name in re.findall(rf"({_ROMAN_ALT})级[（(]([\u4e00-\u9fa5]{{2,12}}作业)[）)]", t):
        pairs.setdefault(_roman_map(roman), set()).add(re.sub(r"^[和、，]", "", name))
    n_sent = re.search(r"分为([一二三四五\d])级", t)
    declared = _n_cnt(n_sent.group(1)) if n_sent else None
    if not pairs:
        return {"by_roman": {}, "names": set(), "n_levels": None, "declared": declared,
                "partial": False,
                "src": os.path.basename(path), "no_levels": True,
                "sentence": (n_sent.group(0) if n_sent else "正文无『分为N级』句")}
    return {"n_levels": n_sent.group(1) if n_sent else str(len(pairs)),
            "declared": declared,
            # 完整性对表: 标准自己声明的级数 vs 真正解析出的配对数。不相等 = **有一档没认出来**
            # (229.3 那次就是这样: 正文写拉丁 (I级), 只认 Unicode 罗马数字 → Ⅰ=轻度整档缺失,
            #  而打印出来仍是"解析成功 3 对", 没人会看出少了一档)。缺档必须降级 C, 不许放行 A。
            "partial": bool(declared) and len(pairs) != declared,
            "by_roman": {k: sorted(v) for k, v in sorted(pairs.items())},
            "names": {x for vs in pairs.values() for x in vs},
            "src": os.path.basename(path), "no_levels": False,
            "sentence": t[t.find("分为"):t.find("分为") + 120] if "分为" in t else ""}


def edition_tables():
    out, notes = {}, []
    for f in NEW_EDITION_PDFS:
        p = os.path.join(RAW, f)
        if not os.path.exists(p):
            notes.append(f"  新版 PDF 不在: {f} → 该号所有命中降级 C (读不到判据就不许判'可只改号')")
            continue
        tab = level_table(p)
        # 号从封面正文取 (不从文件名猜): 文件名形状与库里 code 形状是两套 (GBZT192.2-2025 vs
        # GBZ/T192.2-2025), 拿文件名推号会推错身份键 —— 身份键一律走 norm_code/_base_code 那把尺子
        m = re.search(r"(GBZ/?T?\s?[\d.]+)", ns(pdf_text(p, 1)))
        key = _base_code(norm_code(m.group(1))) if m else None
        if key:
            out[key] = tab
            if tab["no_levels"]:
                notes.append(f"  {f}: 正文没有级别词汇 ({tab['sentence'][:30]}) → 该号命中按"
                             "'无可核断言'处理, 级别这一维不构成耦合")
        else:
            notes.append(f"  {f} 封面读不到标准号 → 该号命中降级 C")
    return out, notes



def stale_targets(conn):
    """(旧号 code, 旧号身份键, 新号 code, 新号身份键)

    方向要从数据里认, 不能猜: 本目录里 `replace_of` 两种用法都存在 ——
      · 新行写"我代替谁" (#54 本批就是这一支: 229.1—2025.replace_of = 'GBZ/T229.1-2010');
      · 旧行写"我代替谁" (nhc_catalog 抓来的存量, 如 WS216-2008.replace_of='WS216-2001')。
    所以规则是**反向找**: 谁 `state='废止'`, 且**另一行**的 replace_of 指到它 (按身份键),
    那一行就是改写目标。只按"本行 state=废止 且 本行 replace_of 非空"找会一条都抓不到
    —— #54 刚翻完的五行恰恰是旧行自己没有 replace_of。"""
    rows = conn.execute("SELECT id, code, state, replace_of FROM standard_db").fetchall()
    ident = {}
    for _id, code, _s, _r in rows:
        ident.setdefault((_base_code(norm_code(code)), _year(code)), []).append((_id, code))
    state_of = {_id: s for _id, _c, s, _r in rows}
    out, seen = [], set()
    for _id, code, _s, rep in rows:
        if not rep:
            continue
        for oid, ocode in ident.get((_base_code(norm_code(rep)), _year(rep)), []):
            if state_of.get(oid) != "废止" or oid == _id:
                continue
            key = (ocode, code)
            if key in seen:
                continue
            seen.add(key)
            out.append((ocode, (_base_code(norm_code(ocode)), _year(ocode)),
                        code, (_base_code(norm_code(code)), _year(code))))
    return out


def owned_paths(d):
    """我们自己起草的正文键 —— 形状判据: `section_states.<节号>.text`;
       客户手填字段 (detections / material …) 一律不在此列, 本工具不许碰。

       返回 (path, sec, text): **sec 必须原样带着**, 因为节号里就有点 ("10.2"), 拿 path 按 '.'
       切会切成 '10' —— 这个坑 tools/fix_project_std_text.py 的注释里写过一次, 我在这支工具里
       又踩了一次 (演练时 15/16 处报"句子定位不到"就是它, 不是库变了)。"""
    res = []
    for k, v in (d.get("section_states") or {}).items():
        if isinstance(v, dict) and isinstance(v.get("text"), str) and v["text"].strip():
            res.append(("section_states." + str(k) + ".text", str(k), v["text"]))
    return res


def _core(s):
    """只留汉字/数字/字母 (去掉空白与所有标点) —— 书名对表用的口径。

    为什么不能只 ns(): 目录里存的旧名本身有标点缺失 ('工作场所职业病危害作业分级 第3部分 高温'
    少一个冒号), 正文写的是 '第3部分：高温' —— 按标点比较时这条**抄着旧书名的句子会被判成
    没抄书名** (实测 a4d21f3a0b 6.1.4 第一轮落的 A)。标点差异不是"另一个书名"。
    """
    return "".join(ch for ch in str(s or "")
                   if "\u4e00" <= ch <= "\u9fff" or ch.isalnum())


def title_coupling(sentence, old_name, new_name):
    """句子里是不是把**旧版的书名**抄进去了 (从目录 name 派生, 不写书名清单)。

    为什么要单独判这一维 (2026-10-17 实测): 2025 这一批不只换了号, **标准名也换了** ——
      GBZ/T 229.1: '工作场所职业病危害作业分级第1部分：生产性粉尘'
                   → '职业病危害作业分级标准 第1部分：生产性粉尘（代替…）'
      GBZ/T 229.3: '工作场所职业病危害作业分级 第3部分 高温'
                   → '职业病危害作业分级标准 第3部分：高温作业（代替…）'
    正文里写的是《旧书名》+ 旧号这种成对引用。只把号换成 2025、书名留着不动, 纸面就变成
    "新号配旧名"的错引 —— 那仍然是只改标签, 而且是我上一版判据看不见的一维。

    先判"已是新书名"再判旧名: 192 族的新名 = 旧名 + '标准' 两个字, 即**旧名是新名的子串**,
    只按"含旧名"判会把已经改对的句子重新拖回 T。
    """
    s = _core(sentence)
    core_new = _core(re.sub(r"[（(]\s*代替[^）)]*[）)]", "", new_name or ""))
    core_old = _core(old_name or "")
    if core_new and core_new in s:
        return False, "句中已是新书名"
    if core_old and core_old in s:
        return True, f"句中抄着旧书名『{old_name}』→ 号与名必须一起换 (新名取自目录 name)"
    return False, ""


def classify(sentence, tab):
    """→ (类别, 理由)。tab=None 时一律 C (判据取不到就不自动放行)

    四步: ① 只挑"像级名"的片段 (以『作业/危害/水平/无害』收尾) 当断言 ——
          "Ⅰ级及以下"这种**只指级别不命名**的写法不算断言, 否则整批健康检查周期句会被误判 C;
    ② 断言里的级名与该罗马数字在新版表里的名字**不相容** → B (内容属旧版, 禁止只改号);
    ③ 断言命名在新版表里完全找不到 (旧版别名/新版没这级) → C 交人工;
    ④ 断言与新版表**一致**也不给 A: 句子里已经印着一个**级别结论** (『判定为0级，即相对无害作业』
       这类), 而换版改的正是算出这个结论的判据 (229.1 级数 4→5、229.1 表2 WB 由 `1≤B≤2→1` 改成
       `B>1→B`、229.2 前言改了分级依据与表2 B 赋值) → 只能重算, 不能只改号。判 L。
       ⚠ 上一版把这一类放进 A, 理由写的是"断言与新版级名表一致" —— 级名一致 ≠ 级别值一致,
         那是把"名字没变"当成"结论没变", 正是要防的只改标签。
    """
    if tab is None:
        return "C", "新版级名表取不到 → 不自动判"
    if tab.get("partial"):
        return "C", (f"级名表只解析出 {len(tab['by_roman'])} 对, 标准自己声明 {tab['declared']} 级 "
                     "→ 有一档没认出来, 不自动判")
    s = ns(sentence)
    # '0' 与拉丁/Unicode 两种罗马数字都在成品里出现过; 多位在前, 防 'IV' 被 'I' 抢先。
    # 级名与"级"之间允许标点与"即/为/属/是" —— 实测正文写过 "判定为0级，即相对无害作业",
    # 只认 "0级（相对无害作业）" 会把这条真实断言放行进 A (第一版就是这样漏的)。
    pat = re.compile(r"(0|IV|VI|IX|III|II|I|V|Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ|Ⅸ)级"
                     r"[（(]?[，,、；;\s]*(?:即|为|属|是)?[（(]?([\u4e00-\u9fa5]{2,10})")
    asserts = []
    for roman, name in pat.findall(s):
        r = to_roman(roman)
        if not re.search(r"(作业|危害|水平|无害)$", name):
            continue                        # 不命名的引用 (如 'Ⅰ级及以下') 不入断言账
        asserts.append((r, name))
    # 没有级名、但句子自己下了级别结论的写法 (『…判定为Ⅱ级』) —— 同样要重算。
    # ⚠ 动词必须收紧到"给出结论"那一类: 第一版把裸 `判定|分级|…` 后面允许 10 个字再跟 `X级`,
    #   于是 "生产性粉尘作业分级I级及以下，4年一次体检" 被判成结论 (L), 而它是**区间引用**
    #   (标准里那句本来就是"Ⅰ级及以下作业 2 年一次体检") → 体检周期句整批会被误拦。
    #   收紧后要求 `判定…为` / `判为` / `定为` / `属于` 这类**谓词**紧邻级别值。
    concl = re.search(r"(判定[^。；]{0,12}?为|判为|判定结果[^。；]{0,8}?为|定为|评为|归为"
                      r"|划分为|级别为)(0|IV|III|II|I|V|Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ)级", s)
    if not asserts:
        if concl:
            return "L", (f"句中给出级别结论 ({concl.group(0)[:24]}) → 换版改的是算级别的判据, "
                         "要重算不是改号")
        return "A", "句中只有依据引用, 无可核的『X级+级名』断言, 也无级别结论"
    bad = []
    unknown = []
    for r, name in asserts:
        allowed = tab["by_roman"].get(r, [])
        overlap = any(a in name or name in a for a in tab["names"])
        if allowed and not any(a in name or name in a for a in allowed):
            bad.append(f"{r}级+{name} (新版该级叫 {'/'.join(allowed)})")
        elif not overlap:
            unknown.append(f"{r}级+{name}")
    if bad:
        return "B", "级名与新版表矛盾 → 内容属旧版: " + "; ".join(bad[:2])
    if unknown:
        return "C", "级名不在新版表里 (旧版别名或新版不存在的级): " + "; ".join(unknown[:2])
    return "L", ("级名与新版表一致, 但句中印着级别结论 (" +
                 "; ".join(f"{r}级+{n}" for r, n in asserts[:2]) +
                 ") → 级别值是旧判据算出来的, 换版必须重算, 不许只改号")


def scan(conn, only_pid=None, all_pairs=False):
    targets = stale_targets(conn)
    tables, notes = edition_tables()
    if not all_pairs:
        # 只判**这一批有一手 PDF 的代替对**。其余 180+ 对的级名判据取不到,
        # 全塞进 C 只会把清单冲长、把"真需要人看的 16 处"淹掉 —— 那是噪声不是保守。
        keep = [(o, ok, n, nk) for (o, ok, n, nk) in targets if nk[0] in tables]
        dropped = len(targets) - len(keep)
        targets = keep
    else:
        dropped = 0
    by_key = {}
    for old_code, old_key, new_code, new_key in targets:
        by_key.setdefault(old_key, (old_code, new_code))
    # 书名对表要用的 name: 身份键 → name (只取这一批涉及的族, 别的族不参与)
    names = {}
    for code, name in conn.execute("SELECT code, name FROM standard_db"):
        names.setdefault((_base_code(norm_code(code)), _year(code)), name or "")
    occ = []
    for pid, data in conn.execute("SELECT id, data FROM project"):
        if only_pid and pid != only_pid:
            continue
        if not data:
            continue
        try:
            d = json.loads(data)
        except Exception:
            continue
        for path, sec, text in owned_paths(d):
            for sent in split_sentences(text):
                if len(sent.strip()) < 8:
                    continue
                # ⚠ 比的是**归一后**的串: 库里 code 是半角 'GBZ/T229.1-2010', 正文写全角
                #    'GBZ/T 229.1—2010' —— 只按字面相等会一条都命中不到 (本项目在破折号上
                #    栽过第三次: 上一次是数 0, 这次是"扫出 0 处"差点被当成干净)
                nsent = _nsd(sent)
                for (ob, _oy), (old_code, new_code) in by_key.items():
                    nold, nnew = _nsd(old_code), _nsd(new_code)
                    if nold in nsent:
                        tab = tables.get(ob)
                        cls, why = classify(sent, tab)
                        old_name = names.get((ob, _oy), "")
                        new_nm = names.get((ob, _year(new_code)), "")
                        # 级名这一维放行 (A) 之后还要过书名这一维: 句中抄着旧版《书名》时,
                        # 只换号 = 新号配旧名, 仍是错引 → 降级 T (号与名一起改, 新名取自目录)
                        if cls == "A":
                            hit, twhy = title_coupling(sent, old_name, new_nm)
                            if hit:
                                cls, why = "T", twhy
                        occ.append({"pid": pid, "path": path, "sec": sec,
                                    "old": old_code, "new": new_code,
                                    "class": cls, "why": why, "sent": sent.strip()[:220],
                                    "sent_full": sent.strip(), "old_name": old_name,
                                    "new_name": new_nm})
                    elif nnew in nsent and ("代替" in sent or "废止" in sent):
                        occ.append({"pid": pid, "path": path, "sec": sec,
                                    "old": old_code, "new": new_code,
                                    "class": "D", "why": "已写新版却仍提旧号 (核对上下文)",
                                    "sent": sent.strip()[:220], "sent_full": sent.strip(),
                                    "old_name": "", "new_name": ""})
    return occ, tables, notes, targets, dropped


def selftest() -> int:
    """分类器必须能把『旧级别名』和『纯引用』分开 —— 拿两句话喂同一张级名表"""
    tab = {"n_levels": "五", "declared": 5, "src": "合成 (照 229.1—2025 的级名表)",
           "by_roman": {"0": ["相对无害作业"], "Ⅰ": ["行动水平作业"], "Ⅱ": ["轻度危害作业"],
                        "Ⅲ": ["中度危害作业"], "Ⅳ": ["高度危害作业"]},
           "names": {"相对无害作业", "行动水平作业", "轻度危害作业", "中度危害作业", "高度危害作业"}}
    cases = [
        ("高温作业依据 GBZ/T 229.3—2010 判定岗位等级", "A", "只有依据引用 (判定的是'等级'这个词, 没给值)"),
        ("生产性粉尘作业分级Ⅰ级轻度危害作业，应加强通风", "B", "2010 的 Ⅰ级=轻度, 与新版表矛盾"),
        ("作业分级为Ⅲ级（中度危害作业）", "L", "级名一致但句中有级别结论 → 要重算"),
        ("按 GBZ/T 229.1—2010 判为Ⅱ级重度危害作业", "B", "新版没有『重度』这一级名"),
        ("生产性粉尘作业分级I级及以下，4年一次体检", "A", "拉丁 I 但**不命名** → 非断言, 不误判 C"),
        ("按 GBZ/T 229.1—2010 判为I级轻度危害作业", "B", "拉丁写法同样要判旧 (只认 Unicode 罗马数字就会漏)"),
        ("粉尘作业分级Ⅰ级（行动水平作业），按 5.2 管理", "L", "新版独有级名, 但仍是级别结论"),
        # ↓ 三条都是本轮在生产正文里实际读到、被上一版判成 A 的形状 (变异用例: 改坏正则它们会红)
        ("依据GBZ/T 229.2—2010进行作业分级，接触水平均判定为0级，即相对无害作业",
         "L", "『0级，即相对无害作业』没有括号, 上一版认不出这条断言 → 直接放行进 A"),
        ("高温作业依据 GBZ/T 229.3—2010 判定为Ⅱ级", "L", "只给了级别值、没给级名 → 仍要重算"),
        ("本项目高温危害来源及作业环境参数等基础数据待补充（需企业提供）",
         "A", "对照组: 句里既无号也无级别 → 不该被任何一维拦下"),
    ]
    bad = 0
    for sent, want, why in cases:
        got, reason = classify(sent, tab)
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} [{got}] 期望 {want} :: {why}\n      实得理由: {reason}")
    # 书名这一维: 拿目录里真实的旧名/新名 (从数据取形状, 不手写书名)
    OLD_NM = "工作场所职业病危害作业分级 第2部分：化学物"
    NEW_NM = "职业病危害作业分级标准 第2部分：化学物（代替GBZ/T 229.2—2010）"
    NM3_OLD = "工作场所职业病危害作业分级 第3部分 高温"          # 目录里就是这样, 没冒号
    NM3_NEW = "职业病危害作业分级标准 第3部分：高温作业（代替GBZ/T 229.3—2010）"
    NM92_OLD = "工作场所空气中粉尘测定  第1部分：总粉尘浓度"
    NM92_NEW = "工作场所空气中粉尘测定标准 第1部分：总粉尘浓度（代替 GBZ/T 192.1—2007）"
    for sent, onm, nnm, want, why in [
        (f"拟建项目需依据GBZ/T 229.2—2010《{OLD_NM}》综合判定", OLD_NM, NEW_NM,
         True, "句中抄着旧书名 → 号+名一起改 (T)"),
        (f"依据GBZ/T 229.2—2025《{NEW_NM[:-12]}》综合判定", OLD_NM, NEW_NM,
         False, "已是新书名 → 不构成旧名耦合"),
        ("应参照 GBZ/T 229.2—2010 进行作业分级。", OLD_NM, NEW_NM,
         False, "只有号没有名 → 不判 T"),
        # 标点口径: 目录里 229.3 旧名写的是 '第3部分 高温' (没冒号), 正文写 '第3部分：高温'
        ("相关设计应符合 GBZ/T 229.3—2010《工作场所职业病危害作业分级 第3部分：高温》的要求",
         NM3_OLD, NM3_NEW, True, "旧名少个冒号不等于不是同一个书名 (第一版按 ns() 比, 这条落进了 A)"),
        # 子串陷阱: 192 族新名 = 旧名 + '标准', 已经改对的句子不能被旧名子串再拖回 T
        ("按 GBZ/T 192.1—2025《工作场所空气中粉尘测定标准 第1部分：总粉尘浓度》测定",
         NM92_OLD, NM92_NEW, False, "句中已是新名 (旧名是新名的子串 → 必须先判新名)"),
    ]:
        got, why2 = title_coupling(sent, onm, nnm)
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} 书名对表 [{got}] 期望 {want} :: {why}\n      实得理由: {why2 or '(无耦合)'}")
    g, r = classify("随便一句", None)
    print(f"  {'✓' if g == 'C' else '✗'} 判据取不到时降级 C (不许当成'可只改号') :: {r}")
    bad += 0 if g == "C" else 1
    part = dict(tab, partial=True, declared=6)
    g, r = classify("作业分级为Ⅲ级（中度危害作业）", part)
    print(f"  {'✓' if g == 'C' else '✗'} 级名表**只解析出一部分**时降级 C :: {r}")
    bad += 0 if g == "C" else 1

    # 真 PDF 的完整性对表: 标准自己声明几级, 就必须解析出几档 —— 这一条就是刚才那个 bug 的变异用例
    # (229.3 正文用拉丁 (I级), 老正则只认 Unicode 罗马数字 → Ⅰ=轻度整档缺失, 却照样"解析成功")
    tables, notes = edition_tables()
    if not tables:
        print("  ⚠ 一手 PDF 不在 %s → 这一节**不算绿** (只自检了合成用例)" % RAW)
    for k, v in sorted(tables.items()):
        if v["no_levels"]:
            print(f"  ✓ {k}: 正文无级别词汇 (测定方法类), 不参与级名对表")
            continue
        ok = not v["partial"]
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {k}: 声明 {v['declared']} 级 / 解析 {len(v['by_roman'])} 对 "
              f"{ {r: '/'.join(n) for r, n in v['by_roman'].items()} }")
    print(f"自检: {'六类判据行为都对' if bad == 0 else str(bad) + ' 条不对'}")
    return 0 if bad == 0 else 1


def code_token_rx(code):
    """把库里 code 变成能在正文里**逐字定位**的正则 (保留这句话自己的写法)。

    为什么不能拿 `_base_code()` 当模板: 它把 '/' 也吃掉了 ('GBZ229.3'), 用它拼出的正则
    匹配不到正文里的 'GBZ/T 229.3' (实测 0 命中)。这里用**去掉年份尾巴的原始 code** 逐字符插
    `\\s*` —— 那正是 scan 的命中判据 (`_nsd(code) in _nsd(sent)`) 的逆运算: 号的字符顺序不变、
    字符之间允许多出空白、年份前是破折号族里的某一个。年份用 `(?![0-9A-Za-z])` 收尾, 防
    'GBZ/T 229.3—20102' 这类被截走。
    """
    y = str(_year(code))
    stem = re.sub("[" + re.escape(STD_SEPS) + r"]+$", "", str(code)[: -len(y)]).strip()
    body = "".join(re.escape(ch) + r"\s*" for ch in stem)
    return re.compile(body + _SEP_CLS + r"\s*" + re.escape(y) + r"(?![0-9A-Za-z])")


def name_token_rx(name):
    """旧书名的逐字定位 (裸形, 没带书名号时用): 原样字符顺序, 字符之间允许多出空白。
       标点**不**放宽 —— 目录里 229.3 旧名少个冒号, 放宽标点会把整句吃掉。空 name → 永不匹配。"""
    chars = [ch for ch in str(name or "").strip() if not ch.isspace()]
    if not chars:
        return re.compile("(?!a)a")
    pat = "".join(re.escape(ch) + r"\s*" for ch in chars)
    return re.compile(pat[:-2])             # 去掉尾巴上多余的 \s*


def new_name_core(new_name):
    """目录 name 里的『（代替GBZ/T 229.1—2010）』是**登记注记**, 不是书名的一部分 → 落地时去掉"""
    return re.sub(r"\s*[（(]\s*代替[^）)]*[）)]\s*$", "", str(new_name or "")).strip()


def rewrite_sentence(sent, old_code, new_code, old_name, new_name, cls):
    """→ (新句, [(动作, 旧片段, 新片段), …])。A 只动号; T 动号 + 动旧书名。

    号是**就地把年份换掉** (保留这句话自己的空格与破折号写法), 不是拿库里那串去覆盖正文 ——
    后者会把 'GBZ/T 229.3—2010' 印成 'GBZ/T229.3-2025' 这种库形, 是另一种脏。
    """
    ops = []
    out = sent
    rx_old = code_token_rx(old_code)
    y_old, y_new = str(_year(old_code)), str(_year(new_code))
    for m in sorted(rx_old.finditer(out), key=lambda x: -x.start()):
        frag = m.group(0)
        if not frag.rstrip().endswith(y_old):
            continue
        head = frag[: len(frag) - len(y_old)]
        fixed = head + y_new
        out = out[: m.start()] + fixed + out[m.end():]
        ops.append(("号", frag, fixed))
    if cls == "T":
        core_old = _core(old_name)
        done = False
        for m in re.finditer(r"《([^》]{4,80})》", out):        # 带书名号: 按 _core 相等整段换
            if _core(m.group(1)) == core_old:
                nn = new_name_core(new_name)
                out = out[: m.start(1)] + nn + out[m.end(1):]
                ops.append(("名", m.group(1), nn))
                done = True
                break
        if not done:                                            # 裸书名 (没书名号) 也认, 但要整词等长
            rx = name_token_rx(old_name)
            for m in rx.finditer(out):
                if _core(m.group(0)) == core_old:
                    nn = new_name_core(new_name)
                    out = out[: m.start()] + nn + out[m.end():]
                    ops.append(("名", m.group(0), nn))
                    done = True
                    break
        if not done:
            ops.append(("名-失败", old_name, "(没定位到旧书名 → 这一处不改名)"))
    return out, ops


def split_sentences(text):
    """与 scan 用的同一个切分: 分隔符留在片段里 → `"".join(...) == text` 恒等 (§6 落地靠它)"""
    return re.split(r"(?<=[。；;])", text)


def apply_writes(occ, only_pid=None, dry=True):
    """→ (每处动作明细, 还原用的原值, 被挡住的清单)。只写 A/T; L/B/C/D 一律不动并点名。

    写的是 `project.data` 里那一个节的 text —— 逐句改、逐句拼回, 拼回前先断言
    `"".join(切分) == 原文` (切分不闭合就整段拒绝), 这样"改一处把别处挤掉"不可能发生。
    """
    want = {"A", "T"}
    rejected = [o for o in occ if o["class"] not in want]
    by_loc = {}
    for o in occ:
        if o["class"] in want and (not only_pid or o["pid"] == only_pid):
            by_loc.setdefault((o["pid"], o["path"]), []).append(o)
    conn = (sqlite3.connect(DB_PATH) if not dry
            else sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True))
    before, edits = {}, []
    try:
        for (pid, path), items in sorted(by_loc.items()):
            row = conn.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
            if not row or not row[0]:
                edits.append({"pid": pid, "path": path, "fail": "项目行或 data 取不到 → 不动"})
                continue
            d = json.loads(row[0])
            sec = items[0]["sec"]                    # 节号原样取 (里面可能有 '.')
            text = ((d.get("section_states") or {}).get(sec) or {}).get("text")
            if not isinstance(text, str):
                edits.append({"pid": pid, "path": path, "fail": "该节 text 不是字符串 → 不动"})
                continue
            pieces = split_sentences(text)
            if "".join(pieces) != text:
                edits.append({"pid": pid, "path": path, "fail": "切分不闭合 → 整段拒绝重写"})
                continue
            used = {}
            for i, piece in enumerate(pieces):
                hits = [o for o in items if piece.strip() == o["sent_full"]]
                if not hits:
                    continue
                cur, ops = piece, []
                for o in hits:            # 同一句里多个旧号 → 依次改, 不许只改第一个
                    cur, more = rewrite_sentence(cur, o["old"], o["new"],
                                                 o.get("old_name", ""), o.get("new_name", ""),
                                                 o["class"])
                    ops += more
                used[i] = (piece, cur, ops, hits)
            if not used:
                edits.append({"pid": pid, "path": path,
                              "fail": "句子在现文里定位不到 (库在扫描后变过?) → 不动"})
                continue
            half = [op for v in used.values() for op in v[2] if op[0] == "名-失败"]
            if half:
                # T 类里旧书名没定位到 = 只改号会留下"新号配旧名"的错引 → **整处不写**, 交人工
                edits.append({"pid": pid, "path": path, "sec": sec,
                              "fail": "旧书名没逐字定位到 → 半状态风险, 这一处不写: "
                                      + "; ".join(h[1] for h in half)[:80]})
                continue
            new_text = "".join(used[i][1] if i in used else p for i, p in enumerate(pieces))
            n_ops = sum(len(v[2]) for v in used.values())
            if new_text == text:
                edits.append({"pid": pid, "path": path, "sec": sec,
                              "note": "幂等: 已是目标值, 不改", "ops":
                              [op for v in used.values() for op in v[2]]})
                continue
            before.setdefault(pid, {})[sec] = text
            edits.append({"pid": pid, "path": path, "sec": sec,
                          "n_sent": len(used), "n_ops": n_ops,
                          "ops": [op for v in used.values() for op in v[2]],
                          "old": text, "new": new_text})
            if not dry:
                d["section_states"][sec]["text"] = new_text
                conn.execute("UPDATE project SET data=? WHERE id=?",
                             (json.dumps(d, ensure_ascii=False), pid))
        if not dry:
            conn.commit()
    finally:
        conn.close()
    return edits, before, rejected


def rollback(path_json):
    """按写前原值整段还原 (逐节 text 换回, 不猜、不合并)"""
    snap = json.loads(Path(path_json).read_text(encoding="utf-8"))
    conn = sqlite3.connect(DB_PATH)
    n = 0
    for pid, secs in (snap.get("before") or {}).items():
        row = conn.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
        if not row or not row[0]:
            print(f"  ⚠ 项目 {pid} 取不到 → 跳过")
            continue
        d = json.loads(row[0])
        for sec, text in secs.items():
            if (d.get("section_states") or {}).get(sec) is not None:
                d["section_states"][sec]["text"] = text
                n += 1
        conn.execute("UPDATE project SET data=? WHERE id=?",
                     (json.dumps(d, ensure_ascii=False), pid))
    conn.commit()
    conn.close()
    print(f"✅ 已还原 {n} 节 (来源 {path_json})")
    return 0


def verify_written(edits):
    """写完**另开只读连接**逐节读回: 旧号在该节 0 命中、新号 ≥1 命中、文本等于预期的那句。

    判据与 scan 同一把 (`_nsd(code) in _nsd(text)`), 否则"改完了"和"扫得出来"是两套口径 ——
    那正是本项目在破折号上栽过的第三个坑。`名-失败` 一律算**半状态**红: 号改了名没改。
    """
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    bad = []
    try:
        for e in edits:
            if "new" not in e:
                continue
            pid, path, sec = e["pid"], e["path"], e["sec"]
            row = conn.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
            text = ((json.loads(row[0]).get("section_states") or {}).get(sec) or {}).get("text") \
                if row and row[0] else None
            if text != e["new"]:
                bad.append(f"  {pid} {path}: 读回与预期不一致 (len {len(text or '')} vs {len(e['new'])})")
                continue
            for act, old_frag, new_frag in e.get("ops", []):
                if act == "号" and _nsd(old_frag) in _nsd(text):
                    bad.append(f"  {pid} {path}: 旧号 {old_frag!r} 读回仍在")
                if act == "号" and _nsd(new_frag) not in _nsd(text):
                    bad.append(f"  {pid} {path}: 新号 {new_frag!r} 读回取不到")
                if act == "名" and _core(new_frag) not in _core(text):
                    bad.append(f"  {pid} {path}: 新书名没落到纸上 (旧名 {old_frag!r})")
                if act == "名-失败":
                    bad.append(f"  {pid} {path}: 半状态 (号改了名没改) —— 这一步本不该写进来")
    finally:
        conn.close()
    return bad


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if "--rollback" in sys.argv:
        return rollback(sys.argv[sys.argv.index("--rollback") + 1])
    apply_mode = "--apply" in sys.argv
    plan_mode = "--plan" in sys.argv          # 演练: 走同一条写入路径, 但连接是只读 URI
    if apply_mode and not ALLOW_WRITE:
        print("⏹ ALLOW_WRITE=False → 拒绝写入 (判类结果要先逐处过目)")
        return 2
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    only = None
    if "--pid" in sys.argv:
        only = sys.argv[sys.argv.index("--pid") + 1]
    occ, tables, notes, targets, dropped = scan(conn, only, "--all" in sys.argv)
    conn.close()

    print(f"候选旧号 {len(targets)} 对 (反向派生: 谁 state=废止 且**另一行**的 replace_of 指到它;"
          f" 键走 norm_code/_base_code, 不看字面)")
    if dropped:
        print(f"  ⓘ 另有 {dropped} 对因**没有一手级名判据**不判 (加 --all 会全落 C 类, 但那是噪声) —— "
              f"本工具只覆盖 #54 这批有 PDF 的族")
    for old_code, _ok, new_code, _nk in targets:
        print(f"  {old_code:22s} → {new_code}")
    print(f"级名表解析: {len(tables)} 张")
    for k, v in tables.items():
        print(f"  {k:12s} {v['src']}  级数={v['n_levels']}  解析={len(v['by_roman'])} 对  "
              f"配对={ {r: '/'.join(n) for r, n in v['by_roman'].items()} }"
              f"{'  (无级别词汇)' if v.get('no_levels') else ''}"
              f"{'  ⚠ 缺档 → 该族命中一律 C' if v.get('partial') else ''}")
    for n in notes:
        print("  ⓘ", n)

    order = {"A": 0, "T": 1, "L": 2, "B": 3, "C": 4, "D": 5}
    occ.sort(key=lambda o: (order[o["class"]], o["pid"], o["path"]))
    cnt = {}
    for o in occ:
        cnt[o["class"]] = cnt.get(o["class"], 0) + 1
    print(f"\n命中 {len(occ)} 处: A(可只改号) {cnt.get('A', 0)} / "
          f"T(号+书名一起改) {cnt.get('T', 0)} / "
          f"L(含级别结论, 要重算) {cnt.get('L', 0)} / "
          f"B(内容属旧版) {cnt.get('B', 0)} / C(交人工) {cnt.get('C', 0)} / D {cnt.get('D', 0)}")
    for o in occ:
        print(f"\n  [{o['class']}] {o['pid']}  {o['path']}")
        print(f"      句: {o['sent']}")
        print(f"      {o['old']} → {o['new']} :: {o['why']}")
        if o["class"] == "T":
            print(f"      新名 (取自目录 name, 落地时去掉『（代替…）』尾巴): {o['new_name']}")
    if not (apply_mode or plan_mode):
        print("\n(只读扫描完成 — 未写任何数据)")
        return 0

    # ---------- 写入分支 (只放行 A/T; L/B/C/D 由 apply_writes 挡住并点名) ----------
    if len(tables) < len(NEW_EDITION_PDFS):
        print(f"\n⏹ 一手级名判据不齐 ({len(tables)}/{len(NEW_EDITION_PDFS)}, RAW={RAW}) "
              "→ 整批不写。读不到判据就不许判'可只改号'。")
        return 2
    writable = [o for o in occ if o["class"] in ("A", "T")]
    if not writable:
        print("\n⏹ 没有 A/T 命中 → 无内容可写 (注意: 0 命中不等于'干净', 先看上面 notes)")
        return 0
    edits, before, rejected = apply_writes(occ, only, dry=plan_mode)
    if plan_mode:
        print(f"\n[演练, 未写库] 可写 {sum(1 for e in edits if 'new' in e)} 处 / "
              f"被挡住 {len(rejected)} 处 (L/B/C/D):")
        for e in edits:
            if e.get("fail") or e.get("note"):
                print("  ⓘ", e["pid"], e["path"], e.get("fail") or e.get("note"))
                continue
            print(f"  · {e['pid']} {e['path']}  {e.get('n_sent')} 句 / {e.get('n_ops')} 动作")
            for act, old_frag, new_frag in e["ops"]:
                print(f"      [{act}] {old_frag!r} → {new_frag!r}")
        return 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = DATA_DIR / f"rewrite_stale_citations_before_{stamp}.json"
    out.write_text(json.dumps({"before": before,
                               "n_sec": sum(len(v) for v in before.values()),
                               "edits": [{"pid": e["pid"], "path": e["path"],
                                          "ops": e.get("ops"), "fail": e.get("fail"),
                                          "note": e.get("note")}
                                         for e in edits if e.get("ops") or e.get("fail")]},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    n_loc = sum(1 for e in edits if "new" in e)
    n_ops = sum(e.get("n_ops", 0) for e in edits)
    print(f"\n✅ 已写 {n_loc} 处 (动作 {n_ops} 个: 号/名逐片段), 被挡住 {len(rejected)} 处 "
          f"(L/B/C/D 不写), 原值 {out} (★ 出库外留一份)")
    for e in edits:
        if e.get("fail") or e.get("note"):
            print("  ⓘ", e["pid"], e["path"], e.get("fail") or e.get("note"))
        else:
            print(f"  · {e['pid']} {e['path']}  {e['n_sent']} 句 / {e['n_ops']} 动作")
            for act, old_frag, new_frag in e["ops"]:
                print(f"      [{act}] {old_frag!r} → {new_frag!r}")
    bad = verify_written(edits)
    if bad:
        print("❌ 读回不合格:")
        for x in bad:
            print(x)
        print(f"   还原: .venv/bin/python {Path(__file__).name} --rollback {out}")
        return 1
    print("  ✓ 读回 (另开只读连接): 每处旧号 0 命中 / 新号在位 / 文本与预期逐字一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
