"""企业画像 (company profile) — 数据处理阶段的结构化聚合层

两层职责 (用户定的原则):
  ① 数据对应: 材料/原始字段 → 项目数据键 (import_materials_from_dir, 已有)
  ② 企业画像: 项目数据键 → 结构化 profile (本模块) — 公司名/所在地(省市区拆解)/
     投资/产能/行业等聚合规范化, 供建表、成文等下游直接取字段

原则:
  - 画像在数据处理时沉淀 (import/update 时机), 下游只读不解析
  - 地址拆解=纯规则 (省/市/区县/详细地址), 不用LLM
  - 交叉验证: location 与 company 两处地名互相印证, 不一致记入 issues
  - 数值规范化: 投资"5000万"→{value:5000, unit:万元}, 产能"12000吨/年"→{value:12000, unit:吨/年}
"""
import re


# 直辖市 (市=省级, 区即区县)
_MUNICIPALITIES = ("北京", "上海", "天津", "重庆")

_ADDR_EXCLUDE = ("开发区", "工业区", "高新区", "园区", "新区", "矿区")
_PROVINCE_SHORT = {"山东", "江苏", "浙江", "广东", "河南", "河北", "湖南", "湖北", "四川", "安徽", "福建",
                   "江西", "辽宁", "吉林", "黑龙江", "山西", "陕西", "甘肃", "青海", "海南", "台湾", "广西", "云南",
                   "贵州", "宁夏", "内蒙", "西藏", "新疆"}
_ADDR_EXCLUDE_TAILS = ("开发区", "工业区", "园区", "厂区", "矿区", "港区", "库区", "灌区", "灾区", "城区", "郊区", "新区")


def _parse_address(raw: str, company: str = "") -> dict:
    """地址字符串 → {province, city, district, detail} (纯规则)
    候选顺序: 省/直辖市 → 区县(剔除非政区尾) → 市级 → 经济开发区前缀
    常熟 case: 江苏省常熟经济开发区…(常熟)有限公司 → province=江苏省, city=常熟市 (开发区规则)"""
    text = str(raw or "").strip()
    out = {"province": "", "city": "", "district": "", "detail": ""}
    if not text:
        return out
    rest = text
    # 省/自治区
    m = re.search(r"([\u4e00-\u9fa5]{2,8}?(?:省|自治区))", rest)
    if m:
        out["province"] = m.group(1)
        rest = rest[m.end():]
    # 直辖市 (上海市金山区 → province=上海市)
    if not out["province"]:
        for mu in _MUNICIPALITIES:
            if rest.startswith(mu):
                out["province"] = mu + "市"
                rest = rest[len(mu) + 1:] if rest[len(mu):].startswith("市") else rest[len(mu):]
                break
    # 区县: 名字尾段不得是开发区/工业区等非政区尾 (候选逐个试, 全部是伪区县才落到下条规则)
    for m in re.finditer(r"(?:[\u4e00-\u9fa5]{2,4}(?:省|市))?([\u4e00-\u9fa5]{2,3}(?:区|县))", rest):
        name = m.group(1)
        if any(name[-2:] in t or name in t for t in _ADDR_EXCLUDE_TAILS):
            continue
        out["district"] = name
        mc = re.search(r"([\u4e00-\u9fa5]{2,4}市)", rest[:m.start()])
        if mc:
            out["city"] = mc.group(1)
        out["detail"] = (rest[:m.start()] + rest[m.end():]).lstrip("，,、 ").strip()
        return out
    # 市级 (XX市/XX县; 剔除开发区类)
    for mc in re.finditer(r"([\u4e00-\u9fa5]{2,4})(市|县)", rest):
        name = mc.group(0)
        if not any(k in name for k in _ADDR_EXCLUDE):
            out["city"] = name
            out["detail"] = (rest[:mc.start()] + rest[mc.end():]).lstrip("，,、 ").strip()
            return out
    # "省/市+XX经济开发区" → XX市 (江苏省常熟经济开发区 → 常熟市)
    m = re.search(r"([\u4e00-\u9fa5]{2,4})(?:省|市)([\u4e00-\u9fa5]{2,4}?)(?:经济)?开发区", rest)
    if m and m.group(2):
        out["city"] = m.group(2) + "市"
        out["detail"] = rest
        return out
    # 省已剥且开头即 "XX经济开发区" → XX市 (常熟经济开发区 → 常熟市)
    m = re.match(r"([\u4e00-\u9fa5]{2,4}?)(?:经济)?开发区", rest)
    if m and m.group(1) and not any(k in m.group(1) for k in _ADDR_EXCLUDE):
        out["city"] = m.group(1) + "市"
        out["detail"] = rest
        return out
    out["detail"] = rest
    return out


