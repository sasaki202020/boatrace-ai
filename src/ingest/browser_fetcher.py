from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; boatrace-ai-mvp/1.0; +https://www.boatrace.jp/)"


@dataclass(frozen=True)
class BrowserFetchResult:
    url: str
    html: str
    fetch_status: str
    source: str


def fetch_html_with_browser(url: str, *, timeout: float = 20.0, output_path: Path | None = None) -> BrowserFetchResult:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        session = requests.Session()
        session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        html = response.text
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(html, encoding="utf-8")
        return BrowserFetchResult(url=url, html=html, fetch_status="live", source="requests_fallback")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
        html = page.content()
        browser.close()
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(html, encoding="utf-8")
        return BrowserFetchResult(url=url, html=html, fetch_status="live", source="playwright")
