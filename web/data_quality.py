"""数据质检 (data quality gate) — 报告生成前的最后一道检查

定位: 导入/更新后自动跑, 结果沉淀 data["quality_report"], 导出前可查.
三层:
  ① 类别覆盖   材料 11 类缺什么 (复用 uploads.coverage_report)
  ② 字段完整度 必填字段缺什么 (复用 project_schema.validate_project)
  ③ 脏数据检测 提取值本身的问题 (本模块新增, 证据驱动 — 每条规则对应实测脏样本)

级别: error  会产出错误内容 (如人数列非数字, 定员表算不出人/班)
      warn   可疑, 建议人工核实 (如 location 解析不出行政区划)
      info   口径提示 (如检测值前缀'<'=低于检出限半定量)
"""
import re


def _dirty_checks(pd: dict) -> list:
    """③ 脏数据检测 — 规则逐条对提取值本身挑毛病"""
    items = []

    # -- staffing: count 必须可转数字 (3.1-2 定员表 人/班=总数÷班次, '8人' 会让算术崩/错)
    bad_rows, total = [], 0
    for s in (pd.get("staffing") or []):
        if not isinstance(s, dict):
            continue
        total += 1
        c = str(s.get("count") or "").strip()
        if c and not re.fullmatch(r"\d+(?:\.\d+)?", c):
            bad_rows.append(f"{s.get('post', '?')}={c[:12]}")
    if bad_rows:
        items.append({"level": "error", "field": "staffing",
                      "message": f"定员表 {len(bad_rows)}/{total} 行人数含非数字字符, 人/班无法计算",
                      "sample": "、".join(bad_rows[:5])})

    # -- staffing: 总人数为 0 → 定员表/全员栈无处下手
    if total and not any(str(s.get("count") or "").strip().isdigit() for s in pd.get("staffing") or []):
        items.append({"level": "error", "field": "staffing",
                      "message": "定员表无任何有效数字人数", "sample": ""})

    # -- detections: ctwa/cste 检测值形态 (数字 / '<x' 低于检出限 / 其他=脏)
    bad_det, lt_det, ndet = [], 0, 0
    for dtc in (pd.get("detections") or []):
        if not isinstance(dtc, dict):
            continue
        ndet += 1
        v = str(dtc.get("ctwa") or "").strip()
        if not v:
            continue
        if v.startswith("<") or v.startswith("≤"):
            lt_det += 1
        elif not re.fullmatch(r"\d+(?:\.\d+)?", v):
            bad_det.append(f"{dtc.get('factor', '?')}={v[:10]}")
    if bad_det:
        items.append({"level": "error", "field": "detections",
                      "message": f"检测数据 {len(bad_det)}/{ndet} 条 CTWA 非数字且非'<检出限'形态",
                      "sample": "、".join(bad_det[:5])})
    if lt_det:
        items.append({"level": "info", "field": "detections",
                      "message": f"{lt_det}/{ndet} 条检测值带'<'前缀(低于检出限, 半定量), 成文时须表述为'低于检出限'不得当实测值",
                      "sample": ""})

    # -- ppe: item 组合文本 ('/'分隔多装备 → 拟配置矩阵列对不齐的根因)
    combo = [str(p.get("item") or "") for p in (pd.get("ppe") or [])
             if isinstance(p, dict) and "/" in str(p.get("item") or "")
             and len(str(p.get("item"))) > 12]  # 安全帽/工作帽 这类短并列合法; 长串才是拼接残留
    if combo:
        items.append({"level": "warn", "field": "ppe",
                      "message": f"PPE 清单 {len(combo)} 条装备名含'/'组合文本, 拟配置表按单装备分列时需拆分",
                      "sample": "、".join(combo[:3])})

    # -- ppe: count 形态 ('1顶/人' 合法; 纯数字无单位=可疑)
    bad_ppe_cnt = [str(p.get("count") or "") for p in (pd.get("ppe") or [])
                   if isinstance(p, dict) and str(p.get("count") or "").strip()
                   and not re.search(r"\d", str(p.get("count")))]
    if bad_ppe_cnt:
        items.append({"level": "warn", "field": "ppe",
                      "message": f"PPE {len(bad_ppe_cnt)} 条数量无数值", "sample": "、".join(bad_ppe_cnt[:3])})

    # -- 画像: region 抽不出 → 气象表/所在地表述全部待补充
    profile = pd.get("profile") or {}
    if not profile:
        items.append({"level": "warn", "field": "profile",
                      "message": "企业画像未沉淀 (旧数据或导入失败), 地域类表回退现场解析", "sample": ""})
    else:
        if not profile.get("region"):
            items.append({"level": "warn", "field": "profile",
                          "message": "画像未解析出所在地行政区划, 气象/所在地表述将为待补充", "sample": ""})
        for iss in profile.get("issues") or []:
            items.append({"level": "warn", "field": "profile", "message": f"画像交叉验证: {iss}", "sample": ""})

    # -- investment: 有数字无单位口径 (万元? 亿元?) — 3.1-3 经济表直接引用
    inv = pd.get("investment") or ""
    m = re.search(r"[\d.]+", str(inv))
    if m and not re.search(r"万|亿|元", str(inv)):
        items.append({"level": "info", "field": "investment",
                      "message": f"投资额'{str(inv)[:15]}'无单位口径, 引用时保持原文不推算", "sample": ""})

    # -- equipment: 名称空行/纯数字行 (设备清单错位)
    bad_eq = [str(e)[:14] for e in (pd.get("equipment") or [])[:200]
              if not str(e or "").strip() or str(e).strip().isdigit()]
    if bad_eq:
        items.append({"level": "warn", "field": "equipment",
                      "message": f"设备清单 {len(bad_eq)} 条空行/纯数字行, 清单错位需核对原表", "sample": "、".join(bad_eq[:3])})

    # -- location 空 → 所在地/周边/选址 3 张表全部待补充
    if not str(pd.get("location") or "").strip():
        items.append({"level": "error", "field": "location",
                      "message": "建设地点为空: 3.2 选址/周边环境/所在地气象 无法成文", "sample": ""})

    return items


