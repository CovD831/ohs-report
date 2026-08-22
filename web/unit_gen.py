"""三级单元生成器 — 数据驱动的生成单元 (非固定prompt)

原理:
  一级/二级 = 报告框架 (固定骨架)
  三级     = 内容单元 (标题因项目而异, 本模块从数据提取+模板循环)
  单元类型 (4种通用模板):
    物质型: 毒理学 (health_effect 357条: 性质/路由/健康效应/致癌性)
    工序型: 工序→物料→危害→措施 (process_text + 标准条款)
    岗位型: 岗位→接触→PPE (A2d + GB 39800.1)
    设备型: 设备→危害环节→措施 (equipment + 条款)
  标题=数据单元名 (自动, 不预设) — 任何项目都对
"""
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _conn():
    return connect()


def extract_units(project: dict, assess: dict) -> list[dict]:
    """从项目数据提取三级单元 (四类)"""
    from knowledge.oel import connect
    conn = connect()
    units = []
    hazards = assess.get("hazards", [])

    # 1) 物质型 (危害因素 → 毒理学)
    for h in hazards[:12]:
        f = h["factor"]
        eff = conn.execute("SELECT effect, note, source FROM health_effect WHERE factor=? LIMIT 1",
                           (f,)).fetchone()
        if not eff:
            # 别名匹配
            eff = None
            for a in conn.execute("SELECT factor FROM health_effect WHERE factor LIKE ?", (f[:2] + "%",)):
                if a[0] and a[0][:2] == f[:2]:
                    eff = conn.execute("SELECT effect, note, source FROM health_effect WHERE factor=? LIMIT 1",
                                       (a[0],)).fetchone()
                    break
        if eff:
            units.append({"type": "物质", "title": f,
                          "text": f"**{f}**：{eff[0] or '—'}。{('（' + str(eff[1]) + '）') if eff[1] and eff[1] != '―' else ''} [来源: {eff[2]}]"})
        else:
            units.append({"type": "物质", "title": f, "text": f"**{f}**：健康效应见GBZ 2.1—2019表1（本库未收录，待核验）。"})

    # 2) 工序型 (process_text 按'- 车间'行)
    proc_units = []
    for ln in (project.get("process_text") or "").splitlines():
        m = re.match(r"- 车间\s*(\S+)\s*\|\s*工序\s*(\S+)\s*\|\s*密闭\s*(\S+)\s*\|\s*危害\s*(.+)$", ln.strip())
        if m:
            hz_str = m.group(4).strip()[:40]
            if hz_str == "密闭":
                hz_str = "见危害识别表"
            proc_units.append({"type": "工序", "title": f"{m.group(1)}·{m.group(2)}",
                               "text": f"**工序**：{m.group(1)}/{m.group(2)}。密闭情况：{m.group(3)}。"
                                       f"产生危害：{hz_str}。"
                                       f"拟采取密闭化、局部排风等措施（GBZ/T 194—2007 4.2.2/3.8）。"})
    units += proc_units[:15]

    # 3) 岗位型 (A2d)
    f_d = Path(__file__).resolve().parent.parent / "data" / "materials" / "A2d_劳动定员表.csv"
    if f_d.exists():
        import csv
        with open(f_d, encoding="utf-8-sig") as fh:
            for r in list(csv.DictReader(fh))[:12]:
                ws, gz, n, content = r.get("车间", ""), r.get("工种", ""), r.get("人数", ""), r.get("工作内容", "")
                units.append({"type": "岗位", "title": f"{ws}·{gz}",
                              "text": f"**{ws}·{gz}岗位**（{n}人，{content}）。接触{'; '.join(h['factor'] for h in hazards[:3]) or '—'}，"
                                      f"配备个体防护用品（GB 39800.1—2020）。"})

    # 4) 设备型 (equipment 取主要)
    for e in project.get("equipment", [])[:12]:
        name = str(e).split("|")[0] if "|" in str(e) else str(e)
        if not name or name in ("未命名",):
            continue
        units.append({"type": "设备", "title": name,
                      "text": f"**{name}**：为主要产毒/噪声/粉尘设备。产生环节：运行、检修、取样。"
                              f"拟采取密闭化（GBZ/T 194 4.2.2）、局部排风（GBZ/T 194 3.8）、"
                              f"噪声隔声（GB/T 50087—2013）、湿式除尘（GBZ 1—2010 7.1.2）。"})

    conn.close()
    return units


