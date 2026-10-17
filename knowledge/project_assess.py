"""项目评估管线 — 识别引擎 + 判定引擎 + 9知识库全链 (职业病危害预评价核心)

输入 (项目数据, 用户提供):
  name        项目名称
  industry    行业码 4位 (GB/T 4754 中类码, 如 261) — 注意: 不带门类字母C
  equipment   [设备名称, ...]        设备清单
  processes   [{unit, process}, ...] 生产单元/工序 (可选, 匹配工作单元规则)
  detections  [{factor, ctwa, cste, cme, peak, value, oel_type, rate, labor,
                bio_value, bio_unit, indicator}...]  检测数据 (可选)

输出 (报告 10.2 各章节数据):
  10.2.1 总论        project_info (基础)
  10.2.3.1 工程概况   industry_chain (行业分类全层级) + industry_risk (风险分类)
  10.2.5.1 危害识别   hazards (设备/工序→危害→OEL/方法/分类/危化品)
  10.2.5.2 健康效应   occupational_diseases (危害→可能职业病)
  10.2.5.3 危害程度   judgements (判定) + grades (作业分级 229, 按 hazard_factor.category
                      路由到 229.1/229.2/229.3/229.4; 定不出级的行也留, 依据列写明原因)
  10.2.8  PPE       ppe_recommendations (危害类别→防护装备)
  10.2.9  健康监护   surveillance (危害→检查项目/周期)
  10.2.3.7 建筑卫生学 illumination_check (车间→照度标准值)
  10.2.3.8 辅助用室   gbz1_auxiliary (卫生特征→浴室/更衣室规则)
  10.2.12 结论       final_conclusion (核心判定摘要)

链路: 行业码→行业链+风险 | 设备→物料→OEL物质→危害→职业病
      | 危害→监护规则/PPE | 危害×检测→判定+作业分级 | 车间→照度检查
用法: python3 -m knowledge.project_assess
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402
from knowledge.identify_demo import split_materials  # noqa: E402
from knowledge.judge_cli import (judge_chemical, judge_physical, judge_bio,  # noqa: E402
                                 get_oel, level_of)
from knowledge.grade_engine import (grade_chemical, grade_dust, grade_heat,  # noqa: E402
                                    WM_TABLE, plan_grade, DUST_WM_HOLD, DUST_B_HOLD,
                                    HEAT_INPUT_HOLD)

# 物理因素→判定函数映射 (判据是名字清单, 只用于**判定**分支; 作业分级不再走它, 见 _catalog_of)
PHYSICAL_FACTORS = ("噪声", "高温", "手传振动", "工频电场", "微波辐射",
                    "超高频辐射", "高频电磁场", "紫外辐射")

# 未检出标记: 检测报告里 "<0.33" / "＜0.33" / "ND" / "未检出" 表示低于检出限
_LT_MARK = ("<", "＜", "≤", "≤")


def _parse_ctwa(v) -> float | None:
    """解析 CTWA 浓度 → float。

    ⚠ 关键(2026-09 修复): 检测报告大量使用**未检出**写法 `<0.33`。
      旧实现 `float(d["ctwa"])` 直接抛 ValueError → ctwa=None → **作业分级被静默丢弃**。
      "<0.33" 是**有效检测结果**(低于检出限), 语义 = 浓度极小 → 取检出限本身做保守估计,
      这样 b=C/PC-TWA 趋近 0 → 正确判为 0 级"相对无害作业"。

    实测: 长兴项目 6 个粉尘(滑石/其他/聚乙烯/电焊烟尘…)全部是 `<0.33`,
      修复前 grades=0 条(整章无作业分级), 修复后有分级。
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("＜", "<").replace("≤", "<")
    if not s:
        return None
    # 未检出: 去掉比较符, 用检出限数值 (保守估计, 见 docstring)
    s = s.lstrip("<").strip()
    if s in ("ND", "nd", "未检出", "—", "-", "/"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# 关系式写法的分档 ('＜10' / '<10%' / '≥50') —— 检测行里的 sio2 有时存的就是这种字符串
_SIO2_REL = re.compile(r"^([≤≥<>＝=＜＞]{1,2})\s*(\d+(?:\.\d+)?)\s*%?$")


def _sio2_band(v) -> str | None:
    """游离SiO2含量(M, %) → GBZ/T 229.1—2025 表1 的分档键。

    ⚠ 关键: WM_TABLE 的键是**分档**("<10"/"10-50"/"50-80"/">80"),
      而检测报告(尤其视觉提取)给的是**原始百分数**(如 3.39)。
      直接把 "3.39" 传进 WM_TABLE.get() 会命中不到档 → 旧实现兜到最坏档 (而且旧表的值是
      2010 版的 1/2/3/4) → 分级既不对也不是"不知道"。必须先分档, 分不出就返回 None。

    分档区间按 229.1—2025 第4页表1 逐格核对 (pdf_cells, span 原样顺序):
      r1 'M＜10'→1 | r2 '10≤M≤50'→2 | r3 '50＜M≤80'→4 | r4 'M＞80'→6
    → 边界是**左开右闭**递进: M=50 属 10-50 (WM=2), M=50.1 才进 50-80 (WM=4)。
      旧实现写的是 `n < 50 → 10-50`, 把 M=50 这一格错分进了 50-80 (WM 2→4, 凭空升一档)。
    """
    if v is None:
        return None
    s = str(v).strip()
    # ① 已是分档键 → 原样返回 (必须先判, 否则 "<10"/">80" 会被 lstrip 改写)
    if s in WM_TABLE:
        return s
    # ② 写成关系式的分档 ('＜10' / '<10%' / '≥50'): 旧实现 `lstrip("<>＜≤")` 把 '＜10' 读成
    #    10 → 落进 10-50 档 (WM 1→2, 分档反了)。关系式按**区间两端**认, 落在同一档才算认得出。
    m = _SIO2_REL.match(s)
    if m:
        op, num = m.group(1), float(m.group(2))
        strict = op in ("＜", "<", "＞", ">")
        if op in ("＜", "<", "≤", "<="):
            lo, hi = 0.0, num - (0.001 if strict else 0)
        else:
            lo, hi = num + (0.001 if strict else 0), 100.0
        a, b = _sio2_band(lo), _sio2_band(hi)
        return a if a and a == b else None
    # ③ 原始百分数 → 按表1 的边界分档
    try:
        n = float(s.lstrip("<>＜≤≥").strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if n < 10:
        return "<10"
    if n <= 50:                      # 10≤M≤50 (含 50)
        return "10-50"
    if n <= 80:                      # 50＜M≤80
        return "50-80"
    return ">80"


# 目录/限值名里自带的游离SiO2 标注, 例如
#   '滑石粉尘（游离SiO2 含量＜10%）' / '矽尘（游离SiO2含量≥10%）' / '稀土粉尘（游离SiO2含量<10 %）'
# 判据是**标准自己的排版写法** (GBZ 2.1 给粉尘定限值时把分档写进了名字), 不是物质清单 ——
# 将来新增任何粉尘条目, 只要照标准带这个标注就自动被认, 不需要同步任何名单。
_SIO2_LABEL = re.compile(r"SiO\s*2\s*含量\s*[（(]?\s*([≤≥<>＝=＜＞]{1,2})\s*(\d+(?:\.\d+)?)\s*%")


def _band_from_labels(names) -> tuple[str | None, str]:
    """一组标准名里的游离SiO2 含量标注 → (唯一分档键 / None, 说明)

    只在接受到**唯一**一档时才给键; 以下几种都必须让调用方走"未定级":
      · 名字里没有标注            → (None, "本行未实测 M、目录名也未标注分档")
      · '≥10%' 这类只给下限的标注 → 跨 10-50/50-80/>80 三档, 取哪一档都是编 (None 保留原因)
      · 多条标注指向不同档        → 不自相裁决 (None)
    """
    pins, saw_label = [], False
    for nm in names:
        for op, num in _SIO2_LABEL.findall(str(nm or "")):
            saw_label = True
            x = float(num)
            strict = op in ("＜", "<", "＞", ">")
            # 把 "<10" 这类关系式变成一个取样区间, 两端都落在同一档才算 pin 住
            if op in ("＜", "<", "≤", "<="):
                lo, hi = 0.0, x - (0.001 if strict else 0)
            else:                                    # ≥ / > 只给下限
                lo, hi = x + (0.001 if strict else 0), 100.0
            a, b = _sio2_band(lo), _sio2_band(hi)
            if a and a == b and a not in pins:
                pins.append(a)
    if len(pins) == 1:
        return pins[0], f"目录名标注 → {pins[0]}"
    if not saw_label:
        return None, "本行未实测 M、目录名也未标注分档"
    return None, f"目录名标注跨多档({'、'.join(pins) or '只给≥下限'})，定不出唯一 WM"


def _norm_factor_names(factor: str) -> tuple[str, str]:
    """(原写法, 归一写法) —— 归一复用 web.entity_link.name_norm, 判据只有一份"""
    from web.entity_link import name_norm
    raw = str(factor or "").strip()
    return raw, name_norm(raw)


def _catalog_of(conn, factor: str) -> tuple[str | None, str | None, str]:
    """检测行因子名 → (类别字段值, 命中的标准名, 命中途径)。

    作业分级的**唯一**路由取数入口 (#39)。判据全部是库里的字段值, 没有任何物质名清单:
      1) hazard_factor.name 精确 (归一后)  → category (职业病危害分类目录)
      2) hazard_factor 双向前缀            → 覆盖 '石灰石粉尘（ 呼尘）'→'石灰石粉尘'、
         '矽尘'→'矽尘（游离SiO2含量≥10%）' 这类带/缺尾部限定的写法
      3) oel_limit.factor_name 精确/前缀   → factor_type (限值表自带的另一套类别字段:
         化学有害因素/物理因素/粉尘/生物因素; '其他粉尘' 在那里是独立条目 '其他粉尘a')
      4) hazard_factor.name 含检测名       → 覆盖目录里的长登记名
      5) 全落空 → (None, …) 由调用方**显式降级并注明"未归类"**, 禁止默认按化学物分档
      (与 web/section_filler._det_bucket 同一口径: 同一批因子在两处必须得同一类别)

    ⚠ 已知的**保守边界** (2026-10-16 实测, 不是漏): 检测名只在限值表里以**别名+括注**登记时
      (如 '2-丁酮' 在 oel_limit 里是 '甲乙酮（2-丁酮）'), 第 3/4 档都够不着 → 该行走"未归类"
      并写明原因。为什么不加 `factor_name LIKE '%检测名%'` 这一档: 子串匹配会把短名误绑到
      **别的类别** (如 '煤' 命中 '煤尘'), 而类别正是这次路由唯一的判据 —— 宁可显式降级,
      也不能静默绑错部分。要收掉这条尾巴应在官方层补 alias 行 (hazard_factor_alias), 不是改判据。
    """
    raw, norm = _norm_factor_names(factor)
    keys = [k for k in (norm, raw) if k]
    for k in keys:
        r = _ds_one(conn, "SELECT category, name FROM hazard_factor WHERE name=? LIMIT 1", (k,))
        if r:
            return r[0], r[1], "hazard_factor.name 精确"
    for k in keys:
        r = _ds_one(conn, "SELECT category, name FROM hazard_factor WHERE name LIKE ? "
                          "ORDER BY LENGTH(name) LIMIT 1", (k + "%",))
        if r:
            return r[0], r[1], "hazard_factor 标准名以检测名开头"
        r = _ds_one(conn, "SELECT category, name FROM hazard_factor WHERE ? LIKE name || '%' "
                          "ORDER BY LENGTH(name) DESC LIMIT 1", (k,))
        if r:
            return r[0], r[1], "检测名以 hazard_factor 标准名开头"
    for k in keys:
        r = _ds_one(conn, "SELECT factor_name, factor_type FROM oel_limit WHERE factor_name=? "
                          "OR factor_name LIKE ? ORDER BY LENGTH(factor_name) LIMIT 1", (k, k + "%"))
        if r:
            return r[1], r[0], "oel_limit.factor_type"
    for k in keys:
        r = _ds_one(conn, "SELECT category, name FROM hazard_factor WHERE name LIKE ? "
                          "ORDER BY LENGTH(name) LIMIT 1", ("%" + k + "%",))
        if r:
            return r[0], r[1], "hazard_factor 标准名含检测名"
    return None, None, "查无类别"


def _oel_rows(conn, std_name: str, factor: str) -> list[tuple]:
    """因子 (原写法 + 命中标准名) → oel_limit 接触限值行 [(factor_name,oel_type,value,unit)]
    读门: oel_limit 有 source_standard 列 → 必须走 Dataset (`_ds_rows`), 不能裸 execute。
    量纲 (oel_type/unit) 是物理因素二级路由的判据, 采样形态 (总尘/呼尘) 在这里配对。"""
    rows: list[tuple] = []
    for k in [x for x in dict.fromkeys([str(factor or "").strip(), str(std_name or "").strip()]) if x]:
        rows.extend(_ds_rows(
            conn, "SELECT factor_name, oel_type, value, unit FROM oel_limit "
                  "WHERE factor_name=? OR factor_name LIKE ?", (k, k + "%")))
    return list(dict.fromkeys(rows))


def _paren_token(text: str) -> str:
    """'石灰石粉尘（ 呼尘）' → '呼尘'; 无尾部括号 → '' (检测行的采样形态就在这儿)"""
    m = re.search(r"[（(]([^（）()]*)[）)]\s*$", str(text or "").strip())
    return re.sub(r"\s", "", m.group(1)) if m else ""


def _dust_contact_ratio(d: dict, rows: list[tuple], factor: str) -> float | None:
    """粉尘行的接触比值 B = CTWA / PC-TWA (229.1 式 A.1)。

    采样形态配对 (判据来自字段值, 不是名单): 检测名尾部括号写了 '呼尘'/'总尘' 时,
    只配 oel_type 里含同一个词的限值行; 没写时按 GBZ/T 192.1 的总尘理解 → 优先 '总尘' 行。
    ⚠ 229.1 附录 A.3 要求"规定有呼吸性限值者优先测呼吸性粉尘并以此算 B" —— 本项目只测了
      总尘时, 分级偏低, 这是数据来源的限制不是算法的; 报告里如需定级应补呼尘实测。
    """
    raw = _parse_ctwa(d.get("ctwa"))
    cands = [r for r in rows if "TWA" in str(r[1] or "")]
    token = _paren_token(factor)
    if token:
        hit = [r for r in cands if token in str(r[1] or "")]
        cands = hit or cands
    total = [r for r in cands if "总尘" in str(r[1] or "")]
    cands = total or cands
    if raw is not None:
        vals = [float(r[2]) for r in cands if _is_num(r[2]) and float(r[2]) > 0]
        if vals:
            return raw / min(vals)          # 多档限值并存时取 B 较大者 (偏保守)
    return None


def _is_num(v) -> bool:
    try:
        float(str(v))
        return True
    except (TypeError, ValueError):
        return False


def merge_process_materials(conn, hazards: dict, process_text: str) -> dict:
    """工艺描述层: 段落文本 → 物质名 → 合并入 hazards (source=工艺描述)
    无OEL的物质 (三羟甲基丙烷/聚乙二醇...) 保持 needs_test=False 仅提示"""
    from knowledge.process_material_extractor import extract_from_text
    names = extract_from_text(conn, process_text)
    for n in names:
        h = hazards.setdefault(n, {"sources": set(), "via": set()})
        h["sources"].add("工艺描述")
    return hazards


def _ds_rows(conn, sql: str, args=()) -> list[tuple]:
    """官方层读门 shortcut: Dataset.rows (读出即过标准号兜底, 见 knowledge/dataset.py)"""
    from knowledge.dataset import Dataset
    return Dataset(conn).rows(sql, args)


def _ds_one(conn, sql: str, args=()) -> tuple | None:
    """官方层读门 shortcut: Dataset.one"""
    from knowledge.dataset import Dataset
    return Dataset(conn).one(sql, args)


def _auto_factor(conn, w: str) -> str:
    """物料名→危害因素: 通用管线, 直接查 oel_limit/hazard_factor (精确/包含), fallback material_dictionary别名.

    不依赖手工映射表 — 任何物料名词走这个自动管线:
      1. oel_limit.factor_name 精确 (邻苯二甲酸酐→邻苯二甲酸酐)
      2. oel_limit 包含 (物料名是因子子串)
      3. hazard_factor.name 精确
      4. material_dictionary 别名 (50%乙二醇→乙二醇)
    顺酐/苯酐等物料名直接命中 oel_limit(马来酸酐/邻苯二甲酸酐), 无需人工补词典。
    """
    if not w:
        return ""
    w = str(w).strip().replace(" ", "")
    if not w:
        return ""
    from knowledge.dataset import Dataset       # 读门: 官方层取数唯一出口
    ds = Dataset(conn)
    # 1. oel_limit 精确
    r = ds.one("SELECT factor_name FROM oel_limit WHERE factor_name=? LIMIT 1", (w,))
    if r:
        return r[0]
    # 2. oel_limit 包含 (物料名是因子子串)
    r = ds.one("SELECT factor_name FROM oel_limit WHERE factor_name LIKE ? LIMIT 1", ("%" + w + "%",))
    if r:
        return r[0]
    # 3. hazard_factor 精确
    r = ds.one("SELECT name FROM hazard_factor WHERE name=? LIMIT 1", (w,))
    if r:
        return r[0]
    # 4. material_dictionary 别名
    r = conn.execute("SELECT oel_factor FROM material_dictionary WHERE material=? LIMIT 1", (w,)).fetchone()
    if r and r[0]:
        return r[0]
    return ""


def identify_hazards(conn, equipment: list[str], processes: list[dict] | None = None,
                     process_text: str = "", materials: list | None = None) -> list[dict]:
    """识别引擎: 项目物料→危害主源 + 设备→物料→OEL + 工序→规则危害 (来源链保留)

    修复: 物料→危害判定改用 _auto_factor 通用管线 (物料名词直接查 oel_limit/hazard_factor),
    不依赖手工 material_dictionary 映射, 任何行业/项目物料自动匹配。
    """
    hazards = {}   # factor_name -> {sources:set, via:set}
    # 0) 项目物料归一化 (原辅料表, 本项目权威物料清单)
    proj_mats = []
    for m in (materials or []):
        if isinstance(m, dict):
            nm = str(m.get("name") or m.get("物料名称") or "").strip()
        else:
            nm = str(m).split("|")[0].strip()
        nm = nm.replace(" ", "")
        if nm and nm not in proj_mats:
            proj_mats.append(nm)
    # 0.5) 项目物料→危害 (主源, _auto_factor 通用管线: 苯乙烯/邻苯二甲酸酐/马来酸酐/乙二醇自动命中)
    for w in proj_mats:
        f = _auto_factor(conn, w)
        if f:
            h = hazards.setdefault(f, {"sources": set(), "via": set()})
            h["sources"].add("原辅料")
            h["via"].add(w)
    # 1) 设备→物料→危害 (equipment_material 设备查物料, _auto_factor 自动匹配; 补充源)
    # 通用性: equipment_material 是行业知识库(长兴表17/压克力线), 设备关联的物料必须属于本项目材料清单
    # (proj_mats), 否则是其他行业知识库噪声 —— 如长兴"乙二醇/环己烷/甲苯/甲醇"污染新泰无机盐项目。
    # 任何项目设备碰到的物料, 只有在本项目原辅料清单里才算真实危害 (准确优先, 宁缺失勿污染)。
    for eq in equipment:
        mats = conn.execute(
            "SELECT DISTINCT material FROM equipment_material WHERE equipment LIKE ?",
            (f"%{eq}%",)).fetchall()
        for (m,) in mats:
            for word in split_materials(m):
                if word not in proj_mats:
                    continue
                f = _auto_factor(conn, word)
                if f:
                    h = hazards.setdefault(f, {"sources": set(), "via": set()})
                    h["sources"].add(f"设备[{eq}]")
                    h["via"].add(word)
    # 2) 工序→规则 (work_unit_rule: 生产单元/工序 → 危害因素)
    if processes:
        for pu in processes:
            unit, proc = pu.get("unit", ""), pu.get("process", "")
            rows = conn.execute(
                "SELECT unit, process, factors FROM work_unit_rule "
                "WHERE (unit LIKE ? OR ?='') AND (process LIKE ? OR ?='')",
                (f"%{unit}%", unit, f"%{proc}%", proc)).fetchall()
            for u, p, fx in rows:
                for f in fx.split(";"):
                    f = f.strip()
                    if f:
                        h = hazards.setdefault(f, {"sources": set(), "via": set()})
                        h["sources"].add(f"工序[{u}/{p}]")
    # 2.5) 设备/工序→物理因素 (噪声/高温/工频电场) —— 2026-09 新增
    # 依据 (8份真实报告调研): 物理因素在报告里是**因子级**, 挂在"评价单元|工序|岗位",
    #   从不点名具体设备 (7/8 报告只用泛化表述"高噪声设备集中布置")。
    #   表格形态示例: "水泵组件生产单元 | 机加工 | 设备运行过程中产生 | 油雾、噪声"
    # 故: 用 physical_factors(设备) 作机制, 但按"工序/单元"归因 (sources 记工序),
    #   不把设备名塞进 sources —— 与真实报告口径一致。
    # 物理因素有 GBZ 2.2 限值 (库内含噪声 LEX,8h=85dB / 高温 WBGT), 走法规表不靠 LLM。
    try:
        from web.hazard_grid import physical_factors
        _phys_src: dict[str, set] = {}
        _pairs = []
        if processes:
            for pu in processes:
                u, p = str(pu.get("unit") or ""), str(pu.get("process") or "")
                if u or p:
                    _pairs.append((u, p, f"{u}{p}"))
        if not _pairs and equipment:
            # 无结构化工序时退化为"设备→工序"关键词规则 (process_from_equipment)
            # ⚠ process_from_equipment 返回 str 或 list, 需归一 (否则按字符迭代 → 工序[/反])
            from web.hazard_grid import process_from_equipment
            for eq in equipment:
                if not isinstance(eq, str):
                    continue
                prs = process_from_equipment(eq) or []
                if isinstance(prs, str):
                    prs = [prs]
                for pr in prs:
                    pr = str(pr).strip()
                    if pr:
                        _pairs.append(("", pr, f"{eq}{pr}"))
        for u, p, blob in _pairs:
            for pf in (physical_factors(blob) or []):
                _phys_src.setdefault(pf, set()).add(f"工序[{u}/{p}]" if u or p else "设备运行")
        # 直接对设备名扫一遍 (兜底: 泵/釜/锅炉等物理源), 但仍以"生产过程"归因
        for eq in (equipment or []):
            if not isinstance(eq, str):
                continue
            for pf in (physical_factors(eq) or []):
                _phys_src.setdefault(pf, set()).add("生产设备运行")
        for pf, srcs in _phys_src.items():
            h = hazards.setdefault(pf, {"sources": set(), "via": set()})
            h["sources"].update(srcs)
    except Exception:
        pass  # 物理因素识别失败不阻断化学主链路

    # 2.5) 工艺描述层: 段落文本 → 物质 (碳酸钠/三羟甲基丙烷/1,6-己二醇...)
    if process_text:
        merge_process_materials(conn, hazards, process_text)
    # 3) 附加 OEL 限值/方法/生物限值/目录分类/危化品CAS标注/监护/职业病
    out = []
    for f, info in sorted(hazards.items()):
        # --- OEL ---
        oels = get_oel(conn, f)
        # --- 检测方法 ---
        method = conn.execute(
            "SELECT standard_code FROM method_catalog WHERE factor LIKE ? LIMIT 1",
            (f"%{f}%",)).fetchone()
        # --- BEI ---
        bio = conn.execute(
            "SELECT COUNT(*) FROM bio_limit WHERE factor_name=?", (f,)).fetchone()[0]
        # --- 分类标签 ---
        cat_row = _ds_one(conn, "SELECT category, name FROM hazard_factor WHERE name=? LIMIT 1", (f,))
        if not cat_row:
            alias = conn.execute(
                "SELECT catalog_name FROM hazard_factor_alias WHERE oel_name=? "
                "AND match_type IN ('exact','core_eq') LIMIT 1", (f,)).fetchone()
            if alias:
                cat_row = _ds_one(conn, "SELECT category, name FROM hazard_factor WHERE name=? LIMIT 1",
                                  (alias[0],))
        # --- 危化品 CAS ---
        cas = conn.execute(
            "SELECT cas FROM oel_limit WHERE factor_name=? AND cas IS NOT NULL LIMIT 1",
            (f,)).fetchone()
        hazchem = None
        if cas and cas[0]:
            h = conn.execute(
                "SELECT name, note, is_toxic FROM hazchem_item WHERE cas=? LIMIT 1",
                (cas[0],)).fetchone()
            if h:
                hazchem = {"name": h[0], "note": h[1],
                           "is_toxic": bool(h[2])}
        # --- 监护规则 (surveillance_rule) ---
        surv = conn.execute(
            "SELECT check_type, cycle, must_items, target_disease FROM surveillance_rule "
            "WHERE factor LIKE ? AND check_type='在岗期间' LIMIT 1", (f"%{f}%",)).fetchone()
        # --- 职业病 (occupational_disease: 危害名称→疾病, 用包含匹配) ---
        diseases = []
        for r in _ds_rows(conn,
                          "SELECT category, name FROM occupational_disease WHERE name LIKE ? OR name LIKE ? LIMIT 3",
                          (f"%{f}%", f"%{f.split('及其')[0]}%")):
            diseases.append(r[0])
        out.append({
            "factor": f,
            "sources": sorted(info["sources"]),
            "via_materials": sorted(info["via"]),
            "oel": [{"type": r["oel_type"], "value": r["value"], "unit": r["unit"]}
                    for r in oels],
            "method": method[0] if method else None,
            "has_bio": bio > 0,
            "needs_test": bool(oels),  # 有OEL需检测(mg/m3等数值对比)
            "catalog": {"category": cat_row[0], "name": cat_row[1]} if cat_row else None,
            "hazchem": hazchem,
            "surveillance": {"cycle": surv[0], "must_items": surv[1],
                             "target_disease": surv[2]} if surv else None,
            "diseases": diseases,
        })
    return out


def industry_chain(conn, industry_code: str) -> dict | None:
    """行业链: 4位中类码 → 门类/大类/中类/小类 全层级 (10.2.3.1)
    industry_code: '261' (中类码) / 'C261' / 'C265合成材料制造'(含文字) — 提取纯代码"""
    if not industry_code:
        return None
    # 提取纯代码: 'C265合成材料制造'→'265', '441电力'→'441', 'C261'→'261'
    import re
    m = re.search(r"(\d{3,4})", str(industry_code))
    if m:
        industry_code = m.group(1)
    else:
        industry_code = str(industry_code).strip()
    # 中类: 直接查 3 位码 (261)
    mid = conn.execute(
        "SELECT code, name FROM industry_class WHERE code=? AND level='中类' LIMIT 1",
        (industry_code,)).fetchone()
    if not mid:
        # 4位小类码 (如2651): 查小类, 用其名称 (chain 显示完整小类, 而非截断误映射)
        if re.match(r"^\d{4}$", str(industry_code)):
            sm = conn.execute(
                "SELECT code, name FROM industry_class WHERE code=? AND level='小类' LIMIT 1",
                (industry_code,)).fetchone()
            if sm:
                mid = (sm[0], sm[1])
                big = conn.execute(
                    "SELECT code, name FROM industry_class WHERE code=? AND level='大类' LIMIT 1",
                    (industry_code[:2],)).fetchone()
        # 兼容: 用户给了 C261 → 取后3位 (仅当输入是 '<字母>3位码' 形式)
        # (修复: 4位码如'2651'是完整小类, 不能截成后3位'651' → 误映射软件开发!
        #  2651 初级形态塑料及合成树脂制造 被 industry_chain 映射到 651 软件开发)
        if not mid and re.match(r"^[A-Za-z]\d{3}$", str(industry_code)):
            mid = conn.execute(
                "SELECT code, name FROM industry_class WHERE code=? AND level='中类' LIMIT 1",
                (industry_code[-3:],)).fetchone()
            industry_code = industry_code[-3:]
        if not mid:
            return None
    # 大类: 中类前2位 (261 → 26)
    big = conn.execute(
        "SELECT code, name FROM industry_class WHERE code=? AND level='大类' LIMIT 1",
        (industry_code[:2],)).fetchone()
    if not big and re.match(r"^\d{4}$", str(industry_code)):
        big = conn.execute(
            "SELECT code, name FROM industry_class WHERE code=? AND level='大类' LIMIT 1",
            (industry_code[:2],)).fetchone()
    # 门类: 大类范围 (26 → C 13-43)
    gate_code = None
    for gate, (lo, hi) in GATE_RANGES.items():
        if lo <= int(industry_code[:2]) <= hi:
            gate_code = gate
            break
    if gate_code:
        gate = conn.execute(
            "SELECT code, name FROM industry_class WHERE code=? AND level='门类' LIMIT 1",
            (gate_code,)).fetchone()
    else:
        gate = None
    return {
        "gate": gate.lastrowid if False else ({"code": gate[0], "name": gate[1]} if gate else None),
        "big": {"code": big[0], "name": big[1]} if big else None,
        "mid": {"code": mid[0], "name": mid[1]},
        "full": " > ".join(filter(None, [
            gate[1] if gate else None,
            big[1] if big else None,
            mid[1],
        ])),
    }


# GB/T 4754 门类范围 (按大类码区间)
GATE_RANGES = {
    "A": (1, 5), "B": (6, 12), "C": (13, 43), "D": (44, 46),
    "E": (47, 50), "F": (51, 52), "G": (53, 60), "H": (61, 62),
    "I": (63, 65), "J": (66, 69), "K": (70, 70), "L": (71, 72),
    "M": (73, 75), "N": (76, 79), "O": (80, 82), "P": (83, 84),
    "Q": (85, 86), "R": (87, 88), "S": (89, 90), "T": (91, 96),
}


def ppe_for_hazards(conn, hazards: list[dict]) -> list[dict]:
    """PPE 建议: 危害类别→防护装备 (GB 39800 9类, 规则映射+人工把关)
    规则 (危害→装备, 按 GB 39800.1 4.3 危害评估逻辑):
      粉尘→HX呼吸防护(op) + YM眼面 | 化学物→FZ化学防护服 + HX + YM
      噪声→TL听力 | 高温→FZ隔热服 | 振动→SF手部 | 电离辐射→SF电离防护手套
      生物→HX + FZ | 坠落→ZL | 物理(电场/微波)→YM + FZ
    """
    # 规则 (危害→装备, 按 GB 39800.1 4.3 危害评估逻辑):
    # 条目用 精确 item_name (化学→化学防护服FZ-07, 呼吸→防毒面具, 眼面→职业眼面YM-04)
    rules = {
        "粉尘": [("HX", "动力送风过滤式呼吸器"), ("YM", "职业眼面部防护具")],
        "噪声": [("TL", "耳塞")],
        "高温": [("FZ", "隔热服"), ("YM", "职业眼面部防护具")],
        "振动": [("SF", "机械危害防护手套")],
        "化学": [("FZ", "化学防护服"), ("HX", "长管呼吸器"), ("YM", "职业眼面部防护具")],
        "生物": [("HX", "动力送风过滤式呼吸器"), ("FZ", "化学防护服")],
        "物理": [("YM", "激光防护镜"), ("FZ", "防静电服")],
    }
    out = []
    for h in hazards:
        cat = (h.get("catalog") or {}).get("category", "")
        keys = []
        if "粉尘" in cat:
            keys.append("粉尘")
        if "化学" in cat or h.get("hazchem"):
            keys.append("化学")
        if "物理" in cat and not any(k in h["factor"] for k in ("噪声", "高温", "振动")):
            keys.append("物理")
        if "噪声" in h["factor"]:
            keys.append("噪声")
        if "高温" in h["factor"]:
            keys.append("高温")
        if "手传振动" in h["factor"]:
            keys.append("振动")
        if "生物" in cat:
            keys.append("生物")
        if not keys:
            keys = ["化学"]  # 默认保守
        items = []
        seen = set()
        for k in keys:
            for code, iname in rules.get(k, ()):
                item = _ds_one(conn,
                    "SELECT item_name, std_code FROM ppe_item "
                    "WHERE cat_code=? AND item_name=? LIMIT 1", (code, iname))
                if not item:
                    item = _ds_one(conn,
                        "SELECT item_name, std_code FROM ppe_item "
                        "WHERE cat_code=? LIMIT 1", (code,))
                if item and item[0] not in seen:
                    seen.add(item[0])
                    items.append({"class": code, "item": item[0], "std": item[1]})
        out.append({"factor": h["factor"], "ppe": items})
    return out


def grade_detections(conn, detections: list[dict]) -> list[dict]:
    """作业分级 (GBZ/T 229) 的路由 + 计算 —— 判据 = 库里的**类别字段**, 不是物质名清单。

    路由: `hazard_factor.category` (兜底 `oel_limit.factor_type`) 决定 229.1/229.2,
      物理因素再看该因子接触限值行的**量纲** (oel_type/unit) 分到 229.3 (WBGT) / 229.4 (dB(A));
      放射性/生物/其他因素、类别查无、量纲无对应部分 → **明确不参与并写明原因**。
      (旧写法 `"高温" in f` → `d.get("sio2")` → else 化学物, 会把没填 sio2 的粉尘行、噪声行
       一律按 229.2 分档 → 成品「关键控制点表」给粉尘印化学物的式子, 依据标准选错部分。)
    输出: 每个因子一条 (同一因子多采样点时"定出级的"赢过"未定级的");
      定级行带 level/name/g/note, 未定级行 level=None 且 note 就是那句原因 ——
      下游 (web/section_filler 的关键控制点表) 直接把 note 印进"依据标准"列, 所以
      **不许静默丢行也不许静默套一个错的式子**: 没级就得让读者看到为什么。
    """
    grades_by_factor: dict = {}

    def _put_grade(e: dict) -> None:
        """同一因子多个采样点 → 只留一条, 且**定出级的那条赢过未定级的那条**。
        下游 web/section_filler 的关键控制点表是 `for g in grades: _grade[factor]=g` (后写覆盖,
        且该文件本轮不可改) —— 所以"未定级行不得抹掉已定级行"这个取舍必须在这里就钉死;
        同为定级行时仍按后写覆盖, 与旧实现/下游口径逐字一致 (旧实现每行都追加, 靠的也是后写)。
        """
        old = grades_by_factor.get(e["factor"])
        if old is not None and old.get("level") is not None and e.get("level") is None:
            return
        grades_by_factor[e["factor"]] = e

    for d in detections or []:
        f = d.get("factor")
        if not f:
            continue
        labor = d.get("labor", "Ⅱ")
        cat, std_name, via = _catalog_of(conn, f)
        rows = _oel_rows(conn, std_name, f)
        dims = [f"{r[1]}|{r[3] or ''}" for r in rows]
        plan = plan_grade(cat, dims)
        entry = {"factor": f, "category": cat, "part": plan["part"], "dims": dims,
                 "route": f"{via}{'' if not cat else f'({cat})'}"}
        if plan["engine"] is None:
            _put_grade({**entry, "level": None, "name": None, "note": plan["reason"]})
            continue
        g = None
        if plan["engine"] == "dust":
            # WM 的两个来源: ①本行实测 M ②标准目录/限值名自带的分档标注; 都没有 → 未定级
            band, why = _sio2_band(d.get("sio2")), ""
            if band is None:
                band, why = _band_from_labels([f, std_name] + [r[0] for r in rows])
            if band is None:
                _put_grade({**entry, "level": None, "name": None,
                            "note": DUST_WM_HOLD.format(why)})
                continue
            b = _dust_contact_ratio(d, rows, f)
            if b is None:
                b = d.get("btw")
            if b is None:
                _put_grade({**entry, "level": None, "name": None, "note": DUST_B_HOLD})
                continue
            g = grade_dust(band, float(b), labor)
        elif plan["engine"] == "heat":
            # 229.3 表1 需要 WBGT 效应值 + 接触时间率。检测行的声明字段是 value/rate
            # (judge_physical 用的就是这一对), 分级侧原来读的是没人写的 wbgt/rate_pct →
            # 两边走法都认; 都没有实测值时**必须留"未定级+原因"**, 不能再拿 30℃/100% 造结论。
            wbgt = d.get("wbgt") if d.get("wbgt") is not None else d.get("value")
            rate = d.get("rate_pct") if d.get("rate_pct") is not None else d.get("rate")
            m = re.search(r"(\d+(?:\.\d+)?)", str(rate)) if rate is not None else None
            rate_pct = float(m.group(1)) if m else None
            if not _is_num(wbgt):
                _put_grade({**entry, "level": None, "name": None, "note": HEAT_INPUT_HOLD})
                continue
            g = grade_heat(labor, rate_pct if rate_pct is not None else 100, float(wbgt))
        elif plan["engine"] == "noise":
            g = None          # 229.4 原文不在仓库 → 只路由不造表 (reason 已写好)
        else:                 # chemical: 计算与旧分支逐字一致 (本轮只改"谁能进这一支")
            if d.get("wd") or d.get("b"):
                g = grade_chemical(d.get("wd", "中度危害"), d.get("b", 0.5), labor)
            else:
                b = None
                if d.get("ctwa") is not None:
                    ctwa = _parse_ctwa(d["ctwa"])
                    oel = get_oel(conn, f)
                    if oel and ctwa is not None:
                        b = ctwa / float(oel[0]["value"]) if float(oel[0]["value"]) > 0 else None
                if b is not None:
                    g = grade_chemical(d.get("wd", "中度危害"), b, labor)
        if g:
            _put_grade({**entry, **g})
        elif plan["engine"] == "chemical":
            pass              # 化学物行算不出 B/WD 时与旧实现一致: 不产分级行 (依据列留 '—')
        else:
            # 定不出来也要留行: 依据列写明"本该走 229 的哪一支、为什么没级", 不许静默
            _put_grade({**entry, "level": None, "name": None,
                        "note": plan["reason"] or "缺该部分分级所需的输入 → 未定级"})

    return list(grades_by_factor.values())


def assess_project(conn, project: dict) -> dict:
    """项目评估: 识别 + 判定 + 分级 + 监护 + PPE 全链"""
    # 1) 行业风险分类 + 行业链
    risk = None
    ind = project.get("industry")
    if ind:
        # 从"441 电力、热力生产和供应业"/"C384"/"C3841"提取纯代码, 兼容 risk_category(C261/D44格式)
        import re
        m = re.search(r"([A-Z]?\d{2,4})", str(ind))
        pure = m.group(1) if m else str(ind).strip()
        candidates = {str(ind), pure}
        # 中类码: 3位取本身, 4位取前3位(C3841→C384), 2位(大类)保留
        m4 = re.search(r"([A-Z]?)(\d{3,4})", pure)
        if m4:
            prefix, digits = m4.group(1), m4.group(2)
            code3 = digits[:3]  # 取前3位 (C3841→384)
            candidates.add("C" + code3)
            candidates.add(code3)
            candidates.add(prefix + digits[:2])  # 大类2位 (D44 或 44)
            candidates.add("D" + digits[:2])
        # 去掉纯文字候选
        candidates = {c for c in candidates if re.search(r"\d", c)}
        for cand in candidates:
            r = conn.execute(
                "SELECT industry_code, industry_name, risk_level FROM risk_category "
                "WHERE industry_code=?", (cand,)).fetchone()
            if r:
                risk = {"code": r[0], "name": r[1], "level": r[2]}
                break
        # 精确匹配: 用行业名关键词在 risk_category 里找更具体的 (焚烧发电→火力发电)
        if risk and ind:
            # 关键词→risk_category 行的匹配 (如"生活垃圾焚烧发电"→"火力发电"严重)
            kw_rows = conn.execute(
                "SELECT industry_code, industry_name, risk_level FROM risk_category "
                "WHERE industry_name LIKE ? OR industry_name LIKE ? OR industry_name LIKE ?",
                ("%" + str(ind)[:6] + "%", "%发电%", "%电力生产%")).fetchall()
            if kw_rows and ("发电" in str(ind) or "电力生产" in str(ind)):
                # 发电/电力生产应取"严重"(火力/热电联产/生物质能)
                for kr in kw_rows:
                    if "严重" in kr[2]:
                        risk = {"code": kr[0], "name": kr[1], "level": kr[2]}
                        break
        # 工艺特征兜底: 垃圾/焚烧发电 → 生物质能发电(严重) (行业代码441太泛化, 按工艺归"严重")
        if risk and risk["level"] != "严重":
            eqs = project.get("equipment") or []
            eqs_txt = " ".join(str(e) for e in eqs)
            if ("垃圾" in str(ind) or "焚烧" in eqs_txt or "垃圾" in eqs_txt
                    or "余热锅炉" in eqs_txt or "汽轮发电机" in eqs_txt):
                r2 = conn.execute(
                    "SELECT industry_code, industry_name, risk_level FROM risk_category "
                    "WHERE industry_name LIKE '%生物质能发电%' AND risk_level='严重'").fetchone()
                if r2:
                    risk = {"code": r2[0], "name": r2[1], "level": r2[2]}
    chain = industry_chain(conn, ind) if ind else None
    # 2) 危害识别
    # 设备字段归一: 提取层两套并存 (equipment / equipment_detail), 老项目只有后者。
    # (同 table_builder 的回退口径 — 设备明细表也用 equipment_detail or equipment)
    _eq = project.get("equipment") or []
    if not _eq:
        _eq = [str(x.get("name") or "").strip() if isinstance(x, dict) else str(x)
               for x in (project.get("equipment_detail") or [])]
        # 剔除表头残留行 (提取脏数据: name=设备名称)
        _eq = [e for e in _eq if e and e not in ("设备名称", "名称")]
    hazards = identify_hazards(conn, _eq,
                               project.get("processes"),
                               project.get("process_text", ""),
                               project.get("materials", []))
    # 3) 判定 (检测数据)
    judgements = []
    for d in project.get("detections", []):
        f = d["factor"]
        if d.get("bio_value") is not None:
            r = judge_bio(conn, f, d.get("indicator"), d["bio_value"], d.get("bio_unit", ""))
        elif any(k in f for k in PHYSICAL_FACTORS):
            r = judge_physical(conn, f, d.get("value"), oel_type=d.get("oel_type"),
                               rate=d.get("rate", "100%"), labor=d.get("labor", "Ⅰ"))
        else:
            r = judge_chemical(conn, f, d.get("ctwa"), d.get("cste"), d.get("cme"),
                               d.get("peak"))
        judgements.append({"factor": f, **r})
    # 4) 作业分级 (GBZ/T 229) —— 路由判据见 grade_detections()
    grades = grade_detections(conn, project.get("detections") or [])
    # 5) PPE 建议
    ppe = ppe_for_hazards(conn, hazards)
    # 6) 接触水平汇总
    levels = {}
    for j in judgements:
        if j.get("level") and isinstance(j["level"], dict):
            lv = j["level"]["level"]
            levels.setdefault(lv, []).append(j["factor"])
    # 危害因素源头过滤: 只保留能回溯项目数据的因子
    # (修复: v10/v11 报告'硫酸二甲酯/苯醌/石蜡烟' — assess从行业模板/检查表带入, 项目data无此物料)
    _proj_mats = set()
    for m in (project.get("materials") or []):
        nm = (str(m.get("name") if isinstance(m, dict) else m)).split("|")[0].strip()
        if nm:
            _proj_mats.add(nm)
    # ⚠ Q1 收紧 (2026-09): hazard_grid 存的是**类比企业**的危害分布, 不是本项目的。
    #   原先 _real_factor() 把 _grid_factors 全量放行 → 类比企业的因素被当成本项目事实
    #   (实测: 树脂厂因类比数据混入 聚乙烯粉尘/电焊烟尘 等非本厂工艺因素)。
    #   现在: grid 因素只有在**本项目也能独立印证**时才放行 ——
    #     ① 出现在本项目物料清单 (_proj_mats) ② 或属于本项目设备派生的物理因素
    #   纯 grid 独有的因素 → 保留但标 grid_only, 不进 hazards 主表 (由类比章节引述)。
    _grid_factors = set()
    for g in (project.get("hazard_grid") or []):
        if isinstance(g, dict):
            for f in str(g.get("factors") or "").replace(" ", "").split("、"):
                if f and len(f) >= 2:
                    _grid_factors.add(f)
    _det_factors = set(str(d.get("factor") or "") for d in (project.get("detections") or []))
    _keep_common = {"噪声", "高温", "局部振动", "工频电场", "紫外辐射", "矽尘", "电焊烟尘",
                    "其他粉尘", "粉尘", "噪声(等效声级)", "WBGT", "电焊弧光", "臭氧",
                    "氮氧化物", "锰及其无机化合物", "三氧化铬", "金属镍与难溶性镍化合物"}

    def _real_factor(f):
        """只有能回溯到本项目自身证据的因子才算真实危害"""
        if not f:
            return False
        return (f in _det_factors        # 本项目检测数据
                or f in _proj_mats       # 本项目物料
                or f in _keep_common)    # 通用物理/粉尘 (任何项目都需评)
        # 注: 不再因 f in _grid_factors 放行 —— 类比数据不构成本项目事实

    hazards = [h for h in hazards if isinstance(h, dict) and _real_factor(h.get("factor", ""))]
    # 去重 (同名因子合并)
    _seen_hz = {}
    for h in hazards:
        _seen_hz.setdefault(h["factor"], h)
    hazards = list(_seen_hz.values())

    return {
        "project": project.get("name", ""),
        "_project_data": project,  # 原文输入 (设备/检测/工艺) 供报告表格全量
        "industry_risk": risk,
        "industry_chain": chain,
        "hazards": hazards,
        "judgements": judgements,
        "grades": grades,
        "ppe": ppe,
        "level_summary": levels,
    }


def main():
    conn = connect()
    # 演示项目: 长兴特殊材料(苏州) 光固化涂料材料项目 (261, 真实设备+检测表25/26)
    project = {
        "name": "长兴特殊材料年产27080吨高性能光固化涂料材料项目",
        "industry": "261",
        "equipment": ["酯化釜", "纯化槽", "洗涤塔", "溶剂回收槽", "中和真空槽",
                      "酯化第一冷凝器", "真空除沫器", "洗釜泵", "油相溶剂泵"],
        "detections": [
            {"factor": "甲苯", "ctwa": 30, "cste": 95},
            {"factor": "环己烷", "ctwa": 0.3, "cste": None, "peak": 4.0},
        ],
    }
    result = assess_project(conn, project)
    print("=" * 60)
    print(f"📋 {result['project']}")
    if result["industry_chain"]:
        c = result["industry_chain"]
        print(f"  行业链: {c['full'] or '未定位'} (中类 {c['mid']['code']} {c['mid']['name']})")
    if result["industry_risk"]:
        r = result["industry_risk"]
        print(f"  风险分类: **{r['level']}** ({r['name']})")
    print("=" * 60)
    print(f"\n🔍 危害识别 ({len(result['hazards'])} 项):")
    for h in result["hazards"]:
        method = f" | 方法: {h['method']}" if h["method"] else " | 方法: 无GBZ/T300"
        bio = " | BEI:有" if h["has_bio"] else ""
        cat = f" [目录:{h['catalog']['category']}]" if h.get("catalog") else " [目录未收录]"
        hz = ""
        if h.get("hazchem"):
            hz = f" ⚠️危化品[{h['hazchem']['name']}]" + ("·剧毒" if h["hazchem"]["is_toxic"] else "")
        surv = " | 监护:有" if h.get("surveillance") else ""
        dis = f" | 职业病:{'、'.join(h['diseases'][:2])}" if h["diseases"] else ""
        print(f"  🔴 {h['factor']}{cat}{hz}{surv}{dis} [{'需检测' if h['needs_test'] else '定性'}]"
              f" ← {'; '.join(h['sources'][:3])}{method}{bio}")
    print(f"\n📊 判定 ({len(result['judgements'])} 条):")
    for j in result["judgements"]:
        status = "✅合格" if j.get("pass") else ("❌不合格" if j.get("pass") is False else "⚠️待测")
        lv = f" 级别:{j['level']['level']}" if isinstance(j.get("level"), dict) else ""
        print(f"  {j['factor']}: {status}{lv}")
        for c in j.get("checks", []):
            mark = "✅" if c["pass"] else "❌"
            print(f"    {mark} {c['rule']}: {c['value']} vs {c['limit']}")
    if result["grades"]:
        print("\n📈 作业分级 (GBZ/T 229, 路由判据=库里的类别字段):")
        for g in result["grades"]:
            lv = g.get("level")
            tag = f"{lv} {g.get('name')}" if lv else "未定级"
            print(f"  {g['factor']}: {tag} [{g.get('category') or '未归类'} → "
                  f"{g.get('part') or '无对应部分'}] (G={g.get('g', '-')}) {g.get('note', '')}")
    if result["ppe"]:
        print(f"\n🧤 PPE 建议 (GB 39800):")
        for p in result["ppe"]:
            items = ", ".join(f"{x['item']}({x['std']})" for x in p["ppe"])
            if items:
                print(f"  {p['factor']}: {items}")
    conn.close()


if __name__ == "__main__":
    main()
