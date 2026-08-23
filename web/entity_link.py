"""实体链接模块 — 让 hazard_factor/oel_limit/health_effect/occupational_disease 匹配上
    解决: ①名字匹配不上(矽尘带括号) ②化学/物理因素分开

关键: CAS号是物质唯一标识(最可靠实体键), 名字做兜底模糊匹配。
     物理因素(噪声/高温/振动/工频/)不走"化学中毒"链, 用物理职业性损害(中暑/噪声聋)。
"""
from __future__ import annotations


def cas_key(cas) -> str:
    """标准化CAS号 (去括号/横线/空格, 统一大写)"""
    if not cas:
        return ""
    return str(cas).strip().replace(" ", "").replace("[", "").replace("]", "").upper()


def name_norm(name) -> str:
    """标准化物质名: 去括号限定(矽尘(游离SiO2含量≥10%)→矽尘), 去空格, 统一"""
    import re
    n = (name or "").strip()
    # 去结尾括号限定 (如"矽尘(游离SiO2含量≥10%)" → "矽尘")
    n = re.sub(r"（[^）]*）$", "", n)
    n = re.sub(r"\([^)]*\)$", "", n)
    n = n.replace(" ", "").replace("　", "").strip()
    return n


def is_physical(name) -> bool:
    """物理因素判断 (噪声/高温/振动/工频电场/紫外/微波/激光...不走化学中毒链)"""
    n = name or ""
    phys = ["噪声", "高温", "振动", "工频", "紫外", "微波", "激光", "红外",
            "射频", "噪声聋", "WBGT", "低气压", "高气压", "局部振动"]
    return any(p in n for p in phys)


def link_oel(conn, name: str) -> list:
    """危害因素 → oel_limit 限值 (优先CAS, 次名字归一化)"""
    if is_physical(name):
        return []  # 物理因素不走化学接触限值链(噪声用噪声卫生限值, 高温用WBGT)
    # 找 hazard_factor 拿 cas
    row = conn.execute("SELECT cas FROM hazard_factor WHERE name=? OR name LIKE ?",
                       (name, name + "%")).fetchone()
    cas = cas_key(row[0] if row else "")
    rows = []
    if cas:
        rows = conn.execute("SELECT oel_type, value, unit FROM oel_limit WHERE cas=?", (cas,)).fetchall()
    if not rows:
        n = name_norm(name)
        rows = conn.execute("SELECT oel_type, value, unit FROM oel_limit WHERE factor_name LIKE ?",
                            (n + "%",)).fetchall()
    return [{"type": r[0], "value": r[1], "unit": r[2]} for r in rows[:4]]


def link_health_effect(conn, name: str) -> list:
    """危害因素 → 健康影响 (先CAS后名字)"""
    row = conn.execute("SELECT cas FROM hazard_factor WHERE name=?", (name,)).fetchone()
    cas = cas_key(row[0] if row else "")
    if cas:
        rows = conn.execute("SELECT effect, route FROM health_effect WHERE cas=?", (cas,)).fetchall()
    else:
        n = name_norm(name)
        rows = conn.execute("SELECT effect, route FROM health_effect WHERE factor LIKE ?",
                            (n + "%",)).fetchall()
    return [{"effect": r[0], "route": r[1]} for r in rows[:3]]


def link_disease(conn, name: str, category_label: str = "") -> list:
    """危害因素 → 职业病 (化学中毒链 vs 物理所致职业病分开)"""
    if is_physical(name):
        # 物理因素 → 物理因素所致职业病 (噪声→噪声聋, 高温→中暑)
        phys_map = {"噪声": "职业性噪声聋", "高温": "中暑", "振动": "手臂振动病"}
        for k, v in phys_map.items():
            if k in name:
                return [v]
        return []
    # 化学链: 用名字关键词匹配职业性化学中毒 (按疾病名包含因素关键词)
    n = name_norm(name) or name
    ds = conn.execute("SELECT name FROM occupational_disease WHERE category LIKE '%化学中毒%'").fetchall()
    # 匹配: 病名含因素, 但单字物质(苯/氨)需病名以它开头(氨中毒)或独立成词(避免'氨'误配'苯的氨基')
    hit = []
    for d in ds:
        dn = d[0]
        if n in dn:
            if len(n) >= 2:
                hit.append(dn)  # 多字: 子串匹配 (甲苯→甲苯中毒/二甲苯中毒可接受)
            elif dn.startswith(n) or n + "中毒" == dn:
                hit.append(dn)  # 单字: 病名以它开头(氨中毒)或独立(苯中毒)
    return hit[:2]


def analyze_chain(conn, name: str) -> dict:
    """单个危害因素的链条完整度 (化学/物理分开判)"""
    oel = link_oel(conn, name) if not is_physical(name) else []
    he = link_health_effect(conn, name)
    od = link_disease(conn, name)
    return {"factor": name, "physical": is_physical(name),
            "has_oel": bool(oel), "has_health": bool(he), "has_disease": bool(od),
            "oel": oel, "health": he, "disease": od,
            "complete": bool((oel or is_physical(name)) and he and od)}


def scan_chain(conn) -> dict:
    """扫描全部危害因素的链条完整度"""
    from collections import Counter
    names = [r[0] for r in conn.execute("SELECT DISTINCT name FROM hazard_factor")]
    stats = Counter()
    broken = []
    for n in names:
        a = analyze_chain(conn, n)
        stats["total"] += 1
        if a["physical"]:
            stats["physical"] += 1
            # 物理因素: 需有健康影响+物理职业病
            if a["has_health"] and a["has_disease"]:
                stats["phys_complete"] += 1
            else:
                broken.append({"name": n, "type": "physical", "health": a["has_health"],
                               "disease": a["has_disease"]})
        else:
            if a["complete"]:
                stats["chem_complete"] += 1
            else:
                broken.append({"name": n, "type": "chemical", "oel": a["has_oel"],
                               "health": a["has_health"], "disease": a["has_disease"]})
    return {"stats": dict(stats), "broken": broken}
