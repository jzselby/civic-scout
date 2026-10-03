"""One shared HTTP session: a clear User-Agent, retries, and polite pacing."""

from __future__ import annotations

import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = "civic-scout/0.1 (newsroom public-records monitor; +https://github.com/jzselby/civic-scout)"
# Seconds between requests to the same site, so a run never hammers a government server.
MIN_INTERVAL = 1.0


class Http:
    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        retry = Retry(total=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET", "HEAD"))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self._last: dict[str, float] = {}

    def _pace(self, url: str) -> None:
        host = requests.utils.urlparse(url).netloc
        wait = self._last.get(host, 0) + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def get(self, url: str, **kwargs) -> requests.Response:
        self._pace(url)
        log.debug("GET %s", url)
        resp = self.session.get(url, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        return resp

    def text(self, url: str, **kwargs) -> str:
        resp = self.get(url, **kwargs)
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        return resp.text

    def content(self, url: str, max_bytes: int = 20_000_000) -> bytes:
        resp = self.get(url, stream=True)
        chunks, size = [], 0
        for chunk in resp.iter_content(65536):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError(f"{url} is larger than {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
