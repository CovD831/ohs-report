"""卫健委 GBZ/WS 标准 — 增量抓取器 (r.jina.ai 通道)

已验证 (2026-08):
  - 标准树 /zwgkzt/wsbz/wsbz-tree.html: r.jina.ai HTML 模式可过瑞数WAF, 正则提取 36 个分类 channelUrl
  - 分类列表页 (如 /wjw/pyl/wsbz.shtml): markdown 含结构化条目
      格式: ·标准号[标准名](https://www.nhc.gov.cn/wjw/xxx/YYYYMM/<hash>.shtml)发布时间 实施时间
  - 详情页: markdown 含 PDF 链接 [标准号 标题.pdf](https://.../files/xxx.pdf)
  - 本地无头浏览器 (crawl4ai) 被 WAF 拦截, 一律走 r.jina.ai (外部渲染服务)

增量策略 (两阶段):
  阶段1 全量补缺: 复用旧库 988 条元数据 + data/gbz_pdfs/ 988 份 PDF (raw 原始文件)
  阶段2 增量发现: 定期抓 36 分类列表页第 1 页 (新标准按日期排最前) → detail_url 与基线差集
                  → 新标准抓详情页取 PDF

温和纪律: r.jina.ai 调用间隔 ≥3s; PDF 下载 (nhc CDN) 间隔 2-3s; 指数退避 3 次上限
输出: raw/nhc_gbz/standards_manifest.jsonl + raw/nhc_gbz/pdfs/
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
TREE_URL = BASE + "/zwgkzt/wsbz/wsbz-tree.html"
RAW = Path(__file__).resolve().parent.parent / "raw" / "nhc_gbz"
PDF_DIR = RAW / "pdfs"
MANIFEST = RAW / "standards_manifest.jsonl"
GBZ_DB = Path.home() / "Desktop/law/data/gbz_standards.db"
OLD_PDFS = Path.home() / "Desktop/law/data/gbz_pdfs"
PROXY = "http://127.0.0.1:7890"   # r.jina.ai 在境外, 走本机梯子

# 列表条目: ·标准号[标准名](url)发布时间 实施时间
LINE_RE = re.compile(
    r"·\s*([^\[]*?)\s*\[([^\]]+)\]\((https?://[^)\s]+\.shtml)[^)]*\)"
    r"\s*(\d{4}年\d{1,2}月\d{1,2}日)?\s*(\d{4}年\d{1,2}月\d{1,2}日)?")
# 详情页 PDF 链接两种形态: [标题.pdf](url) 或 裸 (url.pdf) 图片/附件链接
PDF_RE = re.compile(r"https?://[^)\s]+\.pdf")


def _jina_fetcher() -> PoliteFetcher:
    """r.jina.ai 通道: 境外服务, 间隔放宽到 3s"""
    return PoliteFetcher("nhc_gbz_jina", interval=3.0, proxy=PROXY)


def jina_markdown(url: str, html: bool = False) -> str:
    f = _jina_fetcher()
    jurl = "https://r.jina.ai/" + url
    # 注意: r.jina.ai 拒绝浏览器风格 UA (403), 必须用 curl 风格 UA
    hdrs = {"User-Agent": "curl/8.7.1"}
    if html:
        hdrs["X-Return-Format"] = "html"
    return f.get(jurl, headers=hdrs)


# ---------- 基线 ----------
def load_baseline() -> dict:
    """旧库 detail_url → {standard_number, title, category} (全量补缺的基线)"""
    out = {}
    if not GBZ_DB.exists():
        return out
    conn = sqlite3.connect(str(GBZ_DB))
    for r in conn.execute(
            "SELECT standard_number, title, detail_url, category FROM gbz_standards"):
        url = (r[2] or "").replace(BASE, "")
        if url:
            out[url] = {"standard_number": r[0], "title": r[1], "category": r[3]}
    conn.close()
    return out


def load_manifest() -> dict:
    out = {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                out[d["detail_url"]] = d
    return out


def append_manifest(rows: list[dict]):
    RAW.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------- 增量发现 ----------
def discover_categories() -> list[str]:
    """标准树 HTML 模式 → 36 个分类列表页 URL"""
    html = jina_markdown(TREE_URL, html=True)
    urls = re.findall(r'channelUrl:\s*"([^"]*wsbz\.shtml)"', html)
    skip = ("wzdt", "xinw", "hud", "zhuant", "fuwu", "xinx", "wld")
    cats = [u if u.startswith("http") else BASE + u for u in urls
            if not any(k in u for k in skip)]
    print(f"发现 {len(cats)} 个分类列表页")
    return cats


def parse_list_page(url: str) -> list[dict]:
    """列表页 → 条目 [{standard_number, title, detail_url, publish, implement}]"""
    md = jina_markdown(url)
    items = []
    for line in md.splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        items.append({
            "standard_number": m.group(1).strip(),
            "title": m.group(2).strip(),
            "detail_url": m.group(3).replace(BASE, ""),
            "publish": m.group(4) or "",
            "implement": m.group(5) or "",
        })
    return items


def sync(category_urls: list[str] | None = None, categories: list[str] | None = None):
    """抓分类列表页 → 与基线/已同步差集 → 新标准入 manifest"""
    baseline = load_baseline()
    manifest = load_manifest()
    urls = category_urls or discover_categories()
    if categories:   # 只同步指定分类 (按 URL 包含)
        urls = [u for u in urls if any(c in u for c in categories)]
    new_total = 0
    for url in urls:
        try:
            items = parse_list_page(url)
        except Exception as e:
            log.error("列表页失败 %s: %s", url, e)
            continue
        fresh = [i for i in items
                 if i["detail_url"] not in baseline and i["detail_url"] not in manifest]
        for i in fresh:
            i["category_url"] = url
        append_manifest(fresh)
        for i in fresh:            # 同步内存清单, 防跨分类重复追加
            manifest[i["detail_url"]] = i
        new_total += len(fresh)
        print(f"{url.split('/')[-2] or url}: 页内 {len(items)} 条, 新增 {len(fresh)}")
    print(f"同步完成: 新增 {new_total} 条 (基线 {len(baseline)})")


# ---------- 下载 ----------
def fetch_pdf_url(detail_url: str) -> str | None:
    md = jina_markdown(BASE + detail_url)
    m = PDF_RE.search(md)
    return m.group(0) if m else None


def download(fetcher: PoliteFetcher, limit: int | None = None):
    """新标准详情页 → PDF (已下载跳过, 断点续传)"""
    manifest = load_manifest()
    todo = [v for v in manifest.values() if not _has_pdf(v)]
    print(f"待下载 PDF: {len(todo)} (本次上限 {limit or '无'})")
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    done = 0
    for v in todo[:limit]:
        try:
            pdf_url = fetch_pdf_url(v["detail_url"])
            if not pdf_url:
                log.warning("详情页无PDF链接: %s", v["detail_url"])
                continue
            content = fetcher.get_bytes(pdf_url)
            if not content.startswith(b"%PDF"):
                log.warning("非PDF(魔数异常): %s %d字节", v["standard_number"], len(content))
                continue
            name = re.sub(r'[/—\s]+', '_', v["standard_number"] or v["title"]) or v["detail_url"].split("/")[-1]
            (PDF_DIR / f"{name}.pdf").write_bytes(content)
            done += 1
            print(f"下载 {done}: {v['standard_number']} {v['title'][:30]}")
        except Exception as e:
            log.error("PDF失败 %s: %s", v.get("standard_number"), e)
        time.sleep(random.uniform(2, 3))   # nhc CDN, 2-3s 温和
    print(f"完成 {done} 份")


def _has_pdf(v: dict) -> bool:
    if not PDF_DIR.exists():
        return False
    if v.get("standard_number"):
        base = re.sub(r'[/—\s]+', '_', v["standard_number"])
        if (PDF_DIR / f"{base}.pdf").exists():
            return True
    # 旧库 PDF 复用 (data/gbz_pdfs/, 按标准号匹配)
    if OLD_PDFS.exists() and v.get("standard_number"):
        for f in OLD_PDFS.glob("*.pdf"):
            if v["standard_number"].replace("/", "_").replace("—", "-") in f.name:
                return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["baseline", "sync", "download", "diff"])
    ap.add_argument("--category", action="append", default=None, help="只同步指定分类(URL包含词)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    if args.action == "baseline":
        b = load_baseline()
        print(f"基线 detail_url: {len(b)} 条")
    elif args.action == "sync":
        sync(categories=args.category)
    elif args.action == "diff":
        b, m = load_baseline(), load_manifest()
        new = [v for v in m.values() if v["detail_url"] not in b]
        print(json.dumps({"baseline": len(b), "manifest": len(m),
                          "new_since_baseline": len(new)}, ensure_ascii=False))
        for v in new[:10]:
            print("  ", v["standard_number"], v["title"][:40])
    elif args.action == "download":
        download(PoliteFetcher("nhc_gbz_pdf", interval=1.5), args.limit)


if __name__ == "__main__":
    main()
