"""Google Patents XHR 기반 다운로드 유틸리티

Playwright로 초기 접속(세션/쿠키/헤더/보안 토큰 확보) 후,
해당 상태를 httpx로 재사용하여 XHR 검색 및 특허 PDF를 다운로드합니다.

사용 예시:
  python /Users/lag/coding/PatentResearch/google_patents_xhr_downloader.py \
    --query "machine learning" \
    --out "/Users/lag/coding/PatentResearch/test_downloads" \
    --max-results 3 \
    --headless

참고:
- XHR 엔드포인트(`/xhr/query`) 요청을 실제 브라우저에서 한 번 발생시켜
  필요한 헤더(`x-same-domain`, `user-agent`, `accept-language`, `cookie` 등)와
  URL 파라미터(내부 토큰 포함 가능)를 캡처합니다.
- 캡처된 값으로 httpx(HTTP/2) 클라이언트를 구성해 동일한 요청을 재현합니다.
- 검색 결과에서 특허 상세로 이동해 `meta[name="citation_pdf_url"]`로 PDF 직링크를 추출합니다.

주의: 본 스크립트는 구글 사이트 변경에 민감합니다. 차단(403/429) 시 진단 아티팩트를
      활성화(`--diagnostics`)해 캡처된 요청을 확인하고 지연(`--delay`)을 늘려주세요.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright


GOOGLE_PATENTS_ORIGIN = "https://patents.google.com"


@dataclass
class CapturedRequest:
    """브라우저에서 실제 발생한 XHR 요청 정보.

    Attributes:
        url: 호출된 전체 URL (쿼리 파라미터 포함)
        method: HTTP 메서드
        headers: 요청 헤더(중복 키는 마지막 값 유지)
        referer: 참조 페이지 URL
    """

    url: str
    method: str
    headers: Dict[str, str]
    referer: Optional[str]


@dataclass
class PatentSummary:
    """검색 결과에서 추출된 특허 요약 정보."""

    title: str
    publication_number: Optional[str]
    detail_url: str


class GooglePatentsXHRDownloader:
    """Google Patents에서 XHR로 검색하고 PDF를 다운로드하는 다운로더."""

    def __init__(
        self,
        download_dir: Path,
        headless: bool = True,
        timeout: int = 30,
        delay: float = 1.0,
        diagnostics: bool = False,
    ) -> None:
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.timeout = timeout
        self.delay = delay
        self.diagnostics = diagnostics
        self.diagnostics_dir = self.download_dir / "diagnostics"
        if self.diagnostics:
            self.diagnostics_dir.mkdir(parents=True, exist_ok=True)

    async def _launch_browser(self) -> Tuple[Browser, BrowserContext, Page]:
        """브라우저/컨텍스트/페이지를 초기화한다."""

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=self.headless)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(self.timeout * 1000)
        return browser, context, page

    async def _try_accept_consent(self, page: Page) -> None:
        """구글 동의(Consent) 배너가 있는 경우 최대한 닫는다."""

        async def try_click_targets(target_page: Page) -> bool:
            selectors = [
                "button:has-text('I agree')",
                "button:has-text('Agree')",
                "button:has-text('Accept all')",
                "button[aria-label*='Agree']",
                "#L2AGLb",
                "#introAgreeButton",
                "form[action*='consent'] button[type='submit']",
                "[role='dialog'] button:has-text('Accept')",
                "[role='dialog'] button:has-text('동의')",
            ]
            for sel in selectors:
                try:
                    count = await target_page.locator(sel).count()
                    if count:
                        await target_page.locator(sel).first.click()
                        await target_page.wait_for_timeout(500)
                        return True
                except Exception:
                    continue
            return False

        try:
            # 1) 현재 페이지에서 시도
            if await try_click_targets(page):
                return

            # 2) consent iframe 내부에서 시도
            for frame in page.frames:
                try:
                    url = (frame.url or "").lower()
                except Exception:
                    url = ""
                if "consent" in url or "privacy" in url:
                    try:
                        if await try_click_targets(frame):
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    async def _capture_xhr_request(
        self, page: Page, query: str, diag_dir: Optional[Path] = None
    ) -> Tuple[Optional[CapturedRequest], str, Optional[str]]:
        """검색 중 `/xhr/query` 요청을 하나 캡처한다.

        우선 기본 홈에서 입력→엔터로 시도하고, 실패 시 검색 URL로 직접 이동해
        결과 HTML을 확보한다.

        Returns:
            (captured, search_results_html, xhr_response_text)
        """

        captured: Optional[CapturedRequest] = None
        search_results_html: str = ""
        xhr_response_text: Optional[str] = None

        def on_request(request: Any) -> None:
            nonlocal captured
            try:
                url = request.url
                if "/xhr/query" in url:
                    headers: Dict[str, str] = {}
                    for k, v in request.headers.items():
                        headers[k.lower()] = v
                    captured = CapturedRequest(
                        url=url,
                        method=request.method,
                        headers=headers,
                        referer=request.headers.get("referer"),
                    )
            except Exception:
                pass

        page.on("request", on_request)

        # 1차: 홈으로 진입해 입력→엔터
        try:
            await page.goto(GOOGLE_PATENTS_ORIGIN)
            await self._try_accept_consent(page)
            await page.wait_for_load_state("domcontentloaded")

            # 우선 검색 input(id=searchInput) 시도, 없으면 일반 input 사용
            try:
                await page.wait_for_selector("#searchInput", timeout=3000)
                await page.locator("#searchInput").fill(query)
            except Exception:
                await page.wait_for_selector("input")
                await page.locator("input").first.fill(query)
            await page.keyboard.press("Enter")

            try:
                resp = await page.wait_for_response(
                    lambda r: "/xhr/query" in r.url, timeout=self.timeout * 1000
                )
                try:
                    xhr_response_text = await resp.text()
                except Exception:
                    xhr_response_text = None
            except Exception:
                xhr_response_text = None

            # 결과가 올라올 때까지 보조 대기(결과 컨테이너 또는 article)
            try:
                await page.wait_for_selector(
                    "article, state-modifier.result-title, #resultsContainer",
                    timeout=min(15000, self.timeout * 1000),
                )
            except Exception:
                pass

            try:
                await page.wait_for_load_state("networkidle")
            except Exception:
                pass
            await page.wait_for_timeout(800)

            try:
                search_results_html = await page.content()
            except Exception:
                search_results_html = ""
        except Exception:
            search_results_html = ""

        # 2차 폴백: 검색 URL 직접 이동
        if not search_results_html or "<article" not in search_results_html:
            from urllib.parse import quote_plus
            search_url = f"{GOOGLE_PATENTS_ORIGIN}/?q={quote_plus(query)}&hl=en&num=100"
            try:
                await page.goto(search_url)
                await self._try_accept_consent(page)
                try:
                    await page.wait_for_selector(
                        "article, state-modifier.result-title, #resultsContainer",
                        timeout=min(15000, self.timeout * 1000),
                    )
                except Exception:
                    pass
                try:
                    await page.wait_for_load_state("networkidle")
                except Exception:
                    pass
                await page.wait_for_timeout(800)
                search_results_html = await page.content()
            except Exception:
                # 마지막 시도: domcontentloaded 기준으로라도 HTML 확보
                try:
                    await page.wait_for_load_state("domcontentloaded")
                    search_results_html = await page.content()
                except Exception:
                    pass

        # 진단 파일 저장
        if self.diagnostics:
            try:
                target_dir = diag_dir or self.diagnostics_dir
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / "search_results_page.html").write_text(
                    search_results_html or "", encoding="utf-8"
                )
                if xhr_response_text is not None:
                    (target_dir / "xhr_query_response_original.html").write_text(
                        xhr_response_text, encoding="utf-8"
                    )
            except Exception:
                pass

        return captured, search_results_html, xhr_response_text

    # 불용 함수 제거: _build_client_from_context는 사용하지 않으므로 삭제

    @staticmethod
    async def _build_client_with_cookies(
        context: BrowserContext, captured: Optional[CapturedRequest]
    ) -> httpx.AsyncClient:
        """Playwright 컨텍스트의 쿠키/헤더로 httpx.AsyncClient 생성."""

        cookies_list = await context.cookies()
        cookies_jar = httpx.Cookies()
        for c in cookies_list:
            # httpx 쿠키에 도메인/경로 지정
            domain = c.get("domain") or ".google.com"
            path = c.get("path") or "/"
            cookies_jar.set(
                name=c.get("name"),
                value=c.get("value"),
                domain=domain,
                path=path,
            )

        default_headers: Dict[str, str] = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Referer": GOOGLE_PATENTS_ORIGIN + "/",
        }

        if captured:
            # 중요 헤더만 선별 반영
            if ua := captured.headers.get("user-agent"):
                default_headers["User-Agent"] = ua
            if al := captured.headers.get("accept-language"):
                default_headers["Accept-Language"] = al
            if ref := captured.referer:
                default_headers["Referer"] = ref
            # x-same-domain은 종종 요구됨
            if xsd := captured.headers.get("x-same-domain"):
                default_headers["x-same-domain"] = xsd
            else:
                default_headers["x-same-domain"] = "1"

        client = httpx.AsyncClient(
            headers=default_headers,
            cookies=cookies_jar,
            http2=True,
            timeout=httpx.Timeout(30.0),
        )
        return client

    @staticmethod
    def _parse_results_from_html(html: str) -> List[PatentSummary]:
        """검색 결과 페이지(전체 HTML)에서 특허 리스트 파싱."""

        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        results: List[PatentSummary] = []

        for article in soup.select("article"):
            try:
                title_el = article.select_one("h3 a")
                if not title_el:
                    continue
                title = (title_el.get_text() or "").strip()
                href = title_el.get("href") or ""
                if href and not href.startswith("http"):
                    detail_url = GOOGLE_PATENTS_ORIGIN + href
                else:
                    detail_url = href

                pub = None
                h4_el = article.select_one("h4")
                if h4_el:
                    # 종종 h4 내부 a의 텍스트가 공보번호
                    pub_link = h4_el.select_one("a")
                    if pub_link and pub_link.get_text():
                        pub = pub_link.get_text().strip()

                if title and detail_url:
                    results.append(
                        PatentSummary(
                            title=title, publication_number=pub, detail_url=detail_url
                        ))
            except Exception:
                continue

        return results

    async def _parse_results_from_dom(self, page: Page) -> List[PatentSummary]:
        """Playwright DOM API로 검색 결과를 직접 파싱한다.

        XHR 응답 또는 정적 HTML 파싱이 실패하는 경우의 마지막 폴백.
        """
        results: List[PatentSummary] = []
        try:
            # 결과가 느리게 나타나는 경우 대비
            await page.wait_for_selector("article", timeout=self.timeout * 1000)
        except Exception:
            # article이 없으면 빈 리스트
            return results

        try:
            articles = await page.query_selector_all("article")
        except Exception:
            articles = []

        for article in articles:
            try:
                title_el = await article.query_selector("h3 a")
                if not title_el:
                    continue
                raw_title = (await title_el.inner_text()) or ""
                title = raw_title.strip()
                href = await title_el.get_attribute("href")
                if not href:
                    continue
                if not href.startswith("http"):
                    detail_url = GOOGLE_PATENTS_ORIGIN + href
                else:
                    detail_url = href

                pub = None
                h4_link = await article.query_selector("h4 a")
                if h4_link:
                    try:
                        pub_text = await h4_link.inner_text()
                        if pub_text:
                            pub = pub_text.strip()
                    except Exception:
                        pub = None

                results.append(
                    PatentSummary(
                        title=title or (pub or ""),
                        publication_number=pub,
                        detail_url=detail_url,
                    ))
            except Exception:
                continue

        return results

    @staticmethod
    def _parse_results_from_xhr(content: str) -> List[PatentSummary]:
        """/xhr/query 응답 파싱.

        - JSON 본문: cluster → result[] → id, patent.publication_number, patent.title 사용
        - HTML fragment 본문: 기존 HTML 파서 재사용
        """

        if not content:
            return []

        # 1) JSON 응답 시
        try:
            if content.strip().startswith("{"):
                data = json.loads(content)
                results_node = data.get("results") or {}
                clusters = results_node.get("cluster") or []
                results: List[PatentSummary] = []
                for cluster in clusters:
                    for item in cluster.get("result", []):
                        item_id = item.get("id")  # 예: "patent/US11056471B2/en"
                        pat = item.get("patent") or {}
                        pub = pat.get("publication_number")
                        raw_title = pat.get("title") or ""
                        # 제목에 포함된 태그 제거
                        title = BeautifulSoup(raw_title, "lxml").get_text(" ").strip()

                        if not item_id:
                            continue
                        detail_url = GOOGLE_PATENTS_ORIGIN + "/" + item_id.lstrip("/")

                        results.append(
                            PatentSummary(
                                title=title or (pub or ""),
                                publication_number=pub,
                                detail_url=detail_url,
                            ))
                return results
        except Exception:
            # JSON 파싱 실패 시 HTML 로직으로 폴백
            pass

        # 2) HTML fragment 폴백
        return GooglePatentsXHRDownloader._parse_results_from_html(content)

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        clean = re.sub(r"[<>:\"/\\|?*]", "_", filename)
        return clean[:200]

    async def _fetch_detail_and_pdf(
        self, client: httpx.AsyncClient, detail_url: str
    ) -> Optional[str]:
        """특허 상세 페이지를 가져와 PDF URL을 추출한다."""

        r = await client.get(detail_url)
        if r.status_code >= 400:
            logger.warning(f"detail GET {detail_url} -> {r.status_code}")
            return None

        html = r.text
        soup = BeautifulSoup(html, "lxml")

        # 1순위: citation_pdf_url
        meta = soup.select_one('meta[name="citation_pdf_url"]')
        if meta and meta.get("content"):
            return str(meta.get("content"))

        # 2순위: a[href*='.pdf'] 링크
        a = soup.select_one("a[href$='.pdf'], a[href*='.pdf?']")
        if a and a.get("href"):
            href = a.get("href")
            if href.startswith("http"):
                return href
            return GOOGLE_PATENTS_ORIGIN + href

        return None

    async def _download_pdf(
        self, client: httpx.AsyncClient, pdf_url: str, target_path: Path, referer: str
    ) -> bool:
        """PDF를 스트리밍으로 저장한다."""

        headers = {"Referer": referer, "Accept": "application/pdf,*/*"}
        try:
            async with client.stream("GET", pdf_url, headers=headers) as resp:
                if resp.status_code >= 400:
                    logger.error(
                        f"PDF GET {pdf_url} -> {resp.status_code}"
                    )
                    return False
                with target_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
            return True
        except Exception as exc:
            logger.error(f"PDF download error: {exc}")
            return False

    async def search_and_download(
        self, query: str, max_results: int
    ) -> List[Path]:
        """단일 쿼리로 검색하고 상위 N개의 PDF를 다운로드한다.

        매 쿼리마다 새로운 브라우저/컨텍스트를 생성하여 보안 토큰을 재캡처한다.
        """

        # 쿼리 별 진단 폴더
        diag_dir: Optional[Path] = None
        if self.diagnostics:
            qslug = self._slugify_for_path(query)
            diag_dir = self.diagnostics_dir / qslug

        async with async_playwright() as p:  # 쿼리마다 새로운 Playwright 세션
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page = await context.new_page()
            page.set_default_timeout(self.timeout * 1000)
            # per-query log 파일 추가 (동일 폴더 내)
            log_sink_id: Optional[int] = None
            try:
                log_path = (self.download_dir / "run.log").resolve()
                # 존재할 경우 이어쓰기
                log_sink_id = logger.add(str(log_path), level="INFO")
            except Exception:
                log_sink_id = None

            try:
                captured, page_html, xhr_text = await self._capture_xhr_request(
                    page, query, diag_dir=diag_dir
                )

                # httpx 클라이언트 구성
                client = await self._build_client_with_cookies(context, captured)

                # XHR 우선 시도: 먼저 브라우저에서 받은 원문 XHR 응답으로 파싱
                results: List[PatentSummary] = []
                if xhr_text:
                    results = self._parse_results_from_xhr(xhr_text)

                # 필요 시 캡처된 요청으로 httpx 재현
                if not results and captured and "/xhr/query" in captured.url:
                    logger.info("Replaying captured XHR query via httpx ...")
                    try:
                        # 캡처된 헤더 중 httpx로 전달해도 안전한 헤더만 선별 전달
                        banned = {
                            "cookie",
                            "host",
                            "authority",
                            "method",
                            "path",
                            "scheme",
                            "content-length",
                            "origin",
                        }
                        replay_headers = {
                            k.title(): v
                            for k, v in captured.headers.items()
                            if k.lower() not in banned
                        }
                        # 최소 요구 헤더 보강
                        replay_headers.setdefault("X-Same-Domain", "1")
                        replay_headers.setdefault("Referer", captured.referer or GOOGLE_PATENTS_ORIGIN + "/")

                        resp = await client.get(captured.url, headers=replay_headers)
                        if self.diagnostics and diag_dir is not None:
                            (diag_dir / "xhr_query_response.html").write_text(
                                resp.text, encoding="utf-8"
                            )
                            (diag_dir / "captured_request.json").write_text(
                                json.dumps(
                                    {
                                        "url": captured.url,
                                        "headers": captured.headers,
                                        "referer": captured.referer,
                                    },
                                    indent=2,
                                    ensure_ascii=False,
                                ),
                                encoding="utf-8",
                            )

                        if resp.status_code < 400 and resp.text:
                            results = self._parse_results_from_xhr(resp.text)
                        else:
                            logger.warning(
                                f"XHR {resp.status_code}; falling back to page HTML parse"
                            )
                    except Exception as exc:
                        logger.warning(f"XHR replay failed: {exc}")

                # 폴백: Playwright로 확보한 페이지 전체 HTML 파싱
                if not results:
                    results = self._parse_results_from_html(page_html)

                # 추가 폴백: 검색 URL로 직접 이동하여 다시 파싱
                if not results:
                    from urllib.parse import quote_plus
                    search_url = GOOGLE_PATENTS_ORIGIN + "/?q=" + quote_plus(query) + "&hl=en&num=100"
                    try:
                        await page.goto(search_url)
                        await page.wait_for_load_state("domcontentloaded")
                        try:
                            await page.wait_for_load_state("networkidle")
                        except Exception:
                            pass
                        await page.wait_for_timeout(800)
                        html2 = await page.content()
                        results = self._parse_results_from_html(html2)
                    except Exception:
                        pass

                # 최종 폴백: DOM 직접 파싱
                if not results:
                    try:
                        results = await self._parse_results_from_dom(page)
                    except Exception:
                        results = []

                # 추가 페이징: 더 많은 결과가 필요하면 다음 페이지를 따라가며 수집
                if len(results) < max_results:
                    seen: set[str] = {r.detail_url for r in results}
                    while len(results) < max_results:
                        try:
                            # next 링크 탐색
                            next_locator = page.locator("a[rel='next'], a[aria-label='Next']").first
                            if not await next_locator.count():
                                break
                            href = await next_locator.get_attribute("href")
                            if not href:
                                break
                            next_url = href if href.startswith("http") else GOOGLE_PATENTS_ORIGIN + href
                            await page.goto(next_url)
                            try:
                                await page.wait_for_load_state("networkidle")
                            except Exception:
                                pass
                            await page.wait_for_timeout(600)
                            html_more = await page.content()
                            more = self._parse_results_from_html(html_more)
                            if not more:
                                # DOM 폴백
                                try:
                                    more = await self._parse_results_from_dom(page)
                                except Exception:
                                    more = []
                            # dedup 및 추가
                            for item in more:
                                if item.detail_url not in seen:
                                    results.append(item)
                                    seen.add(item.detail_url)
                                    if len(results) >= max_results:
                                        break
                            if not await next_locator.count():
                                break
                        except Exception:
                            break

                if not results:
                    logger.warning("검색 결과를 찾지 못했습니다.")
                    return []

                results = results[:max_results]
                logger.info(f"Parsed {len(results)} results")

                saved: List[Path] = []
                saved_meta: List[Dict[str, Any]] = []
                for idx, item in enumerate(results, 1):
                    await asyncio.sleep(self.delay)
                    logger.info(
                        f"[{idx}/{len(results)}] {item.publication_number} → detail"
                    )

                    pdf_url = await self._fetch_detail_and_pdf(client, item.detail_url)
                    if not pdf_url:
                        logger.warning("PDF URL을 찾지 못했습니다. 건너뜁니다.")
                        continue

                    base_name = item.publication_number or f"Patent_{idx}"
                    base_name = self._sanitize_filename(base_name)
                    out_path = self.download_dir / f"{base_name}.pdf"

                    ok = await self._download_pdf(
                        client, pdf_url, out_path, referer=item.detail_url
                    )
                    if ok:
                        size = out_path.stat().st_size
                        logger.info(f"✅ Saved {out_path.name} ({size:,} bytes)")
                        saved.append(out_path)
                        saved_meta.append({
                            "publication_number": item.publication_number,
                            "title": item.title,
                            "detail_url": item.detail_url,
                            "pdf_url": pdf_url,
                            "saved_path": str(out_path.resolve()),
                            "size_bytes": size,
                        })
                    else:
                        logger.error(f"❌ Failed to save {out_path.name}")

                # 쿼리 메타데이터 저장
                try:
                    meta = {
                        "query": query,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "download_dir": str(self.download_dir.resolve()),
                        "count": len(saved_meta),
                        "items": saved_meta,
                    }
                    (self.download_dir / "query.json").write_text(
                        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    (self.download_dir / "query.txt").write_text(query, encoding="utf-8")
                except Exception:
                    pass

                return saved
            finally:
                # httpx 클라이언트 종료
                try:
                    if 'client' in locals():
                        await client.aclose()
                except Exception:
                    pass
                await context.close()
                await browser.close()
                # 로그 sink 제거
                if log_sink_id is not None:
                    try:
                        logger.remove(log_sink_id)
                    except Exception:
                        pass

    async def search_and_download_many(
        self, queries: List[str], max_results: int
    ) -> Dict[str, List[Path]]:
        """여러 쿼리를 순차 처리. 각 쿼리마다 보안 토큰을 재캡처한다."""

        results: Dict[str, List[Path]] = {}
        for i, q in enumerate(queries, 1):
            logger.info(f"◇ Query {i}/{len(queries)}: {q}")
            try:
                saved = await self.search_and_download(q, max_results)
                results[q] = saved
            except Exception as exc:
                logger.error(f"Query failed: {q} ({exc})")
                results[q] = []
            if i < len(queries):
                await asyncio.sleep(max(self.delay, 0.5))
        return results

    @staticmethod
    def _slugify_for_path(text: str) -> str:
        slug = re.sub(r"\s+", "_", text.strip())
        slug = re.sub(r"[^A-Za-z0-9_\-]+", "", slug)
        return slug[:50] or "query"


def _build_cli_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        description="Google Patents XHR + httpx 기반 PDF 다운로더"
    )
    parser.add_argument(
        "--query",
        action="append",
        help="검색어 (여러 번 지정 가능)",
    )
    parser.add_argument(
        "--query-file",
        help="쿼리 목록 파일(줄단위)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="PDF 저장 디렉터리 절대경로",
    )
    parser.add_argument(
        "--max-results", type=int, default=5, help="최대 다운로드 수"
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, help="다운로드 사이 대기(초)"
    )
    parser.add_argument(
        "--timeout", type=int, default=30, help="기본 타임아웃(초)"
    )
    parser.add_argument(
        "--headless", action="store_true", help="브라우저 헤드리스 모드"
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="XHR/HTML 진단 아티팩트 저장",
    )
    return parser


async def _amain(argv: List[str]) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    downloader = GooglePatentsXHRDownloader(
        download_dir=out_dir,
        headless=args.headless,
        timeout=args.timeout,
        delay=args.delay,
        diagnostics=args.diagnostics,
    )

    # 쿼리 수집
    queries: List[str] = []
    if args.query:
        queries.extend([q for q in args.query if q and q.strip()])
    if args.query_file:
        qpath = Path(args.query_file).expanduser().resolve()
        if not qpath.exists():
            raise SystemExit(f"query-file not found: {qpath}")
        for line in qpath.read_text(encoding="utf-8").splitlines():
            if line.strip():
                queries.append(line.strip())

    if not queries:
        raise SystemExit("--query 또는 --query-file 중 하나는 필요합니다.")

    if len(queries) == 1:
        saved = await downloader.search_and_download(
            query=queries[0], max_results=args.max_results
        )
        logger.info(f"총 {len(saved)}개 파일 저장 완료: {out_dir}")
    else:
        all_saved = await downloader.search_and_download_many(
            queries=queries, max_results=args.max_results
        )
        total = sum(len(v) for v in all_saved.values())
        logger.info(f"총 {total}개 파일 저장 완료 ({len(queries)}개 쿼리): {out_dir}")
    return 0


def main() -> None:
    try:
        rc = asyncio.run(_amain(sys.argv[1:]))
        raise SystemExit(rc)
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
