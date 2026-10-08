"""表骨架构建器 — 数据处理阶段建表 (表=报告骨架, 先行沉淀)

原则 (用户定):
  1. 材料导入/字段更新时就建好全部表骨架, 数据全的填好, 缺的标"待补充"
  2. 导出时纯读取项目数据里沉淀的表, 不再现算 (section_filler 保留为回退)
  3. 分工: 规则表=纯规则抽取 (数字绝不给LLM); 标准表=标准库固定;
     增强表 (气象/地质/水文等公开区域资料) = LLM 获取 + 强约束

LLM 强约束 (气象类):
  - 输入: 仅地区名 (从 location 规则抽取, 如 "常熟" → 江苏省常熟市)
  - 要求: 只填公开气候常年值 (气温/风速/降水等区域统计), 每格可核;
    不确定就写"待补充", 禁止编造企业数据 (地质承载力/水文观测必须待补充)
  - 输出: 严格 JSON (解析失败=整表待补充, 不降级使用半截数据)
  - 结果缓存: 同一地区名只调一次 (data/llm_tables_cache.json)
"""
import json
import os
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web.number_provenance import prov_llm, prov_rule  # noqa: E402

TABLES_KEY = "built_tables"  # 存在 project.data 里

_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "llm_tables_cache.json"
_REGION_FACTS_PATH = Path(__file__).resolve().parent.parent / "data" / "region_facts.json"


def load_region_facts(region: str) -> dict:
    """材料提取的区域基础数据 (data/region_facts.json, 由 tools/extract_region_facts.py 生成)

    与 weather:region 缓存同 key 空间 (按地区名)。材料真数据优先于 LLM 公开资料:
    可研/地勘里明写的气温/风速/地震烈度/水文等, 直接带页码溯源写入本表。
    """
    try:
        d = json.loads(_REGION_FACTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    f = d.get(region) if isinstance(d, dict) else None
    return f if isinstance(f, dict) else {}

# LLM 环境复用 llm_draft 的配置 (BASE 默认走 workbuddy2api, 与 docker-compose 一致)
BASE = os.environ.get("LLM_BASE_URL", "https://workbuddy2api.henryai.top/v1/chat/completions")
MODEL = os.environ.get("LLM_MODEL", "deepseek-v4.1-flash")
KEY_ENV = os.environ.get("LLM_KEY_ENV", "DEEPSEEK_API_KEY")

_WEATHER_SYSTEM = (
    "你是区域气象资料整理员。输入: 中国某地区名。输出该地区公开气候资料的常年统计值 JSON。"
    "硬性规则: 1) 只填你能给出公开资料依据的常年平均值(气象站公开统计), 数值必须可信; "
    "2) 地质(承载力/地层)/水文(水位)/大气环境 属项目地勘/环评数据, 一律填 \"待补充\", 绝不编造; "
    "3) 任何一格没有把握就填 \"待补充\"; 4) 只输出 JSON, 不输出任何其他文字。"
)

_WEATHER_SCHEMA = {
    "climate": "气候类型(如 亚热带季风气候)",
    "temp_avg": "年平均气温℃", "temp_max_avg": "年平均最高气温℃",
    "temp_min_avg": "年平均最低气温℃", "temp_extreme_max": "极端最高气温℃",
    "temp_extreme_min": "极端最低气温℃",
    "wind_speed_avg": "年平均风速m/s", "wind_prevailing": "年主导风向",
    "rain_avg": "年平均降水量mm", "thunder_days": "年平均雷暴日数天",
    "humidity_avg": "年平均相对湿度%", "snow_max": "最大积雪深度mm",
    "geology": "地质(固定待补充)", "hydrology": "水文(固定待补充)",
    "air_quality": "大气环境(固定待补充)",
}


def extract_region(location: str, company: str = "") -> str:
    """从 location/company 规则抽取地区名 (区县级优先, 市级兜底; 不用LLM)
    "江苏省常熟经济开发区…常熟(市)有限公司" → "常熟市";
    "浙江省宁波市镇海区…" → "镇海区"; "上海市金山区" → "金山区";
    "山东潍坊汇胜股份有限公司" → "潍坊市" (公司名地缀)"""
    text = f"{location or ''} {company or ''}"
    # 1) 区县级: XX区/XX县 (上海市金山区 / 宁波市镇海区); 排除"工业区/开发区"尾缀
    m = re.search(r"(?:[\u4e00-\u9fa5]{2,4}(?:省|市))?([\u4e00-\u9fa5]{2,3}(?:区|县))(?!开发区)", text)
    if m and not any(k in m.group(1) for k in ("工业区", "矿区", "开发区")):
        return m.group(1)
    # 2) 直接的市级/县级名
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,4})(市|县)", text):
        name = m.group(0)
        if not any(k in name for k in ("开发区", "工业区", "新区", "园区")):
            return name
    # 3) "省/市+XX经济开发区" → 取行政区划前缀 (江苏省常熟经济开发区 → 常熟市)
    m = re.search(r"([\u4e00-\u9fa5]{2,4})(?:省|市)([\u4e00-\u9fa5]{2,4}?)(?:经济)?开发区", text)
    if m and m.group(2):
        return m.group(2) + "市"
    # 4) 公司名地缀 (山东潍坊汇胜股份 → 潍坊市)
    m = re.search(r"([\u4e00-\u9fa5]{2,4}(?:省|市))[\u4e00-\u9fa5]{0,6}?([\u4e00-\u9fa5]{2,4})", text)
    if m:
        return m.group(1) if m.group(1).endswith(("市", "县")) else m.group(1)
    # 5) 兜底: 省名
    m = re.search(r"([\u4e00-\u9fa5]{2,6}?(?:省|自治区))", text)
    return m.group(1) if m else ""