def _company_region(company: str) -> str:
    """公司名里的地名 (长兴合成树脂(常熟)有限公司 → 常熟; 山东潍坊汇胜股份 → 潍坊) — 交叉验证用"""
    text = str(company or "")
    m = re.search(r"[（(]([\u4e00-\u9fa5]{2,4})[)）]", text)
    if m:
        return m.group(1)
    # 省名+城市名+字号 (山东潍坊汇胜股份 → 潍坊)
    m = re.search(r"([\u4e00-\u9fa5]{2,4}(?:省|市))([\u4e00-\u9fa5]{2,3})(?=股份|有限|集团|科技|实业|控股)", text)
    if m:
        return m.group(2) if not m.group(1).endswith("市") else ""
    # 省简称开头 (山东/江苏/广东…): 跳过省简称取其后2-3字 (山东潍坊汇胜股份 → 潍坊)
    m = re.match(r"([\u4e00-\u9fa5]{2})([\u4e00-\u9fa5]{2,3})(?=汇胜|股份|有限|集团|科技|实业|控股)", text)
    if m and m.group(1) in _PROVINCE_SHORT:
        return m.group(2)
    # 无省名: 城市名+字号直连 (潍坊汇胜股份 → 潍坊)
    m = re.match(r"([\u4e00-\u9fa5]{2,3})(?=汇胜|股份|有限|集团|科技|实业|控股)", text)
    return m.group(1) if m else ""


def _parse_amount(raw: str) -> dict:
    """'5000万' / '5000万元' / '12000吨/年' → {value, unit, raw} (数字规则, 不经LLM)"""
    text = str(raw or "").strip()
    if not text:
        return {"value": None, "unit": "", "raw": ""}
    m = re.search(r"([\d.]+)\s*(亿|万|千|百)?\s*(元|吨/年|吨|人|平方米|m2|㎡|kw|KW)?", text)
    if not m or not m.group(1):
        return {"value": None, "unit": "", "raw": text}
    val = float(m.group(1))
    scale = {"亿": 100000000, "万": 10000, "千": 1000, "百": 100}.get(m.group(2) or "", 1)
    val *= scale
    unit = (m.group(3) or "").replace("m2", "平方米").replace("㎡", "平方米")
    if unit == "元":
        unit = {"亿": "亿元", "万": "万元"}.get(m.group(2) or "", "元")
    elif not unit and m.group(2) == "亿":
        unit = "亿元"
    elif not unit and m.group(2) == "万":
        unit = "万元"  # "5000万" → 万元 (投资/资金语境默认口径)
    val = round(val, 4)
    return {"value": int(val) if val == int(val) else val, "unit": unit, "raw": text}


def build_profile(pd: dict) -> dict:
    """项目数据 → 企业画像 (提取时调用, 存 pd["profile"])"""
    location = str(pd.get("location") or "")
    company = str(pd.get("company") or "")
    addr = _parse_address(location, company)
    # region: 区县优先, 市兜底, 省再兜底 (气象等地域查询口径)
    region = addr["district"] or addr["city"] or addr["province"]
    # 交叉验证: 公司名地名 vs 地址地名
    issues = []
    creg = _company_region(company)
    if creg and region:
        # 一致 (常熟 ∈ 常熟市) 不告警; 不一致才告警
        if creg not in region and region not in creg:
            issues.append(f"company地名({creg})与location地名({region})不一致, 需人工确认")
    elif creg and not region:
        region = creg
        issues.append("location无行政区划, region取自公司名")
    inv = _parse_amount(pd.get("investment") or "")
    cap = _parse_amount(pd.get("capacity") or "")
    rc = _parse_amount(pd.get("registered_capital") or "")
    return {
        "company": company or str(pd.get("name") or ""),
        "address": {**addr, "raw": location},
        "region": region,
        "investment": inv,
        "capacity": cap,
        "registered_capital": rc,
        "nature": str(pd.get("nature") or ""),
        "industry": str(pd.get("industry") or ""),
        "legal_rep": str(pd.get("legal_rep") or ""),
        "investor": str(pd.get("investor") or ""),
        "founded": str(pd.get("founded") or ""),
        "issues": issues,
    }


if __name__ == "__main__":
    cases = [
        {"location": "江苏省常熟经济开发区沿江工业区兴港路 15 号长兴合成树脂(常熟)有限公司现有厂区内",
         "company": "长兴合成树脂(常熟)有限公司", "investment": "5000万", "capacity": "12000吨/年"},
        {"location": "浙江省宁波市镇海区某路1号", "company": "宁波XX化工有限公司", "investment": "2.3亿元"},
        {"location": "上海市金山区", "company": "", "investment": ""},
        {"location": "", "company": "山东潍坊汇胜股份有限公司", "investment": ""},
    ]
    for pd in cases:
        p = build_profile(pd)
        print(p["address"]["province"], "|", p["address"]["city"], "|", p["address"]["district"],
              "→ region:", p["region"], "| 投:", p["investment"], "| issues:", p["issues"])
