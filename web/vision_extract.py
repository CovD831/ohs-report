"""扫描件视觉提取 — 扫描版检测报告 → 结构化检测数据

背景 (2026-09 实测):
  真实材料里 **21 个 PDF 是纯扫描件** (无文本层), 关键的是:
   05_类比检测报告 = 125页/94页, 0 字符, 只有图
  而检测数据 (含作业分级必需的"游离二氧化硅含量") 全在里面
  → pdfplumber/正则完全取不到 (项目原本无 OCR), 字段静默丢失

方案选择 (实测对比, 见 tools/ 里的对比脚本结论):
  tesseract OCR : 2.2s/页, 免费, 但**数字识别不可靠**
                  (实测 5mg/m³→"Smg/ma", 整行乱码; 且"自信的错"无置信度提示)
  视觉模型      : 6.9s/页, ≈¥0.035/页, 精度显著更好
                  (表格列对齐, 会主动说"本页无数据"而不编造)
  → 选视觉模型。⚠ 但结果标 source="vision" (非 rule), 提示人工核对。

设计 (省 token + 可追溯):
  两遍法: ① 索引遍 (150dpi, 只问"本页检测项目是什么")  ② 精读遍 (180dpi, 只读命中页)
  实测索引遍 30 页 ≈ 150s / ¥0.6; 避免 125 页无脑全量。

用法:
  python -m web.vision_extract <pdf> --index        # 只出索引
  python -m web.vision_extract <pdf> --extract      # 索引+精读关键页 → JSON
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = os.environ.get("LLM_BASE_URL",
                      "https://workbuddy2api.henryai.top/v1/chat/completions")
MODEL = os.environ.get("LLM_MODEL", "deepseek-v4.1-flash")
KEY_ENV = os.environ.get("LLM_KEY_ENV", "DEEPSEEK_API_KEY")

# 索引遍: 极简提问 (省 token)
_INDEX_ASK = ("本页是职业病危害检测报告的一页。用一行回答: "
              "①本页检测项目(危害因素名, 若无写'无') ②是否含'游离二氧化硅'百分比数值(有/无)。"
              "格式: `检测项目=X; 二氧化硅=有/无`。不要输出其它内容。")

# 精读遍: 结构化转录
# ⚠ 实测: 检测报告里至少有两种表型, 不能假设单一 shape
#   (a) 逐样结果表: 检测项目 | 采样点 | 接触时间 | 结果1/2/3 | C_TWA | C_STEL | 判定
#   (b) 岗位汇总表: 车间/岗位 | 工作地点 | 测试点数 | 危害因素×2 | 检测方式×2 |
#                  检测结果×2(如 3/1 = 合格点数/总点数) | 佩戴(PPE)
#   硬套 (a) 的字段去读 (b) 会得到一堆空值 → 先让模型自报表型, 再按型取数。
_EXTRACT_ASK = (
    "这是中国职业病危害检测报告的扫描页。请先判断表格类型, 再精确转录为 JSON。\n"
    "表型A=逐样结果表(有 C_TWA/C_STEL 浓度值); "
    "表型B=岗位汇总表(危害因素/检测方式/检测结果 如 '3/1', 无浓度); "
    "表型D=游离二氧化硅测定表(列: 车间/采样岗位|采样点|粉尘种类|检测结果(%)|判定); "
    "表型C=其他(无检测数据, 如封面/目录/纯说明页)。\n"
    "只输出 JSON:\n"
    '{"table_type":"A|B|C|D",'
    '"items":[{"factor":"危害因素名或检测项目","sampling_point":"采样点或岗位+工作地点",'
    '"exposure_hours":"接触时间h/d","results":["检测值..."],'
    '"ctwa":"C_TWA(仅A)","cstel":"C_STEL/C_PE(仅A)",'
    '"pass_ratio":"如3/1(仅B)","dust_type":"粉尘种类(仅D)",'
    '"sio2_percent":"游离二氧化硅检测结果%(仅D)",'
    '"judgement":"符合/不符合/判定(有则填)"}]}\n'
    "硬性规则: ①数字与单位逐字准确, 不推测不补全 ②本页字段没有的留空字符串 "
    "③页面只有封面/目录/说明且无任何检测数值时才用 C ④'<1.7'这类未检出值原样保留 "
    "⑤合并单元格的车间/岗位要补全到行上 ⑥表型D 必填 factor='游离二氧化硅'"
)


def _load_key() -> str:
    k = os.environ.get(KEY_ENV, "")
    if k:
        return k
    for p in (Path.home() / ".hermes" / ".env",
              Path(__file__).resolve().parent.parent / ".env"):
        if p.exists():
            for ln in p.read_text(errors="ignore").splitlines():
                if ln.startswith(KEY_ENV + "="):
                    return ln.split("=", 1)[1].strip().strip('"')
    return ""


def _is_scanned(pdf_path: Path, sample: int = 4) -> tuple[bool, int]:
    """判定是否扫描件: 抽样页无文本层但有图。返回 (是否扫描件, 总页数)"""
    try:
        import pymupdf
    except ImportError:
        return False, 0
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception:
        return False, 0
    n = doc.page_count
    chars = 0
    for i in range(min(sample, n)):
        chars += len(doc[i].get_text() or "")
    doc.close()
    return (chars <= 5 and n > 0), n


def _render(pdf_path: Path, pno: int, dpi: int) -> bytes:
    """渲染某页 → PNG bytes"""
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    pix = doc[pno].get_pixmap(dpi=dpi)
    data = pix.tobytes("png")
    doc.close()
    return data


def _ask(png: bytes, prompt: str, max_tokens: int = 900, retries: int = 2) -> tuple[str, float]:
    """调视觉模型。返回 (文本, credit)。"""
    import urllib.request
    key = _load_key()
    if not key:
        raise RuntimeError(f"{KEY_ENV} 未配置")
    b64 = base64.b64encode(png).decode()
    body = json.dumps({
        "model": MODEL,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}],
    }).encode()
    last = None
    for a in range(retries + 1):
        try:
            req = urllib.request.Request(
                BASE, data=body, headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read())
            return (d["choices"][0]["message"]["content"] or "",
                    float((d.get("usage") or {}).get("credit") or 0))
        except Exception as e:
            last = e
            if a < retries:
                time.sleep(2 ** (a + 1))
    raise RuntimeError(f"视觉模型调用失败: {type(last).__name__}: {last}")


def build_index(pdf_path: Path, dpi: int = 150, verbose: bool = True,
                limit: int | None = None) -> dict:
    """索引遍: 逐页问"检测项目 + 是否含 SiO2"

    返回 {pages:[{page, factor, has_sio2, raw}], credit, elapsed}
    """
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    n = doc.page_count
    doc.close()
    upto = min(n, limit) if limit else n
    pages, credit, t0 = [], 0.0, time.time()
    for pno in range(upto):
        png = _render(pdf_path, pno, dpi)
        try:
            txt, c = _ask(png, _INDEX_ASK, max_tokens=80)
        except Exception as e:
            txt, c = f"ERR {type(e).__name__}", 0.0
        credit += c
        factor = ""
        m = re.search(r"检测项目\s*=\s*([^;；\n]+)", txt)
        if m:
            factor = m.group(1).strip()
        has_sio2 = bool(re.search(r"二氧化硅\s*=\s*有", txt))
        pages.append({"page": pno + 1, "factor": factor,
                      "has_sio2": has_sio2, "raw": txt.strip()})
        if verbose:
            mark = " ★SiO2" if has_sio2 else ""
            print(f"  p{pno+1:3} | {factor[:60]}{mark}")
    return {"pages": pages, "credit": credit, "elapsed": time.time() - t0,
            "total_pages": n, "scanned_pages": upto}


def _parse_json(txt: str) -> dict:
    """容错解析模型返回的 JSON (可能带 markdown 围栏)"""
    t = txt.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return {"items": []}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"items": []}


def extract_pages(pdf_path: Path, page_nos: list[int], dpi: int = 180,
                  verbose: bool = True) -> dict:
    """精读遍: 只读指定页 → 结构化 items (含 table_type)"""
    items, credit, t0 = [], 0.0, time.time()
    types: dict[int, str] = {}
    for pno in page_nos:                     # pno 为 1-based
        png = _render(pdf_path, pno - 1, dpi)
        try:
            txt, c = _ask(png, _EXTRACT_ASK, max_tokens=2000)
        except Exception as e:
            if verbose:
                print(f"  p{pno} ERR {type(e).__name__}")
            continue
        credit += c
        d = _parse_json(txt)
        tt = str(d.get("table_type") or "").strip().upper()[:1]
        types[pno] = tt or "?"
        got = d.get("items") or []
        for it in got:
            if isinstance(it, dict):
                it["_page"] = pno
                it["_source"] = "vision"
                it["_table_type"] = types[pno]
                items.append(it)
        if verbose:
            print(f"  p{pno}: 表型{types[pno]} {len(got)} 条")
    return {"items": items, "credit": credit, "elapsed": time.time() - t0,
            "types": types}


def vision_detections(pdf_path: Path, index: dict | None = None,
                      dpi: int = 180, verbose: bool = True) -> dict:
    """主入口: 扫描件 → detections 风格的结构化数据

    返回 {detections:[...], sio2:{...}, cost:{credit,elapsed}, index}
    detections 每项:
      {factor, sampling_point, ctwa, cstel, results, judgement, sio2_percent,
       source:'vision', _page, _file}
    """
    pdf_path = Path(pdf_path)
    if index is None:
        if verbose:
            print(f"索引遍 (150dpi): {pdf_path.name}")
        index = build_index(pdf_path, dpi=150, verbose=verbose)
    # 关键页 = 有检测项目的页 (SiO2 页必含)
    key_pages = [p["page"] for p in index["pages"]
                 if p["factor"] and p["factor"] not in ("无", "")]
    if verbose:
        print(f"精读 {len(key_pages)} 页 (180dpi)…")
    res = extract_pages(pdf_path, key_pages, dpi=dpi, verbose=verbose)

    dets, sio2 = [], {}
    for it in res["items"]:
        f = str(it.get("factor") or "").strip()
        if not f:
            continue
        rec = {
            "factor": f,
            "sampling_point": str(it.get("sampling_point") or "").strip(),
            "ctwa": str(it.get("ctwa") or "").strip(),
            "cstel": str(it.get("cstel") or "").strip(),
            "results": it.get("results") or [],
            "pass_ratio": str(it.get("pass_ratio") or "").strip(),
            "dust_type": str(it.get("dust_type") or "").strip(),
            "judgement": str(it.get("judgement") or "").strip(),
            "exposure_hours": str(it.get("exposure_hours") or "").strip(),
            "table_type": it.get("_table_type") or "",
            "source": "vision",
            "_page": it.get("_page"),
            "_file": pdf_path.name,
        }
        sp = str(it.get("sio2_percent") or "").strip()
        if sp:
            rec["sio2_percent"] = sp
            sio2[rec["sampling_point"] or f] = sp
        dets.append(rec)

    return {
        "detections": dets,
        "sio2": sio2,
        "cost": {"credit": index["credit"] + res["credit"],
                 "elapsed": index["elapsed"] + res["elapsed"],
                 "index_pages": index["scanned_pages"],
                 "read_pages": len(key_pages)},
        "index": index,
    }


# ============ 来源标记: 标 vision, 不冒领 rule ============
def as_provenance(rec: dict) -> dict:
    """给 vision 提取的数字挂来源标记 (供 number_provenance 审计)

    用户红线: 机器识别的数 ≠ 确定提取的数 → 单列 vision, 提示人工核对
    """
    from web.number_provenance import prov_vision
    return prov_vision(
        {"file": rec.get("_file", ""), "page": rec.get("_page"),
         "text": f"视觉模型识别 ({rec.get('sampling_point','')})"},
        field=rec.get("factor", ""))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="扫描件视觉提取 (检测报告 → 结构化数据)")
    ap.add_argument("pdf")
    ap.add_argument("--index", action="store_true", help="只出索引")
    ap.add_argument("--extract", action="store_true", help="索引+精读 → JSON")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 页 (试跑)")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = ap.parse_args()

    p = Path(args.pdf)
    if not p.exists():
        print(f"文件不存在: {p}")
        return 1
    is_scan, n = _is_scanned(p)
    print(f"文件: {p.name} | {n} 页 | {'扫描件(无文本层)' if is_scan else '有文本层'}")

    if args.index and not args.extract:
        idx = build_index(p, limit=args.limit)
        print(f"\n索引完成: {idx['scanned_pages']} 页 | "
              f"{idx['elapsed']:.0f}s | credit {idx['credit']:.2f}")
        hits = [x for x in idx["pages"] if x["has_sio2"]]
        print(f"含 SiO2 的页: {[x['page'] for x in hits]}")
        if args.out:
            Path(args.out).write_text(json.dumps(idx, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
            print(f"→ {args.out}")
        return 0

    r = vision_detections(p)
    c = r["cost"]
    print(f"\n完成: {len(r['detections'])} 条检测记录 | "
          f"索引{c['index_pages']}页+精读{c['read_pages']}页 | "
          f"{c['elapsed']:.0f}s | credit {c['credit']:.2f}")
    if r["sio2"]:
        print(f"游离二氧化硅: {json.dumps(r['sio2'], ensure_ascii=False)}")
    if args.out:
        Path(args.out).write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
