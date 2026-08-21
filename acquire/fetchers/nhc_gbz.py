"""卫健委 GBZ/WS 标准抓取器 v2 — search API 驱动

核心升级 (相对 v1 列表页抓取):
  v1 抓列表页 HTML → 瑞数拦截/JS翻页脆弱/漏标准 (基线988只覆盖在线45%)
  v2 用 nhc 搜索后端 JSON API (wsbz_list.js 挖出):
      /search/<channelId>?_isAgg=true&_isJson=true&_pageSize=100&_template=index&page=N
  瑞数由 r.jina.ai 云端渲染解决 → 纯脚本可跑, cron 友好

通道:
  枚举/详情: r.jina.ai (curl 风格 UA, 走梯子), 间隔 3s
  PDF 下载:  直连 nhc 静态文件 (不经瑞数), 间隔 2-3s
  全部:      指数退避 3 次上限 + 失败留痕

配置: acquire/fetchers/nhc_channels.json  {list_url: channelId}  (27 分类, 6 个待解析)
状态: acquire/raw/nhc_gbz/all_catalog.jsonl   {category, standardcode, title, url, pubtime, sstime}
      acquire/raw/nhc_gbz/pdfs/*.pdf
      acquire/raw/nhc_gbz/pdfs/errors.jsonl

用法:
  python3 -m acquire.fetchers.nhc_gbz enum      # 全分类枚举 (增量, url去重)
  python3 -m acquire.fetchers.nhc_gbz diff      # 基线差异 (旧库 vs 在线)
  python3 -m acquire.fetchers.nhc_gbz download --limit N   # 补 PDF (断点续传)
  python3 -m acquire.fetchers.nhc_gbz status    # 完整性报告
"""
import argparse
import json
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from acquire.base import PoliteFetcher, log  # noqa: E402

BASE = "https://www.nhc.gov.cn"
JINA = "https://r.jina.ai/"
RAW = Path(__file__).resolve().parent.parent / "raw" / "nhc_gbz"
CATALOG = RAW / "all_catalog.jsonl"
PDF_DIR = RAW / "pdfs"
CHANNELS_FILE = Path(__file__).resolve().parent / "nhc_channels.json"
GBZ_DB = Path.home() / "Desktop/law/data/gbz_standards.db"
OLD_PDFS = Path.home() / "Desktop/law/data/gbz_pdfs"
PROXY = "http://127.0.0.1:7890"
PAGE_SIZE = 100
OBS_RE = re.compile(r"已被代替|已废止|已无效|废止|无效|代替")


def _jina_fetcher() -> PoliteFetcher:
    return PoliteFetcher("nhc_gbz_jina", interval=3.0, proxy=PROXY)


def load_channels() -> dict:
    """list_url -> channelId (有效 32 位 hex)"""
    return {u: c for u, c in json.loads(CHANNELS_FILE.read_text(encoding="utf-8")).items()
            if len(c) == 32}


# ---------- 枚举 ----------
def search_api(fetcher: PoliteFetcher, channel_id: str, page: int) -> dict:
    """r.jina.ai 代理 search API → JSON (jina 输出含 markdown 头, 需剥离)"""
    url = (f"{BASE}/search/{channel_id}?_isAgg=true&_isJson=true"
           f"&_pageSize={PAGE_SIZE}&_template=index&_rangeTimeGte=&_channelName=&page={page}")
    text = fetcher.get(JINA + url, headers={"User-Agent": "curl/8.7.1"})
    i = text.find("{")
    return json.loads(text[i:]) if i >= 0 else {}


def _extract(results: list[dict], category: str) -> list[dict]:
    out = []
    for r in results:
        meta = {}
        for dm in (r.get("domainMetaList") or []):
            for rl in (dm.get("resultList") or []):
                meta[rl.get("key")] = rl.get("value")
        out.append({
            "category": category,
            "standardcode": meta.get("standardcode", ""),
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "pubtime": meta.get("pubtime", ""),
            "sstime": meta.get("sstime", ""),
        })
    return [it for it in out if it["url"]]


def enum(fetcher: PoliteFetcher, categories: list[str] | None = None) -> dict:
    """全分类枚举, 增量追加 (url 去重)"""
    channels = load_channels()
    RAW.mkdir(parents=True, exist_ok=True)
    seen = set()
    if CATALOG.exists():
        for ln in CATALOG.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                seen.add(json.loads(ln)["url"])
    report = {}
    with open(CATALOG, "a", encoding="utf-8") as f:
        for url, cid in channels.items():
            cat = url.split("/")[-2]
            if categories and cat not in categories:
                continue
            page, cat_new, total, failed = 1, 0, 0, 0
            while True:
                try:
                    d = (search_api(fetcher, cid, page) or {}).get("data") or {}
                except Exception as e:
                    # 单页失败不得静默放弃整个分类: 记录失败页, 跳到下一页继续
                    # (enum 幂等, 下次重跑会补齐失败页; 若中途 break 则剩余页全部丢失且无痕)
                    log.error("枚举失败 %s 页%d: %s (跳过该页, 下次重跑补齐)", cat, page, e)
                    failed += 1
                    page += 1
                    if page * PAGE_SIZE >= (total or 0) and total:
                        break
                    continue
                results = d.get("results") or []
                total = d.get("total") or 0
                if not results:
                    break
                for it in _extract(results, cat):
                    if it["url"] not in seen:
                        f.write(json.dumps(it, ensure_ascii=False) + "\n")
                        seen.add(it["url"])
                        cat_new += 1
                if page * PAGE_SIZE >= total or len(results) < PAGE_SIZE:
                    break
                page += 1
            report[cat] = {"total": total, "new": cat_new, "failed_pages": failed}
            flag = " ⚠️有失败页" if failed else ""
            print(f"{cat}: total={report[cat]['total']} 新增={cat_new} 失败页={failed}{flag}")
    return report