def build_units(sec: str, sub: str, project: dict, assess: dict) -> list[dict]:
    """按 二级小节 → 三级单元 (数据驱动)"""
    units = extract_units(project, assess)
    # 小节过滤: 结果按小节类型倾向排序 (如 2.1 偏好工序/物质; 3.1 偏好设备/工序)
    sub_filter = {
        "2.1": ("工序", "物质"), "2.2": ("工序", "物质"),
        "3.1": ("设备", "工序"), "3.2": ("岗位",), "3.3": ("工序",),
        "4.3": ("设备",), "4.5": ("岗位",), "8.4": ("工序", "设备"),
        "8.5": ("物质",), "1.2": ("设备",), "5.5": ("物质",),
        "9.4": ("物质",),
    }
    want = sub_filter.get(sub, ("工序", "物质", "岗位", "设备"))
    return [u for u in units if u["type"] in want]


def llm_material_text(factor: str, project: dict, assess: dict) -> str:
    """物质单元 LLM 深度描述 (完整毒理学段, 数据来自知识库+材料)"""
    from knowledge.oel import connect
    conn = connect()
    # 知识库数据: 毒理/英文/CAS/限值
    eff = conn.execute("SELECT factor, english, cas, effect, note, source FROM health_effect WHERE factor=? LIMIT 1",
                       (factor,)).fetchone()
    lims = conn.execute("SELECT oel_type, value, unit FROM oel_limit WHERE factor_name=? LIMIT 4",
                        (factor,)).fetchall()
    conn.close()
    # prompt
    info = f"""【物质】{factor}
【英文/CAS】{eff[1] if eff else '—'} / {eff[2] if eff else '—'}
【毒理学特征】{eff[3] if eff else '—'}
【标注】(皮)/(敏)/致癌 等: {eff[4] if eff else '—'}
【职业接触限值】{'; '.join(f'{l[0]}={l[1]}{l[2]}' for l in lims) or '查GBZ 2.1'}
【来源】{eff[5] if eff else 'GBZ 2.1—2019 表1'}"""
    prompt = f"""你是职业卫生评价专家。撰写职业病危害因素健康影响段（预评价报告 10.1.1.x 级内容）。

{info}

要求:
1. 理化性质简述（颜色气味状态）
2. 接触途径（吸入/皮肤/经口）
3. 健康效应（急性/慢性/致癌/致敏, 从毒理学特征展开）
4. 职业病（对应GBZ职业病目录, 如职业性中毒/皮炎）
5. 防护要点（密闭化+局部排风+个体防护）
6. 正式报告语言, 150-300字, 只写标准可信内容"""
    from web.llm_draft import _llm
    return _llm(prompt)


# 物质单元深度生成状态 (内存缓存: factor → text)
_MAT_LLM_CACHE: dict[str, str] = {}


def material_deep(factor: str, project: dict, assess: dict) -> str:
    """物质单元深度文本 (LLM, 缓存)"""
    if factor in _MAT_LLM_CACHE:
        return _MAT_LLM_CACHE[factor]
    try:
        t = llm_material_text(factor, project, assess)
    except Exception as e:
        t = f"（LLM生成失败: {str(e)[:30]}）"
    _MAT_LLM_CACHE[factor] = t
    return t


if __name__ == "__main__":
    from knowledge.project_assess import assess_project
    from knowledge.oel import connect
    conn = connect()
    r = assess_project(conn, {"name": "t", "industry": "261",
                              "equipment": ["酯化釜|丙酮", "纯化槽|甲苯"],
                              "detections": [], "process_text": "- 车间 生产厂房（二） | 工序 特殊单体 | 密闭 密闭 | 危害 密闭"})
    units = extract_units({"name": "t", "equipment": ["酯化釜|丙酮"], "process_text": ""}, r)
    print(f"提取 {len(units)} 个单元:")
    for u in units[:8]:
        print(f"  [{u['type']}] {u['title']}: {u['text'][:60]}")
    conn.close()