def _cache_load() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cache_save(region: str, data: dict):
    c = _cache_load()
    c[region] = data
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")


def _llm_json(prompt: str, system: str) -> dict | None:
    """LLM 调用 + JSON 严格解析 (失败返回 None, 调用方整表待补充)"""
    key = os.environ.get(KEY_ENV)
    if not key:
        for p in (Path.home() / ".hermes" / ".env", Path(__file__).resolve().parent.parent / ".env"):
            if p.exists():
                for ln in p.read_text(errors="ignore").splitlines():
                    if ln.startswith(KEY_ENV + "="):
                        key = ln.split("=", 1)[1].strip().strip('"')
                        break
            if key:
                break
    if not key:
        return None
    body = json.dumps({"model": MODEL, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0.1}).encode()
    req = urllib.request.Request(BASE, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            text = json.loads(r.read())["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else None
    except (OSError, ValueError, KeyError, IndexError):
        return None


def _fact(facts: dict, key: str):
    """材料提取的区域基础事实 → (值, 证据) ; 无 → ("", None)

    facts 形状: {field: {"value": "...", "file": "...", "page": N, "text": "原文片段"}}
    (由 tools/extract_region_facts.py 从可研等材料提取, 存 project.data.region_facts)
    """
    f = (facts or {}).get(key)
    if isinstance(f, dict):
        v = str(f.get("value") or "").strip()
        if v:
            ev = {k: f.get(k) for k in ("file", "page", "text")}
            return v, ev
    return "", None


def build_weather_table(region: str, cache_only: bool = False,
                        facts: dict | None = None) -> dict | None:
    """表3.1-1 所在地常年主要气象因素 — 材料事实 > LLM 公开资料 > 待补充 (强约束)

    cache_only=True: 只读缓存, 未命中返回 None (不联网) — 供导出/评估等
    同步路径做"缓存快补" (事故修复 2026-10: 库中气象表卡 filling 永久"待补充",
    而缓存里数据早已就位; 同步路径不能等 90s 联网, 故加纯缓存模式)。
    facts: 材料 (可研/地勘) 提取的区域基础数据 — 规则来源, 优先级最高;
    材料给了地质/水文等, 就不再写"待补充"(LLM 仍禁填这两类, 但材料可以)。
    磁盘 facts (data/region_facts.json) 自动合并, 显式传参优先。
    """
    facts = {**load_region_facts(region), **{k: v for k, v in (facts or {}).items() if isinstance(v, dict)}}
    if not region and not facts:
        return {"cols": ["序号", "项目", "情况和数据", "备注"],
                "rows": [[i, n, "待补充（需项目所在地气象资料）", "/"]
                         for i, n in enumerate(["气候", "地质", "地震", "气温", "风", "降水", "雷雨", "湿度", "水文", "大气环境", "积雪"], 1)]}
    cache = _cache_load()
    wx = cache.get(f"weather:{region}") if region else None
    if not wx and cache_only and not facts:
        return None  # 未命中且不联网 → 交给调用方占位/后台线程
    if not wx and not cache_only and region:
        schema_txt = "\n".join(f"  {k}: {v}" for k, v in _WEATHER_SCHEMA.items())
        wx = _llm_json(
            f"地区: {region}\n请按以下字段输出该地区公开气候常年值 JSON (值不确定就填 待补充):\n{schema_txt}",
            _WEATHER_SYSTEM) or {}
        if wx:
            _cache_save(f"weather:{region}", wx)
    wx = wx or {}

    def C(k: str):
        """字段取数: 材料事实 → LLM缓存 → 待补充; 返回 (value, evidence|None, from_material)"""
        v, e = _fact(facts, k)
        if v:
            return v, e, True
        wv = str(wx.get(k) or "").strip()
        if wv and "待补充" not in wv:
            return wv, None, False
        return "待补充", None, False

    rows: list = []
    prov = {"_default": prov_rule({"file": "build_weather_table 固定规则",
                                   "text": "代码固定值/待补充 (非LLM生成)"})}
    llm_ev = {"file": f"公开气候资料:{region}", "page": None,
              "text": f"地区 {region} 公开气候常年统计值 (LLM 获取, 需人工核实)"}
    _mat_fallback = {"file": "项目材料(区域基础数据)", "page": None, "text": ""}

    def _finish(idx: int, cells: list, used: list):
        """used: [(value, evidence, from_material), ...] → 行内来源归并 (材料>LLM>规则)"""
        rows.append(cells)
        _has = lambda v: "待补充" not in str(v)  # noqa: E731 (改写过的占位前缀也算缺)
        mat_ev = next((e for (v, e, m) in used if m and _has(v)), None)
        any_mat = any(m and _has(v) for (v, _, m) in used)
        any_cache = any((not m) and _has(v) for (v, _, m) in used)
        if any_mat:
            prov[f"{idx}_2"] = prov_rule(mat_ev or _mat_fallback, field="气象/区域基础数据")
        elif any_cache:
            prov[f"{idx}_2"] = prov_llm(llm_ev, field="气象常年值")

    # 1 气候
    v, e, m = C("climate")
    _finish(0, ["1", "气候", v, "/"], [(v, e, m)])
    # 2 地质 (LLM 禁填; 材料给了就用材料)
    v, e, m = C("geo")
    if v == "待补充":
        v, e, m = "待补充（以项目地勘报告为准）", None, False
    _finish(1, ["2", "地质", v, "/"], [(v, e, m)])
    # 3 地震 (材料给了烈度/加速度则直书; 否则沿用原规则句式)
    v, e, m = C("seismic")
    if v == "待补充":
        cl_v, _, _ = C("climate")
        v = "该地区地震烈度资料见当地抗震设防烈度区划" if cl_v != "待补充" else "待补充"
    _finish(2, ["3", "地震", v, ""], [(v, e, m)])
    # 4 气温
    used = []
    parts = []
    for label, k in (("平均气温", "temp_avg"), ("年平均最高", "temp_max_avg"),
                     ("年平均最低", "temp_min_avg"), ("极端最高", "temp_extreme_max"),
                     ("极端最低", "temp_extreme_min")):
        v, e, m = C(k)
        used.append((v, e, m))
        parts.append(f"{label} {v}")
    _finish(3, ["4", "气温", "；".join(parts), ""], used)
    # 5 风
    used = []
    parts = []
    for label, k in (("年平均风速", "wind_speed_avg"), ("年主导风向", "wind_prevailing")):
        v, e, m = C(k)
        used.append((v, e, m))
        parts.append(f"{label} {v}")
    _finish(4, ["5", "风", "；".join(parts), ""], used)
    # 6 降水
    v, e, m = C("rain_avg")
    _finish(5, ["6", "降水", f"年平均降水量 {v}", ""], [(v, e, m)])
    # 7 雷雨
    v, e, m = C("thunder_days")
    _finish(6, ["7", "雷雨", f"该地区年平均雷暴日数为 {v}", ""], [(v, e, m)])
    # 8 湿度
    v, e, m = C("humidity_avg")
    _finish(7, ["8", "湿度", f"年平均相对湿度 {v}", ""], [(v, e, m)])
    # 9 水文 (LLM 禁填; 材料给了就用材料)
    v, e, m = C("hydrology")
    if v == "待补充":
        v, e, m = "待补充（以当地水文部门资料为准）", None, False
    _finish(8, ["9", "水文", v, ""], [(v, e, m)])
    # 10 大气环境 (LLM 禁填; 材料给了就用材料)
    v, e, m = C("air")
    if v == "待补充":
        v, e, m = "待补充（以项目环评资料为准）", None, False
    _finish(9, ["10", "大气环境", v, ""], [(v, e, m)])
    # 11 积雪
    v, e, m = C("snow_max")
    _finish(10, ["11", "积雪", f"往年最大积雪量 {v}", ""], [(v, e, m)])

    any_data = any("待补充" not in r[2] for r in rows)
    note = f"区域基础数据来源: {region or '项目材料'}" if any_data else ""
    return {"cols": ["序号", "项目", "情况和数据", "备注"], "rows": rows, "prov": prov,
            "note": note, "region": region}


if __name__ == "__main__":
    r = extract_region("江苏省常熟经济开发区沿江工业区兴港路 15 号长兴合成树脂(常熟)有限公司现有厂区内")
    print("region:", r)
    t = build_weather_table(r)
    for row in (t or {}).get("rows") or []:
        print(row)


def build_data_tables(pd: dict) -> dict:
    """数据规则表 (数字全走规则, 不经LLM) — 与 fill_section 同源逻辑, 建表时沉淀

    ⚠ 同源维护: 各表构建逻辑与 web/section_filler.py sec3(产品/原辅材料/设备明细/建构筑物/定员)
    及 sec4(类比危害分布) 逐字同源; 改任一侧表结构必须两处同步 (否则导出 built 覆盖后与回退现算漂移)。
    """
    tables = {}
    # 班制定员表 (表3.1-2): staffing+shifts
    staffs = pd.get("staffing") or []
    sf = pd.get("shifts") or []
    if staffs:
        _SHIFTS_PER = (("四班三运转", 4), ("四班", 4), ("三班两倒", 3), ("三班", 3), ("两班", 2), ("二班", 2))

        def _per_shift(sysm, cnt):
            try:
                n = int(str(cnt).replace("人", ""))
            except (ValueError, TypeError):
                return cnt
            for kw, k in _SHIFTS_PER:
                if kw in str(sysm):
                    return n // k or n
            return n

        rows = []
        for s in staffs:
            sysm = next((x.get("system", "") for x in sf if x.get("post") == s.get("post")), "—")
            rows.append([s.get("post", ""), s.get("dept", ""), "—",
                         _per_shift(sysm, s.get("count", "")), sysm, s.get("count", ""), "/"])
        try:
            tot = sum(int(str(s.get("count", "0")).replace("人", "")) for s in staffs
                      if str(s.get("count", "0")).strip().isdigit())
            rows.append(["合计", "/", "/", "/", "/", str(tot), "/"])
        except (ValueError, TypeError):
            pass
        tables["班制定员表"] = {"cols": ["工种", "工作区域", "工作内容", "人/班", "生产班制", "总人数（人）", "最大班女工数（人）"],
                          "rows": rows}
    # 主要经济技术指标 (表3.1-3 项目概况表) —— ⚠ 单一产地原则:
    #   该表唯一产地 = section_filler (读 data/tech_econ.json 可研表1.2-11 全 15 大项),
    #   此处曾有一份 4 行硬编码副本 → 与 section_filler 各产一份 → 骨架/现算不一致
    #   (同"设备表两份定义"事故)。2026-10 删除本副本, 只留 section_filler 一处。

    # ===== 原辅材料表 (表3.4-2) ← materials — 同源 section_filler sec3 =====
    mats2 = pd.get("materials") or []
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
        tables["原辅材料表"] = {
            "cols": ["序号", "原辅材料名称", "物料性状", "年耗量(t/a)", "存放地点", "最大储存量（t）", "包装方式/规格"],
            "rows": m_rows}

    # ===== 产品产量表 (表3.4-1, 9列双层) ← products — 同源 section_filler sec3 =====
    prods = pd.get("products") or []
    if prods:
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
            tables["产品产量表"] = {
                "cols": ["序号", "名称", "名称", "单位", "主要成分", "年产量吨/年", "年产量吨/年", "变化量", "备注"],
                "header2": ["序号", "名称", "名称", "单位", "主要成分", "扩产前", "扩产后", "变化量", "备注"],
                "merge_rect": [(0, 1, 0, 2), (0, 5, 0, 6)],
                "rows": [[i] + r for i, r in enumerate(p2_rows, 1)]}

    # ===== 设备明细表 (8列, 复刻真稿表·设备一览) ← equipment_detail — 同源 section_filler sec3 =====
    # 真稿 8 列: 序号|设备名称|规格|材质|数量/台|操作条件|内部物料|备注
    # 口径: 本项目设备 (可研「主要设备一览表」提取, 172 台/5 分组)。
    #   真稿为全厂改扩建口径(302 行/12 分组, 含 R27&R28/32线/38线/51线/钠盐炉 等产线),
    #   本项目材料内 0 处提及 → 不硬凑真稿行数, 如实按本项目口径出表, 差异在正文说明。
    #   分组行: {_group: name} → 整行仅名称列有值 (与真稿 316 行表的分组行同构)
    eq_d = pd.get("equipment_detail") or []
    if not eq_d:
        eq_d = [{"name": e} if isinstance(e, str) else e
                for e in (pd.get("equipment") or [])]
        eq_d = [x for x in eq_d if isinstance(x, dict)]
    if eq_d:
        ed8 = []
        for d in eq_d:
            if not isinstance(d, dict):
                continue
            if d.get("_group"):
                ed8.append([d["_group"]] + [""] * 7)   # 分组行: 名称在第 1 列(与 section_filler 同构)
                continue
            # 字段名以提取层为准: 操作条件 / 内部物料 / 备注 (整列直取, 不重组)
            ed8.append([d.get("no", ""), d.get("name", ""), d.get("spec", ""),
                        d.get("材质", "") or d.get("mat", ""),
                        d.get("qty", ""),
                        d.get("操作条件", "") or d.get("op", ""),
                        d.get("内部物料", "") or d.get("media", ""),
                        d.get("备注", "") or d.get("chg", "")])
        tables["设备明细表"] = {
            "cols": ["序号", "设备名称", "规格型号", "材质", "数量/台",
                     "操作条件", "内部物料", "备注"],
            "rows": ed8}

    # ===== 建构筑物表 (表3.7-1) ← buildings — 同源 section_filler sec3 =====
    blds = pd.get("buildings") or []
    if blds:
        b_rows = [[b.get("功能区", ""), b.get("name", ""), b.get("火灾危险类别", ""),
                   b.get("耐火等级", ""), b.get("floors", ""), b.get("area", ""),
                   b.get("floor_area", ""), ""] for b in blds if isinstance(b, dict)]
        # 原报告表3.7-1: 功能区/建构筑物名称/火灾危险类别/耐火等级/层数/占地/建筑/备注 (无序号无高度)
        tables["建构筑物表"] = {
            "cols": ["功能区", "建构筑物名称", "火灾危险类别", "耐火等级", "层数", "占地面积(㎡)", "建筑面积(㎡)", "备注"],
            "rows": b_rows}

    # ===== 类比危害分布表 (表4.2-3, 8列双层) ← hazard_grid — 同源 section_filler sec4 =====
    hg = pd.get("hazard_grid") or []
    hg_rows = []
    for g in hg:
        if not g.get("unit") and not g.get("post"):
            continue
        # 原报告表4.2-3 8列双层: 单元|岗位|接触途径(产品·工段)|因素|作业方式|人数|频次
        hg_rows.append([g.get("unit") or "生产车间主厂房", g.get("post", "—"),
                        g.get("product", "—"), g.get("stage") or "—",
                        (g.get("factors", "") or "—")[:80],
                        "计量、投料、巡检", "/", "/"])
    if hg_rows:
        tables["类比危害分布表"] = {
            "cols": ["评价单元", "岗位", "接触途径", "接触途径", "主要职业病危害因素", "作业方式", "接触人数", "接触频次"],
            "header2": ["评价单元", "岗位", "产品", "工段", "主要职业病危害因素", "作业方式", "接触人数", "接触频次"],
            "merge_rect": [(0, 2, 0, 3)],
            "rows": hg_rows}

    # ===== 体检项目与周期表 (表11.5-1) =====
    # ⚠ 表的唯一产地是 web/health_exam_map.py:build_health_exam_rows (由 section_filler
    #   第11章调用产出, 走导出链路)。此处**不再重复建**, 避免「同一表两份定义」走样
    #   (设备表曾因 table_builder + section_filler 双份定义而不一致)。
    #   若 build_all_tables 需要沉淀骨架, 直接复用同一函数源:
    from web.health_exam_map import build_health_exam_rows as _bhe
    hc_rows = _bhe(pd)
    if hc_rows:
        tables["体检项目与周期表"] = {
            "cols": ["职业病危害因素", "体检类别", "岗前职业健康检查",
                     "岗中职业健康检查", "健康检查周期", "离岗职业健康检查"],
            "rows": hc_rows}

    # 数字溯源: 各表数字都来自 project.data 提取字段 (非 LLM 在"建表"环节生成)。
    # ⚠ 但"建表不用 LLM" ≠ "数字可信": 上游提取器可能本身是 LLM 拼出来的
    #   (用户实证: investment=5000 就是 LLM 拼的)。故逐个字段声明可信度:
    #   - TRACEABLE: 直接从材料规则/正则取出, 能指回材料 (materials/equipment_detail/
    #     buildings/staffing/hazard_grid/products 等, 走 pdfplumber/正则/表单)
    #   - 其余 (如从自由文本 summarise 出来的 investment) → traceable=False,
    #     标 untraced, 审计判 unverified, 逼人工核实出处
    _RULE_EV = {"file": "材料提取字段 (规则/正则)", "page": None,
                "text": "该表数字来自 project.data 提取字段, 非 LLM 生成"}
    # 可为 None: 上游自由文本聚合, 无法在材料里指到确定出处 → 不许盖 rule 章
    _UNTRACED_FIELDS = {"investment"}
    _UNTRACED_EV = {"file": "project.data.investment", "page": None, "text": ""}
    for _tname, _t in tables.items():
        if not isinstance(_t, dict):
            continue
        # 项目概况表 唯一产地 = section_filler (见上方说明); 此处只给其余表盖规则来源
        if _tname != "项目概况表":
            _t.setdefault("prov", prov_rule(_RULE_EV))
    return tables


def build_all_tables(pd: dict, use_llm: bool = True) -> dict:
    """建全表骨架 (导入时机调用): 规则表 + LLM增强表 → 存 project.data[built_tables]
    地区来源: 企业画像 pd["profile"]["region"] (数据处理阶段已沉淀); 无画像才现场抽取"""
    out = {}
    out.update(build_data_tables(pd))
    profile = pd.get("profile") or {}
    region = str(profile.get("region") or "")
    if not region:
        from web.company_profile import build_profile
        profile = build_profile(pd)
        region = str(profile.get("region") or "")
    out["气象因素表"] = build_weather_table(region) if use_llm else build_weather_table("")
    return out
