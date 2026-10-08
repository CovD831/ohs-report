#!/usr/bin/env python3
"""从项目材料提取「区域基础事实」→ data/region_facts.json (供表3.1-1 气象因素表等消费)

背景 (2026-10):
  ada 项目 3.1.3 气象表 14 处"待补充", 而可研 p97-98 明写:
  平均气温15.5℃ / 年均降雨量1062.3mm / 主导风ESE / 年平均风速2.8m/s /
  抗震设防烈度VII度 设计基本加速度0.10g / 长江常熟段水文。数据在材料里, 提取层没接。

原则 (与项目和用户红线一致):
  - **规则提取, 不用 LLM**: 正则在可研文本层定位数值; 每条带 file/page/text 溯源
  - 提取不到 → 不写 (下游仍显"待补充", 诚实标注)
  - 写入 data/region_facts.json: {region: {field: {value, file, page, text}}}
  - 地区名 key 与 llm_tables_cache 的 weather:region 同空间 (build_weather_table 消费)

字段与气象表行对应:
  climate 气候 | geo 地质 | seismic 抗震/地震 | temp_avg 平均气温 | temp_extreme_max 极端最高
  temp_extreme_min 极端最低 | wind_speed_avg 平均风速 | wind_prevailing 主导风
  rain_avg 年降雨量 | thunder_days 雷暴日 | humidity_avg 湿度 | hydrology 水文 | air 大气环境
  snow_max 积雪 (气候极端最高等可留空, 表内逐项显示)

用法:
  python tools/extract_region_facts.py <pid>          # 单项目: 扫材料 → 落盘 json
  python tools/extract_region_facts.py --all          # 全部有材料目录的项目
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

FACTS_PATH = ROOT / "data" / "region_facts.json"
MAT_ROOT = ROOT / "data" / "materials"

# ── 规则: field → [(正则, 值清洗)] ; group(1) = 值 ─────────────────────────
_N = r"([0-9]+(?:\.[0-9]+)?)"
RULES: dict[str, list[tuple[str, str]]] = {
    "temp_avg": [
        (rf"年平均气温\s*{_N}\s*℃", "℃"),
        (rf"年均气温\s*{_N}\s*℃", "℃"),
    ],
    "temp_extreme_max": [
        (rf"极端最高气温\s*{_N}\s*℃", "℃"),
    ],
    "temp_extreme_min": [
        (rf"最低气温零下\s*{_N}\s*℃", "℃(零下)"),
        (rf"极端最低气温\s*零下\s*{_N}\s*℃", "℃(零下)"),
        (rf"极端最低气温\s*{_N}\s*℃", "℃"),
    ],
    "wind_speed_avg": [
        (rf"(?:历年)?平均风速(?:为|约)?\s*{_N}\s*m/s", "m/s"),
    ],
    "wind_prevailing": [
        (r"主导风(?:向)?(?:为|：|:)?\s*([A-Z]{2,3}|[东西南北]{1,2}风?)", ""),
    ],
    "rain_avg": [
        (rf"年均(?:平均)?降雨量\s*{_N}\s*mm", "mm"),
        (rf"年平均降水量\s*{_N}\s*mm", "mm"),
    ],
    "thunder_days": [
        (rf"(?:年平均)?雷暴日数?\s*(?:为|约)?\s*{_N}\s*[天d]", "天"),
    ],
    "humidity_avg": [
        (rf"年平均相对湿度\s*{_N}\s*%", "%"),
    ],
    "seismic": [
        (r"抗震设防烈度为\s*([IVX]+)\s*度[，,、\s]*设计基本加速度为\s*([0-9.]+)g", "烈度+加速度"),
        (r"抗震设防烈度为\s*([IVX]+)\s*度", "烈度"),
    ],
    "climate": [
        (r"(?:地处|位于|属)\s*([\u4e00-\u9fa5]{2,10}气候区?)", ""),
    ],
    "hydrology": [
        (r"(长江[\u4e00-\u9fa5]{2,10}段(?:距离|距)[^。]{5,160}。)", ""),
        (r"(历年平均高潮位[^。]{5,120}。)", ""),
    ],
    "geo": [
        (r"(根据地质资料显示[，,][^。]{20,300}。)", ""),
        (r"(自上而下分[\u4e00-\u9fa5]{1,4}层[^。]{20,260}。)", ""),
    ],
}

# 图/表标题等非正文行的排除 (太短无信息量)
_MIN_LEN = 8


def _clean_val(v: str, unit: str) -> str:
    if unit == "℃(零下)":
        return f"-{v}℃"
    v = re.sub(r"\s+", " ", v).strip()
    if unit in ("℃", "mm", "m/s", "%", "天") and not v.endswith(unit):
        return f"{v}{unit}"
    return v


def _scan_pdf(path: Path) -> list[tuple[int, str]]:
    """PDF → [(页码(1基), 页文本)]"""
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # type: ignore
    out = []
    try:
        doc = pymupdf.open(str(path))
    except Exception:
        return out
    for i in range(doc.page_count):
        try:
            out.append((i + 1, doc[i].get_text()))
        except Exception:
            continue
    doc.close()
    return out


def _scan_docx(path: Path) -> list[tuple[int, str]]:
    try:
        import docx as _docx
        d = _docx.Document(str(path))
    except Exception:
        return []
    return [(0, "\n".join(p.text for p in d.paragraphs))]


def _scan_text(path: Path) -> list[tuple[int, str]]:
    try:
        return [(0, path.read_text(encoding="utf-8", errors="ignore"))]
    except Exception:
        return []


def extract_for_project(pid: str) -> dict:
    """扫项目材料目录, 返回 {field: {value, file, page, text}}"""
    pdir = MAT_ROOT / pid
    if not pdir.is_dir():
        pdir = MAT_ROOT / _resolve_full_pid(pid)
    files: list[Path] = []
    if pdir.is_dir():
        for p in sorted(pdir.rglob("*")):
            if p.is_file() and p.suffix.lower() in (".pdf", ".docx", ".txt", ".md", ".csv"):
                files.append(p)
    facts: dict[str, dict] = {}
    for f in files:
        if f.suffix.lower() == ".pdf":
            pages = _scan_pdf(f)
        elif f.suffix.lower() == ".docx":
            pages = _scan_docx(f)
        else:
            pages = _scan_text(f)
        for pno, text in pages:
            if not text or len(text) < 20:
                continue
            for field, rules in RULES.items():
                if field in facts:
                    continue
                for rx, unit in rules:
                    m = re.search(rx, text)
                    if not m:
                        continue
                    if unit == "烈度+加速度" and m.lastindex and m.lastindex >= 2:
                        val = f"抗震设防烈度{m.group(1)}度，设计基本加速度{m.group(2)}g"
                    else:
                        val = _clean_val(m.group(1), unit)
                    if len(val) < 2 or len(val) > 200:
                        continue
                    # 证据片段: 匹配处前后文
                    s = max(0, m.start() - 40)
                    frag = re.sub(r"\s+", " ", text[s:m.end() + 40]).strip()
                    facts[field] = {
                        "value": val,
                        "file": f.name,
                        "page": pno or None,
                        "text": frag[:160],
                    }
                    break
    return facts


def _resolve_full_pid(prefix: str) -> str:
    """短 id (8位) → 全 id (目录名一般=全 id)"""
    for p in MAT_ROOT.iterdir():
        if p.is_dir() and p.name.startswith(prefix):
            return p.name
    return prefix


def _region_of_pid(pid: str) -> str:
    """从项目库取地区名 (企业画像 region → location 规则抽取兜底)"""
    import sqlite3
    from web.table_builder import extract_region
    try:
        c = sqlite3.connect(str(ROOT / "data" / "ohs.db"))
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT data FROM project WHERE id=?", (pid,)).fetchone()
        c.close()
        if not row:
            return ""
        d = json.loads(row["data"] or "{}")
        prof = d.get("profile") or {}
        r = str(prof.get("region") or "").strip()
        if r:
            return r
        return extract_region(str(d.get("location") or ""), str(d.get("company") or ""))
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pid", nargs="?", help="项目 id (前缀)")
    ap.add_argument("--all", action="store_true", help="全部有材料目录的项目")
    ap.add_argument("--apply", action="store_true", help="写入 data/region_facts.json (默认只预演)")
    args = ap.parse_args()

    pids: list[str] = []
    if args.all:
        pids = [p.name for p in sorted(MAT_ROOT.iterdir()) if p.is_dir()]
    elif args.pid:
        pids = [_resolve_full_pid(args.pid)]
    else:
        ap.error("需要 pid 或 --all")

    store: dict = {}
    if FACTS_PATH.exists():
        try:
            store = json.loads(FACTS_PATH.read_text(encoding="utf-8"))
        except ValueError:
            store = {}

    n_new = 0
    for pid in pids:
        facts = extract_for_project(pid)
        if not facts:
            print(f"[{pid}] 无提取 (材料缺失或未命中规则)")
            continue
        region = _region_of_pid(pid)
        if not region:
            print(f"[{pid}] 提取 {len(facts)} 项, 但无地区名 → 无法挂 key (跳过)")
            continue
        merged = {**(store.get(region) or {}), **facts}   # 新材料优先覆盖同字段
        if merged != (store.get(region) or {}):
            store[region] = merged
            n_new += 1
        print(f"[{pid}] region={region} 提取 {len(facts)} 项:")
        for k, v in facts.items():
            print(f"    {k:18} = {v['value'][:60]}  ({v['file']} p{v['page']})")

    if args.apply and n_new:
        FACTS_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n✅ 已写入 {FACTS_PATH} ({n_new} 个地区更新)")
    elif n_new:
        print(f"\n(预演) {n_new} 个地区待写入 — 加 --apply 落盘")
    else:
        print("\n无新增")


if __name__ == "__main__":
    main()
