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
# ⚠ 必须区分"本页有 SiO2 测定**数值**" vs "本页某因素**名字里**含 SiO2":
#   实测 p58/p59 因素名有"矽尘(游离SiO2含量>10%)" → 旧 prompt 误判为"有"
_INDEX_ASK = ("本页是职业病危害检测报告的一页。用一行回答: "
              "①本页检测项目(危害因素名, 若无写'无') "
              "②本页是否有游离二氧化硅的**检测结果数值(%)**(注意: 因素名称里带SiO2字样不算, "
              "必须是表格里测出的百分比数值)。"
              "格式: `检测项目=X; 二氧化硅=有/无`。不要输出其它内容。")

# 精读遍: 结构化转录
# ⚠ 实测: 检测报告里至少有两种表型, 不能假设单一 shape
#   (a) 逐样结果表: 检测项目 | 采样点 | 接触时间 | 结果1/2/3 | C_TWA | C_STEL | 判定
#   (b) 岗位汇总表: 车间/岗位 | 工作地点 | 测试点数 | 危害因素×2 | 检测方式×2 |
#                  检测结果×2 | 佩戴(PPE)
#   ⚠ 实测纠偏 (300dpi 取证, v59): (b) 的"检测结果×2"里**不是浓度也不是合格率** ——
#     新泰 p25 列头是 `采样时长/小时`, 长兴 p14 是 `采样时间(小时)`, 值都是 0.5/2/3/4/5。
#     旧 prompt 曾写 "如 3/1 = 合格点数/总点数" 当示例 → 模型照抄示例值, 产出伪字段
#     `pass_ratio`(长兴 89/109 条都是 3/1 = 我给的示例)。**已删除该字段与示例**。
#   硬套 (a) 的字段去读 (b) 会得到一堆空值 → 先让模型自报表型, 再按型取数。
_EXTRACT_ASK = (
    "这是中国职业病危害检测报告的扫描页。请先判断表格类型, 再精确转录为 JSON。\n"
    "表型A=逐样结果表(有 C_TWA/C_STEL 浓度值); "
    "表型B=岗位汇总表(危害因素/检测方式/检测结果, 无浓度); "
    "表型D=游离二氧化硅测定表(列: 车间/采样岗位|采样点|粉尘种类|检测结果(%)|判定); "
    "表型C=其他(无检测数据, 如封面/目录/纯说明页)。\n"
    "只输出 JSON:\n"
    '{"table_type":"A|B|C|D",'
    '"items":[{"factor":"危害因素名或检测项目","sampling_point":"采样点或岗位+工作地点",'
    '"exposure_hours":"接触时间h/d","results":["检测值..."],'
    '"ctwa":"C_TWA(仅A)","cstel":"C_STEL/C_PE(仅A)",'
    '"dust_type":"粉尘种类(仅D)",'
    '"sio2_percent":"游离二氧化硅检测结果%(仅D)",'
    '"judgement":"符合/不符合/判定(有则填)"}]}\n'
    "硬性规则: ①数字与单位逐字准确, 不推测不补全 ②本页字段没有的留空字符串 "
    "③页面只有封面/目录/说明且无任何检测数值时才用 C ④'<1.7'这类未检出值原样保留 "
    "⑤合并单元格的车间/岗位要补全到行上 ⑥表型D 必填 factor='游离二氧化硅' "
    "⑦**results 只放数值**(如 ['0.5','0.4','0.3'] 或 ['<1.7'])；"
    "'定点短时间'/'直读'/'个体采样'属**检测方式**, 不是检测结果, 不要放进 results "
    "⑧**不要臆造字段**: 页面没有的列不要输出 "
    "⑨**若本页有表格行但读不清数值, 仍要输出 factor/sampling_point 行(值留空), "
    "不要整页报 C**"
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
    """容错解析模型返回的 JSON (可能带 markdown 围栏)

    ⚠ 必须区分"解析失败"与"表里真没数据":
      原实现两者都返回 {"items": []} → 调用方无法分辨 → 截断的响应被当成空页**静默丢弃**。
      实测: 长兴 p21/p23、新泰 p44 因 max_tokens=2000 截断 → raw 5000+ 字符
            但 `{` 多于 `}` → json.loads 抛错 → 返回 {"items":[]} → 数据无声丢失。
    故: 解析结果带 _parse_ok 标志, 调用方据此决定是否重试。
    """
    t = txt.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return {"items": [], "_parse_ok": False, "_parse_err": "no_json"}
    try:
        d = json.loads(m.group(0))
    except Exception as e:
        return {"items": [], "_parse_ok": False, "_parse_err": f"json:{type(e).__name__}"}
    if not isinstance(d, dict):
        return {"items": [], "_parse_ok": False, "_parse_err": "not_dict"}
    d["_parse_ok"] = True
    return d


def extract_pages(pdf_path: Path, page_nos: list[int], dpi: int = 180,
                  verbose: bool = True, max_tokens: int = 2000,
                  retries: int = 2) -> dict:
    """精读遍: 只读指定页 → 结构化 items (含 table_type)

    ⚠ v59 修复: 原实现有两处**静默丢数据**, 且丢完看起来和"这页本来就没数据"一样:

      ① 模型偶发返回 `{"table_type":"C","items":[]}` (实测同页同图同 prompt,
         连跑 3 次得 [14,0,0] / [12,0,0] —— **非确定性**, temperature=0 也复现)。
      ② 响应被 max_tokens 截断 → JSON 不闭合 → 解析失败 → 当成空页。
         实测长兴 p21/p23、新泰 p44 各丢 10~28 条。

    修复: ① 解析失败 或 ② 空返回 时**重试** (换 DPI 重渲染提高可读性);
          重试后仍空才记为空页, 并在返回里单列 `empty_after_retry` 供审计。
    宁可多花一点 credit, 不可静默丢页 —— 丢了没人知道。
    """
    items, credit, t0 = [], 0.0, time.time()
    types: dict[int, str] = {}
    retried: list[int] = []      # 触发过重试的页
    empty2: list[int] = []       # 重试后仍空的页 (可疑, 供人工审计)
    unresolved: list[int] = []   # 解析始终失败

    # 重试时换更高 DPI (实测 240/300dpi 能改善小字表格的可读性)
    for pno in page_nos:                     # pno 为 1-based
        got_all, tt_final, last_err = [], "", ""
        for attempt in range(retries + 1):
            use_dpi = dpi if attempt == 0 else max(240, dpi)
            png = _render(pdf_path, pno - 1, use_dpi)
            try:
                txt, c = _ask(png, _EXTRACT_ASK, max_tokens=max_tokens)
                credit += c
            except Exception as e:
                last_err = f"{type(e).__name__}"
                if verbose:
                    print(f"  p{pno} ERR {last_err} (attempt {attempt+1})")
                time.sleep(1.5 * (attempt + 1))
                continue

            d = _parse_json(txt)
            tt = str(d.get("table_type") or "").strip().upper()[:1]
            ok = bool(d.get("_parse_ok"))
            got = [it for it in (d.get("items") or []) if isinstance(it, dict)]
            got = [it for it in got if str(it.get("factor") or "").strip()]

            if not ok:
                last_err = str(d.get("_parse_err") or "parse_fail")
                if verbose:
                    print(f"  p{pno} 解析失败({last_err}) 尝试重试 (attempt {attempt+1})")
                # 截断是确定性失败的主因 → 提高 max_tokens 再试
                if max_tokens < 3500:
                    max_tokens = 3500
                time.sleep(0.8)
                continue

            if got:
                got_all, tt_final, last_err = got, tt, ""
                break

            # 解析成功但 0 条 → 可能是模型偶发"全 C" → 重试
            tt_final = tt
            if attempt < retries:
                if verbose:
                    print(f"  p{pno} 空返回, 重试 (attempt {attempt+1})")
                retried.append(pno)
                time.sleep(0.8)
                continue
            break

        if got_all:
            types[pno] = tt_final or "?"
            for it in got_all:
                it["_page"] = pno
                it["_source"] = "vision"
                it["_table_type"] = types[pno]
                items.append(it)
        else:
            types[pno] = tt_final or "?"
            if last_err:
                unresolved.append(pno)
            else:
                empty2.append(pno)

        if verbose:
            print(f"  p{pno}: 表型{types.get(pno,'?')} {len(got_all)} 条")

    if retried and verbose:
        print(f"  [重试过的页] {sorted(set(retried))}")
    if empty2 and verbose:
        print(f"  [重试后仍空] {sorted(set(empty2))} ← 可疑, 建议人工抽查")
    if unresolved and verbose:
        print(f"  [始终解析失败] {sorted(set(unresolved))}")

    return {"items": items, "credit": credit, "elapsed": time.time() - t0,
            "types": types, "retried": sorted(set(retried)),
            "empty_after_retry": sorted(set(empty2)),
            "unresolved": sorted(set(unresolved))}


def _cache_path(pdf_path: Path) -> Path:
    """视觉提取结果缓存路径: data/vision_cache/<key>.json

    ⚠ 必须缓存: 125 页扫描件跑一次 ≈17 分钟 / ¥5.5, 而同一份检测报告
      在导入/重导/校验流程里会被反复读到。不缓存 = 每次都等 17 分钟。
    键含 size+mtime → 报告文件更新后自动失效, 不会拿旧结果。
    """
    import hashlib
    try:
        st = pdf_path.stat()
        raw = f"{pdf_path.resolve()}|{st.st_size}|{int(st.st_mtime)}|{MODEL}"
    except OSError:
        return Path("/dev/null")
    cd = _data_dir() / "vision_cache"
    cd.mkdir(parents=True, exist_ok=True)
    return cd / f"{hashlib.sha1(raw.encode()).hexdigest()[:16]}.json"


def _data_dir() -> Path:
    """数据目录 (本地 data/ 或容器 /app/data)"""
    root = next((p for p in (Path("/app"), Path(__file__).resolve().parent.parent)
                 if (p / "data").exists()), Path.cwd())
    return root / "data"


def vision_detections(pdf_path: Path, index: dict | None = None,
                      dpi: int = 180, verbose: bool = True,
                      use_cache: bool = True, force: bool = False) -> dict:
    """主入口: 扫描件 → detections 风格的结构化数据 (带缓存)

    返回 {detections:[...], sio2:{...}, cost:{credit,elapsed}, index, cached:bool}
    detections 每项:
      {factor, sampling_point, ctwa, cstel, results, judgement, sio2_percent,
       source:'vision', _page, _file}
    """
    pdf_path = Path(pdf_path)
    cp = _cache_path(pdf_path)
    if use_cache and not force and cp.exists():
        try:
            d = json.loads(cp.read_text(encoding="utf-8"))
            d["cached"] = True
            if verbose:
                print(f"命中视觉缓存: {cp.name} ({len(d.get('detections') or [])} 条)")
            return d
        except Exception:
            pass  # 缓存损坏 → 重跑

    if index is None:
        if verbose:
            print(f"索引遍 (150dpi): {pdf_path.name}")
        index = build_index(pdf_path, dpi=150, verbose=verbose)
    # 关键页 = 有检测项目的页 **或 索引标了含SiO2的页**
    # ⚠ bug(实测): 原先只按 factor 非空筛, p5(factor='无' 但二氧化硅=有) 被漏读
    #   → 索引遍说 4 页有 SiO2, 实际只精读 1 页。两条判据要取并集。
    key_pages = sorted({
        p["page"] for p in index["pages"]
        if (p["factor"] and p["factor"] not in ("无", "")) or p.get("has_sio2")
    })
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

    result = {
        "detections": dets,
        "sio2": sio2,
        "cost": {"credit": index["credit"] + res["credit"],
                 "elapsed": index["elapsed"] + res["elapsed"],
                 "index_pages": index["scanned_pages"],
                 "read_pages": len(key_pages)},
        "index": index,
        "cached": False,
    }
    if use_cache:
        try:
            cp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            if verbose:
                print(f"已缓存 → {cp}")
        except Exception:
            pass
    return result


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


# ============ 扫描件: 营业执照 → 企业/项目基本信息 ============
# 背景: 材料包里的营业执照多为**扫描件**, 现有提取器读不到 →
#       company/project_name/founded/registered_capital/legal_rep 全是 None
#       (实测 c8c7ff0a4d 这 5 个字段全空), 而报告 1.1/3.1 正需要这些。
_LICENSE_ASK = (
    "这是一张中国企业**营业执照**。请提取以下字段, 用 JSON 返回 (读不到的字段给空串, 不要猜):\n"
    '{"company":"企业名称(全称)", "credit_code":"统一社会信用代码",\n'
    ' "legal_rep":"法定代表人", "registered_capital":"注册资本",\n'
    ' "founded":"成立日期", "address":"住所/注册地址",\n'
    ' "business_scope":"经营范围", "type":"类型(如有限责任公司)",\n'
    ' "valid_to":"营业期限至"}\n'
    "注意: ① 照原样抄写, 不要改写或补全 ② 数字/日期必须与图上一致 ③ 非营业执照页返回 {}"
)


def extract_license(pdf_path: Path, dpi: int = 200, verbose: bool = True,
                    use_cache: bool = True, force: bool = False) -> dict:
    """营业执照扫描件 → 企业基本信息 (带缓存)

    ⚠ 与检测数据同规: 视觉读取的结果一律标 source='vision' + needs_review,
      绝不伪装成"规则确定提取" (用户红线)。
    """
    pdf_path = Path(pdf_path)
    cp = _cache_path(pdf_path).with_name(_cache_path(pdf_path).stem + "_license.json")
    if use_cache and not force and cp.exists():
        try:
            d = json.loads(cp.read_text(encoding="utf-8"))
            if verbose:
                print(f"命中执照缓存: {cp.name}")
            d["cached"] = True
            return d
        except Exception:
            pass

    import fitz
    doc = fitz.open(str(pdf_path))
    out: dict = {}
    credit = 0.0
    n = min(len(doc), 3)          # 执照通常 1-2 页, 最多读 3 页
    for i in range(n):
        try:
            # ⚠ _render 是 **0-based** (内部 doc[pno]); 传 i 而非 i+1
            png = _render(pdf_path, i, dpi)
            txt, cr = _ask(png, _LICENSE_ASK, max_tokens=700)
            credit += cr
            d = _parse_json(txt)
            # 命中任一关键字段即认为这页是执照
            if any(d.get(k) for k in ("company", "credit_code", "legal_rep")):
                out.update({k: v for k, v in d.items() if v})
                out["_page"] = i + 1
                if verbose:
                    print(f"  第{i+1}页 提取到: {str(out.get('company', ''))[:30]}")
                break
        except Exception as e:
            if verbose:
                print(f"  第{i+1}页失败: {type(e).__name__}: {e}")
    doc.close()

    res = {"fields": out, "source": "vision", "needs_review": True,
           "cost": {"credit": credit}, "_file": pdf_path.name}
    # ⚠ 失败结果**不写缓存**: 早期把"0 字段"也缓存了 → 下次直接命中空结果,
    #   看起来像"提取器不工作" (实测踩过: 页索引 off-by-one 报错后缓存了 {})。
    if out:
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return res


def main():
    import argparse
    ap = argparse.ArgumentParser(description="扫描件视觉提取 (检测报告 → 结构化数据)")
    ap.add_argument("pdf")
    ap.add_argument("--index", action="store_true", help="只出索引")
    ap.add_argument("--extract", action="store_true", help="索引+精读 → JSON")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 页 (试跑)")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    ap.add_argument("--no-cache", action="store_true", help="跳过缓存, 强制重跑")
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

    r = vision_detections(p, use_cache=not args.no_cache)
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
