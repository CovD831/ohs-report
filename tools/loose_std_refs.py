#!/usr/bin/env python3
"""成品里"标准引用形态"的枚举器 (任务 #44 取证, 全程只读)

它只回答一个问题, 而且只用**数**回答: 印在纸上的标准引用里, 有哪些**形状**是现有尺子
(`STD_REF_PAT` = 改写器 / web.std_guard / V-05 共用的那把、`STD_COLLECT_PAT` =
tools/test_std_existence 的存在性采集尺、`_YEAR_TAIL` = standard_version 的年份尾巴)
**结构上看不见**的。

为什么现有尺子看不见 (形状层面的根因, 不是"某个号对不对"; 下面括号里是**举例的形状**, 
  不是判据清单 —— 判据一律写在代码的语法零件里, 由 tools/test_loose_std_refs.py 的味道尺子盯着):
  1. 两把尺子都把"连接号 + 4 位年"写成**必选项** → 无年份引用 (规范里等于"引用最新版") 一条都不采
     (形如 `GBZ 2.1`、`GBZ/T 196` 这种写在正文里的号, 采集尺与改写尺同时看不见);
  2. 两把尺子都是**大写硬匹配** (族名单来自 `fam_alt()`, 没有 `re.I`) → 小写前缀整条看不见
     (如 `gbz/t 192.1-2007` 混在一整段大写号里时, 只有它逃过对表);
  3. `STD_SEPS` 只收了 9 个连字符 (Unicode Pd/Sm), **不含波浪号类**, 也不认"号↔年之间只留空白"
     (如 `GBZ1～2013`、`GBZ 2.1 2019`) → 这类写法落网外;
  4. `_YEAR_TAIL` 只认 19xx/20xx → 两位年 / 别的四位年剥不出身份键 (如 `-94`、`-9999`);
  5. 号里出现**并列分部** (一个 token 写两个分部, 如 `GBZ/T 192.1/.2`) 或**占位分部** (分部位置是
     字母, 如 `GBZ/T 192.x`) 时, 两把尺子的数字段 `\d+(?:\.\d+)?` 都在该处断裂 → 整条不采
     (`_base_code` 的注释早已承认这类"认不出");
  6. 号被**断点**劈开时, 只有劈在数字/年份内部 (`GBZ 2.` + 换行 + `1-2019`) 才两把尺子都看不见 ——
     劈在族与数字、号与年之间时它们的 `\s*` 恰好能跨过换行。这一条是**夹具测出来的**, 不是先验的:
     第一版把"断字"整体当成盲区, 探针一喂就红了。

本工具**不修任何尺子**, 只做: 枚举 + 分母 + 与现有尺子的交叉对表 (网外 N 条 / 网内 M 条)。

分母铁律 (本项目踩过的坑, 写死在本文件的输出里):
  数出 0 之前必须先看见分母。上一轮调研里 `grep -o "2\.2[-—]2019"` 把全角破折号塞进方括号字符类,
  **静默数出 0**, 差点被当成"成品里没有这种写法"。所以:
    (a) 连接符**逐字符分开数** (全角 U+2014 与半角 U+002D 是两个键, 永不合并);
    (b) 判定一律走 `is_conn()/is_std_sep()` 的**集合成员测试** —— 不把多字节字符插进 `[…]` 字符类,
        那正是上轮事故的形状 (Python 的 str 正则能扛, grep/POSIX 不能; 尺子不该依赖宿主环境);
    (c) 每一类计数都配触发分母 (号形命中数 / 块数 / 字符数 / 实测连接符集 / 相邻块对数);
    (d) 任何一个 0 都打印 `zero_note`, 说清是"真没有"还是"尺子断了";
    (e) 每次运行先跑 `self_probe()`: 用**从语法零件自身派生**的合成探针逐类证明"这类只要出现就抓得到"
        —— 探针红 = 尺子断了, 与成品里有没有这种写法无关 (本文件开发过程中它就靠这条抓到一次
        "忘了加 re.I → 小写类整类静默归 0")。

输入: `knowledge.paths.DATA_DIR` 下所有 `report_*.docx` —— 取字复用
`tools.export_fingerprint.texts_from_docx`, 它返回 **3** 个值 (段落文本 / 逐格文本 / 每表行数),
按 2 个解包会当场 ValueError + 项目侧 `project.data` 文本 (只读连接 `knowledge.oel.connect_ro`)。

用法:
  .venv/bin/python tools/loose_std_refs.py                # 全量枚举 + 交叉对表
  .venv/bin/python tools/loose_std_refs.py --proposal     # 只打判据提案 (结构化, 不进产品代码)
  .venv/bin/python tools/loose_std_refs.py --json         # 机器可读汇总 (给守卫/台账用)
  .venv/bin/python tools/loose_std_refs.py --no-project   # 只扫成品

判据提案 (第 3 项交付物; P1..P5 是**建议**, 本文件不动产品代码, 落地另开任务)。
派生方式 = 从 `knowledge/standard_version.py` 已有的号语法取四件零件: 族前缀 (`fam_alt`) +
可选卷/分部 (`\d+(?:\.\d+)*`) + 连接符类 (`STD_SEPS` 的**结构化超集**) + 可选年 (`(?:19|20)\d{2}`),
按"**放宽采集、不放宽判定**"叠成一把宽松尺:

  P1 年份从必选改可选, 但**分两层**: 无年份形态先落"待定版"桶, 交给已有的
     `standard_version.resolve_current()` 按官方层定版 → 定不上版本的那部分才是缺陷。
     能力边界: 它仍不回答"这个号存不存在" —— 存在性归 tools/test_std_existence.py。
  P2 连接符类 = `STD_SEPS` ∪ {语料里实测出现在"号↔年"槽位上的字符}; 成员只由
     **Unicode 类别 (Pd/Sm/Sk/Pc) + 槽位约束**派生, 并逐字符记账。
     能力边界: "号↔年之间只留空白"要单独成一条 (它不属于任何连字符类); 数值区间里的波浪号
     (温度 22～28 ℃ 那类) 不在槽位上, 不算标准号连接符 —— 旧尺子没有这个区分, 靠槽位约束才有。
  P3 尺子加 `re.I` **只放宽采集**; 大小写本身仍单独点名 (归一后并成一个号 ≠ 书写合规)。
     能力边界: 并形之后它不看"这个号被印成了另一个真号"(把工程规范印成卫生标准前缀那类)
     —— 那是族一致性/存在性判断, 归 tools/test_std_existence.py 的正向点名。
  P4 系列总称/占位/并列: 判据 = **官方层结构** (库里同族只有带分部的行、正文写族根)
     ∪ 号内出现占位分部 ∪ 号内出现并列斜杠 ∪ 尾部还是分部号 (区间写法)。
     全部读 `standard_db.code` 经 `_base_code()/_family()` 派生, 不列"哪些号算总称"。
     能力边界: 库里连该族的一行都没有时 (授权族以外的行业号) P4 判不出总称 —— 这一段由
     tools/test_std_existence.py 的 M7 盲区探测 (`STD_LOOSE_PAT`) 补, 但它**要带年份才响**。
  P5 断字: 尺子必须跑在**文档顺序的块序列**上 (段落流 + 表格逐格), 而不是把整篇拼成一根字符串 ——
     拼接会让"任意两块之间"都像号内换行 (假阳), 反过来也会把真断字拼掉 (假阴)。
     能力边界: `texts_from_docx` 只取正文段落与表格单元格 → 页眉/页脚/脚注/尾注/文本框/图片化正文
     它一个字都看不见; run 级 (`</w:t>` 之间) 的断裂在 python-docx 里已被合并, 同样看不见。
     那两类的分母在 tools/export_fingerprint.py 的 n_paras/n_tables 与 tools/audit_scanned.py。

每条 P 都写"仍看不见哪一类、由谁补", 是因为本项目的规矩是**绿灯要说清覆盖范围**:
把"号对不对"的绿当成"引用形态都合规"的绿, 正是 #44 要拆掉的错觉。

⚠ 只读取证: 不写库、不导 docx、不进 GUARD_TESTS (它的结论随本地成品数量漂, 放进部署链等于让部署
  成败取决于本机缓存了哪几份成品 —— 与 tools/gate_coverage.py 同一类理由)。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

# 路径一律从 knowledge.paths 取 (认 OHS_DATA_DIR); 自己拼 ROOT/'data' 会被 tools/chain_audit.py 点名
from knowledge.paths import DATA_DIR                                     # noqa: E402
from knowledge.oel import connect_ro                                     # noqa: E402  只读连接
from knowledge.standard_version import (                                 # noqa: E402  尺子单一产地
    STD_COLLECT_FAMS, STD_COLLECT_PAT, STD_REF_PAT, STD_SEPS, _base_code, _family, fam_alt)
from tools.export_fingerprint import texts_from_docx                     # noqa: E402

# ── 零件: 一律派生, 不抄第二份 ────────────────────────────────────────────────
# ⚠ 族分支必须整体括住: 裸 `GBZ|GB|WS` 会把 `|` 右边的支劈成"无约束前缀"(standard_version 踩过)。
CONN_CATS = ("Pd", "Sm", "Sk")        # 候选连接符 = Unicode 类别派生。Po 刻意排除 (顿号/句号/斜杠是散文),
                                      # Pc 也排除: `_` 是标识符续字符, 项目侧 JSON 键名 (gbz1_rule) 会伪装成号
SEP_SET = set(STD_SEPS)              # 现有尺子的连接符集 (逐字符比, 不做字符类相等)
CJK_BLOCKS = "\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
PLACEHOLDER_CLS = r"[^\s0-9" + CJK_BLOCKS + r"]"     # 分部位置上的"非数字、非空白、非汉字" = 占位
NUM_PARTS = r"\d+(?:\.\d+)*"                         # 族号 + 可选卷/分部
NUM_PARALLEL = r"(?:/\d*(?:\.\d+)?)*"                # 并列分部 (一个 token 写两个分部)
NUM_PLACEHOLDER = NUM_PARTS + r"\." + PLACEHOLDER_CLS
NUM = r"(?:" + NUM_PLACEHOLDER + r"|" + NUM_PARTS + NUM_PARALLEL + r")"
YEAR = r"(?:19|20)\d{2}"
# 号形头: 族 + 可选推荐性标记 + 号。⚠ `re.I` 是**刻意的**: 两把旧尺子都是大写硬匹配,
#   小写形态它们整条看不见; 本枚举器要看得见它们, 但"看得见"≠"合规" → 大小写仍单独成一类。
# ⚠ 号后紧跟 `_` 的不算: 那是标识符续字符 (库表名/JSON 键), 不是印在纸上的引用。
HEAD = re.compile(r"(?<![0-9A-Za-z])(" + fam_alt(STD_COLLECT_FAMS) + r")"
                  r"(?:\s*/?\s*[TZ])?\s*(?P<num>" + NUM + r")(?![0-9A-Za-z_])", re.I)
TAIL_DIGITS = re.compile(r"\d{1,4}(?:\.\d+)*")
BLANKS = " \t\r\n\u3000"


def is_conn(ch: str) -> bool:
    """ch 可能是"号↔年"的连接符吗 —— 按 Unicode 类别判, 不写字符类 (上轮事故的形状)。"""
    return bool(ch) and unicodedata.category(ch) in CONN_CATS


def is_std_sep(ch: str) -> bool:
    """ch 是否被**现有**尺子的 STD_SEPS 收着 (集合成员测试, 一个字符一个字符比)。"""
    return ch in SEP_SET


def witness(validator, start: int, stop: int) -> str:
    """在码位区间里找一个**满足判据**的字符当探针见证 —— 探针字符由判据派生, 不写死。"""
    return next((chr(c) for c in range(start, stop) if validator(chr(c))), "")


def placeholder_witness() -> str:
    return witness(lambda c: bool(re.fullmatch(PLACEHOLDER_CLS, c)), 0x41, 0x5B)


def off_class_conn_witness() -> str:
    """候选连接符类里、STD_SEPS 没收的那个字符。只在"连字符/波浪号码位段"找见证:
    全码位扫会挑出 '+' (它也是 Sm) —— 合法见证, 但不是可信探针形状。"""
    return (witness(lambda c: is_conn(c) and not is_std_sep(c), 0x7E, 0x2600)
            or witness(lambda c: is_conn(c) and not is_std_sep(c), 0xFF00, 0xFFF0))


# ── 类别 ──────────────────────────────────────────────────────────────────────
CATS = ("break", "case", "series", "sep", "no_year", "other", "in_grammar")
# 主因桶的取法: 书写缺陷 (大小写/总称占位/分隔符) 优先于"无年份"—— 后者按规范是合法写法
#   (等于最新版), 混在一起会把缺陷稀释成"规范写法"。
# ⚠ "断字"不在这里: 它是**块边界**上的性质, 同一个号可以既"在册"又"跨断点" → 单独记账,
#   否则"主因相加 = 命中总数"这条内部一致性就没了 (第一版就把一条号数过两次)。
PRIMARY_ORDER = ("case", "series", "sep", "no_year", "other")
LABEL = {
    "break": "词内断字",
    "case": "大小写不规范",
    "series": "系列总称或占位",
    "sep": "分隔符变体",
    "no_year": "无年份",
    "other": "其他",
    "in_grammar": "形态在册(两把尺子都认)",
}
BOUNDARY = {          # 说明文本 (dict 的值不参与判定): 每一类"这一把尺子仍然看不见什么、由谁补"
    "break": "只有'劈在数字/年份内部'的断字才掉在两把尺子之外: 它们的 `\s*` 恰好能跨过换行 "
             "(族↔数字、号↔年之间的断点因此**不是盲区**); run 级断裂 (</w:t> 之间) 与页眉/页脚/"
             "脚注/文本框/图片化正文 texts_from_docx 一个字都取不到, 表块与段块之间的块序也已丢失",
    "case": "并形之后它不回答'这个号到底存不存在' —— 存在性归 tools/test_std_existence.py",
    "series": "库里没有该族任何行时判不出总称 (授权族以外的行业号) —— 由 test_std_existence 的 M7 "
              "盲区探测补, 但它要求带年份才响",
    "sep": "号与年之间**什么符号都没有**且尾数不是四位年形态时本类不响 (落进'无年份'); "
           "数值区间里的波浪号 (22～28 那类) 不在号↔年槽位上, 不算连接符",
    "no_year": "无年份 ≠ 错: 规范里它等于'最新版'。该写哪一版由 standard_version.resolve_current 定, "
               "本类只负责让'号对不对'的绿灯不再顺手覆盖它",
    "other": "年份位数/年份被吞进分部号这类只登记形状, 不判合法性",
    "in_grammar": "在册只代表'尺子看得见', 不代表号真实存在、不代表版本现行 —— 那两问各有主尺子",
}


# ── 取字 ──────────────────────────────────────────────────────────────────────
def docx_sources(data_dir: Path = DATA_DIR, pattern: str = "report_*.docx") -> list:
    """每份成品 → (文件名, 块文本列表)。texts_from_docx 返回 **3** 个值, 这里三个都接住。"""
    out = []
    for p in sorted(Path(data_dir).glob(pattern)):
        paras, cells, _rows_per_table = texts_from_docx(p)
        out.append((p.name, list(paras) + [c[3] for c in cells]))
    return out


def project_sources(conn) -> list:
    """项目侧 project.data 原样文本 (含 JSON 转义), 每个项目一块。"""
    return [(f"project:{pid}", [data or ""])
            for pid, data in conn.execute("SELECT id, data FROM project ORDER BY id")]


def official_backing(conn) -> tuple:
    """(standard_db 身份键集, 库里只有分部版的族根集, 行数)。
    剥年份/剥推荐性标记用 standard_version 自己的 `_base_code/_family` —— 不在这里另写一条正则。"""
    codes = [r[0] for r in conn.execute("SELECT code FROM standard_db") if r[0]]
    bases = {_base_code(c) for c in codes}
    part_parents = {_family(b) for b in bases if "." in b}
    return bases, {p for p in part_parents if p not in bases}, len(codes)


def coverage(text: str, pat) -> list:
    """pat 在 text 上的覆盖前缀和 → `covered()` 变 O(1)。
    为什么不用 span 列表逐个比: 一次运行 3.4 万命中 × 2.8 万 span = 亿级比较, 尺子比判据还慢。"""
    cov = bytearray(len(text))
    for m in pat.finditer(text):
        cov[m.start():m.end()] = b"\x01" * (m.end() - m.start())
    pref = [0] * (len(text) + 1)
    for i, b in enumerate(cov):
        pref[i + 1] = pref[i] + b
    return pref


def covered(pref: list, s: int, e: int) -> bool:
    return e > s and pref[e] - pref[s] == e - s


# ── 判定 ──────────────────────────────────────────────────────────────────────
def tail_slot(text: str, pos: int) -> tuple:
    """号形 token 之后 → (连接符, 尾数, 中间空白, 尾数起点)。
    只认两种槽位: '候选连接符 + 数字' 或 '纯空白 + 四位年'。
    空白后跟裸数字 (如 `GBZ/T 205 4.1.8`) 是**条款号**, 不算年份 —— 按无年份处理。
    尾数起点用于'断点是不是落在号↔年之间'的判定 (P5)。"""
    i = pos
    while i < len(text) and text[i] in BLANKS:
        i += 1
    gap = text[pos:i]
    sep = ""
    while len(sep) < 2 and i + len(sep) < len(text) and is_conn(text[i + len(sep)]):
        sep += text[i + len(sep)]
    j = i + len(sep)
    while j < len(text) and text[j] in " \t\u3000":
        j += 1
    m = TAIL_DIGITS.match(text, j)
    if not m:
        return sep, "", gap, j
    tail = m.group(0)
    if not sep and not re.fullmatch(YEAR, tail):
        return "", "", gap, j                     # 无连接符 + 尾数不是年份形态 → 条款号 → 按无年份算
    return sep, tail, gap, j


def classify(fam_part: str, num: str, sep: str, tail: str, gap: str,
             base: str, general: set) -> tuple:
    """一次号形命中 → (原因列表, 子形状列表)。原因**可叠加**; 主因由 PRIMARY_ORDER 取。
    判据全部是形态/结构测试, 没有任何"已知写法清单"。"""
    reasons: list = []
    subs: list = []
    if fam_part.upper() != fam_part:
        reasons.append("case")
        subs.append("前缀含小写字母")
    if re.search(r"\." + PLACEHOLDER_CLS, num):
        reasons.append("series")
        subs.append("分部位置是占位符")
    if "/" in num:
        reasons.append("series")
        subs.append("一个 token 并列多个分部")
    if base and base in general:
        reasons.append("series")
        subs.append("库里只有分部版而正文写族根")
    if tail:
        if not re.fullmatch(YEAR, tail):
            if "." in tail:
                reasons.append("series")
                subs.append("尾部还是分部号 (区间写法)")
            else:
                reasons.append("other")
                subs.append(f"尾数 {len(tail)} 位, 不是 19xx/20xx 年份形态")
        if sep and not all(is_std_sep(ch) for ch in sep):
            reasons.append("sep")
            for ch in sep:
                if not is_std_sep(ch):
                    subs.append(f"连接符 U+{ord(ch):04X} 不在 STD_SEPS")
        elif not sep and gap:
            reasons.append("sep")
            subs.append("号与年之间只有空白")
    else:
        reasons.append("no_year")
        subs.append("无年份")
    if re.search(r"\.\d{3,}", num):
        reasons.append("other")
        subs.append("年份被吞进分部号")
    return (reasons or ["in_grammar"]), subs


def primary_of(reasons: list) -> str:
    for cat in PRIMARY_ORDER:
        if cat in reasons:
            return cat
    return "in_grammar"


# ── 断字 (P5): 断点可以落在一个号的任何位置 —— 块内软换行 (`<w:br>`) 或相邻两块的边界 ──
WIN = 24                                   # 断点两侧各取多少字符当窗口
_PARTIAL = re.compile(r"(?i)(?<![0-9A-Za-z])(" + fam_alt(STD_COLLECT_FAMS) + r")"
                      r"(?:\s*/?\s*[TZ])?\s*\d*(?:\.\d*)*\Z")     # 结尾正好停在"半截号"上 (允许写到一半的分部)


def _strip_conn_tail(s: str, limit: int = 2) -> str:
    """剥掉结尾的候选连接符 (最多 limit 个) —— 连接符留在左块、数字跑到右块就是'号↔年'断点。"""
    i, left = len(s), limit
    while i > 0 and left > 0 and is_conn(s[i - 1]):
        i -= 1
        left -= 1
    return s[:i]


def formed_codes(text: str) -> set:
    """文本里**成形**的号 (身份键+年)。'年份被吞进分部号' (如 2.12019) 也算成形, 否则跨行的
    那种吞年写法会被漏掉。"""
    out = set()
    for m in HEAD.finditer(text):
        _sep, tail, _gap, _at = tail_slot(text, m.end())
        key = _base_code(text[m.start():m.end()])
        if re.fullmatch(YEAR, tail):
            out.add(key + "-" + tail)
        elif re.search(r"\.\d{3,}", m.group("num")):
            out.add(key + "-吞年")
    return out


def break_at(left: str, right: str) -> dict:
    """一处断点是否把某个号劈成两半。判据 = **同一把尺子**, 不是猜"哪里像断字":
      ① 左块结尾是半截号 (剥掉尾连接符后匹配 `_PARTIAL`);
      ② 右块开头是数字;
      ③ 拼掉断点后**多出一个成形的号** (`formed_codes(a+b) − a − b`) —— 说明这个号本来就是一整块。
    再单独回答"现有尺子跨不跨得过这个断点": 两把尺子在号↔年、族↔数字之间都写了 `\s*`,
    而 `\s` 含换行 → 这两类断点它们**看得见**; 只有劈在数字/年份内部的才掉在网外。"""
    a, b = left[-WIN:], right[:WIN]
    if not a or not b or not _break_case(left, right) or not _PARTIAL.search(_strip_conn_tail(a)):
        return {}
    new = formed_codes(a + b.lstrip(BLANKS)) - formed_codes(a) - formed_codes(b)
    if not new:
        return {}
    last = a[-1]
    sub = ("数字/年份内部跨断点" if (last.isdigit() or last == ".")
           else "号与年之间跨断点" if is_conn(last) else "族前缀与数字之间跨断点")
    window = a + "\n" + b
    cut = len(a)
    in_collect = any(m.start() < cut < m.end() for m in STD_COLLECT_PAT.finditer(window))
    in_ref = any(m.start() < cut < m.end() for m in STD_REF_PAT.finditer(window))
    return {"sub": sub, "net": in_collect or in_ref, "ctx": window.replace("\n", "⏎"),
            "token": sorted(new)[0]}


def _one_hit(name: str, text: str, m, bases: set, general: set, pref_c: list, pref_r: list,
             conns: Counter, sub: Counter, trigger: Counter) -> dict:
    """一处号形命中 → 记账 + 分类, 返回一条 hit (带原文上下文, 打印时看得见字)。"""
    trigger["head"] += 1
    fam_part = m.group(0)[:m.start("num") - m.start()]      # 前缀+标记 (切片要减掉命中起点)
    sep, tail, gap, _at = tail_slot(text, m.end())
    for ch in sep:
        conns[ch] += 1
        if not is_std_sep(ch):
            trigger["conn_off_class"] += 1
    base = _base_code(re.sub(r"\s+", "", fam_part + m.group("num")))
    reasons, subs = classify(fam_part, m.group("num"), sep, tail, gap, base, general)
    if base and base not in bases and base not in general:
        # 只登记"官方层连身份键都没有", 不把它当成形态缺陷 —— 号存不存在归 test_std_existence
        subs.append("官方层查无此身份键 (存在性另有主尺子)")
    for s in subs:
        sub[s] += 1
    return {"src": name, "start": m.start(), "end": m.end(), "raw": m.group(0),
            "ctx": text[max(0, m.start() - 28):m.end() + 14],
            "reasons": reasons, "primary": primary_of(reasons),
            "collect": covered(pref_c, m.start(), m.end()),
            "ref": covered(pref_r, m.start(), m.end()), "sep": sep, "tail": tail, "base": base}


def scan(sources: list, bases: set, general: set) -> dict:
    """核心。sources = [(名字, 块文本列表)] —— 夹具可以直接构造, 不必碰真库/真成品。
    ⚠ 判据跑在**块**上 (提案 P5): 把整篇拼成一根字符串, 任意两块之间都像号内换行 (假阳),
      真断字又会被拼掉 (假阴) —— 所以块内跑一遍, 相邻块的**边界窗口**再跑一遍。"""
    hits: list = []
    trigger: Counter = Counter()
    conns: Counter = Counter()
    denom: Counter = Counter()
    sub: Counter = Counter()
    breaks = {"total": 0, "in_net": 0, "sub": Counter(), "samples": []}
    for name, blocks in sources:
        denom["sources"] += 1
        denom["blocks"] += len(blocks)
        denom["adjacent_pairs"] += max(len(blocks) - 1, 0)
        for block in blocks:
            denom["chars"] += len(block)
            found = list(HEAD.finditer(block))
            if found:                        # 绝大多数单元格没有号 → 不为它建两张覆盖表
                pref_c = coverage(block, STD_COLLECT_PAT)
                pref_r = coverage(block, STD_REF_PAT)
                for m in found:
                    hits.append(_one_hit(name, block, m, bases, general, pref_c, pref_r,
                                         conns, sub, trigger))
            pos = block.find("\n")
            while pos >= 0:                  # 块内软换行 (`<w:br>`) 也是一处断点
                denom["breaks_checked"] += 1
                _add_break(break_at(block[:pos], block[pos + 1:]), name, "块内软换行",
                           breaks, sub)
                pos = block.find("\n", pos + 1)
        for a, b in zip(blocks, blocks[1:]):  # 相邻两块的边界
            denom["breaks_checked"] += 1
            _add_break(break_at(a, b), name, "相邻块边界", breaks, sub)
    return {"denom": dict(denom), "trigger": dict(trigger), "conns": dict(conns), "sub": dict(sub),
            "breaks": {"total": breaks["total"], "in_net": breaks["in_net"],
                       "out_net": breaks["total"] - breaks["in_net"], "sub": dict(breaks["sub"]),
                       "samples": breaks["samples"][:6]},
            "primary": dict(Counter(h["primary"] for h in hits)),
            "flags": dict(Counter(r for h in hits for r in h["reasons"])),
            "hits": hits,
            "net_in": sum(1 for h in hits if h["collect"] or h["ref"]),
            "net_in_collect": sum(1 for h in hits if h["collect"]),
            "net_in_ref": sum(1 for h in hits if h["ref"]),
            "net_out": sum(1 for h in hits if not (h["collect"] or h["ref"])),
            "cat_net": {c: (sum(1 for h in hits if h["primary"] == c and (h["collect"] or h["ref"])),
                            sum(1 for h in hits if h["primary"] == c and not (h["collect"] or h["ref"])))
                        for c in CATS}}


def _add_break(rec: dict, name: str, where: str, breaks: dict, sub: Counter) -> None:
    """break_at 的结论入账 (没劈开任何东西时 rec 是空 dict)。"""
    if not rec:
        return
    breaks["total"] += 1
    breaks["in_net"] += 1 if rec["net"] else 0
    breaks["sub"][rec["sub"]] += 1
    sub["断字·" + rec["sub"]] += 1
    if len(breaks["samples"]) < 6:
        breaks["samples"].append((name, where, rec["token"], rec["ctx"], rec["net"]))


# ── 探针: 尺子自证能红 ────────────────────────────────────────────────────────
def _break_case(left: str, right: str) -> bool:
    """断点右侧去掉前导空白后必须以数字开头 (否则右半截根本不是一段号数)。"""
    b = right.lstrip(BLANKS)
    return bool(b) and b[0].isdigit()


def probe_cases(bases: set, general: set) -> dict:
    """每类一组探针**用例**; 一条用例 = 一组块 (两块以上 = 相邻块边界, 单块里的 \\n = 软换行)。"""
    fam = max(STD_COLLECT_FAMS, key=len)              # 名单里最长的族根 (派生, 不是字面量)
    num, yr, short, ph = "9.9", "1999", "99", placeholder_witness()
    ch = off_class_conn_witness()
    probes = {
        "no_year": [[f"按 {fam} {num} 执行。"]],
        "case": [[f"按 {fam.lower()} {num}-{yr} 执行。"], [f"按 {fam.lower()}/t {num}-{yr} 执行。"]],
        "series": [[f"按 {fam} {num}.{ph} 执行。"], [f"按 {fam} {num}-{num} 执行。"],
                   [f"按 {fam} 9.1/9.2-{yr} 执行。"]],
        "sep": ([[f"按 {fam} {num}{ch}{yr} 执行。"]] if ch else []) + [[f"按 {fam} {num} {yr} 执行。"]],
        "break": [[f"按 {fam} {num}.\n{num[0]}-{yr} 执行。"],      # 劈在数字内部 → 网外
                  [f"按 {fam} {num}—", f"{yr} 执行。"],            # 号与年之间 → 旧尺子的 \s* 跨得过
                  [f"按 {fam}", f" {num}-{yr} 执行。"]],           # 族前缀与数字之间 → 同上
        "other": [[f"按 {fam} {num}-{short} 执行。"], [f"按 {fam} {num}-9999 执行。"],
                  [f"按 {fam} {num}.{yr} 执行。"]],
    }
    # 总称探针: 族根**从库里挑** (数据派生) —— 命中"库里只有分部版而正文写族根"这一支
    for root in sorted(general):
        for f in sorted(STD_COLLECT_FAMS, key=len, reverse=True):
            if root.startswith(f):
                probes["series"].append([f"按 {f} {root[len(f):]} 执行。"])
                break
        else:
            continue
        break
    return probes


def self_probe(bases: set, general: set) -> dict:
    """探针逐类必须被归到它自己的桶 → 全 True 才说明尺子活着。返回 {类: 是否抓到}。
    断字看 `breaks.total` (它不入主因桶), 其余看 `primary`。"""
    got = {}
    for cat, cases in probe_cases(bases, general).items():
        ok = False
        for blocks in cases:
            st = scan([(f"probe:{cat}", blocks)], bases, general)
            ok = ok or (st["breaks"]["total"] > 0 if cat == "break" else bool(st["primary"].get(cat)))
        got[cat] = ok
    return got


# ── 输出 ──────────────────────────────────────────────────────────────────────
def samples(stats: dict, per_cat: int = 3) -> dict:
    """每类挑几条, 带原文那一段 —— 让人看见字, 不是只看计数。"""
    out: dict = defaultdict(list)
    for h in stats["hits"]:
        if len(out[h["primary"]]) < per_cat:
            out[h["primary"]].append((h["src"], h["raw"].strip(), h["ctx"].replace("\n", "⏎")))
    return dict(out)


def zero_notes(stats: dict) -> list:
    """分母铁律落地: 任何一类数出 0, 当场给出"是分母不对还是真没有"。"""
    out = []
    d, t, conns = stats["denom"], stats["trigger"], stats["conns"]
    brk = stats["breaks"]
    for cat in CATS:
        if cat == "in_grammar" or (brk["total"] if cat == "break" else stats["primary"].get(cat, 0)):
            continue
        line = (f"  {LABEL[cat]} = 0 | 分母: 号形命中 {t.get('head', 0):,} 条 · "
                f"块 {d.get('blocks', 0):,} 个 · 字符 {d.get('chars', 0):,}")
        if cat == "sep":
            off = sorted(ch for ch in conns if not is_std_sep(ch))
            observed = {f"U+{ord(c):04X}": n for c, n in conns.items()}
            line += f" · 实测连接符 {observed or '无'}"
            line += (" → 观测到的连接符**全部落在 STD_SEPS 内** = 真没有 (探针能红, 见 0 节)" if not off
                     else f" → 观测到 off-class 字符 {[f'U+{ord(c):04X}' for c in off]} 却没归类 = **尺子断了**")
        elif cat == "break":
            line += (f" · 检查过的断点 {d.get('breaks_checked', 0):,} 处 (块内软换行 + 相邻块边界)"
                     f" · 相邻块对 {d.get('adjacent_pairs', 0):,} 对"
                     f" → 分母不为 0 而断字为 0 = 真没有 (run 级断裂本工具看不见, 见第 7 节)")
        elif stats["flags"].get(cat, 0):
            line += (f" · 该类叠加原因计数 {stats['flags'][cat]:,} > 0 → 主因被更靠前的类吃掉了, "
                     f"**看第 2 节, 别当成 0**")
        else:
            line += " · 该类原因计数也是 0 → 真没有"
        out.append(line)
    return out


def render(stats: dict, notes: list, probes: dict, extra: dict) -> None:
    d = stats["denom"]
    print("### 0. 分母 (数之前先看它, 数出 0 之后更要看它)")
    print(f"  成品 docx {extra['n_docx']} 份 · project.data {extra['n_project']} 行 · "
          f"块 {d['blocks']:,} 个 · 字符 {d['chars']:,} (docx {extra['chars_docx']:,} + 项目 {extra['chars_project']:,})")
    print(f"  号形命中 {stats['trigger'].get('head', 0):,} 条 · standard_db {extra['n_db_rows']:,} 行 · "
          f"身份键 {extra['n_bases']:,} 个 · 库里只有分部版的族根 {extra['n_general']:,} 个")
    print("  尺子自证 (合成探针逐类必须能红): " +
          "  ".join(f"{LABEL[k]}={'✓' if v else '✗ 断了'}" for k, v in probes.items()))
    print("\n### 1. 按'为什么尺子看不见'分类")
    print("     (前六行是**主因桶**, 互斥, 相加 = 号形命中总数; 词内断字一行是块边界上的性质, 单独记账)")
    brk = stats["breaks"]
    for cat in CATS:
        if cat == "break":
            n, inside, outside = brk["total"], brk["in_net"], brk["out_net"]
            tag = " (单独记账)"
        else:
            n = stats["primary"].get(cat, 0)
            inside, outside = stats["cat_net"][cat]
            tag = ""
        print(f"  {n:6,}  {LABEL[cat]:<16} 网内 {inside:,} / 网外 {outside:,}{tag}")
    if brk["sub"]:
        for s, n in sorted(brk["sub"].items(), key=lambda x: -x[1]):
            print(f"        断子形状: {s} × {n:,}")
    print("\n### 2. 各原因计数 (一条命中可有多因 → 相加 > 命中总数是正常的)")
    for f, n in sorted(stats["flags"].items(), key=lambda x: -x[1]):
        print(f"  {n:6,}  {LABEL[f]}")
    print("\n### 2b. 子形状 (每类里到底是哪一种写法, 同样只报实测计数)")
    for s, n in sorted(stats["sub"].items(), key=lambda x: -x[1])[:18]:
        print(f"  {n:6,}  {s}")
    print("\n### 3. 命中样例 (原文那一段, 看得见字)")
    for cat in CATS:
        if cat == "break":
            for src, where, token, ctx, net in brk["samples"]:
                print(f"  [{LABEL[cat]}·{'网内' if net else '网外'}] {src[:46]} ({where})")
                print(f"        拼掉断点后是: {token!r}   原文: …{ctx}…")
            continue
        for src, raw, ctx in stats["_samples"].get(cat, []):
            print(f"  [{LABEL[cat]}] {src[:46]}")
            print(f"        号: {raw!r}   原文: …{ctx}…")
    print("\n### 4. 连接符逐字符记账 (全角与半角**分开** —— 混进同一个字符类就是上轮那次静默 0)")
    if stats["conns"]:
        for ch, n in sorted(stats["conns"].items(), key=lambda x: -x[1]):
            tag = "在 STD_SEPS 内" if is_std_sep(ch) else "**不在 STD_SEPS → 两把尺子都看不见**"
            print(f"  {n:6,}  U+{ord(ch):04X} {unicodedata.name(ch, '?')}  {tag}")
    else:
        print(f"  (本批没观测到任何'号↔年'槽位上的连接符 —— 号形命中 {stats['trigger'].get('head', 0):,} 条。"
              f"要么成品真的只用无年份形态, 要么槽位判据断了: 先看第 0 节探针与第 5 节分母)")
    print("\n### 5. 数出 0 的分母核对")
    print("\n".join(notes) if notes else "  本批没有 0 计数类")
    print("\n### 6. 与现有尺子交叉对表 (网内 = 命中落在该尺子的匹配区间里)")
    print(f"  形态命中: 网外 = {stats['net_out']:,} 条 / 网内 = {stats['net_in']:,} 条 "
          f"(合计 {len(stats['hits']):,}) —— 另加断字 {brk['total']:,} 处 (网外 {brk['out_net']:,} / "
          f"网内 {brk['in_net']:,}); 两笔分开是因为断字是**块边界**上的性质, 同一个号可以既在册又跨断点")
    print("     ⚠ '网内' 只代表**这个形状尺子看得见**, 不代表它判对了: 号存不存在归 test_std_existence,")
    print("        版本现不现行归 web.std_guard + standard_db 的 state/replace_of (双判据)。")
    print(f"    · 存在性采集尺 STD_COLLECT_PAT (tools/test_std_existence.py) 覆盖 {stats['net_in_collect']:,} 条")
    print(f"    · 改写/废止尺 STD_REF_PAT (web/std_guard.py + V-05 + 改写器) 覆盖 {stats['net_in_ref']:,} 条")
    print(f"    · std_guard 的**判定动作**扫同样这批成品: 点名 {extra['guard_codes']} 个废止号 / "
          f"{extra['guard_hits']} 处 —— 全在'形态在册'里; 网外那 {stats['net_out']:,} 条它一条够不着")
    print(f"    · 扫描面差异 (诚实记账): test_std_existence 默认只读 --docx /tmp/fp_docx 的现生成成品"
          f" (在盘 {extra['fp_docx_n']} 份), 本工具读 {extra['n_docx']} 份本地成品 → 这一节是**判据级**对表"
          f" (import 同一批编译好的正则, 单一产地), 不是跑批级复现")
    print("\n### 7. 能力边界 (本枚举器仍看不见什么 / 由谁补)")
    for cat in CATS:
        print(f"  {LABEL[cat]:<16} → {BOUNDARY[cat]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposal", action="store_true", help="只打判据提案 (P1..P5 + 能力边界)")
    ap.add_argument("--json", action="store_true", help="机器可读汇总")
    ap.add_argument("--no-project", action="store_true", help="只扫成品")
    ap.add_argument("--no-guard", action="store_true", help="跳过 std_guard 判定动作对表 (省一次建图)")
    ap.add_argument("--data-dir", default=None, help="覆盖成品目录 (默认 DATA_DIR, 认 OHS_DATA_DIR)")
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args(argv)

    if args.proposal:
        print(__doc__)
        print("派生零件 (运行时从单一产地取, 不是抄的第二份):")
        print(f"  族名单   = fam_alt(STD_COLLECT_FAMS) = {fam_alt(STD_COLLECT_FAMS)}")
        print(f"  号段     = {NUM_PARTS}  并列 {NUM_PARALLEL}  占位 {NUM_PLACEHOLDER}")
        print(f"  连接符   = STD_SEPS 的 {len(SEP_SET)} 个字符 ∪ 槽位实测; 类别判据 = {CONN_CATS}")
        print(f"  年       = {YEAR}  (_YEAR_TAIL 只认 19xx/20xx, 位数放宽落在'其他')")
        print(f"  探针见证 = 占位符 {placeholder_witness()!r} / off-class 连接符 "
              f"{off_class_conn_witness()!r} (都由判据在码位段里找见证)")
        print("  主因桶优先序 = " + " > ".join(LABEL[c] for c in PRIMARY_ORDER))
        return 0

    data_dir = Path(args.data_dir) if args.data_dir else DATA_DIR
    sources = docx_sources(data_dir)
    n_docx = len(sources)
    conn = connect_ro()
    try:
        if not args.no_project:
            sources += project_sources(conn)
        bases, general, n_rows = official_backing(conn)
        stats = scan(sources, bases, general)
        guard_codes: set = set()
        guard_hits = 0
        if not args.no_guard:
            from web.std_guard import scan_superseded        # 只读判据, 不改写不建表
            for name, blocks in sources:
                if not name.startswith("report_"):
                    continue
                for it in scan_superseded("\n".join(blocks), conn):
                    guard_codes.add(it["norm"])
                    guard_hits += 1
    finally:
        conn.close()

    probes = self_probe(bases, general)
    stats["_samples"] = samples(stats, args.samples)
    extra = {
        "n_docx": n_docx, "n_project": len(sources) - n_docx,
        "chars_docx": sum(len(x) for n, b in sources if n.startswith("report_") for x in b),
        "chars_project": sum(len(x) for n, b in sources if n.startswith("project:") for x in b),
        "n_db_rows": n_rows, "n_bases": len(bases), "n_general": len(general),
        "guard_codes": len(guard_codes), "guard_hits": guard_hits,
        "fp_docx_n": len(list(Path("/tmp/fp_docx").glob("*.docx"))) if Path("/tmp/fp_docx").is_dir() else 0,
    }
    notes = zero_notes(stats)
    if args.json:
        print(json.dumps({"denom": stats["denom"], "trigger": stats["trigger"], "primary": stats["primary"],
                          "flags": stats["flags"], "sub": stats["sub"], "breaks": stats["breaks"],
                          "conns": {f"U+{ord(k):04X}": v for k, v in stats["conns"].items()},
                          "net_in": stats["net_in"], "net_out": stats["net_out"],
                          "net_in_collect": stats["net_in_collect"], "net_in_ref": stats["net_in_ref"],
                          "probes": probes, "extra": extra, "zero_notes": notes},
                         ensure_ascii=False, indent=2))
        return 0 if all(probes.values()) else 1
    render(stats, notes, probes, extra)
    return 0 if all(probes.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
