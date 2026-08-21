"""国家法律法规库 (flk.npc.gov.cn) — 增量抓取器

已实测 (2026-08): 两个端点纯 HTTP 直连可用, 无需浏览器 (旧版用 DrissionPage 浏览器自动化, 已弃):
  列表: POST /law-search/search/list    pageSize=100, 在线总量 ≈ 29,692
  下载: GET  /law-search/download/pc?format=docx&bbbs=xxx → data.url (flkoss OBS 公网 CDN)

增量策略 (bbbs 差集):
  本地清单 = 旧库 metadata (24,905 条 bbbs) + downloads/ 14,905 份 docx 存在性
  在线列表 = 逐页拉取全部 bbbs
  只下载: 本地无此 bbbs 或 有 bbbs 但无 docx 的条目

温和纪律 (继承 PoliteFetcher):
  列表/下载链接 API → 打政府站 flk.npc.gov.cn: 1.5s 间隔 (PoliteFetcher 默认)
  docx 文件 → 打 OBS CDN (flkoss.obs-bj2.cucloud.cn, 专为文件分发): 2-3s 间隔
  指数退避 3 次上限; 旧版 8-15s 每条 (含状态机) 实测过慢, 已按通道分层

输出:
  raw/flk_npc/manifest.jsonl  每行 {bbbs, title, flxz, zdjgName, gbrq, sxrq, sxx, source}
  raw/flk_npc/docx/{bbbs}.docx   (downloaded 状态 = 文件存在性, 支持断点续传)

用法:
  python3 -m acquire.fetchers.flk_npc build-local    # 一次性: 从旧库建本地清单
  python3 -m acquire.fetchers.flk_npc sync --max-pages N   # 拉在线列表增量
  python3 -m acquire.fetchers.flk_npc diff           # 看差集 (新增/缺docx 数量)
  python3 -m acquire.fetchers.flk_npc download --limit N   # 下载缺失 (断点续传)
"""
import argparse
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from acquire.base import PoliteFetcher, log  # noqa: E402

BASE = "https://flk.npc.gov.cn"
SEARCH_URL = BASE + "/law-search/search/list"
DL_URL = BASE + "/law-search/download/pc"
RAW = Path(__file__).resolve().parent.parent / "raw" / "flk_npc"
DOCX_DIR = RAW / "docx"
MANIFEST = RAW / "manifest.jsonl"
OLD_DB = Path.home() / "Desktop/law/database/law_database.db"
OLD_DOWNLOADS = Path.home() / "Desktop/law/downloads"

SEARCH_PAYLOAD = {
    "searchRange": 1, "sxrq": [], "gbrq": [], "searchType": 2, "sxx": [],
    "gbrqYear": [], "flfgCodeId": [], "zdjgCodeId": [], "searchContent": "",
    "orderByParam": {"order": "-1", "sort": ""},
}
PAGE_SIZE = 100


def _headers():
    return {"Referer": BASE + "/search", "Content-Type": "application/json"}


