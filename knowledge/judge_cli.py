"""判定引擎 — 检测值 → 符合性结论 (纯计算, 能计算的绝不生成)

依据: GBZ 2.1—2019 第6章 (化学/粉尘) + GBZ 2.2—2007 (物理因素)
规则 (全部编号引用, 判定只做数值计算):
  6.3.1  CME ≤ MAC                            (有MAC的物质)
  6.3.2  CTWA ≤ PC-TWA 且 CSTE ≤ PC-STEL      (双限值物质)
  6.3.3  仅PC-TWA: 瞬时>3xPC-TWA ≤15min/次, ≤4次/日, 间隔≥1h, 绝对≤5x  (PE)
  6.4    行动水平 (0.5×OEL) 触发控制措施
  6.5.1  接触水平5级: ≤1% | >1-10% | >10-50% | >50-100% | >100% OEL
  物理:  LEX,8h/LEX,40h ≤ 85 dB(A) (GBZ2.2-11.2.1); 峰值≤表10 140/130/120
         WBGT ≤ 表8矩阵(接触时间率×体力强度); 振动≤5 m/s²; 电场≤5 kV/m

输入: [检测记录] (factor/检测值/类型/接触信息)
输出: 每条记录判定结论 + 接触水平分级 + 超限倍数

用法: python3 -m knowledge.judge_cli
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from knowledge.oel import connect  # noqa: E402


def get_oel(conn, factor_name: str, oel_type: str | None = None) -> list[dict]:
    """查限值: factor_name + 可选 oel_type"""
    if oel_type:
        rows = conn.execute(
            "SELECT oel_type, value, unit, conditions, source_standard FROM oel_limit "
            "WHERE factor_name=? AND oel_type=?", (factor_name, oel_type)).fetchall()
    else:
        rows = conn.execute(
            "SELECT oel_type, value, unit, conditions, source_standard FROM oel_limit "
            "WHERE factor_name=?", (factor_name,)).fetchall()
    return [dict(zip(("oel_type", "value", "unit", "conditions", "source"), r)) for r in rows]


def to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("×10", "e").replace("⁰", "0"))
    except ValueError:
        return None


def level_of(ratio: float) -> dict:
    """接触水平分级 (6.5.1): ratio = 接触水平/OEL"""
    if ratio <= 0.01:
        return {"level": "0", "desc": "基本无接触", "control": "不需采取行动"}
    if ratio <= 0.10:
        return {"level": "Ⅰ", "desc": "接触极低", "control": "一般危害告知，如标签、SDS 等"}
    if ratio <= 0.50:
        return {"level": "Ⅱ", "desc": "有接触但无明显健康效应",
                "control": "一般危害告知，特殊危害告知"}
    if ratio <= 1.0:
        return {"level": "Ⅲ", "desc": "显著接触，需采取行动限制活动",
                "control": "一般/特殊危害告知、职业卫生监测、职业健康监护、作业管理"}
    return {"level": "Ⅳ", "desc": "超过OELs",
            "control": "一般/特殊危害告知、职业卫生监测、职业健康监护、作业管理、"
                       "个体防护用品和工程、工艺控制"}


def judge_chemical(conn, factor_name: str, ctwa: float | None, cste: float | None,
                   cme: float | None, peak: float | None = None) -> dict:
    """化学有害因素判定 (6.3.1/6.3.2/6.3.3) — 返回合规结论+分级"""
    # 数值参数统一转 float (提取的检测数据可能是字符串, 避免 str/float 运算报错)
    ctwa = to_float(ctwa); cste = to_float(cste); cme = to_float(cme); peak = to_float(peak)
    oels = get_oel(conn, factor_name)
    by_type = {r["oel_type"]: r for r in oels}
    result = {"factor": factor_name, "checks": [], "pass": True, "level": None}
    # MAC 判定 (6.3.1)
    if "MAC" in by_type and cme is not None:
        mac = to_float(by_type["MAC"]["value"])
        ok = cme <= mac
        result["checks"].append({
            "rule": "6.3.1 CME≤MAC", "value": cme, "limit": mac, "pass": ok,
            "ratio": cme / mac if mac else None,
        })
        result["pass"] &= ok
    # TWA/STEL 双限值 (6.3.2)
    if "PC-TWA" in by_type and ctwa is not None:
        twa = to_float(by_type["PC-TWA"]["value"])
        ok = ctwa <= twa
        result["checks"].append({
            "rule": "6.3.2 CTWA≤PC-TWA", "value": ctwa, "limit": twa, "pass": ok,
            "ratio": ctwa / twa if twa else None,
        })
        result["pass"] &= ok
        # 接触水平 (6.5.1, 用TWA比值)
        result["level"] = level_of(ctwa / twa) if twa else None
    if "PC-STEL" in by_type and cste is not None:
        stel = to_float(by_type["PC-STEL"]["value"])
        ok = cste <= stel
        result["checks"].append({
            "rule": "6.3.2 CSTE≤PC-STEL", "value": cste, "limit": stel, "pass": ok,
            "ratio": cste / stel if stel else None,
        })
        result["pass"] &= ok
    # 峰接触浓度 PE (6.3.3): 仅PC-TWA无PC-STEL
    if "PC-TWA" in by_type and "PC-STEL" not in by_type and peak is not None:
        twa = to_float(by_type["PC-TWA"]["value"])
        peak_ok = peak <= 3 * twa        # 3倍/15min
        abs_ok = peak <= 5 * twa         # 绝对5倍
        ok = peak_ok and abs_ok
        result["checks"].append({
            "rule": "6.3.3 PE峰接触(3x≤15min, 绝对5x)", "value": peak, "limit": f"3x={3*twa:.1f} 5x={5*twa:.1f}",
            "pass": ok, "peak_ok": peak_ok, "abs_ok": abs_ok,
        })
        result["pass"] &= ok
        if not result["level"] and twa:
            result["level"] = level_of(peak / twa)
    return result


def judge_physical(conn, factor_name: str, value: float, **cond) -> dict:
    """物理因素判定: 噪声/高温WBGT/振动/电场 — 用 conditions 精确匹配"""
    oels = get_oel(conn, factor_name)
    # 高温 WBGT: 需要接触时间率+体力劳动强度
    if factor_name == "高温":
        rate = cond.get("rate", "100%")        # 接触时间率
        labor = cond.get("labor", "Ⅰ")          # 体力劳动强度
        match = None
        for r in oels:
            c = r.get("conditions") or ""
            if c.startswith("接触时间率" + rate) and f"强度{labor}级" in c:
                match = r
                break
        if match:
            limit = to_float(match["value"])
            ok = value <= limit
            return {"factor": factor_name, "pass": ok,
                    "checks": [{"rule": f"GBZ2.2表8 WBGT({rate},{labor}级)", "value": value,
                                "limit": limit, "pass": ok, "ratio": value / limit}],
                    "level": level_of(value / limit)}
        return {"factor": factor_name, "pass": None, "checks": [],
                "error": f"无匹配WBGT条件 (rate={rate}, labor={labor})"}
    # 噪声: LEX,8h / LEX,40h
    if factor_name == "噪声":
        oel_type = cond.get("oel_type", "LEX,8h")
        match = next((r for r in oels if r["oel_type"] == oel_type), None)
        if match:
            limit = to_float(match["value"])
            ok = value <= limit
            return {"factor": factor_name, "pass": ok,
                    "checks": [{"rule": f"GBZ2.2 11.2.1 {oel_type}≤{limit}dB(A)",
                                "value": value, "limit": limit, "pass": ok,
                                "ratio": value / limit}],
                    "level": level_of(value / limit)}
    # 其他物理: 单值
    if len(oels) == 1 or oels:
        match = oels[0]
        limit = to_float(match["value"])
        if value is None:
            return {"factor": factor_name, "pass": None, "checks": [], "error": "无检测值"}
        ok = value <= limit
        return {"factor": factor_name, "pass": ok,
                "checks": [{"rule": f"{match['source']} {match['oel_type']}",
                            "value": value, "limit": limit, "pass": ok,
                            "ratio": value / limit}],
                "level": level_of(value / limit)}
    return {"factor": factor_name, "pass": None, "checks": [], "error": "无限值"}


def parse_limit_value(lim: str) -> list[dict]:
    """解析限值文本 → [(value, unit)]: '47 μmol/mol Cr（100 μg/g Cr）' 取主副两值"""
    out = []
    for m in re.finditer(r"([\d.]+)\s*([a-zA-Zμµ%·/\s\-]*[a-zA-Zμµ%])", lim):
        v = float(m.group(1))
        u = m.group(2).strip()
        if u and v > 0:
            out.append({"value": v, "unit": u})
    if not out:
        nums = re.findall(r"[\d.]+", lim)
        if nums:
            out.append({"value": float(nums[0]), "unit": ""})
    return out


def unit_match(value_unit: str, limit_units: list[str]) -> str | None:
    """单位归一: 检测单位 vs 限值单位, 宽松匹配 (μ/µ归一, 忽略空格/括号)"""
    if not value_unit:
        return None
    vu = value_unit.replace("μ", "µ").replace(" ", "").lower()
    for lu in limit_units:
        ln = lu.replace("μ", "µ").replace(" ", "").lower()
        if vu == ln or vu in ln or ln in vu:
            return lu
    return None


def judge_bio(conn, factor_name: str, indicator: str | None,
              value: float, unit: str = "") -> dict:
    """生物监测判定 (BEI): 检测值 vs 生物接触限值 (单位匹配, 防假超标)
    unit 缺省时按主值(第一数值)比较"""
    if indicator:
        rows = conn.execute(
            "SELECT indicator, limit_value, sample_time FROM bio_limit "
            "WHERE factor_name=? AND indicator=?", (factor_name, indicator)).fetchall()
    else:
        rows = conn.execute(
            "SELECT indicator, limit_value, sample_time FROM bio_limit "
            "WHERE factor_name=?", (factor_name,)).fetchall()
    if not rows:
        return {"factor": factor_name, "pass": None, "error": "无BEI记录"}
    out = {"factor": factor_name, "indicator": rows[0][0], "checks": [], "unmatched_units": []}
    used = False
    matched_checks = []
    for ind, lim, st in rows:
        parts = parse_limit_value(lim)
        if not parts:
            continue
        # 单位匹配: 选与检测单位一致的 limit 值; 无匹配则用主值
        target = None
        if unit:
            target = next(
                (p for p in parts if unit_match(unit, [p["unit"]])), None)
        if target is None:
            target = parts[0]
            if unit and len(parts) > 1:
                out["unmatched_units"].append(f"{ind}: 检测[{unit}] vs 限值[{lim}]")
                continue  # 单位不匹配的指标不参与判定 (防假超标)
        ok = value <= target["value"]
        used = True
        matched_checks.append({
            "rule": f"BEI {ind} (采样:{st or '-'})", "value": value,
            "unit": target["unit"], "limit_text": lim,
            "limit": target["value"], "pass": ok,
            "ratio": value / target["value"] if target["value"] else None,
        })
    # 单位已匹配的指标: 全部合格才合格 (任一超标即不合格, 保守安全语义)
    out["checks"] = matched_checks
    if matched_checks:
        out["pass"] = all(c["pass"] for c in matched_checks)
    else:
        out["pass"] = None
    return out


def action_level(conn, factor_name: str) -> dict:
    """行动水平 AL (3.9): 一般为该因素容许浓度的一半 → 触发控制措施 (6.4)"""
    oels = get_oel(conn, factor_name)
    for r in oels:
        if r["oel_type"] in ("PC-TWA", "MAC"):
            v = to_float(r["value"])
            if v:
                return {"factor": factor_name, "oel": v, "al": v * 0.5,
                        "source": r["source"], "note": "行动水平触发: 防尘防毒工程控制、监测、健康监护、告知、培训 (GBZ 2.1 6.4)"}
    return {"factor": factor_name, "oel": None, "al": None}


def mixed_ratio(conn, records: list[dict]) -> dict:
    """混合接触比值 (3.8): Σ(实际接触水平/限值) — 多因素联合接触
    比值>1 时即使单项合格也要采取控制措施"""
    total = 0.0
    parts = []
    for rec in records:
        factor = rec["factor"]
        value = rec.get("ctwa") or rec.get("value")
        oels = get_oel(conn, factor)
        twa = next((to_float(r["value"]) for r in oels if r["oel_type"] == "PC-TWA"), None)
        if twa and value:
            ratio = value / twa
            total += ratio
            parts.append({"factor": factor, "value": value, "twa": twa, "ratio": ratio})
    return {"total_ratio": total, "parts": parts,
            "pass": total <= 1.0 if parts else None,
            "note": "混合接触比值>1: 应采取控制措施 (GBZ 2.1 3.8)" if total > 1 and parts else ""}


def main():
    conn = connect()
    # 演示: 典型检测记录
    demos = [
        # 苯 (2019修改单: 3/6)
        {"factor": "苯", "ctwa": 2.1, "cste": 5.0, "cme": None},
        # 甲醛 (MAC 0.5)
        {"factor": "甲醛", "ctwa": None, "cste": None, "cme": 0.62},
        # 甲苯 (50/100)
        {"factor": "甲苯", "ctwa": 30, "cste": 95, "cme": None},
        # 噪声 (85)
        {"factor": "噪声", "value": 87.5, "oel_type": "LEX,8h"},
        # 高温 (2号8h, 强度Ⅱ级 28)
        {"factor": "高温", "value": 29.5, "rate": "100%", "labor": "Ⅱ"},
        # 生物监测: 苯(尿中苯巯基尿酸 47 μmol/mol Cr)
        {"bio": True, "factor": "苯", "value": 52.0, "unit": "μmol/mol Cr"},
        # 生物监测: 铅(血中铅 400 µg/L)
        {"bio": True, "factor": "铅及其化合物", "value": 430.0, "unit": "µg/L"},
    ]
    for d in demos:
        if d.get("bio"):
            r = judge_bio(conn, d["factor"], None, d["value"], d.get("unit", ""))
            print(f"\n🧪 生物监测[{d['factor']}]: {'✅合格' if r.get('pass') else '❌不合格'}")
            for c in r.get("checks", []):
                mark = "✅" if c["pass"] else "❌"
                print(f"   {mark} {c['rule']}: {c['value']} vs {c['limit']} (比值{c['ratio']:.2f})")
        elif d["factor"] in ("苯", "甲醛", "甲苯"):
            r = judge_chemical(conn, d["factor"], d.get("ctwa"), d.get("cste"), d.get("cme"))
            print(f"\n🔬 {d['factor']}: {'✅合格' if r['pass'] else '❌不合格'}"
                  f" 级别:{r['level']['level'] if r['level'] else '-'}")
            for c in r["checks"]:
                mark = "✅" if c["pass"] else "❌"
                print(f"   {mark} {c['rule']}: {c['value']} vs {c['limit']} (比值{c['ratio']:.2f})")
        else:
            kwargs = {k: v for k, v in d.items() if k not in ("factor", "value")}
            r = judge_physical(conn, d["factor"], d["value"], **kwargs)
            print(f"\n🔬 {d['factor']}: {'✅合格' if r.get('pass') else '❌不合格'}")
            for c in r.get("checks", []):
                mark = "✅" if c["pass"] else "❌"
                print(f"   {mark} {c['rule']}: {c['value']} vs {c['limit']} (比值{c['ratio']:.2f})")
    # 行动水平演示
    print("\n=== 行动水平 (GBZ 2.1 3.9/6.4) ===")
    for f in ("苯", "甲苯"):
        al = action_level(conn, f)
        print(f"   {f}: OEL={al['oel']} → 行动水平={al['al']} (超此值须采取控制措施)")
    # 混合接触演示 (3.8): 苯+甲苯 同时接触
    print("\n=== 混合接触比值 (GBZ 2.1 3.8) ===")
    mix = mixed_ratio(conn, [
        {"factor": "苯", "ctwa": 1.8},
        {"factor": "甲苯", "ctwa": 40.0},
    ])
    print(f"   Σ比值 = {mix['total_ratio']:.2f} ({'✅≤1 可控' if mix['pass'] else '❌>1 需控制'})")
    for p in mix["parts"]:
        print(f"     {p['factor']}: {p['value']}/{p['twa']} = {p['ratio']:.2f}")
    conn.close()


if __name__ == "__main__":
    main()
