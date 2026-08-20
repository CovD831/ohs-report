"""温和抓取公共基座 — 所有抓取器的统一网络层

抓取纪律 (用户明确要求, 全局强制):
- 目标均为国家/政府平台, 禁止暴力爬取
- 异常 → 指数退避重试, 上限 3 次 (2s → 4s → 8s)
- 请求间隔限速 (默认 ≥1.5s), 礼貌 UA
- 失败留痕 raw/<source_id>/errors.jsonl, 不静默不无限重试
"""
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("acquire")

DEFAULT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MAX_RETRIES = 3          # 指数退避重试上限 (三次)
BASE_BACKOFF = 2.0       # 2s → 4s → 8s
DEFAULT_INTERVAL = 1.5   # 两次请求最小间隔 (秒)
DEFAULT_TIMEOUT = 25


class PoliteFetcher:
    """带指数退避与限速的 HTTP 抓取器"""

    def __init__(self, source_id: str, interval: float = DEFAULT_INTERVAL,
                 max_retries: int = MAX_RETRIES, timeout: int = DEFAULT_TIMEOUT,
                 proxy: str | None = None, raw_dir: Path | None = None):
        self.source_id = source_id
        self.interval = interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_ts = 0.0
        self._raw_dir = raw_dir or (Path(__file__).resolve().parent / "raw" / source_id)
        handlers = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        self._opener = urllib.request.build_opener(*handlers)

    # ---------- 公共请求 ----------
    def get(self, url: str, headers: dict | None = None) -> str:
        """GET 并返回文本"""
        return self._request(url, headers)

    def get_bytes(self, url: str, headers: dict | None = None) -> bytes:
        """GET 并返回原始字节 (docx/pdf 等二进制)"""
        return self._request(url, headers, binary=True)

    def get_json(self, url: str, headers: dict | None = None) -> dict:
        """GET 并解析 JSON"""
        return json.loads(self.get(url, headers))

    def post_json(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        """POST JSON 并解析响应"""
        body = json.dumps(payload).encode("utf-8")
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        text = self._request(url, headers=hdrs, data=body, method="POST")
        return json.loads(text)

    # ---------- 核心: 指数退避重试 ----------
    def _request(self, url: str, headers: dict | None = None,
                 data: bytes | None = None, method: str = "GET",
                 binary: bool = False) -> str | bytes:
        self._throttle()
        hdrs = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs, data=data, method=method)
        for attempt in range(self.max_retries + 1):
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return raw if binary else raw.decode("utf-8", errors="replace")
            except (urllib.error.URLError, urllib.error.HTTPError,
                    TimeoutError, ConnectionError) as e:
                wait = BASE_BACKOFF * (2 ** attempt)   # 2s, 4s, 8s
                if attempt < self.max_retries:
                    log.warning("[%s] 第%d次失败 (%s), 指数退避 %.0fs 后重试",
                                self.source_id, attempt + 1, e, wait)
                    time.sleep(wait)
                else:
                    self._record_error(url, e)
                    raise

    # ---------- 限速与留痕 ----------
    def _throttle(self):
        gap = time.time() - self._last_ts
        if gap < self.interval:
            time.sleep(self.interval - gap)
        self._last_ts = time.time()

    def _record_error(self, url: str, err: Exception):
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        with open(self._raw_dir / "errors.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "url": url, "error": str(err),
            }, ensure_ascii=False) + "\n")
        log.error("[%s] 重试%d次仍失败, 已留痕 errors.jsonl: %s", self.source_id, self.max_retries, url)