def quality_check(pid: str, pd: dict) -> dict:
    """完整质检: ①类别覆盖 ②字段完整度 ③脏数据 → {summary, items, coverage, fields}"""
    out: dict = {"items": []}
    # ①② 复用已有 (失败不阻断)
    try:
        from web.uploads import coverage_report
        cov = coverage_report(pid, project=pd)
        out["coverage"] = {"completeness": cov.get("completeness"),
                           "core_missing": cov.get("core_missing")}
        for m in cov.get("core_missing") or []:
            out["items"].append({"level": "warn", "field": "coverage",
                                 "message": f"缺核心资料[{m['cat']} {m['name']}]: {m.get('why', '')}", "sample": ""})
        out["fields"] = {"completeness": (cov.get("field_completeness") if "field_completeness" in cov
                                          else (cov.get("fields") or {}).get("completeness")),
                         "required_missing": cov.get("field_missing") or []}
        for f in (out["fields"]["required_missing"] or [])[:8]:
            out["items"].append({"level": "warn", "field": f.get("field", ""),
                                 "message": f"必填字段缺: {f.get('desc') or f.get('field')} (来源: {f.get('source', '')})", "sample": ""})
    except Exception:
        pass
    # ③ 脏数据
    out["items"].extend(_dirty_checks(pd))
    # 汇总 + 稳定排序 error > warn > info
    rank = {"error": 0, "warn": 1, "info": 2}
    out["items"].sort(key=lambda x: rank.get(x.get("level"), 3))
    out["summary"] = {lv: sum(1 for i in out["items"] if i.get("level") == lv)
                      for lv in ("error", "warn", "info")}
    out["ok"] = out["summary"]["error"] == 0
    return out


if __name__ == "__main__":
    import json
    d = json.load(open("/tmp/proj_full.json"))
    r = quality_check("local", d)
    print("summary:", r["summary"])
    for it in r["items"]:
        print(f"  [{it['level']}] {it['field']}: {it['message']}"
              + (f" | 例: {it['sample']}" if it["sample"] else ""))
