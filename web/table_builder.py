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

TABLES_KEY = "built_tables"  # 存在 project.data 里

_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "llm_tables_cache.json"

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


def build_weather_table(region: str) -> dict:
    """表3.1-1 所在地常年主要气象因素 — LLM 公开资料 + 强约束 (缺→待补充)"""
    if not region:
        return {"cols": ["序号", "项目", "情况和数据", "备注"],
                "rows": [[i, n, "待补充（需项目所在地气象资料）", "/"]
                         for i, n in enumerate(["气候", "地质", "地震", "气温", "风", "降水", "雷雨", "湿度", "水文", "大气环境", "积雪"], 1)]}
    cache = _cache_load()
    wx = cache.get(f"weather:{region}")
    if not wx:
        schema_txt = "\n".join(f"  {k}: {v}" for k, v in _WEATHER_SCHEMA.items())
        wx = _llm_json(
            f"地区: {region}\n请按以下字段输出该地区公开气候常年值 JSON (值不确定就填 待补充):\n{schema_txt}",
            _WEATHER_SYSTEM) or {}
        if wx:
            _cache_save(f"weather:{region}", wx)
    # 地质/水文/大气环境永远待补充 (项目地勘/环评数据, LLM 禁填)
    W = lambda k: str(wx.get(k) or "待补充")  # noqa: E731
    rows = [
        ["1", "气候", W("climate"), "/"],
        ["2", "地质", "待补充（以项目地勘报告为准）", "/"],
        ["3", "地震", W("climate") != "待补充" and "该地区地震烈度资料见当地抗震设防烈度区划" or "待补充", ""],
        ["4", "气温", f"平均气温 {W('temp_avg')}；年平均最高 {W('temp_max_avg')}；年平均最低 {W('temp_min_avg')}；"
                     f"极端最高 {W('temp_extreme_max')}；极端最低 {W('temp_extreme_min')}", ""],
        ["5", "风", f"年平均风速 {W('wind_speed_avg')}；年主导风向 {W('wind_prevailing')}", ""],
        ["6", "降水", f"年平均降水量 {W('rain_avg')}", ""],
        ["7", "雷雨", f"该地区年平均雷暴日数为 {W('thunder_days')}", ""],
        ["8", "湿度", f"年平均相对湿度 {W('humidity_avg')}", ""],
        ["9", "水文", "待补充（以当地水文部门资料为准）", ""],
        ["10", "大气环境", "待补充（以项目环评资料为准）", ""],
        ["11", "积雪", f"往年最大积雪量 {W('snow_max')}", ""],
    ]
    return {"cols": ["序号", "项目", "情况和数据", "备注"], "rows": rows,
            "note": f"气象数据来源: {region}公开气候资料" if any("待补充" not in r[2] for r in rows) else ""}


if __name__ == "__main__":
    r = extract_region("江苏省常熟经济开发区沿江工业区兴港路 15 号长兴合成树脂(常熟)有限公司现有厂区内")
    print("region:", r)
    t = build_weather_table(r)
    for row in t["rows"]:
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
    # 主要经济技术指标 (表3.1-3)
    invest = pd.get("investment") or ""
    if invest or pd.get("capacity"):
        tables["项目概况表"] = {"cols": ["序号", "项目名称", "单位", "指标", "备注"],
                          "rows": [
                              ["1", "厂区总占地面积", "平方米", "依托现有", ""],
                              ["2", "新建建筑面积", "平方米", "依托现有", ""],
                              ["3", "项目投资总额", "万元", invest or "—", ""],
                              ["4", "职业病防治经费概算", "万元", "待补充（需企业核实）", ""],
                          ]}

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

    # ===== 设备明细表 (表3.6-1, 9列双层) ← equipment_detail — 同源 section_filler sec3 =====
    eq_d = pd.get("equipment_detail") or []
    if eq_d:
        ed9 = [[d.get("车间", ""), d.get("位号", ""),
                d.get("name", "") if isinstance(d, dict) else d,
                d.get("spec", ""), d.get("qty", ""), d.get("qty", ""), "—",
                d.get("材质", ""), ""] for d in eq_d if isinstance(d, dict)]
        tables["设备明细表"] = {
            "cols": ["车间", "位号", "设备名称", "规格型号", "数量", "数量", "数量", "材质", "备注"],
            "header2": ["车间", "位号", "设备名称", "规格型号", "现有", "扩建后全厂", "变化", "材质", "备注"],
            "merge_rect": [(0, 4, 0, 6)],
            "rows": ed9}

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
