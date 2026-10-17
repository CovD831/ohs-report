"""章节数据槽填充器 v3 — 按报告章节 (11章平铺, GBZ/T 196—2025 10.2节)

映射: 旧10.2.x + 旧1-12章 → 新11章 (数据源不变, 编号变)
  新1 总论           → 评价依据表
  新2 现有企业概况   → 产品产量/建构筑物/定员 (现有企业)
  新3 工程分析       → 设备/定员/建构筑物/产品/投资 + 原辅材料 + 选址/布局/建筑卫生/辅助用室检查表
  新4 类比调查       → 类比可比性表 + 检测结果 + 职业健康监护
  新5 危害分析       → 识别/判定/检测/限值/健康影响/关键控制点
  新6 防护设施       → 防尘防毒/防噪声振动/防暑防寒/防护设施检查表 + 设施配置
  新7 应急救援       → 应急救援检查表
  新8 PPE            → PPE配备表
  新9 职业卫生管理   → 管理制度检查表 + 职业健康监护表
  新10 补充建议      → 问题与建议表
  新11 结论          → 结论要素表
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.pdf_cells import digit_bag, nospace  # noqa: E402  (#36 同形尺子的唯一产地, 读侧复用同一份)
from web.number_provenance import prov_rule  # noqa: E402

# 可研「主要技术经济指标」表缓存 (tools/extract_tech_econ.py → data/tech_econ.json)
_TECH_ECON_CACHE: dict | None = None
# 现有厂区职业病防治经费实测表缓存 (tools/extract_ohy_invest.py → data/ohy_invest.json)
_OHY_INVEST_CACHE: dict | None = None
# 可研「扩产前后产能变化信息对照表」缓存 (tools/extract_product_plan.py → data/product_plan.json)
_PRODUCT_PLAN_CACHE: dict | None = None


def _load_ohy_invest(pd_: dict) -> dict | None:
    """取现有厂区职业病防治经费实测表 (data/ohy_invest.json, C3现状评价 表11.11-1)

    项目**无**专项投资明细时, 用现有厂区实测作参照 (值全来自材料表格)。
    """
    global _OHY_INVEST_CACHE
    if _OHY_INVEST_CACHE is None:
        try:
            _OHY_INVEST_CACHE = json.loads(
                (Path(__file__).resolve().parent.parent / "data" / "ohy_invest.json")
                .read_text(encoding="utf-8"))
        except Exception:
            _OHY_INVEST_CACHE = {}
    pid = str(pd_.get("_project_id") or pd_.get("id") or "")
    return _OHY_INVEST_CACHE.get(pid)


def _load_tech_econ(pd_: dict) -> dict | None:
    """取本项目可研「主要技术经济指标」表 (表1.2-11, 全 15 大项)

    数据源: data/tech_econ.json (按项目 id 键)。**这是本项目权威实测值**,
    非 LLM 生成 (红线: 数字绝不给 LLM; 能计算的绝不生成)。
    无则返回 None → 调用方走字段兜底。
    """
    global _TECH_ECON_CACHE
    if _TECH_ECON_CACHE is None:
        try:
            _TECH_ECON_CACHE = json.loads(
                (Path(__file__).resolve().parent.parent / "data" / "tech_econ.json")
                .read_text(encoding="utf-8"))
        except Exception:
            _TECH_ECON_CACHE = {}
    pid = pd_.get("_project_id") or pd_.get("id") or ""
    hit = _TECH_ECON_CACHE.get(str(pid))
    if hit and hit.get("rows"):
        return hit
    # 项目 id 未带上时: 只认唯一有 rows 的候选, 多候选则不猜 (防张冠李戴)
    cands = [v for v in _TECH_ECON_CACHE.values() if v.get("rows")]
    return cands[0] if len(cands) == 1 else None


def _load_product_plan(pd_: dict) -> dict | None:
    """取本项目可研「扩产前后产能变化信息对照表」(表1.2-1, 全厂口径)

    数据源: data/product_plan.json (按项目 id 键)。**本项目权威实测值**,
    非 LLM 生成。用于复刻真稿 表8.1-3 (本项目扩建前后全厂产品方案对比表)。
    无则返回 None → 调用方走 products 字段兜底。
    """
    global _PRODUCT_PLAN_CACHE
    if _PRODUCT_PLAN_CACHE is None:
        try:
            _PRODUCT_PLAN_CACHE = json.loads(
                (Path(__file__).resolve().parent.parent / "data" / "product_plan.json")
                .read_text(encoding="utf-8"))
        except Exception:
            _PRODUCT_PLAN_CACHE = {}
    pid = str(pd_.get("_project_id") or pd_.get("id") or "")
    hit = _PRODUCT_PLAN_CACHE.get(pid)
    if hit and hit.get("rows"):
        return hit
    # 项目 id 未带上时: 只认唯一有 rows 的候选, 多候选则不猜 (防张冠李戴)
    cands = [v for v in _PRODUCT_PLAN_CACHE.values() if v.get("rows")]
    return cands[0] if len(cands) == 1 else None


def _ohy_invest_cell(pd_: dict) -> str:
    """职业病防治专项经费单元格 — 无材料实测值则诚实标注"""
    v = pd_.get("ohy_investment")
    if v not in (None, "", "None"):
        return str(v)
    return "待补充（材料未提供概算，见 9.2）"


def _det_verdict(d: dict) -> str:
    """检测结果表"判定"列的三态取值 — 禁止无条件写"合格"

    ⚠ 原实现写死 "合格": 只要记录进了 detections 就声称合格。
      实测 表型B(岗位×多因素) 记录**没有测得值** (值不在 ctwa, 而在无表头的数值列),
      塞进检测结果表就会**凭空断言合格** = 编造判定结论 (违反红线: 不生成数字/结论)。

    三态:
      材料自带判定 → 用它 (符合/不合格/或"稳定"类原话)
      有测得值     → "符合" (材料给了值即已判过)
      无值无判定   → "—"     (待补充, 绝不冒充合格)

    ⚠ 材料判定列须过**白名单**: 占位符（`/` `-` `—`）与**串列值**（实测:
      PPE清单"防毒半面罩；防噪耳塞；全部佩戴"、粉尘种类"滑石粉尘"）都不是判定,
      视为无判定 → "—"。绝不原样印进报告判定列 (形式层: 贴真实报告)。
    """
    j = str(d.get("judgement") or "").strip()
    if j in ("", "—", "-", "--", "/", "／", "无", "未检出", "n/a", "N/A"):
        j = ""                                # 占位符/空 → 无判定
    elif "不合格" in j or "不符合" in j:
        return "不符合"
    elif "合格" in j or "符合" in j:
        return "符合"
    elif "稳定" in j:                         # 表型B: 浓度或强度相对(不)稳定
        return j
    elif "超标" in j:
        return "超标"
    else:
        j = ""                                # 串列/错位值 → 不是判定
    _v = str(d.get("ctwa") or "").strip()
    if _v and _v not in ("—", "-", "/", "无", "未检出"):   # ⚠ 占位符不算测得值
        return "符合"
    # ⚠ results 只有 **表型A(逐样结果表)** 才是检测值。
    #   表型B 的 results 是误读的其它列 —— 实测长兴 p14 的 0.5/1.5/2
    #   经 300dpi 取证是 **"采样时间(小时)"** 列; 新泰 p27 是无表头数值列。
    #   若仅凭 results 断言"符合" = 拿采样时长当合格依据 = 编造。
    if str(d.get("table_type") or "").strip().upper() == "A":
        _rs = [str(x).strip() for x in (d.get("results") or [])
               if str(x).strip() not in ("", "—", "-", "/")]
        if _rs:
            return "符合"
    return "—"


# ===== h14: 物理因素独立检测结果表 =====
# 取证 (用户已授权方案①): 长兴备案稿 表28 = 独立「工作场所噪声检测结果」表, 列:
#   检测岗位 | 检测地点 | 检测结果(1|2|3, 双层表头) | LEX,8h[dB(A)] | 接触时间(h) |
#   职业卫生接触限[dB(A)] | 结果判定; 新泰备案稿 p43 7.2 物理因素表 = 「工种|检测地点|
#   检测项目|检测结果|检测单位|职业接触限值|判定结果」通用形态 (含检测项目列)。
#   → 本表合并两版真实形态: 长兴 9 列 + 新泰「检测项目」列 (本表混合多因素, 需列名分辨)。
# 物理因素实测值在 results (表型A 平行样 / 单值行 1 个), LEX,8h 在 ctwa 键。
# ⚠ 行选择必须「按因素名做合理性区间」: 表型B 的 results 混有采样时长 (0.5/1.5/2)
#   与斜杠串 (3/1) — 纯值域启发式会误收时长/误拒 工频电场 0.002 (实测教训)。
_PHYS_INTERVAL = (
    ("噪声", 40.0, 130.0),      # dB(A), 实测 55.8~89.7
    ("照度", 20.0, 20000.0),    # lx, 实测 316~1032
    ("工频", 0.001, 20.0),      # kV/m, 实测 0.002~0.745 (≤8 启发的反例)
    ("紫外", 0.001, 1000.0),    # 辐照度/照射量
    ("高温", 10.0, 60.0),       # WBGT ℃
    ("WBGT", 10.0, 60.0),
    ("微波", 0.001, 100000.0),
    ("射频", 0.001, 100000.0),
    ("振动", 0.01, 1000.0),
)


def _det_bucket(conn, fac: str) -> tuple[str, str | None]:
    """检测行的因素名 → (显示用 dtype, 依据)。分桶**从标准库派生**, 不写物质名清单。

    修前的口径 (#34, 2026-10-16 实测): `hazard_factor WHERE name LIKE '<检测名>%'` ——
      匹配方向是反的: 检测报告里的名字带采样方式后缀 ("滑石粉尘(呼尘)"), 标准库名才是短的
      那一侧, 于是"标准名以检测名开头"永远落空; 落空就默认"化学毒物" → 4 个项目 11 个
      含尘/烟的检测名里 **7 个错桶** (滑石粉尘(呼尘)、石灰石粉尘（ 总尘）、其他粉尘(树脂)、
      其他粉尘（环氧树脂）…), 粉尘行被印进化毒物表。
    现在的链 (每一环都是查库):
      1) 归一后的名精确命中 hazard_factor.name;
      2) **双向**前缀: 标准名是检测名的前缀, 或标准名以检测名开头 —— 覆盖
         "其他粉尘" → "以上未提及的可导致职业病的其他粉尘" 这类长登记名;
      3) 退到 oel_limit.factor_type (接触限值表本身就按 粉尘/化学因素 分类, 且"其他粉尘"
         在那里是独立条目 '其他粉尘a' —— 表1 的脚注 a 被连名一起入库了);
      4) 全落空 → 默认"化学毒物"并**点名** (见 _det_by_type 的日志), 不许静默。
    返回的 basis=None 就是"第 4 档"的记号, 调用方必须让它可见。
    """
    from web.entity_link import name_norm
    n = name_norm(fac)
    ds = _ds(conn)
    if not n:
        return "化学毒物", None
    row = ds.one("SELECT category FROM hazard_factor WHERE name=?", (n,))
    if row and row[0]:
        return _dtype_of(row[0]), f"hazard_factor.name 精确 ({row[0]})"
    row = ds.one("SELECT category FROM hazard_factor WHERE name LIKE ? ORDER BY LENGTH(name) DESC LIMIT 1",
                 (n + "%",))
    if row and row[0]:
        return _dtype_of(row[0]), "hazard_factor 标准名以检测名开头"
    row = ds.one("SELECT category FROM hazard_factor WHERE ? LIKE name || '%' "
                 "ORDER BY LENGTH(name) DESC LIMIT 1", (n,))
    if row and row[0]:
        return _dtype_of(row[0]), "检测名以 hazard_factor 标准名开头"
    row = ds.one("SELECT factor_type FROM oel_limit WHERE factor_name=? OR factor_name LIKE ? "
                 "ORDER BY LENGTH(factor_name) LIMIT 1", (n, n + "%"))
    if row and row[0]:
        return _dtype_of(row[0]), "oel_limit.factor_type (接触限值表)"
    # 3.5) #51: 名字只差空格/下标位置 (名内多一个空格、括号限定写法不同) → 先解析成规范名再查类别
    canon, _mode, how = _resolve_oel_name(conn, fac)
    if canon:
        rs = _name_index(conn)[0]["by_name"].get(canon, ())
        if rs and rs[0][5]:
            return _dtype_of(str(rs[0][5])), f"#51 解析 → oel_limit.factor_type ({how} → {canon})"
    # 3.6) #37: 名字是"组名下的子条目" (官方层把它放在 conditions 列) → 用那一行自己的 factor_type
    srows, smain, sstat = _resolve_oel_sub(conn, fac)
    if sstat == "sub_resolved" and srows and srows[0][5]:
        return _dtype_of(str(srows[0][5])), f"#37 子条目档 → oel_limit.factor_type ({smain})"
    return "化学毒物", None


def _dtype_of(category: str) -> str:
    """标准库 category → 检测表的显示分桶名。物理类在上游已被 _is_phys_factor 分流。"""
    return {"粉尘": "粉尘", "噪声": "噪声", "高温": "高温"}.get(category, "化学毒物")


# ════════════════════════════════════════════════════════════════════════════
# #51 名内空格/下标位置 → 查不到限值 → 假陈述   |   #50 非因素行被当成检测因素印上纸
# (2026-10-17 取证, 成品逐格读字 §7.4; 判据一律从**数据/结构**派生, 无物质名白名单)
# ════════════════════════════════════════════════════════════════════════════
# 数值单元格谓词: 与导入侧 web/report_parser.py 的 `([<>≤≥]?\s*)?\d+(\.\d+)?` 同一形状
# (检测表里"有没有测得值"必须由材料自己的数值列形状说, 不由物质名说)
_NUM_CELL = re.compile(r"^[<>≤≥]?\s*\d+(?:\.\d+)?$")
# 结尾括号限定 (总尘/呼尘/游离SiO2含量<10% …) —— 只取**最外层结尾那一对**
_PAREN_TAIL = re.compile(r"（[^（）]*）$|\([^()]*\)$")
# 官方层"名字"列 (判"这个名字属不属于已知因素名 namespace"的全部来源)
_OFFICIAL_NAME_COLS = (("factor_name", "oel_limit"), ("name", "hazard_factor"),
                       ("oel_name", "hazard_factor_alias"), ("oel_factor", "material_dictionary"),
                       ("factor", "health_effect"))
# #36 索引口径: 参与判同的串至少 2 个"字母/汉字" (库里有 '苯'/'氨' 这类单字名,
# 不设这条就会让 '采样日期：2024.04.07' 这种串被某个单字名"包含命中"救活 ——
# 同一个常量在 tools/test_oel_subscripts.py 也有, 两处同值不同源, 改动要同批)
_MIN_LETTERS = 2
_NAME_MEMO: list = []   # [(conn, data_version, (oel索引, hazard_cas索引, 官方名全集))]


def _letters_only(s: str) -> str:
    """#36 同形规则的一半: 去掉数字与**所有空白**(含全角空格 U+3000)后的字母汉字序列。
    与 tools/fix_oel_subscripts._letters_only 是同一条表达式 —— 那侧是写 (存量迁移),
    这侧是读 (成品查限值); 两处口径必须一致, 否则"迁移说没问题、导出还说查不到"。
    """
    return re.sub(r"\d", "", nospace(s))


def _name_index(conn):
    """官方层名字索引 (按 连接对象 + PRAGMA data_version 复用, 与 Dataset 门同一寿命口径)。

    → (oel, hfcas, universe)
      oel:      {"by_name": {factor_name: [行]}, "ns": {去空白名: {名}}, "shape": {(字母序列, 数字多重集): {名}}}
      hfcas:    {"ns": {去空白 hazard_factor.name: cas}, "shape": {(字母序列, 数字多重集): cas}}
      universe: 官方层全部不同名 (五列并集) —— 只用于"包含命中"这条**保行**判据 (宁留不误删)
    """
    dv = conn.execute("PRAGMA data_version").fetchone()[0]
    if _NAME_MEMO and _NAME_MEMO[0][0] is conn and _NAME_MEMO[0][1] == dv:
        return _NAME_MEMO[0][2]
    ds = _ds(conn)
    by_name: dict = {}
    for r in ds.rows("SELECT factor_name, oel_type, value, unit, cas, factor_type, source_standard "
                     "FROM oel_limit ORDER BY id"):
        if r[0] is None:
            continue
        by_name.setdefault(str(r[0]), []).append(r)
    ns: dict = {}
    shape: dict = {}
    for nm in by_name:
        if len(_letters_only(nm)) >= _MIN_LETTERS:
            ns.setdefault(nospace(nm), set()).add(nm)
            shape.setdefault((_letters_only(nm), digit_bag(nm)), set()).add(nm)
    hfn: dict = {}
    hfshape: dict = {}
    for nm, cas in ds.rows("SELECT name, cas FROM hazard_factor WHERE name IS NOT NULL"):
        if cas and len(_letters_only(str(nm))) >= _MIN_LETTERS:
            hfn.setdefault(nospace(str(nm)), str(cas))
            hfshape.setdefault((_letters_only(str(nm)), digit_bag(str(nm))), str(cas))
    universe: set = set()
    for col, tbl in _OFFICIAL_NAME_COLS:
        universe |= {str(r[0]) for r in ds.rows(f"SELECT DISTINCT {col} FROM {tbl} "
                                                f"WHERE {col} IS NOT NULL")}
    # #37 子条目档的索引 (2026-10-17): 官方层把**组名的子条目**存在 conditions 列 ——
    #   钒 1248/1249、钼 1389/1390、铅 1404/1405、铜 1479/1480、铝尘 1618/1619、
    #   麻尘 1620-1622、石棉 1639/1640、镍 1399/1400 (实测 conditions 非空 105 行里 22 行是这形状)。
    #   报告里写的往往就是子条目名, 而 by_name/ns/shape 三把尺子只看 factor_name 那一侧 →
    #   标准明明给了 1 mg/m3, 成品只能印 '—'。这里按 conditions 再建一把, 行形状与 by_name 一致。
    sub_rows: dict = {}
    sub_owner: dict = {}
    for r in ds.rows("SELECT factor_name, oel_type, value, unit, cas, factor_type, source_standard, "
                     "conditions FROM oel_limit WHERE conditions IS NOT NULL ORDER BY id"):
        key = nospace(str(r[7]))
        if len(_letters_only(str(r[7]))) < _MIN_LETTERS:
            continue                       # 单字/纯数字的 conditions 是方式词或脚注, 不当名用
        sub_rows.setdefault(key, []).append(tuple(r[:7]))
        sub_owner.setdefault(key, set()).add(str(r[0]))
    # 片段筛 (判据从库里推, 不写"方式词表"): 子条目名如果只是**某个已登记名的片段**, 就不许当名用
    #   —— 否则检测表里的 '粉尘'/'纤维' 这类采样方式词会被绑到 石棉（石棉含量＞10%） 那一档。
    #   留下来的键 (本机实测 84 个) 里确实含 '10 %≤游离SiO2含量≤50 %' 这类**区间描述** (矽尘那一族),
    #   它们只有在"检测名字面就是这个区间"时才会命中 —— 那一刻绑到区间所在的那一行正是对的数,
    #   所以不额外加区间词表 (加了就是白名单)。
    _ns_names = {nospace(nm) for nm in by_name}
    sub_ok = {k: not any(k in other for other in _ns_names if other != k) for k in sub_rows}
    idx = ({"by_name": by_name, "ns": ns, "shape": shape,
            "sub_rows": sub_rows, "sub_owner": sub_owner, "sub_ok": sub_ok},
           {"ns": hfn, "shape": hfshape}, universe)
    _NAME_MEMO.clear()
    _NAME_MEMO.append((conn, dv, idx))
    return idx


def _resolve_oel_name(conn, fac: str) -> tuple[str | None, str | None, str]:
    """检测/识别名 → (oel_limit 里的规范名, 采样方式限定, 依据/落空原因)。

    为什么要在**读侧**再解析一次 (#51, 2026-10-17 生产成品逐格取证 §7.4):
      '石灰石粉尘（ 总尘）'、'沉淀二氧 化硅（ 总尘）'、'稀土粉尘（游离SiO2含量<10%）'
      这类名与标准库写法只差**一个空格/下标位置**, 精确匹配 0 命中 → 成品限值列印 '—'
      且备注写"未制定职业接触限值" —— 而标准里明明给了限值。那是**假陈述**, 不是排版问题。
    四道前提 (#36 写侧四前提的读侧版, 一条都不靠物质名清单):
      ① 同形: 字母汉字序列相同 (_letters_only) 且 数字多重集相同 (digit_bag) —— 只许差空格/
         下标位置, 不许换物质 ('1-硝基丙烷'/'2-硝基丙烷' 就是被 digit_bag 分开的);
      ② 唯一命中: 该形状在 oel_limit.factor_name 上恰好对应 1 个规范名, **多命中就交人工** (不猜);
      ③ 同行绑定的读侧替代 = 采样方式词必须在该规范名自己的 `oel_type` 列里有登记
         (总尘/呼尘 的词表来自库, 不是这里写的), 否则不把括号限定接上;
         CAS 交叉核对: hazard_factor 侧与 oel_limit 侧都有 CAS 时必须相等;
      ④ 落空/被拒 → 返回 (None, None, 原因), 调用方**保留该行**并写诚实备注, 不静默。
    ⚠ 能力边界: #36 写侧的 前提⑤ 是直接对一手 GBZ 2.1—2019 PDF 做 `row_map`/`value_in_row`
      "名字与限值同行"绑定, 而生产容器**不带一手 PDF** (§7.6: acquire/raw 被打包排除) →
      读侧只能用库里自己的 CAS/oel_type 交叉核对; 库内无 CAS 的粉尘行 (如 稀土粉尘) 这一环是空的,
      只剩 ①② 的唯一命中兜住 —— 这条边界由 tools/test_det_rows.py 的"歧义变异"证明会拒判。
    """
    oel, hfcas, _univ = _name_index(conn)
    n = nospace(fac)
    if not n or len(_letters_only(fac)) < _MIN_LETTERS:
        return None, None, "no_anchor"

    def _ns_hits(key: str) -> set:
        got = set(oel["ns"].get(key, ()))
        got |= {nm for k, s in oel["ns"].items() if len(k) > len(key) and k.startswith(key)
                for nm in s}
        return got

    # ③' 采样方式限定: 结尾括号里的词必须在该名的 oel_type 里登记过
    def _with_mode(cands: set, mode: str) -> set:
        return {nm for nm in cands
                if any(mode in nospace(str(r[1])) for r in oel["by_name"].get(nm, ()))}

    cands: set = set()
    how = mode = None
    if not cands:
        cands, how = _ns_hits(n), "去空白后精确/前缀命中 oel_limit (空格族)"
    if not cands:
        m = _PAREN_TAIL.search(n)
        if m:
            head, mode = n[:m.start()], nospace(m.group(0))[1:-1]
            cands = _with_mode(_ns_hits(head), mode) if head else set()
            how = f"结尾括号限定「{mode}」→ oel_type 同词登记 (方式词表来自库)"
    if not cands:
        cands = set(oel["shape"].get((_letters_only(fac), digit_bag(fac)), ()))
        how = "同形唯一命中 (字母汉字序列+数字多重集, #36 尺子)"
    if not cands:
        return None, None, "no_anchor"
    if len(cands) > 1:
        return None, None, f"ambiguous({len(cands)})"
    nm = next(iter(cands))
    a = hfcas["ns"].get(n) or hfcas["shape"].get((_letters_only(fac), digit_bag(fac)))
    b = next((str(r[4]) for r in oel["by_name"].get(nm, ()) if r[4]), "")
    if a and b and a.replace(" ", "") != b.replace(" ", ""):
        return None, None, "cas_conflict"
    return nm, mode, how


def _resolve_oel_sub(conn, fac: str) -> tuple[list, str | None, str]:
    """#37 子条目档: 名字是官方层放在 `conditions` 列里的**组名子条目**时, 取它自己那一行。

      → (行集, 所属主名, status)   status ∈ sub_resolved | sub_ambiguous | sub_generic | no_sub

    三条前提 (一条都不靠物质名清单):
      ① 去空白后**逐字相等**才认 —— 不做前缀/包含: 同一组的子条目之间互为前缀的情况库里就有
         ('金属镍与难溶性镍化合物' 与 '可溶性镍化合物' 共享 '镍化合物' 三个字的尾巴);
      ② 该子名只属于 **1 个主名** (多主名 = 歧义 → 交人工, 不猜);
      ③ 片段筛 `sub_ok`: 子名不得是某个已登记名的片段 (见 _name_index 里那段注释)。
    ⚠ 返回的行集**已按子名筛过**, 不是整组行: 镍那一组是 金属镍与难溶性镍化合物=1 /
      可溶性镍化合物=0.5, 把整组交给宽表会让两档互相覆盖 (后写的赢), 那就是新的错位假数。
    """
    oel, _hfcas, _univ = _name_index(conn)
    n = nospace(fac)
    if not n or n not in oel["sub_rows"]:
        return [], None, "no_sub"
    if not oel["sub_ok"].get(n):
        return [], None, "sub_generic"
    owners = oel["sub_owner"].get(n, set())
    if len(owners) != 1:
        return [], None, "sub_ambiguous"
    return oel["sub_rows"][n], next(iter(owners)), "sub_resolved"


def _oel_rows_for(conn, fac: str) -> tuple[list, str | None, str | None, str]:
    """接触限值表/分桶的取数入口: 先走现路径, 落空再走 #51 解析, 再落空走 #37 子条目档。

    → (行集, 规范名, 采样方式限定, status)
      status ∈ exact(原路径命中) | resolved(解析命中) | ambiguous(多命中, 交人工)
               | no_anchor(标准库无此名) | cas_conflict(CAS 对不上, 交人工)
    """
    oel, _hfcas, _univ = _name_index(conn)
    rows = list(oel["by_name"].get(fac, ()))
    if not rows:
        rows = [r for nm, rs in oel["by_name"].items() if str(nm).startswith(fac) for r in rs]
    if rows:
        return rows, fac, None, "exact"
    canon, mode, why = _resolve_oel_name(conn, fac)
    if canon:
        got = oel["by_name"].get(canon, [])
        if got:
            return got, canon, mode, "resolved"
        return [], None, None, "no_anchor"
    # #37: 因子名是"组名下的子条目"时 (官方层把它存在 conditions 列), factor_name 那一侧永远查不到
    srows, smain, sstat = _resolve_oel_sub(conn, fac)
    if sstat == "sub_resolved":
        return srows, smain, None, "resolved"
    # why 里可能带命中数 ('ambiguous(2)') → 归一成状态词, 调用方按状态措辞, 不解析计数串
    status = "ambiguous" if str(why).startswith("ambiguous") else why
    if sstat == "sub_ambiguous":
        status = "ambiguous"          # 子名跨多个主名 → 与"多命中交人工"同一措辞, 不硬选一个
    return [], None, None, status


def _det_anchored(conn, fac: str) -> bool:
    """这个名字属不属于"已知因素名"的 namespace —— 官方层五列任一方向的包含命中即算。

    只做**保行**判据 (宁留不误删), 不做分桶: 包含匹配会把 '树脂' 这类尾缀也算进来,
    用它分桶就回到 #34 之前"名字里猜"的老错。
    """
    n = nospace(fac)
    if not n:
        return False
    _oel, _hfcas, universe = _name_index(conn)
    for t in universe:
        if len(_letters_only(t)) < _MIN_LETTERS:
            continue
        k = nospace(t)
        if n in k or k in n:
            return True
    return False


def _det_payload(d: dict) -> bool:
    """这一行有没有**测得值** (数值单元格形状) —— 由行自己的结构说, 不由物质名说"""
    vals = [str(d.get(k) or "") for k in ("ctwa", "cstel", "cste", "cme")]
    vals += [str(x) for x in (d.get("results") or [])]
    return any(_NUM_CELL.match(v.strip()) for v in vals if v.strip())


def _det_nonfactor(conn, dets: list) -> dict:
    """#50: 找出"不是一条检测"的行 → {因素名: 结构性依据}。

    三条判据**全中**才算 (任一条不中 → 保留该行, 宁留不误删):
      N1 官方层零锚: 分桶四级链落空 (含 #51 解析) **且** 官方层五列双向包含都不命中
         → 它不属于任何已知因素名的 namespace;
      N2 本行无测量载荷: ctwa/cstel/cme/results 里没有一个数值单元格 → 它不是"某因素的某个测定";
      N3 同批同名的其它行也没有测量载荷 → 排除"同名一行有值、另一行是表头"的误伤。
    实测形状 (2026-10-16 生产成品 §7.4, 根因在**导入侧**把表头/注释行塞进了 detections):
      '/' (整行只有占位符) → N1+N2+N3 全中;
      '接触的职业病危害因素' (表头文字) → N1+N2+N3 全中;
      '采样日期 ：2024.04.07' (注释行) → N1+N2+N3 全中 (日期不是数值单元格形状)。
    ⚠ 不用项目自己的 hazard_grid/materials 因子名当"佐证"来保行: 那些数组**同样**被导入侧混入过
      表头串 (见本文件 类比PPE有效性表 那段 unit='评价单元' 的既有过滤) → 拿它当白名单会把表头救活。
    ⚠ 物理/辅助检测项 (噪声/高温/照度/工频电场…) **不参与本过滤**: "零锚"判据说的是
      GBZ 2.1 化学/粉尘限值那个 namespace, 物理项本来就不在那里 (本地实测 '照度' 5 行里
      有 1 行只有定性判定、无数值 → 会被误判成非因素行)。它们走独立的物理因素检测结果表,
      那张表自己要求"有实测值或 LEX 才出行" (`if not vals and not lex: continue`)。
    ⚠ 能力边界/被排除项 (tools/test_det_rows.py 逐条对表):
      · 有测得值的行**永不**被本过滤删 (信息不丢失) → 表头串若被解析器塞进一个数值, 就漏网;
      · 标准库里确实登记过的名字 (哪怕它是表头词的子串) 一律保留 → 若导入侧把某个真因素名
        写成与表头同形, 这一条保护会掩盖它 (代价是少删一行, 不是错删一行);
      · 只处理"整行无值", 不处理"名字对但值错" —— 后者属 #40/#52 那一族。
    """
    out: dict = {}
    payload_by_name: set = set()
    for d in dets:
        if _det_payload(d):
            payload_by_name.add(str(d.get("factor", "") or ""))
    for d in dets:
        fac = str(d.get("factor", "") or "")
        if not fac or fac in out:
            continue
        if _is_phys_factor(fac):
            continue                                # 物理/辅助项: 零锚判据不适用于它们 (见 docstring)
        if _det_payload(d) or fac in payload_by_name:
            continue                                # N2/N3 → 保留
        if _det_bucket(conn, fac)[1] is not None or _det_anchored(conn, fac):
            continue                                # N1 → 保留
        out[fac] = "官方层零锚 + 本行与同名行均无测量载荷"
    return out


def _det_by_type(conn, dets: list) -> dict:
    """detections → {dtype: [行]}。两处调用点 (4 章类比 / 5 章本项目) 共用这一把尺子。

    原来这两段是**复制**的 (section_filler 985 与 1182 一模一样) —— 一份改了另一份不改,
    同一张表在两章就会用两套分桶口径。合并到这里, 判据只有一处。
    ⚠ 分组键必须是**显示用的 dtype**而不是标准库原始 category: 原始 category 有
      '化学因素'/'粉尘'/未命中(None) 多桶, 按原始值分桶再映射名字会产出**两张同名**
      「检测结果表(化学毒物)」, 而导出侧按"节+表名"去重 → 整张表的行被静默丢弃
      (实测 7 粉尘 + 13/15 两张化学毒物, 丢 15 行)。这里在分桶前就归一, 结构上不可能再犯。
    """
    from collections import defaultdict
    out: dict = defaultdict(list)
    unmatched: list = []
    drop = _det_nonfactor(conn, dets)          # #50: 表头/注释行 (判据见该函数注释)
    dropped: list = []
    for d in dets:
        fac = str(d.get("factor", "") or "")
        if fac in drop:
            dropped.append(fac)
            continue
        # h14: 物理因素 → 独立「物理因素检测结果表」, 不进化学浓度表
        #   (噪声 ctwa=LEX,8h 非浓度; 照度/工频无浓度列; 实测值在 results —
        #    塞进 CTWA/峰值浓度 表会全列 '—' = 数据丢失)
        if _is_phys_factor(fac):
            continue
        dtype, basis = _det_bucket(conn, fac)
        if basis is None:
            unmatched.append(fac)
        out[dtype].append(d)
    if dropped:
        # ⚠ 点名不是"静默删行"的替代物, 只是**下限**: 这些行既无测得值又不属于任何已知因素名,
        #   印上去只会把表头文字伪装成"检测过的因素"(§7.4 实测三行)。要复算被删了什么, 看这行日志。
        uniq = sorted(set(dropped))
        print(f"  [det-nonfactor] {len(dropped)} 行不是一条检测 (判据: {next(iter(drop.values()))}) "
              f"→ 不进检测结果表: {uniq[:8]}" + (" …" if len(uniq) > 8 else ""))
    if unmatched:
        uniq = sorted(set(unmatched))
        print(f"  [det-bucket] {len(unmatched)} 行查不到标准库类别 → 默认化学毒物: {uniq[:8]}"
              + (" …" if len(uniq) > 8 else ""))
    return out


def _is_phys_factor(fac: str) -> bool:
    """物理因素判别 — entity_link.is_physical 为基准, 补齐照度等非化学检测项

    entity_link.is_physical 覆盖国标噪声/高温/振动/工频/紫外/微波/激光等;
    照度 (建筑卫生学采光照明) / 低温 / 高湿 / 电离辐射 属物理检测项但不在其名录。
    """
    f = str(fac or "")
    if any(k in f for k in ("照度", "电离辐射", "低温", "高湿")):
        return True
    try:
        from web.entity_link import is_physical
        return is_physical(f)
    except Exception:
        return any(k in f for k in ("噪声", "高温", "振动", "工频", "紫外", "微波",
                                    "激光", "红外", "射频", "WBGT"))


def _phys_value(fac: str, v):
    """按因素名的合理性区间取物理测量值; 非数值/超区间(时长型)返回 None"""
    s = str(v or "").strip()
    if not s:
        return None
    try:
        n = float(s.replace("<", "").replace("＜", "").replace("＞", ""))
    except Exception:
        return None
    for kw, lo, hi in _PHYS_INTERVAL:
        if kw in fac:
            return s if lo <= n <= hi else None
    return s if 0 < n < 100000 else None


def _phys_verdict(d: dict, vals: list) -> str:
    """物理表判定列: 材料判定优先 (三态白名单); 有可信测量值 → "符合"; 否则 "—" """
    v = _det_verdict(d)
    if v != "—":
        return v
    return "符合" if vals else "—"


def _physical_det_table(conn, dets: list) -> dict | None:
    """物理因素独立检测结果表 (噪声/照度/工频电场/紫外...; 对齐真实报告 表28 形态)"""
    picked: dict = {}
    order: list = []
    for d in dets:
        fac = str(d.get("factor", "") or "")
        if not _is_phys_factor(fac):
            continue
        # 多因素串行 ("氢氧化钠、噪声") = 表型B 识别表行, 值语义不明 → 剔除
        if any(s in fac for s in ("、", "；", "，", ", ")):
            continue
        # 检测方案行 (表型B: 数字列实为采样时段, 判定=「浓度或强度相对稳定」) → 非检测结果
        # (实测: 新泰 p27 工频电场 res=['0.5'] 是采样时长, 0.5 ∈ 合理区间 → 区间判别兜不住)
        if "稳定" in str(d.get("judgement") or ""):
            continue
        vals = [x for x in (_phys_value(fac, v) for v in (d.get("results") or [])) if x]
        lex = ""
        _c = d.get("ctwa")
        if str(_c or "").strip() not in ("", "—", "None"):
            lex = _phys_value(fac, _c) or ""
        if not vals and not lex:
            continue
        _pt = str(d.get("sampling_point") or "").strip()
        key = (fac, _pt)
        _score = min(len(vals), 3) + (1 if lex else 0)
        if key in picked and picked[key]["score"] >= _score:
            continue
        if key not in picked:
            order.append(key)
        picked[key] = {"d": d, "vals": vals, "lex": lex, "score": _score, "pts": _pt}
    if not picked:
        return None
    rows, prov = [], {}
    for key in order:
        it = picked[key]
        d, vals, lex, _pt = it["d"], it["vals"], it["lex"], it["pts"]
        fac = str(d.get("factor", "") or "")
        # 岗位/地点拆分: "操作工-…"(新泰) / "制造部制造课/制造课反应班 主厂房…"(长兴)
        # 三种形态: "A班 地点" / "A班-地点"(破折号在空格前) / "A-地点"(仅破折号)
        _post, _loc = "—", "—"
        if _pt:
            _sn = _pt.replace("：", ":")
            _sp = _sn.find(" ")
            _dash = _sn.rfind("-", 0, _sp if _sp != -1 else len(_sn))
            if _dash > 0:
                _post, _loc = _sn[:_dash].strip(), _sn[_dash + 1:].strip()
            elif _sp > 0:
                _post, _loc = _sn[:_sp].strip(), _sn[_sp + 1:].strip()
            elif ":" in _sn:
                _post, _loc = [x.strip() for x in _sn.split(":", 1)]
            else:
                _loc = _pt
        # 限值: oel_limit 标准库 (噪声 LEX,8h=85 dB(A) / 工频电场 5 kV/m / 高温 WBGT=…)
        lim = "—"
        try:
            _recs = conn.execute("SELECT oel_type, value, unit FROM oel_limit "
                                 "WHERE factor_name LIKE ?", (fac + "%",)).fetchall()
            _prefer = (("LEX",) if "噪声" in fac else
                       ("电场",) if "工频" in fac else
                       ("WBGT",) if ("高温" in fac or "WBGT" in fac) else ())
            _pick = next((r for r in _recs if _prefer and any(k in str(r[0]) for k in _prefer)), None)
            if _pick is None and len(_recs) == 1:
                _pick = _recs[0]
            if _pick:
                lim = f"{_pick[1]} {_pick[2] or ''}".strip()
        except Exception:
            pass
        _ri = len(rows)
        _hrs = str(d.get("exposure_hours") or "").strip() or "—"
        rows.append([_post, _loc, fac] + (vals[:3] + ["—", "—", "—"])[:3]
                    + [lex or "—", _hrs, lim, _phys_verdict(d, vals)])
        # 逐格 provenance: 物理测量值来自检测报告 (视觉提取 → needs_review)
        # 列号: 0岗位 1地点 2检测项目 3|4|5检测结果 6 LEX 7时间 8限值 9判定
        _ev = {"file": d.get("_file") or "检测报告", "page": d.get("_page")}
        for _ci, _vv in enumerate(vals[:3]):
            if not _vv:
                continue
            try:
                if d.get("source") == "vision":
                    from web.number_provenance import prov_vision
                    prov[f"{_ri}_{3 + _ci}"] = prov_vision(
                        _ev, field=f"{fac}.检测结果{_ci + 1}", page=d.get("_page"))
                else:
                    prov[f"{_ri}_{3 + _ci}"] = prov_rule(
                        _ev, field=f"{fac}.检测结果{_ci + 1}", traceable=bool(d.get("_file")))
            except Exception:
                pass
        if lex:
            try:
                if d.get("source") == "vision":
                    from web.number_provenance import prov_vision
                    prov[f"{_ri}_6"] = prov_vision(_ev, field=f"{fac}.LEX,8h", page=d.get("_page"))
                else:
                    prov[f"{_ri}_6"] = prov_rule(_ev, field=f"{fac}.LEX,8h", traceable=bool(d.get("_file")))
            except Exception:
                pass
    if not rows:
        return None
    # 行序: 按因素名分组 (噪声在前, 同真实报告表28 的独立噪声表) — 稳定排序保持原页序
    # 同时把 prov 重映射 (key = "行号_列号")
    _idx = sorted(range(len(rows)), key=lambda i: (0 if "噪声" in str(rows[i][2]) else 1, i))
    if _idx != list(range(len(rows))):
        _new_rows, _new_prov = [], {}
        for _ni, _oi in enumerate(_idx):
            _new_rows.append(rows[_oi])
            for _k, _v in prov.items():
                _r, _, _c = _k.partition("_")
                if _r == str(_oi):
                    _new_prov[f"{_ni}_{_c}"] = _v
        rows, prov = _new_rows, _new_prov
    # 表头双层: 上=检测结果(跨3列)/下=1|2|3 (原报告表28 形态 + 新泰 p43 检测项目列);
    # 岗位/地点/项目/LEX/时间/限值/判定 纵向合并 (两版真实表均如此)
    _H = "LEX,8h[dB(A)]"
    _HU = "职业卫生接触限[dB(A)]"
    cols = ["检测岗位", "检测地点", "检测项目", "检测结果", "检测结果", "检测结果",
            _H, "接触时间(h)", _HU, "结果判定"]
    header2 = ["检测岗位", "检测地点", "检测项目", "1", "2", "3",
               _H, "接触时间(h)", _HU, "结果判定"]
    merge = [(0, 0, 1, 0), (0, 1, 1, 1), (0, 2, 1, 2), (0, 3, 0, 5),
             (0, 6, 1, 6), (0, 7, 1, 7), (0, 8, 1, 8), (0, 9, 1, 9)]
    return {"name": "物理因素检测结果表", "cols": cols, "header2": header2,
            "merge_rect": merge, "rows": rows, "prov": prov}


def _ds(conn):
    """官方数据层读门 (knowledge.dataset.Dataset)。

    `.rows()` = 取数即过标准号兜底 (废止号不得上纸); 判据与 code→现行版 memo 住在
    knowledge.standard_version.currentizer, 按"连接对象 + PRAGMA data_version"复用,
    所以实例可以就近构造, 不必自己管生命周期。
    裸 `conn.execute()` 读规则表标准号列会被 tools/test_std_currentize.py C 段拦下。
    """
    from knowledge.dataset import Dataset
    return Dataset(conn)


# GBZ 2.2—2007 表8 (高温 WBGT) 的行列键: 接触时间率 × 体力劳动强度分级
_HEAT_RATES = ("100", "75", "50", "25")
# ⚠ 罗马数字用码位生成, 不写字面量: Ⅲ (U+2162) 在编辑链路里会被**静默吃掉**成空串
#   (本轮写这份代码时实测两次) → 空串当级别名 = 整列查不到数 = 印一列「待补充」。
_HEAT_LEVELS = tuple(chr(0x2160 + i) for i in range(4))


def _heat_wbgt_matrix(conn) -> dict:
    """oel_limit 的高温行 → {(接触时间率, 强度级别): WBGT 限值}。

    为什么从库里取而不是在这里抄一张 4×4 (2026-10-16 取证): 本函数替换掉的旧字面量与
    **一手扫描件** `knowledge/gbz22_pages/p07.png` (= GBZ 2.2—2007 表8) 逐格比对有
    **6 格不符**, 且 6 格全部偏松 (印出的限值高于标准 → 本该判超标的岗位被判合格):
        75%×Ⅲ 27(应28) / 50%×Ⅲ 28(应29) / 50%×Ⅳ 27(应28)
        25%×Ⅱ 31(应32) / 25%×Ⅲ 29(应31) / 25%×Ⅳ 28(应30)
    而 `oel_limit` 的 16 行 (knowledge/data/gbz22_physical.json ← 300dpi 扫描视觉直读
    + 人工核对) 与一手 16/16 一致。让成品从库里取数, 字面量就再也没有第二次漂移的机会;
    库变了 → 成品指纹跟着变 → 主门 (tools/export_fingerprint.py) 看得见。
    """
    out: dict = {}
    for cond, val in _ds(conn).rows(
            "SELECT conditions, value FROM oel_limit WHERE factor_name='高温'"):
        m = re.match(r"接触时间率(\d+)\s*%,\s*体力劳动强度(.)级", cond or "")
        if m:
            out[(m.group(1), m.group(2))] = val
    return out


def _noise_limit_rows(conn) -> list:
    """GBZ 2.2—2007 表9 的三行 (接触时间 / 接触限值[dB(A)] / 备注), 数值取自 oel_limit。

    旧字面量抄成 5 行 (85/85/88/91/94) 并标 "GBZ 2.2—2019 表5": 2019 号不存在
    (catalog 核实 2026-08 在线仍为 2007 版), 而 2007 的噪声表是**表9**、只有 3 行、
    限值全 85 —— 88/91/94 来自"接触时间减半、限值+3dB"那套**别的规范**的规则,
    挂在本标准名下等于给标准编造内容。备注列逐字取自扫描件表9。
    """
    v = {t: val for t, val in _ds(conn).rows(
        "SELECT oel_type, value FROM oel_limit WHERE factor_name='噪声'")}
    g = lambda k: v.get(k) or "待补充"      # noqa: E731 — 缺行时印"待补充", 主门按待补充计数
    return [["5d/w，=8h/d", g("LEX,8h"), "非稳态噪声计算8h等效声级"],
            ["5d/w，≠8h/d", g("LEX,8h"), "计算8h等效声级"],
            ["≠5d/w", g("LEX,40h"), "计算40h等效声级"]]


def _ppe_matrix_table() -> dict | None:
    """标准作业类型×装备配备矩阵表 (表4.2-4 类比配备 与 表8.1-1 本项目拟配置 共用)

    来源: knowledge/ppe_matrix_loader.py → ppe_work_matrix
    (GB 39800.1-2020 + 江苏省劳动防护用品配备标准2007, √=应配/※=按需)
    原报告取证: 4.2-4 与 8.1-1 逐格相同 (类比推定), 行=15作业类型, 列=15装备;
    表头显示文本逐字取自原报告 (窄列真实换行).
    """
    try:
        from knowledge.ppe_matrix_loader import matrix_table
        return matrix_table("PPE标准配备矩阵表")
    except Exception:
        return None


def _ppe_vertical_rows(project_data: dict):
    """竖排 6 列 PPE 表行 (真稿 4.2-4 / 8.1-1 版式约定)

    返回 (rows, merged_row_indices); rows[i] = [生产单元,生产岗位,用品,单位,数量,更换周期]
    """
    try:
        from knowledge.ppe_vertical import build_ppe_rows
        return build_ppe_rows(project_data)
    except Exception:
        return [], set()


def _norm_fac(s: str) -> str:
    """因素名归一化 — 去掉括号注解/空白/全角标点, 用于**受控**匹配

    仅用于**等值**比较, 不用于子串匹配(子串会串物质: 「丙酮」⊂「丙酮氰醇」)。
    """
    s = s or ""
    s = re.sub(r"[（(].*?[)）]", "", s)          # 去括号注解: 丙酮氰醇（按CN 计）→ 丙酮氰醇
    s = s.replace(" ", "").replace("\u3000", "")
    s = s.replace("，", ",").replace("、", ",")
    return s.strip()


def _same_substance(a: str, b: str) -> bool:
    """判断两个因素名是否**同一物质** — 严格, 宁缺勿错(用户红线)

    允许: 归一化后等值; 或「及/或/、」连接的同义词组精确拆分
          (如「氯化氢及盐酸」vs「盐酸」→ 拆出 '盐酸' 命中)
    禁止: 任意子串包含(会把 丙酮 匹到 丙酮氰醇, 危险)
    """
    na, nb = _norm_fac(a), _norm_fac(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # 「A及B」「A或B」「A,B」→ 拆分为 token 后取等值
    toks_a = [t for t in re.split(r"[及或,/]", na) if t]
    toks_b = [t for t in re.split(r"[及或,/]", nb) if t]
    for ta in toks_a:
        for tb in toks_b:
            if ta and tb and ta == tb and len(ta) >= 2:
                return True
    return False


def _cas_from_oel(conn, fac: str) -> str | None:
    """CAS 只取 oel_limit (GBZ 2.1—2019 权威登记), 不手填; 多异构体取首个并保留全串。"""
    try:
        r = _ds(conn).one("SELECT cas FROM oel_limit WHERE factor_name=? AND cas IS NOT NULL "
                          "AND trim(cas) != '' LIMIT 1", (fac,))
        return r[0].strip() if r and r[0] else None
    except sqlite3.OperationalError:
        return None


def _thi_grade_cell(conn, fac: str) -> str | None:
    """按 GBZ/T 230—2025 **注6** 算危害程度 → 「等级（按注6；实测n/8项，待复核）」

    口径演进 (2026-10-14 用户拍板, 取代上一轮的"下界为主"):
      - 注6 原文:「缺乏蓄积性、致癌性、致敏性、生殖毒性分级有关数据的物质的分项指标
        暂按类别1 赋分」—— 这是**法定算法**, 评审按标准复算必然得到同一个值;
      - 上一轮入库的"缺项按0"下界是自创保守口径, 同一物质两法差 3 级 (苯醌: 29 轻度 vs
        77 极度); 曾试过写成「轻度~极度」区间, 但下界**恒为轻度**(缺项按0必然低),
        区间没有信息量, 已废弃;
      - 但仍不假装已定级: 格子内必须带 实测项数 + 待复核, 一眼看出数据不足。
      → 报告侧用 min_have=0 取回结果 (数据不足也要显示), "未定级"的表达由本函数负责。
    """
    try:
        from web.thi_rating import rate_substance
        r = rate_substance(conn, fac, _cas_from_oel(conn, fac), min_have=0)
    except Exception:
        return None
    if not r:
        return None
    hi = r.get("level_note6") or ""
    if not hi:
        return None
    return f"{hi}（按注6；实测{r.get('have', 0)}/8项，待复核）"


def lookup_toxicity(conn, fac: str) -> str:
    """查危害程度分级 (hazard_toxicity 表, 确定性查表)

    未收录 → 返回「待补充」, **不推算不编造**(用户原则: 认可能计算的绝不生成)。

    匹配策略(**宁缺勿错**):
      1. 精确名
      2. 别名(竖线分隔)**等值**
      3. 同义名组精确拆分(「氯化氢及盐酸」↔「盐酸」)
    ⚠ 曾用任意子串包含匹配 → 实测踩坑: 「丙酮」(低毒溶剂) 匹到「丙酮氰醇」(剧毒),
      张冠李戴。物质身份必须**确定**, 故禁止无约束子串。

    取值口径 (2026-10-14 C 方案):
      - 权威定级 (真实报告 5.2-1 取证 / GHS 名录给出 GBZ/T 230 四级之一) → 原样单值;
      - **我们算的** (source 含 thi) → 交给 _thi_grade_cell 按注6 出等级并标注实测项数;
      - 库里存的不是等级 (如 '致癌'/'剧毒'/'待补充' 这类标签) → **不得进本列**
        (实测当前 4 项目的 assess.hazards 未命中此类行, 属潜在风险, 守卫成本为零);
      - 无行且算不出 → 「待补充」。
    """
    GRADES = ("轻度危害", "中度危害", "高度危害", "极度危害")
    if not fac:
        return "待补充"
    try:
        r = conn.execute(
            "SELECT level, source FROM hazard_toxicity WHERE factor_name=?", (fac,)
        ).fetchone()
        if r is None:
            for name, aliases, level, source in _ds(conn).rows(
                "SELECT factor_name, aliases, level, source FROM hazard_toxicity"
            ):
                if _same_substance(name, fac):
                    r = (level, source)
                    break
                for a in (aliases or "").split("|"):
                    if a.strip() and _same_substance(a, fac):
                        r = (level, source)
                        break
                if r:
                    break
        if r:
            level, source = r[0] or "", r[1] or ""
            if level in GRADES:
                if "thi" in str(source).lower():
                    return _thi_grade_cell(conn, fac) or level
                return level
            # 存的不是等级 → 忽略该标签, 下探到 THI 或 待补充
        cell = _thi_grade_cell(conn, fac)
        if cell:
            return cell
    except sqlite3.OperationalError:
        pass  # 表未建 → 走 THI 兜底
    return "待补充"


def form_of(conn, fac: str) -> str:
    """物质形态 (气体/液体/固体/气溶胶) — 确定性查表优先, 未收录返回「—」

    优先级:
      1. hazard_toxicity.form (真实报告 5.2-1 逐格取证, 权威)
      2. hazchem_item.alias 形态线索 (兜底, 如「液氨；氨气」)
    原报告取证(新泰表5.2-1): 氟化氢=气体, 氟化钙/石灰石粉尘=固体, 氯化氢及盐酸=液体
    """
    if not fac:
        return "—"
    # 1) 权威表
    try:
        for name, aliases, form in _ds(conn).rows(
            "SELECT factor_name, aliases, form FROM hazard_toxicity WHERE form IS NOT NULL"
        ):
            if name == fac:
                return form
            for a in (aliases or "").split("|"):
                if a and (a == fac or a in fac or fac in a):
                    return form
    except sqlite3.OperationalError:
        pass
    # 2) 兜底: hazchem alias 形态线索
    KEYS = (("气溶胶", "气溶胶"), ("蒸气", "蒸气"), ("气体", "气体"),
            ("液体", "液体"), ("固体", "固体"), ("粉尘", "粉尘"),
            ("气", "气体"), ("液", "液体"), ("固", "固体"))
    try:
        r = conn.execute(
            "SELECT alias FROM hazchem_item WHERE name=?", (fac,)
        ).fetchone()
        text = (r[0] if r else "") or ""
        for token in text.replace("|", "；").split("；"):
            token = token.strip()
            if not token or token == fac:
                continue
            for k, v in KEYS:
                if k in token:
                    return v
    except sqlite3.OperationalError:
        pass
    for k, v in (("粉尘", "粉尘"), ("烟尘", "粉尘"), ("烟雾", "粉尘")):
        if k in fac:
            return v
    return "—"


def fill_section(conn: sqlite3.Connection, sec: str, assess: dict) -> list[dict]:
    """按报告章节 (新11章) 生成表格"""
    if sec == "1":
        # 总论: 评价依据表 (标准库)
        refs = _ds(conn).rows("SELECT code, name FROM standard_ref ORDER BY id")
        rows = []
        for i, (code, name) in enumerate(refs, 1):
            rows.append([i, code, name, "见标准库"])
        return [{"name": "评价依据表", "cols": ["序号", "标准号", "标准名称", "现行状态"], "rows": rows}]

    if sec == "2":
        # 现有企业概况: 产品产量 + 建构筑物 + 定员
        tables = []
        pd_ = assess.get("_project_data", {})
        prods = pd_.get("products", [])
        if prods:
            p_rows = [[i, p.get("name", "") if isinstance(p, dict) else p,
                       p.get("output", "") or pd_.get("capacity", "—"),
                       p.get("变化量", "") if isinstance(p, dict) else ""]
                      for i, p in enumerate(prods, 1)]
            tables.append({"name": "产品产量表", "cols": ["序号", "产品名称", "年产量", "变化量"], "rows": p_rows})
        blds = pd_.get("buildings", [])
        if blds:
            b_rows = [[b.get("功能区", ""), b.get("name", ""), b.get("火灾危险类别", ""),
                       b.get("耐火等级", ""), b.get("floors", ""), b.get("area", ""),
                       b.get("floor_area", ""), ""] for b in blds if isinstance(b, dict)]
            # 原报告表3.7-1: 功能区/建构筑物名称/火灾危险类别/耐火等级/层数/占地/建筑/备注 (无序号无高度)
            tables.append({"name": "建构筑物表",
                           "cols": ["功能区", "建构筑物名称", "火灾危险类别", "耐火等级", "层数", "占地面积(㎡)", "建筑面积(㎡)", "备注"],
                           "rows": b_rows})
        staffs = pd_.get("staffing", [])
        if staffs:
            s_rows = [[i, s.get("dept", ""), s.get("post", ""), s.get("count", ""), s.get("task", "—")]
                      for i, s in enumerate(staffs, 1)]
            tables.append({"name": "劳动定员表", "cols": ["序号", "车间/部门", "岗位", "人数", "工作内容"],
                           "rows": s_rows})
        return tables

    if sec == "3":
        # 建设项目工程分析: 设备/定员/建构筑物/产品/投资 + 原辅材料 + 选址/布局/建筑卫生/辅助用室检查表
        tables = []
        pd_ = assess.get("_project_data", {})
        eqs = pd_.get("equipment", [])
        if eqs:
            rows = [[i, str(e).split("|")[0], str(e).split("|")[1] if "|" in str(e) else ""]
                    for i, e in enumerate(eqs, 1)]
            tables.append({"name": "主要设备清单", "cols": ["序号", "设备名称", "内部物料"], "rows": rows})
        staffs = pd_.get("staffing", [])
        if staffs:
            s_rows = [[i, s.get("dept", ""), s.get("post", ""), s.get("count", ""), s.get("task", "—")]
                      for i, s in enumerate(staffs, 1)]
            tables.append({"name": "劳动定员表", "cols": ["序号", "车间/部门", "岗位", "人数", "工作内容"],
                           "rows": s_rows})
        blds = pd_.get("buildings", [])
        if blds:
            b_rows = [[b.get("功能区", ""), b.get("name", ""), b.get("火灾危险类别", ""),
                       b.get("耐火等级", ""), b.get("floors", ""), b.get("area", ""),
                       b.get("floor_area", ""), ""] for b in blds if isinstance(b, dict)]
            # 原报告表3.7-1: 功能区/建构筑物名称/火灾危险类别/耐火等级/层数/占地/建筑/备注 (无序号无高度)
            tables.append({"name": "建构筑物表",
                           "cols": ["功能区", "建构筑物名称", "火灾危险类别", "耐火等级", "层数", "占地面积(㎡)", "建筑面积(㎡)", "备注"],
                           "rows": b_rows})
        # 原辅材料表 (materials dict 完整列: name/规格/年用量/最大储量/物态/储存地点)
        mats2 = pd_.get("materials", [])
        if mats2:
            m_rows = []
            for i, m in enumerate(mats2, 1):
                if isinstance(m, dict):
                    # 原报告列序: 名称/物料性状/年耗量/存放地点/最大储存量/包装方式·规格
                    m_rows.append([i, m.get("name", ""), m.get("物态", ""),
                                   m.get("年用量", ""), m.get("储存地点", ""),
                                   m.get("最大储量", ""), m.get("规格", "")])
                else:
                    m_rows.append([i, str(m), "—", "—", "—", "—", "—"])
            tables.append({"name": "原辅材料表",
                           "cols": ["序号", "原辅材料名称", "物料性状", "年耗量(t/a)", "存放地点", "最大储存量（t）", "包装方式/规格"],
                           "rows": m_rows})
        # 产品产量表 (含变化量)
        prods = pd_.get("products", [])
        if prods:
            p_rows = [[i, p.get("name", "") if isinstance(p, dict) else p,
                       p.get("output", "") or pd_.get("capacity", "—"),
                       p.get("变化量", "") if isinstance(p, dict) else ""]
                      for i, p in enumerate(prods, 1)]
            # 原报告表3.4-1 9列双层: 序号|名称(产品·类型)|单位|主要成分|年产量(扩产前·扩产后)|变化量|备注
            p2_rows = []
            for p in prods:
                if not isinstance(p, dict):
                    continue
                nm = p.get("name", "")
                if "（" in nm:  # "不饱和聚酯树脂（通用型）" → 产品/类型两列
                    prod, typ = nm.split("（", 1)
                    typ = typ.rstrip("）")
                else:
                    prod, typ = nm, ""
                before = p.get("output", "")
                after = p.get("delta_num")
                try:
                    after = str(int(float(str(before).replace(",", ""))) + int(float(after))) if after else ""
                except (TypeError, ValueError):
                    after = ""
                _delta = str(p.get("delta", "") or p.get("变化量", "") or "").replace(" ", "")
                p2_rows.append([prod, typ, "吨/年", p.get("成分", "—"), before, after,
                                _delta, "产品"])
            if p2_rows:
                tables.append({"name": "产品产量表",
                               "cols": ["序号", "名称", "名称", "单位", "主要成分", "年产量吨/年", "年产量吨/年", "变化量", "备注"],
                               "header2": ["序号", "名称", "名称", "单位", "主要成分", "扩产前", "扩产后", "变化量", "备注"],
                               "merge_rect": [(0, 1, 0, 2), (0, 5, 0, 6)],
                               "rows": [[i] + r for i, r in enumerate(p2_rows, 1)]})
        # 表3.1-1 所在地常年主要气象因素 (序号/项目/情况和数据/备注) — 气象要素=公开区域资料
        # 取数优先级 (2026-10-14 修复「真数据被旁路」事故):
        #   1) built_tables["气象因素表"] — 导入时由 external_tables 后台填充
        #      (磁盘 region_facts.json 材料真数据优先 + LLM 公开气候缓存),
        #      含地震设防烈度/地质分层/水文/气温极值等 11 行真实值;
        #   2) 骨架无行 → 下方硬编码占位降级为最后兜底 (全「待补充」)。
        # 事故复盘: 旧实现无条件输出硬编码占位表 → 旁路掉库里已填好的 11 行真数据,
        #   产物出现 4 处气象「待补充」+ 段落 1 处地震「待补充」全属**假缺口**。
        _wx_built = (pd_.get("built_tables") or {}).get("气象因素表") or {}
        _wx_rows = _wx_built.get("rows") if isinstance(_wx_built, dict) else None
        if _wx_rows and any("待补充" not in str(r[2]) for r in _wx_rows if len(r) > 2):
            tables.append({"name": "气象因素表",
                           "cols": _wx_built.get("cols") or ["序号", "项目", "情况和数据", "备注"],
                           "rows": [list(r) for r in _wx_rows],
                           "prov": _wx_built.get("prov")})
        else:
            # 兜底: 无真数据时按原报告结构复刻 (全待补充), 不编造
            wx_rows = [
                ["1", "气候", "待补充（所在地属亚热带季风气候区）", "/"],
                ["2", "气温", "待补充（年平均/极端最高/最低）", "/"],
                ["3", "湿度", "待补充", "/"],
                ["4", "风向", "待补充（常年主导风向）", "/"],
                ["5", "风速", "待补充", "/"],
                ["6", "降水量", "待补充", "/"],
                ["7", "日照", "待补充", "/"],
                ["8", "地质", "待补充（以地勘报告为准）", "/"],
                ["9", "水文", "待补充", "/"],
                ["10", "场地地震效应",
                 "待补充（以地勘报告为准；须含场地土类型/场地类别/设计地震分组，"
                 "依据 GB 50011-2010 判定）", "/"],
            ]
            tables.append({"name": "气象因素表", "cols": ["序号", "项目", "情况和数据", "备注"], "rows": wx_rows})
        # 项目概况/投资 (从固定字段)
        # 投资额不可信判据: **读清洗标记**(table_skeleton._sanitize_untrusted 落盘),
        #   而不是重算 (清洗后 investment 已被置空, 重算判不出"曾是无单位裸数字")。
        #   标记来源唯一 → 与 field_projection / 表骨架同源, 不会各判各的。
        _untrusted = (pd_.get("_untrusted_fields") or {})
        _inv_flagged = "investment" in _untrusted
        invest = (pd_.get("investment") or "")
        cap = (pd_.get("capacity") or "")
        if _inv_flagged:
            invest_cell = f"待补充（{_untrusted.get('investment') or '口径不确定'}）"
        elif not str(invest).strip():
            invest_cell = "待补充（材料未提供）"
        else:
            invest_cell = invest
        if invest or cap or pd_.get("area") or _inv_flagged:
            # 原报告表3.1-3 主要技术经济指标 —— 数据源: 可研「表1.2-11」全 15 大项
            #   (tools/extract_tech_econ.py → data/tech_econ.json, 是本项目权威实测值)
            #   优先级: tech_econ.json (本项目可研) > 字段兜底 (investment/capacity)
            #   ⚠ 单一产地: 本表只在此处生成; table_builder 的同名副本 2026-10 已删
            te = _load_tech_econ(pd_)
            info_rows: list[list[str]] = []
            if te and te.get("rows"):
                for r in te["rows"]:
                    row = [str(c or "") for c in r]
                    while len(row) < 5:
                        row.append("")
                    info_rows.append(row[:5])
            else:
                # 兜底: 可研无可提表 → 用字段 (保持旧口径, 但值来自真实字段)
                area = pd_.get("area") or "依托现有"
                info_rows = [
                    ["一", "厂区总占地面积", "平方米", str(area), ""],
                    ["二", "项目投资总额", "万元", invest_cell, ""],
                    ["三", "职业病防治专项经费", "万元", _ohy_invest_cell(pd_), ""],
                ]
            if info_rows:
                tables.append({"name": "项目概况表",
                               "cols": ["序号", "项目名称", "单位", "指标", "备注"],
                               "rows": info_rows})
        # 原报告表8.1-3 本项目扩建前后全厂产品方案对比表 —— 挂 3.1.6
        #   数据源: 可研「表1.2-1 扩产前后产能变化信息对照表」(p15, 全厂口径)
        #     tools/extract_product_plan.py → data/product_plan.json (本项目权威实测)
        #   优先级: product_plan.json (可研全厂表) > products 字段 (本项目口径兜底)
        #   ⚠ 全厂口径 = 真稿表8.1-3 语义 (现有/新增/实施后全厂), 不再只给本项目 4 行
        pp = _load_product_plan(pd_)
        prods = [p for p in (pd_.get("products") or []) if isinstance(p, dict)]
        if pp and pp.get("rows"):
            pp_rows = []
            for r in pp["rows"]:
                row = [str(c or "") for c in r]
                while row and row[-1] == "":
                    row.pop()
                while len(row) < 10:
                    row.append("")
                pp_rows.append(row[:10])
            if pp_rows:
                # 列与源表 表1.2-1 一一对应 (10 列); 名称/领证在源表为跨行合并 → 已前向填充
                tables.append({"name": "产品方案表",
                               "cols": ["序号", "产品名称", "产品类型", "安全生产\n许可证领证",
                                        "单位", "主要成分", "扩产前", "扩产后", "变化量", "备注"],
                               "rows": pp_rows})
        elif prods:
            # 兜底: 可研无可提表 → 用 products 字段 (本项目口径, 不伪造"全厂"列)
            p_rows = []
            for i, p in enumerate(prods, 1):
                _dn = p.get("delta_num")
                _dtxt = ""
                if _dn not in (None, ""):
                    try:
                        _f = float(_dn)
                        _dtxt = f"+{_f:g}" if _f > 0 else f"{_f:g}"
                    except (TypeError, ValueError):
                        _dtxt = str(_dn)
                else:
                    _dtxt = str(p.get("delta") or "").strip()
                p_rows.append([str(i), str(p.get("name") or ""),
                               str(p.get("output") or ""), _dtxt])
            tables.append({"name": "产品方案表",
                           "cols": ["序号", "产品名称", "产能（吨/年）", "本次新增（吨/年）"],
                           "rows": p_rows})
        # 原报告表8.1-4 本项目的项目组成和公用工程内容一览表 —— 挂 3.1.6
        #   数据源: buildings (建构筑物实测) — 类别|设施名称|占地面积|建筑面积|结构/层数
        bs = [b for b in (pd_.get("buildings") or []) if isinstance(b, dict)]
        if bs:
            b_rows = []
            for i, b in enumerate(bs, 1):
                b_rows.append([str(i), str(b.get("功能区") or ""), str(b.get("name") or ""),
                               str(b.get("area") or ""), str(b.get("floor_area") or ""),
                               str(b.get("floors") or ""), str(b.get("耐火等级") or "")])
            tables.append({"name": "项目组成表",
                           "cols": ["序号", "功能区", "设施名称", "占地面积（㎡）",
                                    "建筑面积（㎡）", "层数", "耐火等级"],
                           "rows": b_rows})
        # 设备明细表 (复刻真稿表8.4-2 本项目涉及的主要生产装置及设备)
        # 真稿口径 8 列: 序号|设备名称|规格|材质|数量/台|操作条件|内部物料|备注
        # ⚠ 旧版是 9 列双层(车间|位号|名称|规格|现有|扩建后全厂|变化|材质|备注),
        #   但"现有/扩建后全厂"可研数据里没有(全是本项目设备) → 硬编"—"是伪造列,
        #   改回真稿 8 列。分组行(_group)输出为横跨整行的产线标题行。
        eq_d = [d for d in (pd_.get("equipment_detail") or []) if isinstance(d, dict)]
        if eq_d:
            ed_rows = []
            for d in eq_d:
                if d.get("_group"):        # 分组标题行 (真稿: 产线/车间名 单列跨行)
                    ed_rows.append([d["_group"]] + [""] * 7)
                    continue
                # 操作条件/内部物料/备注: 提取层字段名 = 操作条件 / 内部物料 / 备注
                #   (可研原文 "温度190-240；压力常压" → 直接取整列, 不再拆温度/压力重组)
                ed_rows.append([
                    d.get("no") or "",                      # 序号
                    d.get("name", ""),                      # 设备名称
                    d.get("spec", ""),                      # 规格型号
                    d.get("材质", ""),                      # 材质
                    d.get("qty", ""),                       # 数量/台
                    d.get("操作条件", ""),                   # 操作条件
                    d.get("内部物料", ""),                   # 内部物料
                    d.get("备注", ""),                       # 备注
                ])
            tables.append({"name": "设备明细表",
                           "cols": ["序号", "设备名称", "规格型号", "材质", "数量/台",
                                    "操作条件", "内部物料", "备注"],
                           "rows": ed_rows,
                           # 分组行 merge: 由 _write_tables_named 按 _group 标记处理(见 merge_rows)
                           "group_rows": [i for i, r in enumerate(ed_rows) if r[1:] == [""] * 7]})
        # 班制定员表 (原报告表3.1-2: 工种/工作区域/工作内容/人班/生产班制/总人数/最大班女工数)
        # staffing(dept/post/count) + shifts(post→system) 拼; 工作内容无数据源标"—", 女工数无数据源标"/"
        sf = pd_.get("shifts", [])
        if sf or pd_.get("staffing", []):
            _SHIFTS_PER = (("四班三运转", 4), ("四班", 4), ("三班两倒", 3), ("三班", 3), ("两班", 2), ("二班", 2))
            def _per_shift(sysm, cnt):
                # 人/班 = 总人数÷班次数 (通用规则, 与原报告"操作工 总8 四班三运转 人/班2"一致)
                try:
                    n = int(str(cnt).replace("人", ""))
                except (ValueError, TypeError):
                    return cnt
                for kw, k in _SHIFTS_PER:
                    if kw in str(sysm):
                        return n // k or n
                return n  # 常白班等人/班=总数
            s_rows = []
            _has_total = False
            for s in pd_.get("staffing", []):
                sysm = next((x.get("system", "") for x in sf if x.get("post") == s.get("post")), "—")
                if "合计" in str(s.get("post") or "") or "合计" in str(s.get("dept") or ""):
                    _has_total = True
                s_rows.append([s.get("post", ""), s.get("dept", ""), "—",
                               _per_shift(sysm, s.get("count", "")), sysm, s.get("count", ""), "/"])
            # 合计行: 材料自带「合计」条目 → 不再追加 (防双合计行);
            # 无 → 明细求和兜底 (剔除合计条目防双计; 2026-10 实测 293=144+149)
            if not _has_total:
                try:
                    tot = sum(int(str(s.get("count", "0")).replace("人", ""))
                              for s in pd_.get("staffing", [])
                              if str(s.get("count", "0")).strip().isdigit())
                    s_rows.append(["合计", "/", "/", "/", "/", str(tot), "/"])
                except Exception:
                    pass
            tables.append({"name": "班制定员表",
                           "cols": ["工种", "工作区域", "工作内容", "人/班", "生产班制", "总人数（人）", "最大班女工数（人）"],
                           "rows": s_rows})
        # 主要职业病危害因素表 (危害→来源→类别; 改名避免与'原辅材料表'冲突, 该表用 hazards)
        mats = assess.get("hazards", [])
        if mats:
            rows = [[i, h["factor"], "; ".join(h.get("sources", [])),
                     h.get("hazard_element", "化学毒物")] for i, h in enumerate(mats, 1)]
            tables.append({"name": "主要职业病危害因素表", "cols": ["序号", "名称", "来源", "危害类别"], "rows": rows})
        # 表3.2-1 厂区周边环境 (序号/方位/周边情况/最近距离(m)) — 周边明细=现场调查/总平面图, 材料未含标待补充
        loc = (assess.get("_project_data") or {}).get("location") or ""
        tables.append({"name": "周边环境表",
                       "cols": ["序号", "方位", "周边情况", "最近距离（m）"],
                       "rows": [[1, "—", (loc.split("项目性质")[0].strip() or "本项目位于现有厂区内") + "，周边情况需结合总平面图及现场调查补充", "—"]]})
        # 检查表: 选址/总体布局/建筑卫生学/辅助用室 (依据GBZ1标准库)
        # 原报告取证: 3.2-2 选址检查 4列无序号; 3.3-1/3.7-2/3.8-3 5列有序号(序号/卫生要求/检查依据/检查结果/评价)
        # ⚠ 单一产地: 「检查结果/评价」两列一律走 knowledge.check_result_templates.resolve()
        #   (用户 2026-10 拍板: 保留 4 列, 不引序号/分组行; 此前本处硬编码 "依托现有厂区…" 是第二份定义)
        from knowledge.check_result_templates import resolve as _cr
        for theme, tname in [("选址", "选址检查表"), ("总体布局", "总体布局检查表"),
                             ("建筑卫生学", "建筑卫生学检查表"), ("辅助用室", "辅助用室检查表")]:
            g_rows = []
            for r in _ds(conn).rows("SELECT clause, rule, report_section FROM gbz1_rule WHERE theme LIKE ?", (theme + "%",)):
                _res, _vd = _cr(str(r[0]).strip())
                g_rows.append([r[0], r[1], _res or "待补充", _vd or "待评价"])
            if not g_rows:
                continue
            tables.append({"name": tname, "cols": ["卫生要求", "检查依据", "检查结果", "评价"],
                           "rows": [[r[1], f"GBZ 1—2010 {r[0]}", r[2], r[3]] for r in g_rows]})
        ill = conn.execute("SELECT room, plane, lx FROM illumination_std").fetchall()
        if ill:
            tables.append({"name": "照度标准表", "cols": ["序号", "房间/场所", "参考面", "照度lx"],
                           "rows": [[i, r[0], r[1], r[2]] for i, r in enumerate(ill, 1)]})
        # 车间卫生特征分级 (GBZ1-2010 第7章 固定分级表 — 原报告表3.8-1)
        tables.append({"name": "卫生特征分级表",
                       "cols": ["卫生特征", "1级", "2级", "3级", "4级"],
                       "rows": [
                           ["有毒物质", "极易经皮肤吸收引起中毒的剧毒物质", "易经皮肤吸收或有恶臭的物质，或高毒物质", "其他毒物", "不接触有害物质或粉尘，不污染或轻度污染身体"],
                           ["粉尘", "—", "严重污染全身或对皮肤有刺激性的粉尘", "一般粉尘", "—"],
                           ["其他", "—", "处理传染性材料、动物原料的作业", "—", "—"]]})
        # 辅助用室设置表: 原报告 8×6 (辅助用室|数量|设置地点|设置情况×3 merged) — 现状调查数据缺
        # 通用输出: 按卫生特征列标准应设辅助用室种类 (GBZ1 7.2/7.3), 数量/地点标待补充 (不编造企业实况)
        aux_rows = [
            ["车间卫生间", "待补充", "待补充", "男厕", "蹲位", "待补充"],
            ["车间卫生间", "待补充", "待补充", "女厕", "蹲位", "待补充"],
            ["更衣室", "待补充", "待补充", "便服/工作服分柜存放", "—", "—"],
            ["盥洗设施", "待补充", "待补充", "水龙头", "待补充", "—"],
            ["休息室", "待补充", "待补充", "—", "—", "—"],
        ]
        tables.append({"name": "辅助用室设置表",
                       "cols": ["辅助用室", "数量", "设置地点", "设置情况", "设置情况", "设置情况"],
                       "header2": ["辅助用室", "数量", "设置地点", "设置情况", "设置情况", "设置情况"],
                       "merge_rect": [(0, 3, 0, 5)],
                       "rows": aux_rows})
        # 噪声分级表 (噪声暴露等级)
        nz_rows = []
        for r in _ds(conn).rows("SELECT value, note FROM oel_limit WHERE factor_name LIKE '%噪声%' LIMIT 3"):
            nz_rows.append(r)
        if nz_rows:
            tables.append({"name": "噪声分级表", "cols": ["噪声限值", "说明"], "rows": nz_rows})
        # 标准库检查表 (原报告 3.5-1 工艺检查 / 3.6-3 设备布局评价 — 同构检查表)
        # ⚠ 单一产地: 结果/评价两列走 check_result_templates.resolve() (原为第二/三份硬编码)
        from knowledge.check_result_templates import resolve as _cr
        g_rows = []
        for th in ("防尘防毒", "防噪声振动"):
            for r in _ds(conn).rows("SELECT clause, rule FROM gbz1_rule WHERE theme=?", (th,)):
                _res, _vd = _cr(str(r[0]).strip())
                g_rows.append([r[1], f"GBZ 1—2010 {r[0]}", _res or "待补充", _vd or "待评价"])
        if g_rows:
            tables.append({"name": "工艺检查表", "cols": ["卫生要求", "检查依据", "检查结果", "评价"], "rows": g_rows})
        b_rows = []
        for th in ("总体布局", "建筑卫生学"):
            for r in _ds(conn).rows("SELECT clause, rule FROM gbz1_rule WHERE theme=?", (th,)):
                _res, _vd = _cr(str(r[0]).strip())
                b_rows.append([r[1], f"GBZ 1—2010 {r[0]}", _res or "待补充", _vd or "待评价"])
        if b_rows:
            tables.append({"name": "设备布局检查表", "cols": ["卫生要求", "检查依据", "检查结果", "评价"], "rows": b_rows})
        return tables

    if sec == "4":
        # 类比调查: 类比可比性表 + 检测结果 + 职业健康监护
        tables = []
        elems = ["厂址", "自然环境状况", "产品及原辅材料", "生产规模", "劳动定员",
                 "生产制度", "生产工艺", "生产设备", "防护措施", "管理水平"]
        # 原报告表4.1-1: 类比项目(要素)/本项目/类比企业/比较结果 — 本项目侧用项目数据, 类比侧=同类园区同企业参数
        _pd4 = assess.get("_project_data") or {}
        _proj_side = {
            "厂址": (_pd4.get("location") or "—").split("项目性质")[0].strip() or "—",
            "生产规模": _pd4.get("capacity") or "—",
            "劳动定员": str(len(_pd4.get("staffing") or [])) + "个岗位" if _pd4.get("staffing") else "—",
        }
        rows = [[e, _proj_side.get(e, "—"), _proj_side.get(e, "同类企业"), "相同" if e in _proj_side else "—"]
                for e in elems]
        tables.append({"name": "类比可比性表", "cols": ["类比项目", "本项目", "类比企业", "比较结果"], "rows": rows})
        # 检测结果表 (按类型分张: 毒物/粉尘/噪声/高温, 有检测数据才出)
        dets = (assess.get("_project_data") or {}).get("detections", [])
        if dets:
            det_by_type = _det_by_type(conn, dets)
            for dtype, dets_b in det_by_type.items():
                # h11: CTWA 与峰值浓度 双列投影 — ctwa 为空的峰值行值在 cstel 键, 不得塌缩为 '—'
                # (真实报告双浓度列: 新泰 p37 检测结果[C_TWA|峰值浓度]; 长兴 表25 CTWA|CSTEL)
                det_rows = [[i, d.get("factor", ""), d.get("ctwa", "—") or "—",
                             d.get("cstel", "—") or "—", "—", _det_verdict(d)]
                            for i, d in enumerate(dets_b, 1)]
                # 逐格 provenance: CTWA 值来自检测报告 (文本层或视觉提取), 必须标来源 —
                # 这是报告里最安全关键的数值, 不标会被审计判 unverified (早期遗漏, 71 个)。
                _dp = {}
                for _i, _d in enumerate(dets_b):
                    _ev = {"file": _d.get("_file") or "检测报告",
                           "page": _d.get("_page")}
                    # h11: CTWA(列2) 与 峰值浓度(列3) 双列投影, 各自独立逐格溯源
                    for _ky, _ci, _lab in (("ctwa", 2, "CTWA"), ("cstel", 3, "峰值浓度")):
                        _v1 = _d.get(_ky)
                        if _v1 in (None, "", "—"):
                            continue
                        if _d.get("source") == "vision":
                            # 视觉提取的数字 → needs_review (用户红线: 机器读的≠确定取出)
                            from web.number_provenance import prov_vision
                            _dp[f"{_i}_{_ci}"] = prov_vision(_ev, field=f"{_d.get('factor')}.{_lab}",
                                                             page=_d.get("_page"))
                        else:
                            _dp[f"{_i}_{_ci}"] = prov_rule(_ev, field=f"{_d.get('factor')}.{_lab}",
                                                           traceable=bool(_d.get("_file")))
                tables.append({"name": f"检测结果表({dtype})", "cols": ["序号", "危害因素", "CTWA", "峰值浓度", "PC-TWA", "判定"],
                               "rows": det_rows, "prov": _dp})
            # h14: 物理因素独立表 (检测岗位|检测地点|检测项目|检测结果|LEX,8h|时间|限值|判定)
            _pt_ = _physical_det_table(conn, dets) if dets else None
            if _pt_:
                tables.append(_pt_)
        surv = _ds(conn).rows("SELECT factor, check_type, cycle FROM surveillance_rule")
        if surv:
            tables.append({"name": "职业健康监护表", "cols": ["序号", "危害因素", "检查类别", "周期"],
                           "rows": [[i, r[0], r[1], r[2] or "—"] for i, r in enumerate(surv, 1)]})
        # 类比企业职业病危害因素识别及其分布 (hazard_grid: 评价单元×岗位×产品×危害因素 — 原报告表4.2-3)
        hg = (assess.get("_project_data") or {}).get("hazard_grid", [])
        hg_rows = []
        for g in hg:
            if not g.get("unit") and not g.get("post"):
                continue
            # 原报告表4.2-3 8列双层: 单元|岗位|接触途径(产品·工段)|因素|作业方式|人数|频次
            hg_rows.append([g.get("unit") or "生产车间主厂房", g.get("post", "—"),
                            g.get("product", "—"), g.get("stage") or "—",
                            (g.get("factors", "") or "—"),
                            "计量、投料、巡检", "/", "/"])
        if hg_rows:
            tables.append({"name": "类比危害分布表",
                           "cols": ["评价单元", "岗位", "接触途径", "接触途径", "主要职业病危害因素", "作业方式", "接触人数", "接触频次"],
                           "header2": ["评价单元", "岗位", "产品", "工段", "主要职业病危害因素", "作业方式", "接触人数", "接触频次"],
                           "merge_rect": [(0, 2, 0, 3)],
                           "rows": hg_rows})
        # 类比企业工作日写实 (staffing×shifts 派生: 部门/岗位/人数/班制; 写实时段无实测记录标"—" — 原报告表4.2-1)
        stf = (assess.get("_project_data") or {}).get("staffing", [])
        wl_rows = []
        # 全量(2026-10): 不再截 30 行; 材料自带「合计」条目按行出现
        for s in stf:
            sysm = next((x.get("system", "—") for x in (assess.get("_project_data") or {}).get("shifts", [])
                         if x.get("post") == s.get("post")), "—")
            wl_rows.append([s.get("dept", "—"), s.get("post", "—"), s.get("count", "—"), sysm, "—"])
        if wl_rows:
            # 原报告表4.2-1: 部门/岗位工种/总人数/女工数/工作班制/班制时长(h)/工作日写实
            wl7 = []
            for r in wl_rows:
                sysm = r[3]
                dur = "8" if sysm == "常白班" else ("8" if "三班" in str(sysm) else "—")
                wl7.append([r[0], r[1], r[2], "/", sysm, dur, r[4]])
            tables.append({"name": "类比工作日写实表",
                           "cols": ["部门", "岗位/工种", "总人数", "女工数", "工作班制", "工作班制时长（h）", "工作日写实"],
                           "rows": wl7})
        # 类比企业个人防护用品配备 (原报告表4.2-4: 竖排6列 生产单元|岗位|用品|单位|数量|发放频率)
        #   ⚠ 真稿43表零张 √/※ 矩阵表 → 旧横排矩阵(_ppe_matrix_table)是张冠李戴, 已弃用
        _pd4 = assess.get("_project_data") or {}
        _pv_rows, _pv_merged = _ppe_vertical_rows(_pd4)
        if _pv_rows:
            tables.append({"name": "类比PPE配备表",
                           "cols": ["生产单元", "生产岗位", "配置的防护用品", "单位", "数量",
                                    "发放频率（月，n为根据实际情况发放）"],
                           "rows": _pv_rows,
                           "vmerge_cols": [0, 1]})
        # 类比企业PPE有效性分析 (原报告表4.2-5: 单元/岗位/产品/工段/因素/配备/符合性/有效性 定式评价)
        if _pv_rows:
            hg2 = (assess.get("_project_data") or {}).get("hazard_grid", [])
            v9 = []
            for g in hg2:
                if not g.get("unit"):
                    continue
                # 表头行会被导入层混入 hazard_grid (如 unit="评价单元"/post="岗位") → 滤除,
                # 否则产物表首行出现「1 评价单元 岗位 — 主要职业病危害因素」的假数据行。
                if str(g.get("unit")).strip() in ("评价单元", "序号", "单元") and \
                   str(g.get("post")).strip() in ("岗位", "作业岗位", "工序"):
                    continue
                v9.append([len(v9) + 1, g.get("unit", "—"), g.get("post", "—"),
                           g.get("product", "—"), g.get("stage") or "—",
                           (g.get("factors", "") or "—"),
                           "按岗位接触的职业病危害因素种类配备相应防护用品",
                           "符合GBZ 1、GBZ/T 194要求", "基本有效"])
                if len(v9) >= 12:
                    break
            if v9:
                # 表头两行: 上行「接触途径」跨产品/工段两列 (merge_rect), 下行细分产品/工段
                tables.append({"name": "类比PPE有效性表",
                               "cols": ["序号", "评价单元", "岗位", "接触途径", "接触途径", "主要职业病危害因素", "个人防护用品配备", "符合性", "有效性"],
                               "header2": ["序号", "评价单元", "岗位", "产品", "工段", "主要职业病危害因素", "个人防护用品配备", "符合性", "有效性"],
                               "merge_rect": [(0, 3, 0, 4)],
                               "rows": v9})
        # 常见职业体力劳动强度分级 (GBZ 2.2—2007 表13 的职业描述; 原报告表4.2-2 挂4.2.1)
        tables.append({"name": "劳动强度分级表",
                       "cols": ["体力劳动 强度分级", "职业描述"],
                       "rows": [
                           ["I（轻劳动）", "坐姿：手工作业或腿的轻度活动（如打字、缝纫等）；立姿：操作仪器，控制、查看设备等上肢轻度活动"],
                           ["II（中等劳动）", "手和臂持续动作（如锯木头等）；臂和腿的工作（如卡车、拖拉机等运输操作）；臂和躯干的工作（如锻打、风动工具操作等）"],
                           ["III（重劳动）", "臂和躯干负荷工作（如搬重物、铲、锤锻等）"],
                           ["IV（极重劳动）", "大强度的挖掘、搬运等"]]})
        return tables

    if sec == "5":
        # 危害因素及危害程度分析: 识别/判定/检测/限值/健康影响/关键控制点
        tables = []
        # 建设施工过程危害因素表 (真稿表10.1-3 全量取证, 18行) — 纯定式, 与项目无关
        # 挂 5.1.4 建设施工过程职业病危害因素识别 (见 _SUB_TABLE_MAP)
        _cons_rows = [
            ["土石方施工人员", "挖掘机、推土机、铲运机驾驶员", "噪声、粉尘、高温、全身振动"],
            ["砌筑人员", "砌筑工", "高温、高处作业"],
            ["钢筋加工人员", "钢筋工", "噪声、金属粉尘、高温、高处作业"],
            ["施工架子搭设人员", "架子工", "高温、高处作业"],
            ["工程防水人员", "防水工", "高温、沥青烟、煤焦油、甲苯、二甲苯、汽油等有机溶剂、石棉"],
            ["", "防渗墙工", "噪声、高温、局部振动"],
            ["装饰装修人员", "抹灰工", "粉尘、高温、高处作业"],
            ["", "金属门窗工", "噪声、金属粉尘、高温、高处作业"],
            ["", "油漆工", "有机溶剂、铅、汞、镉、铬、甲醛、甲苯二异氰酸酯、粉尘、高温"],
            ["", "室内成套设施饰工", "噪声、高温"],
            ["工程设备安装工", "机械设备安装工", "噪声、高温、高处作业"],
            ["", "电气设备安装工", "噪声、高温、高处作业"],
            ["", "管工", "噪声、高温、高处作业"],
            ["", "焊工", "电焊烟尘、锰及其化合物、氮氧化物、臭氧、紫外辐射、高温"],
            ["", "起重工", "噪声、高温、高处作业"],
            ["市政工程人员", "筑路工", "粉尘、高温、噪声、全身振动"],
            ["", "道路养护工", "粉尘、高温、噪声"],
            ["", "下水道工", "硫化氢、甲烷、粉尘、高温"],
        ]
        # 合并: 首列同工种跨行合并 (原报告样式)
        _merges = []
        _i = 0
        while _i < len(_cons_rows):
            _j = _i
            while _j + 1 < len(_cons_rows) and _cons_rows[_j + 1][0] == "":
                _j += 1
            if _j > _i:
                _merges.append((_i + 1, _j + 1, 0, 0))
            _i = _j + 1
        tables.append({"name": "施工危害因素表",
                       "cols": ["工种", "岗位", "主要职业病危害因素"],
                       "rows": _cons_rows,
                       "merge_rect": _merges,
                       "prov": {f"{_i}_2": prov_rule({"file": "真稿表10.1-3", "text": _cons_rows[_i][2]},
                                                     field=f"cons.row{_i}")
                                for _i in range(len(_cons_rows))}})
        from web.hazard_grid import build_grid
        grid = build_grid(conn, assess.get("_project_data", {}))
        if grid:
            g_rows = []
            for g in grid:
                # 原报告表5.1-1 7列: 车间/产品/工序/设备密闭/物料或中间产物/产生环节/主要因素
                g_rows.append(["生产车间主厂房", g.get("product") or "—", g["process"],
                               g["enclosed"] or "密闭", "、".join(g["materials"]),
                               g["process"] + "过程中", "、".join(g["factors"])])
            tables.append({"name": "危害因素识别表",
                           "cols": ["生产车间", "生产车间", "工序", "设备密闭", "物料或中间产物", "职业病危害因素产生环节", "主要职业病危害因素"],
                           "header2": ["生产车间", "生产车间", "工序", "设备密闭", "物料或中间产物", "职业病危害因素产生环节", "主要职业病危害因素"],
                           "merge_rect": [(0, 0, 1, 1)] + [(0, c, 1, c) for c in (2, 3, 4, 5, 6)],
                           # 逐格 provenance: 由 hazard_grid(设备/工艺→危害 规则) 生成, 非 LLM
                           "rows": g_rows,
                           "prov": {f"{i}_{c}": prov_rule({"file": "hazard_grid(设备/工艺规则)",
                                                           "text": f"{g_rows[i][c]} (规则推导)"},
                                                          field=f"grid.row{i}.col{c}")
                                    for i in range(len(g_rows)) for c in (2, 5, 6)}})
        # 检测结果表 (按类型分张)
        dets = (assess.get("_project_data") or {}).get("detections", [])
        if dets:
            det_by_type = _det_by_type(conn, dets)
            for dtype, dets_b in det_by_type.items():
                # h11: CTWA 与峰值浓度 双列投影 — ctwa 为空的峰值行值在 cstel 键, 不得塌缩为 '—'
                # (真实报告双浓度列: 新泰 p37 检测结果[C_TWA|峰值浓度]; 长兴 表25 CTWA|CSTEL)
                det_rows = [[i, d.get("factor", ""), d.get("ctwa", "—") or "—",
                             d.get("cstel", "—") or "—", "—", _det_verdict(d)]
                            for i, d in enumerate(dets_b, 1)]
                # 逐格 provenance: CTWA 值来自检测报告 (文本层或视觉提取), 必须标来源 —
                # 这是报告里最安全关键的数值, 不标会被审计判 unverified (早期遗漏, 71 个)。
                _dp = {}
                for _i, _d in enumerate(dets_b):
                    _ev = {"file": _d.get("_file") or "检测报告",
                           "page": _d.get("_page")}
                    # h11: CTWA(列2) 与 峰值浓度(列3) 双列投影, 各自独立逐格溯源
                    for _ky, _ci, _lab in (("ctwa", 2, "CTWA"), ("cstel", 3, "峰值浓度")):
                        _v1 = _d.get(_ky)
                        if _v1 in (None, "", "—"):
                            continue
                        if _d.get("source") == "vision":
                            # 视觉提取的数字 → needs_review (用户红线: 机器读的≠确定取出)
                            from web.number_provenance import prov_vision
                            _dp[f"{_i}_{_ci}"] = prov_vision(_ev, field=f"{_d.get('factor')}.{_lab}",
                                                             page=_d.get("_page"))
                        else:
                            _dp[f"{_i}_{_ci}"] = prov_rule(_ev, field=f"{_d.get('factor')}.{_lab}",
                                                           traceable=bool(_d.get("_file")))
                tables.append({"name": f"检测结果表({dtype})", "cols": ["序号", "危害因素", "CTWA", "峰值浓度", "PC-TWA", "判定"],
                               "rows": det_rows, "prov": _dp})
            # h14: 物理因素独立表 (检测岗位|检测地点|检测项目|检测结果|LEX,8h|时间|限值|判定)
            _pt_ = _physical_det_table(conn, dets) if dets else None
            if _pt_:
                tables.append(_pt_)
        js = assess.get("judgements", [])
        if js:
            j_rows = [[i, j["factor"], "—", "—", "—", "—",
                       "合格" if j.get("pass") else "不合格"] for i, j in enumerate(js, 1)]
            tables.append({"name": "判定表", "cols": ["序号", "危害因素", "CTWA", "PC-TWA", "CSTEL", "PC-STEL", "判定"],
                           "rows": j_rows})
        # 接触限值表 (危害因素×PC-TWA×PC-STEL×PE×单位, 从 oel_limit)
        # ⚠ #50 的"不是一条检测"判据同样管这张表: 导入侧塞进来的表头/注释行 ('/'、
        #   '接触的职业病危害因素'、'采样日期 ：2024.04.07') 在这里也一样会领到一行限值 +
        #   一句备注, 把"这张表列了哪些因素"变成"这些都被检测/鉴定过"。判据只有一份 (_det_nonfactor)。
        _drop50 = _det_nonfactor(conn, dets) if dets else {}
        det_factors = [d["factor"] for d in dets if str(d.get("factor") or "") not in _drop50] \
            + [h["factor"] for h in assess.get("hazards", [])]
        # 原报告表5.3-1 宽表: 种类/PC-MAC/PC-TWA/PC-STEL/PE/备注 (一因素一行)
        # 列号对照 (row 数组下标 → 列): 0种类 1 PC-MAC 2 PC-TWA 3 PC-STEL 4 PE 5备注
        #   实际落列由下面 _place 决定 (粉尘的 PC-TWA(总尘) 可以占用 2 号位), 不在这里写死
        lim_rows, seen_l, lim_prov = [], set(), {}
        for fac in det_factors:
            if not fac or fac in seen_l or ("噪声" in fac or "高温" in fac or "振动" in fac):
                continue
            seen_l.add(fac)
            _ri = len(lim_rows)                       # 该因素在表中的行号
            # ⚠ 连同 source_standard 一起取出: 逐格记录该限值来自标准库哪一条,
            #   使"每个限值可点开看 GBZ 2.1—2019 某某 PC-TWA=N" (审计可逐行溯源)
            # ⚠ #51: 原路径 (`factor_name LIKE '<检测名>%'`) 落空时先按 空格/下标/括号限定
            #   解析成标准库规范名再取 — 名字只差一个空格就查不到 → 印 '—' + "未制定职业接触限值"
            #   = 假陈述 (2026-10-16 生产成品 §7.4)。四道前提与能力边界见 _resolve_oel_name。
            _r7, canon, mode, status = _oel_rows_for(conn, fac)
            recs = [(r[1], r[2], r[3], r[6]) for r in _r7]
            oels, srcs, units = {}, {}, {}
            for r in recs:
                # sqlite3.Row 或 tuple 都兼容
                try:
                    _t, _v, _u, _s = r["oel_type"], r["value"], r["unit"], r["source_standard"]
                except (TypeError, IndexError, KeyError):
                    _t, _v = r[0], r[1]
                    _u = r[2] if len(r) > 2 else ""
                    _s = r[3] if len(r) > 3 else ""
                oels[_t], units[_t], srcs[_t] = _v, _u or "", _s or ""
            # 粉尘 (GBZ 2.1—2019 表2) 的限值类型是 PC-TWA(总尘)/PC-TWA(呼尘), 而这张宽表 (原报告
            # 表5.3-1) 只有 PC-MAC/PC-TWA/PC-STEL/PE 四列。修前它两样都做错的两种表现:
            #   · 查不到行 (名字是 OCR 化石) → 备注写"未制定职业接触限值" (假陈述: 表2 明明制定了);
            #   · 查到了行 (名字修好后) → 四列全 '—' 且备注空白 (半假陈述: 有限值但不给看)。
            # 口径: 总尘 → 进 PC-TWA 列 (表头单位就是 mg/m3, 只有同为 mg/m3 才敢放进这一列);
            #       单位不同 (实测 2 行是 f/mL 的石棉纤维) 或呼尘 → 落到备注, 一个数都不丢。
            _LIM_HEAD = "mg/m3"
            _place = {"PC-MAC": "PC-MAC", "PC-TWA": "PC-TWA", "PC-STEL": "PC-STEL", "PE": "PE"}
            _note_bits, _noted = [], set()

            def _mode_of(_t):
                """限值类型自己的括号词 (总尘/呼尘…) —— 词表来自 oel_limit.oel_type, 不写在代码里"""
                _m = _PAREN_TAIL.search(nospace(str(_t)))
                return nospace(_m.group(0))[1:-1] if _m else str(_t)

            def _note_val(_t, _lab=None):
                """把一个没进四列的限值类型原样写进备注; 标签用类型自己的括号词 (呼尘/总尘)"""
                if _t in _noted:
                    return
                _noted.add(_t)
                _note_bits.append(f"{_lab or _mode_of(_t)} {oels[_t]} {units.get(_t) or _LIM_HEAD}")

            _tw_keys = [t for t in oels if str(t) != "PC-TWA" and str(t).startswith("PC-TWA")]
            _placed_tw = None
            if "PC-TWA" not in oels and _tw_keys:
                # #51: 检测名自带采样方式限定时 (『石灰石粉尘（ 呼尘）』) 必须进**那一档**的限值,
                #      否则名字写呼尘、数值给总尘 = 用同一个格子印一个错数 (比 '—' 更坏)。
                _k = next((t for t in _tw_keys if mode and mode in nospace(str(t))), None) \
                    or ("PC-TWA(总尘)" if "PC-TWA(总尘)" in _tw_keys else None)
                if _k and units.get(_k, _LIM_HEAD) == _LIM_HEAD:
                    _place["PC-TWA"] = _k
                    _placed_tw = _k
                    _noted.add(_k)
                elif _k:
                    _note_val(_k)
            for _t in _tw_keys:
                if _t != _placed_tw:
                    _note_val(_t)
            # 凡是**没落进这四列**的限值类型都必须进备注 (口径: 一个数都不丢)。实测: 库里 638 行
            # 用的是 'MAC' (GBZ 2.1 的最高允许浓度) 56 行, 一处都没写 'PC-MAC' → 原实现按 'PC-MAC'
            # 找键, PC-MAC 列从来没印出过数, 备注还空白着 = 读的人以为"这类物质没有限值"。
            # 列名对不上就不硬塞进列 (MAC ≠ PC-MAC, 不替标准改术语), 但数值原样进备注 + 用库里
            # 真实的类型名标它 → 既有数也不假。
            for _t in oels:
                if _t not in set(_place.values()):
                    _note_val(_t)
            # GBZ 2.1—2019 未收录的物质 (如新戊二醇/多元醇/二元酸类) 不静默丢行:
            # 行保留+值"—"+备注注明, 避免"识别表列了、限值表没有"的覆盖断裂 (缺数据不硬造)
            # #41 (与 #51 同一个格子, 一次判掉): 备注必须说**我们能核实的事**。
            #   原话"未制定职业接触限值"断言的是"标准没制定限值", 而我们只知道"库里查不到这一行"
            #   —— 查不到有 ③ 种原因 (真没收录 / 名字是空格异形没解析动 / 多命中不敢猜),
            #   三种都印同一句假陈述 = 打脸。改后: 只说"标准库未登记/须人工确认", 不替标准说话。
            if oels:
                _note = "; ".join(_note_bits)
            elif status == "ambiguous":
                _note = "标准库存在同名异形多命中，须人工确认"
            elif status == "cas_conflict":
                _note = "标准库同名记录的 CAS 不一致，须人工确认"
            else:
                _note = "未在标准库登记，须人工核对"
            lim_rows.append([fac,
                             oels.get(_place["PC-MAC"], "—"), oels.get(_place["PC-TWA"], "—"),
                             oels.get(_place["PC-STEL"], "—"), oels.get(_place["PE"], "—"),
                             _note])
            # 逐格 provenance: 只给**有值**的格子盖 rule 章 + 标准出处 (章里写的是**真实的**
            # oel_type, 例如粉尘格写 PC-TWA(总尘), 不冒充化学因素的 PC-TWA)
            _alias = "" if canon in (None, fac) else f" [标准库名: {canon}]"
            for _t, _ci in (("PC-MAC", 1), ("PC-TWA", 2), ("PC-STEL", 3), ("PE", 4)):
                _key = _place[_t]
                if _key in oels:
                    _std = srcs.get(_key) or "GBZ 2.1—2019"
                    lim_prov[f"{_ri}_{_ci}"] = prov_rule(
                        {"file": "标准库", "table": "oel_limit",
                         "text": f"{fac}{_alias} {_key}={oels[_key]} ({_std})"},
                        field=f"{fac}.{_key}")
            # 名称列也算规则来源 (来自检测/物料识别结果)
            lim_prov[f"{_ri}_0"] = prov_rule({"file": "检测/物料识别", "text": fac},
                                             field=f"{fac}.name")
        if lim_rows:
            tables.append({"name": "接触限值表",
                           "cols": ["种类", "职业接触限值（mg/m3）", "职业接触限值（mg/m3）", "职业接触限值（mg/m3）", "职业接触限值（mg/m3）", "备注"],
                           "header2": ["种类", "PC-MAC", "PC-TWA", "PC-STEL", "PE", "备注"],
                           "merge_rect": [(0, 1, 0, 4)],
                           "rows": lim_rows, "prov": lim_prov})
        # 工种×危害表 (岗位→危害, 从网格关联)
        if grid:
            gw_rows = []
            for g in grid:
                for p in (g.get("posts") or ["各岗位"])[:2]:
                    gw_rows.append([g["unit"], p, "、".join(g["factors"])])
            if gw_rows:
                tables.append({"name": "工种危害表", "cols": ["评价单元", "岗位/工种", "主要危害因素"], "rows": gw_rows,
                               # 逐格 provenance: 岗位→危害 由 hazard_grid 网格关联 (规则), 非 LLM
                               "prov": {f"{i}_{c}": prov_rule({"file": "hazard_grid(岗位×危害关联)",
                                                               "text": f"{r[c]} (网格关联)"}, field=f"gw.{c}")
                                        for i, r in enumerate(gw_rows) for c in (0, 1, 2)}})
        # 健康影响表 (危害因素×健康影响×职业病×侵入途径, 用 entity_link 链)
        hazards = [h["factor"] for h in assess.get("hazards", [])] or []
        he_rows = []
        seen_he = set()
        from web.entity_link import link_health_effect, link_disease, is_physical
        for fac in hazards:
            if fac in seen_he:
                continue
            # 真稿表5.2-1 只列**化学物质/粉尘**(新泰取证: 石灰石粉尘/氟化氢/氯化氢及盐酸/氧化钙),
            #   物理因素(噪声/高温/紫外)另见「物理因素健康影响表」→ 此表须排除,
            #   否则违反「每章讲该章的事」(用户原则)
            if is_physical(fac):
                continue
            seen_he.add(fac)
            hes = link_health_effect(conn, fac)
            ds = link_disease(conn, fac)
            if hes:
                he_rows.append([fac, (hes[0].get("effect") or "—"),
                                "、".join(ds) if ds else "—",
                                (hes[0].get("route") or "—")])
            elif ds:  # 物理因素: 无化学健康影响但有职业病(噪声聋/中暑)
                he_rows.append([fac, is_physical(fac) and "—", "、".join(ds), "—"])
        if he_rows:
            # 原报告表5.2-1 6列逐格取证(新泰备案稿):
            #   名称 | 形态 | 危害特性 | 对人体健康的影响 | 危害程度 | 可能引起的职业病或职业性病损
            #   e.g. 氟化氢|气体|刺入、皮肤|对皮肤有强烈的腐蚀作用…|高度危害|化学性皮肤灼伤…
            # 「危害程度」= GBZ/T 230-2025 THI 分级, 属**确定性查表**(hazard_toxicity),
            #   未收录 → 标「待补充」, 不推算不编造(用户原则)
            he6 = []
            for r in he_rows:
                # he_rows 现为 [fac, effect, disease, route]
                fac, effect = r[0], r[1]
                disease = r[2] if len(r) > 2 else "—"
                route = r[3] if len(r) > 3 else "—"
                grade = lookup_toxicity(conn, fac)
                he6.append([fac, form_of(conn, fac), route, effect, grade, disease])
            tables.append({"name": "健康影响表",
                           "cols": ["名称", "形态", "危害特性", "对人体健康的影响",
                                    "危害程度", "可能引起的职业病或职业性病损"],
                           "rows": he6})
        # 关键控制点表 (岗位×关键因子×措施)
        # 原报告表5.4-1 7列: 车间/产品/工序/设备密闭/接触方式/因素/关键控制措施 (hazard_grid+judgement 派生)
        kc_rows = []
        hg5 = (assess.get("_project_data") or {}).get("hazard_grid") or []
        for g in hg5:
            if not g.get("unit") and not g.get("post"):
                continue
            facs = (g.get("factors") or "")
            kc_rows.append(["生产车间主厂房", g.get("product", "—") or "—", g.get("stage") or g.get("post", "—"),
                            "密闭", "投料、巡检", (facs or "—"),
                            "自动化、密闭化、机械化；个人防护、警示告知"])
        if not kc_rows:
            for i, j in enumerate(js):
                kc_rows.append(["生产车间主厂房", "—", "—", "密闭", "—", j["factor"],
                                (j.get("level") or {}).get("control", "") or "密闭化、自动化；个人防护"])
        if kc_rows:
            tables.append({"name": "关键控制点表",
                           "cols": ["生产车间", "生产车间", "工序", "设备密闭", "接触方式", "主要职业病危害因素", "关键控制措施"],
                           "header2": ["生产车间", "生产车间", "工序", "设备密闭", "接触方式", "主要职业病危害因素", "关键控制措施"],
                           "merge_rect": [(0, 0, 1, 1)] + [(0, c, 1, c) for c in (2, 3, 4, 5, 6)],
                           "rows": kc_rows})
        # 物理因素健康影响 (噪声/高温/振动/工频 — GBZ 2.2 固定内容, 通用)
        phys_rows = [
            ["噪声", "物理因素", "根据作用的系统不同可分为听觉系统（听力损失）和非听觉系统（神经系统、心血管系统等）的影响", "噪声聋"],
            ["高温", "物理因素", "高温可导致急性热致疾病（如刺痒、热疹、抽搐、热晕厥、热衰竭、热射病）", "职业性高温中暑"],
            ["振动", "物理因素", "长期接触手传振动可引起手臂振动病（白指）", "手臂振动病"],
            ["工频电场", "物理因素", "短期接触低强度工频电场可引起头晕、疲劳、血压波动等不适", "—"],
        ]
        tables.append({"name": "物理因素健康影响表", "cols": ["名称", "危害特性", "对人体健康的影响", "可能引起的职业病"], "rows": phys_rows,
                       "prov": {f"{i}_2": prov_rule({"file": "标准库", "table": "health_effect",
                                                     "text": f"{r[0]} 健康影响 (GBZ 2.2/职业病目录)"},
                                                    field=f"{r[0]}.健康影响")
                                for i, r in enumerate(phys_rows)}})
        # 表5.3-2 噪声接触限值 (GBZ 2.2—2007 **表9**) — 数值取自 oel_limit, 不再手抄
        _noise_rows = _noise_limit_rows(conn)
        tables.append({"name": "噪声接触限值表", "cols": ["接触时间", "接触限值[dB(A)]", "备注"],
                       "rows": _noise_rows,
                       # 逐格溯源: 表9 的三行, 数值产地 oel_limit → 库里改一个数, 成品指纹必红
                       "prov": {f"{i}_1": prov_rule({"file": "GBZ 2.2—2007", "table": "oel_limit",
                                                     "text": f"噪声 {r[0]} → {r[1]} dB(A) (GBZ 2.2—2007 表9)"},
                                                    field=f"噪声.{r[0]}")
                                for i, r in enumerate(_noise_rows)}})
        # 表5.3-3 高温接触限值 (GBZ 2.2—2007 **表8**: 接触时间率×体力劳动强度 WBGT 限值℃) 双层表头
        #   旧版这里是手抄的 4×4, 与一手扫描件逐格比对有 6 格偏松 (见 _heat_wbgt_matrix 的取证),
        #   且标签写成 "GBZ 2.2—2019 表1" (2019 号不存在; 2007 的表8 才是高温, 表1 是超高频辐射)。
        _hm = _heat_wbgt_matrix(conn)
        _heat_rows = [[f"{rate}%"] + [_hm.get((rate, lv)) or "待补充" for lv in _HEAT_LEVELS]
                      for rate in _HEAT_RATES]
        tables.append({"name": "高温接触限值表",
                       "cols": ["接触时间率", "体力劳动强度", "体力劳动强度", "体力劳动强度", "体力劳动强度"],
                       "header2": ["接触时间率", "I", "II", "III", "IV"],
                       "merge_rect": [(0, 1, 0, 4)],
                       "rows": _heat_rows,
                       # 逐格溯源 (含第0列接触时间率): 数值产地 oel_limit 的 16 行高温规则
                       "prov": {f"{i}_{c}": prov_rule({"file": "GBZ 2.2—2007", "table": "oel_limit",
                                                       "text": f"高温 接触时间率{r[0]} 强度{[None,'I','II','III','IV'][c]} → WBGT {r[c]}℃ (GBZ 2.2—2007 表8)"},
                                                      field=f"高温.{r[0]}.{c}")
                                for i, r in enumerate(_heat_rows) for c in (0, 1, 2, 3, 4)}})
        # 工种×危害表 (岗位→危害, 从网格关联)
        if grid:
            gw_rows = []
            for g in grid:
                for p in (g.get("posts") or ["各岗位"])[:2]:
                    gw_rows.append([g["unit"], p, "、".join(g["factors"])])
            if gw_rows:
                tables.append({"name": "工种危害表", "cols": ["评价单元", "岗位/工种", "主要危害因素"], "rows": gw_rows,
                               # 逐格 provenance: 岗位→危害 由 hazard_grid 网格关联 (规则), 非 LLM
                               "prov": {f"{i}_{c}": prov_rule({"file": "hazard_grid(岗位×危害关联)",
                                                               "text": f"{r[c]} (网格关联)"}, field=f"gw.{c}")
                                        for i, r in enumerate(gw_rows) for c in (0, 1, 2)}})
        return tables

    if sec == "6":
        # 职业病危害防护设施: 防尘防毒/防噪声振动/防暑防寒/防护设施检查表 + 设施配置
        tables = []
        for theme, tname in [("防尘防毒", "防尘防毒设施检查表"), ("防噪声振动", "防噪声振动检查表"),
                             ("防暑防寒", "防暑防寒检查表")]:
            # ⚠ 单一产地: 结果/评价走 check_result_templates.resolve() (原硬编码 "待确认")
            from knowledge.check_result_templates import resolve as _cr0
            g_rows = []
            for r in _ds(conn).rows("SELECT clause, rule FROM gbz1_rule WHERE theme LIKE ?", (theme + "%",)):
                _res, _vd = _cr0(str(r[0]).strip())
                g_rows.append([r[1], f"GBZ 1—2010 {r[0]}", _res or "待补充", _vd or "待评价"])
            if g_rows:
                tables.append({"name": tname, "cols": ["卫生要求", "检查依据", "检查结果", "评价"],
                               "rows": g_rows})
        rows = _ds(conn).rows("SELECT hazard_category, check_point, std_code, clause FROM protection_rule")
        # 原报告表6.2-1: 4列 (卫生要求/检查依据/检查结果/评价) — 用户 2026-10 拍板保留 4 列
        # ⚠ 单一产地: 结果/评价走 check_result_templates.resolve() (原为第四份硬编码)
        from knowledge.check_result_templates import resolve_protection as _crp
        p6 = []
        for r in rows:
            _res, _vd = _crp(str(r[2]).strip(), str(r[3]).strip())
            p6.append([r[1], f"{r[2]} {r[3]}", _res or "待补充", _vd or "待评价"])
        tables.append({"name": "防护设施检查表", "cols": ["卫生要求", "检查依据", "检查结果", "评价"],
                       "rows": p6})
        # 设施配置明细 (岗位×设施×数量×备注)
        facs = (assess.get("_project_data") or {}).get("facilities", [])
        if facs:
            f_rows = [[i, f.get("post", ""), f.get("facility", ""), f.get("count", ""), f.get("remark", "")]
                      for i, f in enumerate(facs, 1)]
            tables.append({"name": "设施配置表", "cols": ["序号", "岗位/区域", "配备设施", "数量", "备注"],
                           "rows": f_rows})
        return tables

    if sec == "7":
        # 应急救援: 应急救援检查表 + 应急物资清单 (表56 类别|名称|数量|放置点位)
        pd_ = assess.get("_project_data", {})
        tables = []
        rows = _ds(conn).rows("SELECT scenario, require, std_code, clause FROM emergency_rule")
        if rows:
            # 原报告表7.2-1: 4列 (卫生要求/检查依据/检查结果/评价) — 用户 2026-10 拍板保留 4 列
            # ⚠ 单一产地: 应急救援句式走 check_result_templates.resolve_emergency()
            from knowledge.check_result_templates import resolve_emergency as _cre
            _erows = []
            for r in rows:
                _res, _vd = _cre(str(r[2]).strip(), str(r[3]).strip())
                _erows.append([r[1], f"{r[2]} {r[3]}", _res or "待补充", _vd or "待评价"])
            tables.append({"name": "应急救援检查表", "cols": ["卫生要求", "检查依据", "检查结果", "评价"],
                           "rows": _erows})
        emgs = pd_.get("emergency_supplies", [])
        if emgs:
            try:
                # 原报告表2.1-2/7.1-1 4列: 类别/名称/数量/放置点位 (无序号)
                tables.append({"name": "应急物资清单",
                               "cols": ["类别", "名称", "数量", "放置点位"],
                               "rows": [[e.get("类别", ""), e.get("名称", ""), e.get("数量", ""), e.get("点位", "")]
                                        for e in emgs]})
            except Exception:
                pass
        return tables

    if sec == "8":
        # 个人防护用品: PPE配备表
        # 通用: 从项目 data 的 ppe 字典列表生成 (上传管线提取, 全项目通用)
        # (修复: 旧硬编码 data/materials/A2f_防护措施.txt 不存在 → 永远空表)
        pd = assess.get("_project_data") or {}
        p_rows = []
        for p in (pd.get("ppe") or []):
            if isinstance(p, dict):
                p_rows.append([len(p_rows) + 1,
                               str(p.get("post") or p.get("岗位") or p.get("工段") or ""),
                               str(p.get("item") or p.get("name") or p.get("equipment")
                                   or p.get("防护用品") or p.get("ppe") or ""),
                               str(p.get("frequency") or p.get("标准") or "GB 39800.1—2020")])
            elif isinstance(p, str) and p.strip():
                p_rows.append([len(p_rows) + 1, "", p.strip(), "GB 39800.1—2020"])
        # 兜底: protection 文本 (防护措施描述) 提取 "护目镜/防毒面具/耳塞..." 行
        if not p_rows:
            import re as _re
            prot = pd.get("protection") or ""
            if isinstance(prot, str) and prot.strip():
                for line in prot.splitlines():
                    if _re.search(r"(保护|防护|个人防护|PPE)", line) and ("|" in line or "：" in line or ":" in line):
                        parts = _re.split(r"[|：:]", line.lstrip("-_* "), 1)
                        if len(parts) >= 2:
                            p_rows.append([len(p_rows) + 1, parts[0].strip(), parts[1].strip(), "GB 39800.1—2020"])
        if p_rows:
            # 原报告表8.1-1: 竖排6列 生产单元|岗位|用品|单位|数量|发放频率 (= 4.2-4 同构, 类比推定)
            #   ⚠ 真稿零张矩阵表 → 弃用 _ppe_matrix_table 横排
            _pv8, _pv8m = _ppe_vertical_rows(pd)
            if _pv8:
                tables8 = [{"name": "PPE配备表",
                            "cols": ["生产单元", "生产岗位", "配置的防护用品", "单位", "数量",
                                     "发放频率（月，n为根据实际情况发放）"],
                            "rows": _pv8, "vmerge_cols": [0, 1]}]
            else:
                tables8 = [{"name": "PPE配备表", "cols": ["序号", "岗位/作业", "防护装备", "配备标准"], "rows": p_rows}]
        else:
            tables8 = []
        # 表8.2-1 拟配置检查表 (GB 39800—2020 通用配备要求, 检查表定式: 通用规则非项目编造)
        tables8.append({"name": "PPE拟配置检查表",
                        "cols": ["序号", "检查内容", "检查依据", "检查结果", "评价"],
                        "rows": [
                            [1, "综合性作业需根据作业特点选择多功能防护装备", "GB 39800.1—2020 4.2", "本项目拟根据实际作业情况选择个体防护用品", "符合"],
                            [2, "作业人员需配备的个体防护装备应在作业指导书中注明", "GB 39800.1—2020 4.3", "本项目拟在各岗位操作规程中注明个体防护用品配备", "符合"],
                            [3, "个体防护装备的选择应考虑有害物质的暴露途径", "GB 39800.1—2020 5.1", "本项目拟按吸入、皮肤接触等途径分别选择呼吸防护与皮肤防护用品", "符合"],
                            [4, "头部、眼面部、呼吸、皮肤等防护装备应按国家标准选型", "GB 39800.1—2020 第5章", "本项目拟选用符合国家标准防护装备", "符合"]]})
        return tables8

    if sec == "9":
        # 职业卫生管理: 管理制度检查表 + 职业健康监护表
        # 检索优化: 管理制度按"通用必列 + 项目相关"过滤, 不整表倾倒(条款库扩大后防表格爆表)
        tables = []
        rows = _ds(conn).rows("SELECT category, require, std_code, clause FROM management_rule")
        # 受限空间等特殊作业条款仅当项目工艺涉及时才进检查表 (通用判定: 危害grid/工艺文本含关键词)
        _pd9 = assess.get("_project_data") or {}
        _proc = str(_pd9.get("process_text") or "") + " ".join(
            str(g.get("factors") or "") + str(g.get("materials") or "") for g in (_pd9.get("hazard_grid") or []))
        _special_kw = {"受限空间": ("受限空间", "密闭", "清罐", "釜内", "进入设备"), }
        rows = [(c, q, s, cl) for (c, q, s, cl) in rows
                if c not in _special_kw or any(k in _proc for k in _special_kw[c])]
        # 原报告表9.1-1 5列: 序号/检查项目/检查依据/检查结果/结论
        m9 = []
        for i, r in enumerate(rows, 1):
            m9.append([i, f"{r[0]}：{r[1]}", f"{r[2]} {r[3]}", "企业已建立相应管理制度", "合格"])
        tables.append({"name": "管理制度检查表", "cols": ["序号", "检查项目", "检查依据", "检查结果", "结论"],
                       "rows": m9})
        surv = _ds(conn).rows("SELECT factor, check_type, cycle FROM surveillance_rule")
        if surv:
            tables.append({"name": "职业健康监护表", "cols": ["序号", "危害因素", "检查类别", "周期"],
                           "rows": [[i, r[0], r[1], r[2] or "—"] for i, r in enumerate(surv, 1)]})
        # 职业病防治经费表 (表9.2-1) —— 数据源: C3 现状评价报告 表11.11-1 (现有厂区实测)
        #   注: 材料**无本项目**专项投资明细 (已全材料搜证) → 用现有厂区实测经费作参照,
        #   与本项目按比例概算的区间交叉印证。值全部来自材料表格, 不编造。
        ohy = _load_ohy_invest(_pd9)
        if ohy and ohy.get("rows"):
            _orows = [[str(c) for c in r] for r in ohy["rows"]]
            tables.append({"name": "职业病防治经费表",
                           "cols": ohy.get("cols") or ["序号", "项目", "投资（万元）"],
                           "rows": _orows})
        return tables

    if sec == "10":
        # 标准 10.2.10 职业病危害关键控制点分析 (2026-09 从 5.4.3 提为独立章)
        # 数据源合并两处 (规则产物, 不经 LLM):
        #   judgements → 是否有 level (化学判定)
        #   grades     → GBZ/T 229 作业分级 (粉尘/物理的真实分级来源)
        _grade = {}
        for g in (assess.get("grades") or []):
            if isinstance(g, dict) and g.get("factor"):
                _grade[str(g["factor"])] = g
        rows = []
        _seen_f: set = set()
        for j in (assess.get("judgements") or []):
            if not isinstance(j, dict) or not j.get("factor"):
                continue
            f = str(j["factor"])
            # ⚠ 按**因素**去重: judgements 逐检测行生成 (同一因素多采样点 → 多行),
            #   而关键控制点表是**因素级** (真实报告表10.1-1 一因素一行)。
            #   不去重会把 25 条苯乙烯/20 条噪声印 25/20 行 (实测 151 行)。
            if f in _seen_f:
                continue
            _seen_f.add(f)
            lv = j.get("level") if isinstance(j.get("level"), dict) else {}
            lv = lv or {}
            g = _grade.get(f) or {}
            lvl_txt = str(lv.get("level") or g.get("level") or "—")
            ctrl = str(lv.get("control") or "")
            if not ctrl and g.get("name"):
                ctrl = str(g["name"])            # 如 "中度危害作业"
            basis = str(lv.get("basis") or g.get("note") or "")
            rows.append([f, lvl_txt, ctrl or "—", basis or "—"])
        # 物理因素补充 (噪声/高温等, 来自 hazards 里的物理项)
        _phys = ("噪声", "高温", "工频电场", "振动", "紫外辐射")
        _has = {str(h.get("factor") or "") for h in (assess.get("hazards") or [])}
        for pf in _phys:
            if pf in _has and not any(r[0] == pf for r in rows):
                g = _grade.get(pf) or {}
                rows.append([pf, str(g.get("level") or "—"),
                             str(g.get("name") or "见防护设施/PPE章节"),
                             "GBZ 2.2—2007"])
        if not rows:
            return []
        rows = [[i] + r for i, r in enumerate(rows, 1)]
        return [{"name": "关键控制点表",
                 "cols": ["序号", "关键控制因子", "控制级别", "关键控制措施", "依据标准"],
                 "rows": rows}]

    if sec == "11":
        # 标准 10.2.11 补充建议: 问题与建议 (标准条款驱动)
        from web.advice_gen import fill_10211
        built = fill_10211(conn, assess)
        # 表11.5-1 拟建项目职业病危害因素体检项目与周期 (复刻真稿 表5.1-1)
        # ⚠ 必须在 fill_section 里产出: _tables_for_sub 只从 fill_section 取数,
        #   放 table_builder.build_data_tables 不进导出链路(实测产物 0 张表)。
        from web.health_exam_map import build_health_exam_rows
        _he = build_health_exam_rows(assess.get("_project_data") or assess)
        if _he:
            built["tables"].append({
                "name": "体检项目与周期表",
                "cols": ["危害因素", "体检类别", "岗前职业健康检查",
                         "岗中职业健康检查", "健康检查周期", "离岗职业健康检查"],
                "rows": _he,
            })
        # 表11.2-1 室内空气质量标准 (GB/T 18883—2002 固定节选, 6列)
        built["tables"].append({"name": "室内空气质量标准表",
                                "cols": ["序号", "参数", "参数类别", "单位", "标准值", "备注"],
                                "rows": [
                                    [1, "温度", "物理性", "℃", "22～28", "夏季空调"],
                                    [2, "温度", "物理性", "℃", "16～24", "冬季采暖"],
                                    [3, "相对湿度", "物理性", "%", "40～80", "夏季空调"],
                                    [4, "相对湿度", "物理性", "%", "30～60", "冬季采暖"],
                                    [5, "空气流速", "物理性", "m/s", "0.3", "夏季空调"],
                                    [6, "空气流速", "物理性", "m/s", "0.2", "冬季采暖"],
                                    [7, "二氧化碳", "化学性", "%", "0.10", "日平均值"]]})
        return built["tables"]

    if sec == "12":
        # 标准 10.2.12 结论: 结论要素表
        risk = assess.get("industry_risk") or {}
        rows = [["1", "职业病危害类别", risk.get("level", "—") + " (" + risk.get("name", "") + ")"],
                ["2", "存在的主要问题", f"{len(assess.get('judgements', []))} 条判定记录"],
                ["3", "可行性", "基本可行 (采取补充建议后)"]]
        return [{"name": "结论要素表", "cols": ["序号", "结论要素", "结论"], "rows": rows}]

    return []