# ---------- 差异 ----------
def load_catalog() -> list[dict]:
    if not CATALOG.exists():
        return []
    return [json.loads(l) for l in CATALOG.read_text(encoding="utf-8").splitlines() if l.strip()]


def diff() -> dict:
    """在线全量 vs 旧库基线: 缺失拆解 现行/废止"""
    items = load_catalog()
    online = {it["url"].replace(BASE, "") for it in items}
    conn = sqlite3.connect(str(GBZ_DB))
    base = {(r[0] or "").replace(BASE, "") for r in
            conn.execute("SELECT detail_url FROM gbz_standards")}
    conn.close()
    missing = [it for it in items if it["url"].replace(BASE, "") not in base]
    obs = [it for it in missing if OBS_RE.search(it["title"])]
    cur = [it for it in missing if not OBS_RE.search(it["title"])]
    return {"online": len(online), "baseline": len(base),
            "missing": len(missing), "obsolete": len(obs), "current": len(cur)}


# ---------- 下载 ----------
def fetch_pdf_url(fetcher: PoliteFetcher, detail_url: str) -> str | None:
    md = fetcher.get(JINA + BASE + detail_url, headers={"User-Agent": "curl/8.7.1"})
    m = re.search(r"https?://[^)\s]+\.pdf", md)
    return m.group(0) if m else None


def download(fetcher: PoliteFetcher, pdf_fetcher: PoliteFetcher,
             limit: int | None = None, categories: list[str] | None = None):
    """补 PDF: 详情页(jina)取链接 → 直连下载 → %PDF 校验 (断点续传)"""
    items = load_catalog()
    if categories:
        items = [it for it in items if it["category"] in categories]
    todo = [it for it in items if not has_pdf(it)]
    print(f"待下载 PDF: {len(todo)} (本次上限 {limit or '无'})")
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    done, no_pdf = 0, 0
    for it in todo[:limit]:
        try:
            pdf_url = fetch_pdf_url(fetcher, it["url"].replace(BASE, ""))
            if not pdf_url:
                no_pdf += 1
                log.warning("无PDF附件: %s %s", it.get("standardcode") or "?", it["title"][:30])
                continue
            content = pdf_fetcher.get_bytes(pdf_url)
            if not content.startswith(b"%PDF"):
                log.warning("非PDF(魔数): %s %s %d字节", it.get("standardcode"), it["title"][:20], len(content))
                continue
            name = _pdf_name(it)
            (PDF_DIR / name).write_bytes(content)
            done += 1
            if done % 10 == 0:
                print(f"已下载 {done}...")
        except Exception as e:
            log.error("下载失败 %s: %s", it.get("url"), e)
        time.sleep(random.uniform(2, 3))
    print(f"完成 {done} 份, 无附件 {no_pdf}")


def _pdf_name(it: dict) -> str:
    base = re.sub(r"[/—\s]+", "_", it.get("standardcode") or it["title"]) or "unknown"
    return f"{base}.pdf"


def has_pdf(it: dict) -> bool:
    if (PDF_DIR / _pdf_name(it)).exists():
        return True
    # 旧库 PDF 复用 (标题/标准号匹配)
    if OLD_PDFS.exists():
        code = (it.get("standardcode") or "").replace("/", "_").replace("—", "-")
        for f in OLD_PDFS.glob("*.pdf"):
            if code and code in f.name:
                return True
            if it["title"][:10] and it["title"][:10].replace(" ", "") in f.name.replace(" ", ""):
                return True
    return False


# ---------- 状态 ----------
def status() -> dict:
    items = load_catalog()
    from collections import Counter
    by_cat = Counter(it["category"] for it in items)
    have_pdf = sum(1 for it in items if has_pdf(it))
    return {"catalog": len(items), "have_pdf": have_pdf,
            "missing_pdf": len(items) - have_pdf, "by_category": dict(by_cat)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["enum", "diff", "download", "status"])
    ap.add_argument("--category", action="append", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    jina = _jina_fetcher()
    if args.action == "enum":
        enum(jina, args.category)
    elif args.action == "diff":
        print(json.dumps(diff(), ensure_ascii=False, indent=2))
    elif args.action == "download":
        download(jina, PoliteFetcher("nhc_gbz_pdf", interval=1.5), args.limit, args.category)
    elif args.action == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