def load_manifest() -> dict:
    """bbbs -> 元数据行 (去重, 后写覆盖前写)"""
    out = {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                out[d["bbbs"]] = d
    return out


def append_manifest(rows: list[dict]):
    RAW.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


_OLD_DOCX_BBBS: set | None = None


def _old_docx_bbbs() -> set:
    """旧 downloads/ 已有 docx 的 bbbs 集合 (标题匹配, 进程内只算一次)"""
    if not OLD_DOWNLOADS.exists():
        return set()
    existing = {f.stem.replace(" ", "") for f in OLD_DOWNLOADS.glob("*.docx")}
    return {v["bbbs"] for v in load_manifest().values()
            if v.get("title") and v["title"].replace(" ", "") in existing}


def has_docx(bbbs: str) -> bool:
    """新 docx 目录存在 或 旧 downloads/ 已复用 (docx/doc 两种扩展名)"""
    global _OLD_DOCX_BBBS
    if _OLD_DOCX_BBBS is None:
        _OLD_DOCX_BBBS = _old_docx_bbbs()
    return (DOCX_DIR / f"{bbbs}.docx").exists() or (DOCX_DIR / f"{bbbs}.doc").exists() \
        or bbbs in _OLD_DOCX_BBBS


def detect_format(header: bytes) -> str:
    """按魔数识别 Word 格式: PK→docx, OLE2→doc (flk CDN 对部分法律返回老版 .doc)"""
    if header[:2] == b"PK":
        return "docx"
    if header[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "doc"
    return "unknown"


def build_local_manifest():
    """一次性迁移: 旧库 bbbs+元数据 → 清单 (docx 存在性按旧 downloads/ 标题匹配)"""
    conn = sqlite3.connect(str(OLD_DB))
    rows = conn.execute(
        "SELECT bbbs, title, flxz, zdjgName, gbrq, sxrq, sxx FROM metadata").fetchall()
    conn.close()
    m = load_manifest()
    existing = {f.stem.replace(" ", "") for f in OLD_DOWNLOADS.glob("*.docx")} \
        if OLD_DOWNLOADS.exists() else set()

    def _has(title: str) -> bool:
        return bool(title) and title.replace(" ", "") in existing

    new_rows = []
    for bbbs, title, flxz, zdjg, gbrq, sxrq, sxx in rows:
        if bbbs in m:
            continue  # 已记录
        new_rows.append({
            "bbbs": bbbs, "title": title or "", "flxz": flxz, "zdjgName": zdjg,
            "gbrq": gbrq, "sxrq": sxrq, "sxx": sxx, "source": "local",
        })
    append_manifest(new_rows)
    with_docx = sum(1 for r in new_rows if _has(r["title"]))
    print(f"本地清单迁移: {len(new_rows)} 条新增 (其中旧 downloads/ 已有 docx ≈ {with_docx})")


def sync_list(fetcher: PoliteFetcher, max_pages: int | None = None):
    """拉取在线全量列表, 只追加 manifest 中没有的 bbbs

    完整性保证: 终止条件用 "已抓完的条数 >= total" 判断, 且必须 > (不是 >= 提前退出)。
    在线 29,692 = 297 页 (最后一页 92 条), 若用 page*PAGE_SIZE >= total 判断会提前 1 页退出。
    """
    m = load_manifest()
    payload = dict(SEARCH_PAYLOAD)
    payload["pageSize"] = PAGE_SIZE
    page, total_new, total = 1, 0, 0
    while True:
        payload["pageNum"] = page
        data = fetcher.post_json(SEARCH_URL, payload, headers=_headers())
        rows = data.get("rows", [])
        total = data.get("total", 0)
        fetched = len(rows)
        fresh = [{
            "bbbs": r["bbbs"], "title": r.get("title"), "flxz": r.get("flxz"),
            "zdjgName": r.get("zdjgName"), "gbrq": r.get("gbrq"),
            "sxrq": r.get("sxrq"), "sxx": r.get("sxx"), "source": "online",
        } for r in rows if r["bbbs"] not in m]
        append_manifest(fresh)
        for r in fresh:
            m[r["bbbs"]] = r
        total_new += len(fresh)
        print(f"页{page}: 本页{fetched}条 新增 {len(fresh)} (累计新增 {total_new} / 在线总量 {total})")
        if fetched == 0:
            if page * PAGE_SIZE < total:
                # 未覆盖 total 就空页: 瞬时空响应可能, 记录并继续 (下次 sync 会重试该页)
                log.error("页%d空响应但total=%d未覆盖, 继续下一页 (幂等, 下次补齐)", page, total)
            else:
                break  # 已覆盖全部, 正常结束
        # 已抓到的条数是否覆盖全部: 用 (已抓页数*每页数) >= total 判断, 而非 page*size
        # 因为 page 是"下一轮要抓的页码", 提前判断会漏最后一页 (非整页时)
        if page * PAGE_SIZE >= total:
            break
        page += 1
        if max_pages and page > max_pages:
            break
    print(f"同步完成: 新增 {total_new} 条, 在线总量 {total}, manifest 现有 {len(m)} 条")


def diff() -> dict:
    """差集: 新增(在线有本地无) / 缺docx / 完整"""
    m = load_manifest()
    local_bbbs = {v["bbbs"] for v in m.values() if v["source"] == "local"}
    new_laws = [v for v in m.values() if v["source"] == "online" and v["bbbs"] not in local_bbbs]
    missing_docx = [v for v in m.values() if not has_docx(v["bbbs"])]
    return {"total_manifest": len(m), "new_laws": len(new_laws),
            "missing_docx": len(missing_docx), "complete": len(m) - len(missing_docx)}


def download_new(fetcher: PoliteFetcher, limit: int | None = None):
    """下载缺失 docx (断点续传: 已有文件跳过), 下载间隔 8-15s"""
    m = load_manifest()
    todo = [v for v in m.values() if not has_docx(v["bbbs"])]
    print(f"待下载: {len(todo)} (本次上限 {limit or '无'})")
    DOCX_DIR.mkdir(parents=True, exist_ok=True)
    done = 0
    for v in todo[:limit]:
        try:
            r = fetcher.get_json(f"{DL_URL}?format=docx&bbbs={v['bbbs']}",
                                 headers={"Referer": BASE + "/"})
            url = (r.get("data") or {}).get("url")
            if not url:
                log.error("无下载URL: %s %s", v["bbbs"], v.get("title"))
                continue
            content = fetcher.get_bytes(url)
            fmt = detect_format(content[:8])
            if fmt == "unknown":
                log.warning("非Word文件(魔数异常): %s %d字节", v["bbbs"], len(content))
                continue
            ext = ".doc" if fmt == "doc" else ".docx"
            (DOCX_DIR / f"{v['bbbs']}{ext}").write_bytes(content)
            done += 1
            print(f"下载 {done}: {v['bbbs']} {v.get('title', '')[:30]}")
        except Exception as e:
            log.error("下载失败 %s: %s", v["bbbs"], e)
        time.sleep(random.uniform(2, 3))   # CDN 文件分发通道, 2-3s 足够温和 (API 通道仍由基座 1.5s 限速)
    print(f"完成 {done} 份")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["build-local", "sync", "diff", "download"])
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    fetcher = PoliteFetcher("flk_npc")
    if args.action == "build-local":
        build_local_manifest()
    elif args.action == "sync":
        sync_list(fetcher, args.max_pages)
    elif args.action == "diff":
        d = diff()
        print(json.dumps(d, ensure_ascii=False, indent=2))
    elif args.action == "download":
        download_new(fetcher, args.limit)


if __name__ == "__main__":
    main()
