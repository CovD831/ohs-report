"""评价依据标准: 无年份号 → 现行版 解析 (通用, 不写死年份)

背景 (2026-10 事故):
    报告"评价依据表"列的是 GBZ/T 196-2025 第2章规范性引用清单, 形式为**无年份号**
    (如 'GBZ 188', 'GBZ/T 196' —— 规范写法: 无年份 = 引用最新版)。
    旧实现把无年份号直接给 LLM, LLM 凭训练记忆**自己补年份**, 补成了废止版:
        GBZ 188   → LLM 写 GBZ 188-2014  ❌ (现行是 GBZ 188-2025)
        GBZ/T 196 → LLM 写 GBZ/T 196-2007 ❌ (现行是 GBZ/T 196-2025)
    而 standard_db 里**已经有**现行版记录 —— 只是从没被用来给 LLM 定版。

修法 (确定性查表, 不靠 LLM):
    对每个无年份标准号, 从 standard_db 取**未废止的最新版本** → 得到"应写版本",
    作为"标准版本基准"喂进 prompt; LLM 照抄而非自补。

用法:
    from knowledge.standard_version import resolve_all_refs
    refs = resolve_all_refs(conn)   # [{ref_code, name, current_code, state}, ...]
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path          # content_hold 的登记表路径 (STD_RENDER_HOLD_FILE) 用它

_RE_YEAR = re.compile(r"(?:19|20)\d{2}")

# ── 标准号"连接号"与引用形态: 单一产地 (2026-10-14) ────────────────────────────
# 为什么收在这里: 仓库里曾各写 **6 份** 标准号归一化/匹配正则
#   (standard_version / validators V-05 / std_guard / table_skeleton / 两个入库脚本),
#   实测已经裂开: 全角"－"改写器认得 → 正文被静默改掉, 但 V-05 不认 → 该报警的不报;
#   库里存的 code 本身就有 'GBZ/T181－2006' 这种全角写法。
#   判据只能有一把尺子, 所以字符集与前缀族都从这里出。
STD_SEPS = "-–—−－‐‑‒―"                       # 半角- / en / em / minus / 全角 / 各种连字符
_SEP_CLS = f"[{STD_SEPS}]"
_WS_RE = re.compile(r"[\s\u3000]+")
_SEP_RE = re.compile(_SEP_CLS)

# 引用形态: GB 30871—2022 / GBZ/T 196-2025 / WS/T 757—2016 / GBZ2.1－2019 / GBZ/T300.100-2018
# ⚠ 不能用 \b 收尾: 中文字符在 \w 内, '2002第' 之间无 \b → 紧跟中文的标准号会漏匹配。
#   改用 (?![0-9A-Za-z]) 只排除字母数字 (中文/标点可跟随)。
# ⚠ 前缀分支必须整体括住, 否则被 | 劈成两段 → 左支无约束只匹配到 "GB" (实测踩过)。
# ⚠ 前缀族**只收 standard_db 能判定的家族** (STD_FAMS, 实测分布: GB/GBZ/WS 及其 /T /Z)。
#   曾扩到 HG/AQ/JB/DB: 库里根本没有这些行业的行 → 每条化工行业标准引用都只能落进
#   "查不到"分支 = 凭空造出 4 条假告警 (实测 ada/c8c7 的 1.3.2 编制依据节)。
#   std_guard 的黑名单同样来自 standard_db, 收窄不损失它的判定能力。
#   → 需要"印出即采集"的口径 (存在性护栏) 请看 STD_COLLECT_PAT, 它按实测形态放宽,
#     但族名单同样取自这里, 不再是各文件自抄一份 (2026-10-16 N1)。
#   族名单改用 STD_FAMS 拼接后, 行为与原字面量 `(GBZ?|WS)` **逐串等价**
#   (实测 29,530 条语料: 4 份成品全文 + 全库 code/replace_of/name + 16 条刁钻写法,
#    命中串与归一号 0 处不同) → 长写法排在前面是必须的 ('GBZ' 必须先于 'GB')。
STD_FAMS: tuple = ("GBZ", "GB", "WS")            # standard_db 里判定得了的族 (改写/V-05)
STD_COLLECT_FAMS: tuple = STD_FAMS + ("HG",)     # 采集面: 2026-10 实测成品真印出的族
STD_MARKER = "T"                                  # 推荐性标记: 换版时会在 有/无 之间移动
STD_LOOSE_FAMS = r"[A-Z]{2,4}"                   # 采集面盲区探测器用 (见 tools/test_std_existence.py)


def fam_alt(fams=STD_FAMS) -> str:
    """族名单 → 正则分支, **长写法在前** ('GBZ42' 必须先配 'GBZ', 配 'GB' 会把 'Z' 当标记)"""
    return "|".join(sorted(fams, key=len, reverse=True))


# 身份键 (库内比对用的"该族唯一规范写法"): 族根 + 编号, 与书写形态/推荐性标记无关。
#   GBZ/T 189.9—2007 / GBZ/T189.9-2007 / GBZT189.9-2025 / gbz t 189.9 — 2007 → 'GBZ189.9'
# 为什么必须同时认 `GBZ/T`(有斜杠) 与 `GBZT`(无斜杠) 两种形态 (2026-10-16 #42 实测):
#   一手目录快照里**两种写法都在** (988 条里 21 条是无斜杠形态, 如 'GBZT 189.9—2025' 与
#   'GBZ/T 189.9-2007' 是同一份目录给的两版), 而旧 _base_code 只做
#   `.replace("GBZ/T","GBZ")` → 无斜杠形态永远带不上同一个键 →
#   `resolve_current('GBZ/T 189.9—2007')` 把 2007 那行**自己当现行版**返回
#   (2025 版就在库里, name 明写"代替 GBZ/T 189.9—2007")。有斜杠的同族
#   (229.1—2010→2025 / 192.1—2007→2025) 反而正常 —— 缺陷只在形态上, 与号无关,
#   所以修法也只能是形态规则 (字符类), 不许写成"GBZT189.9→GBZ/T 189.9"这种逐号同义词表。
# ⚠ 只抹 `T` (推荐性), **保留 `Z`** (指导性技术文件 GB/Z 与强制性 GB 是不同文档),
#   这是既有口径 (`.replace("GBZ/T","GBZ")` 而不动 GB/Z) 的等价推广, 不是重新设计。
_ID_PAT = re.compile(rf"^({fam_alt(STD_COLLECT_FAMS)})(?:/?[{re.escape(STD_MARKER)}])?"
                     r"(\d+(?:\.\d+)?)$")
_YEAR_TAIL = re.compile(_SEP_CLS + r"(?:19|20)\d{2}$")      # 规范年份尾巴 (19xx/20xx)
# 渲染层的前缀识别 (_rewrite): 族根 + 可选标记 (斜杠形态) + 编号
_REWRITE_PAT = re.compile(rf"^({fam_alt(STD_COLLECT_FAMS)})((?:/[TZ])?)\s*(\d+(?:\.\d+)?)")

STD_REF_PAT = re.compile(
    r"(?<![0-9A-Za-z])(" + fam_alt() + r")(?:\s*/\s*([TZ]))?\s*"
    r"(\d+(?:\.\d+)?)"
    r"\s*" + _SEP_CLS + r"\s*((?:19|20)\d{2})(?![0-9A-Za-z])")

# 采集尺子 (存在性口径, 消费方 = tools/test_std_existence.py): 与 STD_REF_PAT 同一族名单来源、
#   同一 STD_SEPS 字符类, 两处**刻意**放宽, 放宽的都是"存在"判断要看得见的形态:
#     (1) 年份收任意 4 位数字 —— 要能抓 'GBZ 9.9-9999' 这类连年份形态都不对的编造号;
#     (2) 推荐性标记允许**无斜杠**形态 (HGT20698-2009 是真印出来的写法) —— 否则同一号的
#         两种形态在采集面上是两个号 (N1 的实测缺陷正是"印了但尺子看不见");
#     (3) 族名单 = STD_COLLECT_FAMS (含 HG), 但"改写"仍只用 STD_FAMS (收窄有实测理由, 见上)。
#   它不参与改写、不参与 V-05, 所以放宽它不会凭空造出告警 —— 造出来的是"必须核实"的点名。
STD_COLLECT_PAT = re.compile(
    r"(?<![0-9A-Za-z])(" + fam_alt(STD_COLLECT_FAMS) + r")(?:\s*/?\s*([TZ]))?\s*"
    r"(\d+(?:\.\d+)?)"
    r"\s*" + _SEP_CLS + r"\s*(\d{4})(?![0-9A-Za-z])")


def norm_code(code: str) -> str:
    """任意书写形态 → 库内比对口径: 去空白(含全角空格) + 连接号统一 '-' + 大写"""
    return _SEP_RE.sub("-", _WS_RE.sub("", code or "")).upper()


def ref_code(match: re.Match) -> str:
    """STD_REF_PAT 的一次命中 → 比对口径标准号 ('GBZ/T196-2025')"""
    prefix, suffix, num, year = match.group(1), match.group(2), match.group(3), match.group(4)
    return norm_code(f"{prefix}{('/' + suffix) if suffix else ''}{num}-{year}")


def collect_code(match: re.Match) -> str:
    """STD_COLLECT_PAT 的一次命中 → 带标记的规范写法 ('GBZ/T196-2025' / 'HG/T20698-2009')"""
    prefix, suffix, num, year = match.group(1), match.group(2), match.group(3), match.group(4)
    return norm_code(f"{prefix}{('/' + suffix) if suffix else ''}{num}-{year}")


def std_key(code: str) -> str:
    """存在性比对口径: 身份键 + 年份尾巴 ('GBZ2.2-2007' / 'GBZ9.9-9999' / 无年份时只给身份键)

    与 norm_code 的区别: norm_code 只抹空白与连接号, **保留书写形态差异**
    ('GBZT189.9-2025' 与 'GBZ/T189.9-2025' 是两个字符串); std_key 把它们并成一个。
    存在性判断问的是"这个号(+年份)在不在真实源头里", 书写形态不该影响答案 (#42/#43 同一根因)。
    """
    s = norm_code(code)
    tail = re.search(_SEP_CLS + r"(\d{4})$", s)
    return f"{_base_code(s[:tail.start()])}-{tail.group(1)}" if tail else _base_code(s)


def canonical_spelling(code: str) -> str:
    """库内可能存的"无斜杠推荐性"写法 → 该族规范写法 ('GBZT189.9-2025' → 'GBZ/T189.9-2025')

    纯形态规则 (族根 + 裸标记 + 数字 → 补出斜杠), 不查任何号表。给 `_rewrite` 用:
    现行版若存在库里是 'GBZT...' 的行, 报告里仍该印规范写法, 而不是把库内缺陷抄进成品。
    """
    s = norm_code(code)
    return re.sub(rf"^({fam_alt(STD_COLLECT_FAMS)})({re.escape(STD_MARKER)})(?=\d)",
                  r"\1/\2", s)


def _base_code(code: str) -> str:
    """标准身份键: 去年份 + 去推荐性标记(/T 与裸 T 两种形态) + 去空白

    为什么去 /T: 同一标准的**推荐性↔强制性会换版时改变** —
        报告依据表写 'GB/T 12801' (2008 版是推荐性),
        现行版 'GB 12801-2025' 已改为**强制性**(不再带 /T)。
    若把 /T 当身份一部分, 就永远匹配不上现行版, 只能解析到废止的旧版。
    GBZ/T → GBZ 同理; 裸形态 'GBZT300' / 'GBT31716' / 'WST368' 也一并归到族根
    (#42: 旧实现只认带斜杠的 'GBZ/T'/'GB/T', 无斜杠形态永远配不上 →
     'WS/T' 也在这里统一, 于是 'WS 104-1999' 与 'WS/T 104-2014' 落进同一个键 ——
     它们本就是同一标准的两版, 旧键把它们当成两个不相干的号)。

    ⚠ 部分号保留: 'GB 39800.1' / 'GB/T 38144.1' 的分部号 (".1"/".2") **属于身份**,
    不得剥除 — 否则 'GB 39800' (总称) 会错配到 'GB39800.10' (机械分部) 或
    永远匹配不上任何分部 (2026-10 事故: 12 处 '现行版本待补充' 即此因)。
    ⚠ 认不出的形态 (多分部压缩写法 'GB/T27781/2/3/4-2011'、两位年 'GB14883.10-94'、
      '（GBZ2.1-2019）第1号修改单') 原样返回 —— 那是**数据侧**的非规范 code, 由
      tools/test_std_supersede_consistency.py 逐行点名, 不在这里靠同义词表硬凑。
    """
    s = norm_code(code)
    s = _YEAR_TAIL.sub("", s)              # 去年份
    m = _ID_PAT.match(s)
    return (m.group(1) + m.group(2)) if m else s


def _part_no(base: str) -> int | None:
    """取分部号: 'GB39800.2' → 2; 无分部 → None"""
    m = re.match(r"^[A-Z]+\d+\.(\d+)$", base)
    return int(m.group(1)) if m else None


def _family(base: str) -> str:
    """族键: 'GB39800.2' → 'GB39800' (用于找不到分部时的同族回退)"""
    m = re.match(r"^([A-Z]+\d+)", base or "")
    return m.group(1) if m else (base or "")


def _year(code: str) -> int:
    """标准号年份 = 最后一个 4 位数 (GB17017-2010 的 17017 是编号, 不是年份)"""
    m = _RE_YEAR.findall(code or "")
    return int(m[-1]) if m else 0


class StdConfigMissing(RuntimeError):
    """随代码走的登记表 (std_render_hold.json 等) 缺失。
    具名, 因为"静默返回空集"会把配置事故伪装成正常升级行为 —— 那正好是这份登记要防的事。"""


def content_hold(conn: sqlite3.Connection | None = None,
                 path: Path | None = None) -> set[str]:
    """「判据内容尚未按新版复核」的**旧版**归一号集合 (来源: `std_render_hold.json`)。

    背景 (2026-10 事故, 用户拍板保守回退): `GB 18664-2025` 编号真实、已于 2026-09-01 实施并
      全部代替 `GB/T 18664-2002` —— 但 `protection_rule` #31–36 的判据**文字**仍是 2002 版内容。
      读出纠版 (`RefFixer.fix`) 会把存的 `GB/T 18664—2002` 等号改成 `GB 18664—2025` →
      用"现行版的名义"发"旧版判据" = 用户明令禁止的「只换标签」(见
      tools/fix_official_std_codes.py 的 CONTENT_CHANGED: 术语/缺氧阈值/适合性检验频次/
      新增判废章/APF 数值表 等内容级差异**尚未取得一手文本复核**, 不许据二手转述改数值)。

    语义: 一条 (旧→新) 登记即表示"复核前该替代关系**保留旧标签、不做等号替换**"。
      判据的四个消费方 —— 改写器 `RefFixer.replacement`、V-05 `web/validators`、
      废止黑名单 `web/std_guard.superseded_map`、读出纠版不变式 `tools/test_std_currentize.py`
      —— 都读这一份, **来源单一**, 不会"一条看得见一条看不见"(2026-10-14 的裂尺子教训)。

    2026-10-16 从官方层表 `std_render_hold` 搬到这里 (用户拍板: 官方层只装官方来源数据, 这份是
      我们自己的复核状态; 而且当时表建了却没进 DDL/manifest → 生产容器里根本不会有它,
      读它= 静默降级)。`conn` 参数保留只为调用方不动。

    ⚠ 不篡改 standard_db 真值: 4288 仍 `废止`、6506 仍 `现行` 且 `replace_of` 保留 ——
      standard_db 说的是"法规事实", 本表说的是"本报告的判据复核状态", 两回事。
    文件读不到**不静默返回空集**: 空集=“没有待复核项”=照常把旧标签刷成新版, 那正是本登记要
    防的事; 所以缺文件直接具名抛错, 让人去修部署而不是让缺陷以"绿"的形式走出去。
    """
    from knowledge.paths import STD_RENDER_HOLD_FILE
    p = Path(path or STD_RENDER_HOLD_FILE)
    if not p.exists():
        raise StdConfigMissing(f"{p} 不存在 —— 待复核登记读不到时不许当成'没有待复核项'"
                               f"(那会把旧判据刷上新编号)。容器里遇到: 检查 Dockerfile 的 COPY")
    import json as _json
    entries = _json.loads(p.read_text(encoding="utf-8")).get("entries") or []
    return {norm_code(e["old_code"]) for e in entries if e.get("old_code")}



class RefFixer:
    """standard_db 的"现行/废止"判据, 一份连接算一次 + code→现行版 记忆化。

    为什么要有这个类 (2026-10-15 实测): 逐行版 `make_ref_fixer` 每调一次就重扫
    standard_db 全表建状态图, 而一次导出要读几十上百次规则表; 每命中一处替换还要
    再发 1 条 `SELECT state WHERE code=?` + 1 次 `resolve_current` 全表扫 ——
    同一个标准号被反复判、反复扫。这里把状态图、代替反查表、standard_db 行集
    与"号→现行版"的结果收进实例, 由 `currentizer(conn)` 按连接身份复用。

    ⚠ 判据本身一行没改: 尺子仍是 STD_REF_PAT, 仍是 state 与 replace_of 双判据,
      仍是"推断出的代替版必须年份更晚且自身未废止"的保守取向。
      改动只有"算一次"替换了"每次重算"。
    """

    def __init__(self, conn: sqlite3.Connection, data_version: int):
        self.conn = conn                    # 持强引用: 缓存活着期间 id() 不可能被复用
        self.data_version = data_version
        self.rows = [tuple(r) for r in conn.execute(
            "SELECT code, name, state, replace_of FROM standard_db")]
        self.ident = f"{id(conn)}:{data_version}:{len(self.rows)}"
        self._state: dict[tuple, str] = {}       # (去/T 去年份的号, 年份) → state
        self._state_exact: dict[str, str] = {}   # 库内原始 code → state (原 WHERE code=? 的等价物)
        self._sup: dict[tuple, list] = {}        # 被代替号 → 代替它的现行版候选
        for code, _name, state, rep in self.rows:
            st = state or ""
            self._state.setdefault((_base_code(code), _year(code)), st)
            self._state_exact.setdefault(code, st)
            if rep and str(rep).strip():
                b, y = _base_code(rep), _year(rep)
                if b and y:
                    self._sup.setdefault((b, y), []).append(
                        {"code": code, "state": st, "year": _year(code) or 0})
        self._memo: dict[str, str | None] = {}
        # 判据内容待复核的旧号 (见 content_hold / std_render_hold.json): 命中则保留旧标签、不升级。
        # 登记表读不到时**抛错**, 不再"合成库无此表→空集"地把旧判据刷上新编号。
        self._hold: frozenset = frozenset(content_hold(conn))

    # ── 反查表 (V-05 与改写器共用同一份) ────────────────────────────────────
    def supersession_index(self) -> dict:
        """被代替关系反查表: 归一化的"被代替标准号" → 代替它的现行版信息。

        单一产地: V-05 校验 与 fix_standard_refs 替换 共用此判据 (避免第三份平行实现)。
        键用 (_base_code, _year) —— replace_of 常写成 'GBZ/T 230—2010'(带空格/全角破折号),
        与库内 code 'GBZ230-2010' 归一后同键。
        """
        return {"ident": self.ident, "map": self._sup}

    def is_superseded(self, ref: str) -> dict | None:
        """该标准号是否已被代替 —— state 与 replace_of **双判据**。

        事故 (2026-10-14 实测): `GBZ230-2010` 在 standard_db 里 state='实施中',
        但 `GBZ/T230-2025` 的 replace_of 明写「代替GBZ/T 230—2010」。旧实现只信
        state=='废止' → 引用已作废的 2010 版时 **既不替换也不报警** (两级护栏同时漏网)。
        普查 2222 行: 5 条"被代替仍标实施中" + 29 条"被代替旧行压根不在库里"。

        保守取向: 推断出的代替版必须 (a) 年份更晚 且 (b) 自身不是废止态; 否则不改判。
        返回代替版信息 dict (可多条取最新), 未被视为废止则返回 None。
        """
        b, y = _base_code(ref), _year(ref)
        if not b or not y:
            return None
        cands = [x for x in self._sup.get((b, y), [])
                 if x["year"] > y and x["state"] not in ("废止", "已废止")]
        return max(cands, key=lambda x: x["year"]) if cands else None

    def resolve_current(self, ref: str) -> dict | None:
        """无年份/任意标准号 → 库中**未废止的最新版**记录

        返回 {code, name, state, part_fallback} 或 None (库中无该标准号)

        分部处理 (2026-10):
          - 引用带分部号 ('GB/T 38144.1') → 精确匹配同分部
          - 引用无分部 ('GB 39800', 报告依据表常见总称) → 库里只有 'GB39800.1/.2/...'
            → 取**第1部分(总则/基础分部)**, 并在 part_fallback 标注,
            由调用方在提示块说明 "总称→第1部分" (不写 "待补充" — 库里有权威版本)。
        """
        base = _base_code(ref)
        if not base:
            return None
        rows = self.rows
        same = [r for r in rows if _base_code(r[0]) == base]
        part_fallback = None
        if not same and _part_no(base) is None:
            # 无分部号的引用 → 同族第1部分 兜底 ('GB39800' → 'GB39800.1')
            fam = _family(base) + ".1"
            cand = [r for r in rows if _base_code(r[0]) == fam]
            if cand:
                same, part_fallback = cand, "总称→第1部分"
        if not same:
            return None
        # 优先未废止的, 再按年份降序
        alive = [r for r in same if (r[2] or "") not in ("废止", "已废止")]
        if not alive and _part_no(base) is not None:
            # 分部引用但同分部全部废止 → 族(无分部)兜底: 「换版时分部合并回主版」情形
            # (GB/T 38144.1-2019 废止 → GB 38144-2025 合并版, 2026-10 取证)
            fam = _family(base)                       # 'GB38144.1' → 'GB38144'
            cand = [r for r in rows if _base_code(r[0]) == fam]
            alive_cand = [r for r in cand if (r[2] or "") not in ("废止", "已废止")]
            if alive_cand:
                same, part_fallback = cand, "分部→合并主版"
                alive = alive_cand
        if not alive:
            # 同族全部废止 → 跨族代替兜底: 沿 replace_of **反向**找现行代替者
            # (实例: GB/T 11651-2008 被 GB 39800.1-2020 全部代替 — SAMR 详情页取证, 2026-10)
            # ⚠ 分隔符不能含 '/': 'GB/T 11651-2008' 会被 `/` 切成 'T 11651-2008'。
            hit = []
            for r in rows:
                for _tok in re.split(r"[,，;；]+", (r[3] or "")):
                    if _tok.strip() and _base_code(_tok.strip()) == base:
                        hit.append(r)
                        break
            alive_hit = [r for r in hit if (r[2] or "") not in ("废止", "已废止")]
            if alive_hit:
                same, part_fallback = hit, "被代替→现行版"
                alive = alive_hit
        pool = sorted(alive or same, key=lambda r: _year(r[0]), reverse=True)
        c, n, s = pool[0][0], pool[0][1], pool[0][2]
        return {"code": c, "name": n, "state": s, "deprecated_only": not alive,
                "part_fallback": part_fallback}

    # ── 号 → 现行版: 判定 (可记忆化) 与 书写形态 (每次匹配各自算) ───────────
    def replacement(self, full: str, year: int) -> str | None:
        """归一化标准号 → 应改成的现行版 code; None = 原样不动。按号记忆化。"""
        if full in self._memo:
            return self._memo[full]
        # 内容待复核的旧号: 保留旧标签, 不做等号替换 (见 content_hold / std_render_hold)。
        #   这是"只换标签"红线的渲染期闸门 —— standard_db 的真值(旧号废止/新号现行)不动,
        #   只是不把旧标签自动刷成新标签; 复核完成后由数据方删除登记, 闸门自动解除。
        if norm_code(full) in self._hold:
            self._memo[full] = None
            return None
        out = None
        try:
            state = self._state.get((_base_code(full), year), "")
            if not state:
                # 按全号(base+年份键)匹配不到时再按库内原始写法查一次 (旧行为)
                state = self._state_exact.get(full, "") or ""
            sup = None if state in ("废止", "已废止") else self.is_superseded(full)
            if state in ("废止", "已废止") or sup:
                cur = self.resolve_current(full)
                if cur and cur.get("code") \
                        and (cur.get("state") or "") not in ("废止", "已废止") \
                        and _year(cur["code"]) > year:
                    out = cur["code"]
        except Exception:
            out = None
        self._memo[full] = out
        return out

    @staticmethod
    def _rewrite(matched: str, new_code: str) -> str | None:
        """现行版 code → 沿用原文书写风格的写法; 认不出前缀形态则 None (不动)

        前缀族取自 fam_alt(STD_COLLECT_FAMS) (单一产地, 不再各抄一份字面量)。
        旧实现的字面量是 `(GBZ/T|GB/T|GBZ|GB/Z|GB)` —— 既没有 WS/WS/T (卫生行业标准的
        现行版永远写不出来, 只能原样留着旧号), 也不认库里的无斜杠行 'GBZT189.9-2025'
        (#42: 键修好后 resolve_current 能给出 2025 那行, 但 _rewrite 认不出它的前缀
        → 判据判对了、渲染层又静默不动)。两处都由形态规则补上: 先把裸标记写成 'X/T',
        再按族根 + 可选标记取前缀。
        """
        code = canonical_spelling(new_code)          # 'GBZT189.9-2025' → 'GBZ/T189.9-2025'
        m2 = _REWRITE_PAT.match(code)
        if not m2:
            return None
        pfx, rest = m2.group(1) + m2.group(2), m2.group(3)
        yr_new = code[code.rfind("-") + 1:]
        spaced = bool(re.match(r"^[A-Za-z/]+\s+\d", matched))
        dash = "—" if "—" in matched else "-"
        return f"{pfx} {rest}{dash}{yr_new}" if spaced else f"{pfx}{rest}{dash}{yr_new}"

    def fix(self, text: str) -> tuple[str, list[str]]:
        if not text:
            return text, []
        # 快路径: STD_REF_PAT 必须含 4 位年份才可能命中 → 先排掉"苯/噪声/PC-TWA"这类单元格,
        # 读门逐格过判据时不必对每个字符串都跑一遍 sub (语义等价, 见 §7 单测)
        if not _RE_YEAR.search(text):
            return text, []
        changes: list[str] = []

        def _sub(m):
            new_code = self.replacement(ref_code(m), int(m.group(4)))
            if not new_code:
                return m.group(0)
            new_full = self._rewrite(m.group(0), new_code)
            if not new_full:
                return m.group(0)
            changes.append(f"{m.group(0)} → {new_full}")
            return new_full

        return STD_REF_PAT.sub(_sub, text), changes

    def cell(self, v):
        """单元格/字段值 → 纠版后的值 (非字符串原样: 数字、None 不参与)"""
        return self.fix(v)[0] if isinstance(v, str) else v


_FIXER: RefFixer | None = None


def currentizer(conn: sqlite3.Connection) -> RefFixer:
    """按连接身份取判据实例 —— 门只该在这里建缓存, 站点不各自建。

    失效口径 (两条同时用, 单缺一条就会拿陈旧判据印废止号):
      - `conn is`: 换连接/换库文件即重建; 缓存持强引用, id() 不会被复用后撞键。
      - `PRAGMA data_version`: 抓**行数不变的原地 UPDATE**
        (tools/fix_official_std_codes.py --apply、待办 #17 那种"只改 state 列"正是这种,
         旧口径 `COUNT(standard_db)` 对它完全失明; 2026-10-15 实测他连接写 2→3→4)。
    同连接自己写入不反映在 data_version 上 —— 本门按只读连接用 (knowledge.oel.connect_ro),
    永远不会自写; 写侧工具改完 standard_db 请调 `invalidate_caches()`。
    """
    global _FIXER
    dv = conn.execute("PRAGMA data_version").fetchone()[0]
    f = _FIXER
    if f is None or f.conn is not conn or f.data_version != dv:
        f = _FIXER = RefFixer(conn, dv)
    return f


def invalidate_caches() -> None:
    """写侧改完 standard_db 后调用: 判据与 memo 立即作废 (下一次读重建)。"""
    global _FIXER
    _FIXER = None


def supersession_index(conn: sqlite3.Connection) -> dict:
    """兼容出口: 判据住在 RefFixer (单一产地), 这里只按连接取。"""
    return currentizer(conn).supersession_index()


def is_superseded(conn: sqlite3.Connection, ref_code: str) -> dict | None:
    """该标准号是否已被代替 —— 判据见 RefFixer.is_superseded (双判据, 单一产地)。"""
    return currentizer(conn).is_superseded(ref_code)


def resolve_current(conn: sqlite3.Connection, ref_code: str) -> dict | None:
    """任意标准号 → 库中未废止的最新版 —— 判据见 RefFixer.resolve_current。

    算法原文 (分部精确 → 总称取第1部分 → 分部全废合并回主版 → 沿 replace_of 反查
    跨族代替) 一行未改, 只是行集来自按连接缓存的 standard_db 快照而非每次全表扫。
    """
    return currentizer(conn).resolve_current(ref_code)


def resolve_all_refs(conn: sqlite3.Connection) -> list[dict]:
    """standard_ref 全表 → 附现行版解析 (报告"评价依据表"用)

    返回 [{ref_code, name, current_code, state, note}, ...]
      ref_code    : 报告里的写法 (无年份, 如 'GBZ 188')
      current_code: 应写的完整号 (如 'GBZ188-2025')
      note        : 未收录/仅有废止版 等异常说明
    """
    out = []
    for code, name in conn.execute("SELECT code, name FROM standard_ref ORDER BY id"):
        cur = resolve_current(conn, code)
        rec = {"ref_code": code, "name": name,
               "current_code": cur["code"] if cur else "",
               "state": cur["state"] if cur else "",
               "note": ""}
        if cur is None:
            rec["note"] = "标准库未收录, 无法判定现行版 → 待补充"
        elif cur.get("deprecated_only"):
            rec["note"] = "库中仅有废止版, 现行版缺失 → 待补充"
        elif (cur["state"] or "") == "废止":
            rec["note"] = "解析到的仍是废止版 → 待核实"
        out.append(rec)
    return out


def make_ref_fixer(conn: sqlite3.Connection):
    """兼容出口: 返回 fix(text) → (text', changes) 的闭包。

    2026-10-15 起状态表不再"每次调用都重建": 判据住在 RefFixer, 由 currentizer(conn)
    按连接身份复用 (含 code→现行版 的 memo)。逐格场景优先用 knowledge.dataset.Dataset。
    """
    return currentizer(conn).fix


def rows_of(conn: sqlite3.Connection, sql: str, args=()) -> list[list]:
    """官方数据层 → 行, **读出即做标准号兜底** (2026-10-15 用户拍板: 代码兜底)

    规则库 (emergency_rule / protection_rule / management_rule / ppe_item / oel_limit…)
    存的"依据标准号"实测就有废止版 (本地与生产都挂着 GBZ 2.2—2007 / GB/T 18664—2002),
    而部署刻意排除 data/ → 不能假设两份库都被同步过。废止号不得进报告:
    在**唯一读出点**纠版, 比在十几处拼接点各补一遍可靠 (漏一处就漏印)。

    新代码用 knowledge.dataset.Dataset.rows() —— 同一份判据、同一个出口,
    但它还多表态一件事: 这条路到底走不走纠版 (raw() 是不走的那条, 必须写理由)。
    """
    f = currentizer(conn)
    return [[f.cell(v) for v in r] for r in conn.execute(sql, args).fetchall()]


def fix_standard_refs(text: str, conn: sqlite3.Connection | None = None) -> tuple[str, list[str]]:
    """把 text 中**废止/无效**标准号替换为现行版 (确定性, 不靠 LLM)

    单一产地: 原 web/app.py `_fix_standard_refs` 迁入 (2026-10) — 当时 tools/ 或迁移脚本
    import web.app 会连带触发其副作用 (_cleanup_expired / init_tasks); S1c′ D0 已把
    那些调用移进启动钩子, 但函数留在 knowledge 层 (判定逻辑不该住在 web 层)。

    事故 (2026-10): LLM 凭记忆引废止版 (GBZ 2.2—2007 / GBZ 188—2014)。
    已通过"标准版本基准"从源头减少, 但 LLM 仍可能偶发写错; 此处做确定性兜底替换:
    用 standard_db 把文中的 废止标准号 → 其现行版标准号。

    返回 (新文本, 改动说明列表); 无改动原样返回。conn=None 时自开连接 (只读用途)。
    """
    if not text:
        return text, []
    own = False
    if conn is None:
        try:
            from knowledge.oel import OfficialSchemaMissing, connect_ro
            conn = connect_ro()           # 读路径走只读连接 (S1a′): 校验时不可能顺手建表
            own = True
        except OfficialSchemaMissing:
            return text, []               # 库/schema 不在 → 明确这一种降级, 其余异常照常外抛
        except OSError as e:              # 文件权限等设备级问题: 也不该伪装成"没有引用要改"
            raise RuntimeError(f"标准号纠版无法打开库: {e}") from e
    # 判定与替换逻辑全部在 RefFixer (单一产地); 逐格场景请直接取 currentizer(conn)
    try:
        return currentizer(conn).fix(text)
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    from knowledge.paths import DB_PATH        # 路径唯一产地, 不在这里自己拼
    conn = sqlite3.connect(str(DB_PATH))
    refs = resolve_all_refs(conn)
    ok = sum(1 for r in refs if r["current_code"] and not r["note"])
    print(f"standard_ref {len(refs)} 项, 已解析出现行版 {ok} 项\n")
    for r in refs:
        mark = "✅" if not r["note"] else "⚠"
        print(f"  {mark} {r['ref_code']:<14} → {r['current_code'] or '—':<20} {r['note']}")
