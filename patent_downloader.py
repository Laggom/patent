"""Google Patents XHR 기반 다운로드 유틸리티

Playwright로 초기 접속(세션/쿠키/헤더/보안 토큰 확보) 후,
해당 상태를 httpx로 재사용하여 XHR 검색 및 특허 PDF를 다운로드합니다.

사용 예시:
  # PDF 다운로드
  python google_patents_xhr_downloader.py \
    --query "machine learning" \
    --out "./downloads" \
    --max-results 3 \
    --headless
    
  # 검색 결과 개수만 확인 (PDF 다운로드 건너뛰기)
  python google_patents_xhr_downloader.py \
    --query "CO2 membrane inventor:\"Haiqing Lin\"" \
    --out "./downloads" \
    --count-only \
    --headless
    
  # 여러 쿼리 검색 결과 개수 확인
  python google_patents_xhr_downloader.py \
    --query "quantum computing" \
    --query "machine learning" \
    --out "./downloads" \
    --count-only \
    --headless

참고:
- XHR 엔드포인트(`/xhr/query`) 요청을 실제 브라우저에서 한 번 발생시켜
  필요한 헤더(`x-same-domain`, `user-agent`, `accept-language`, `cookie` 등)와
  URL 파라미터(내부 토큰 포함 가능)를 캡처합니다.
- 캡처된 값으로 httpx(HTTP/2) 클라이언트를 구성해 동일한 요청을 재현합니다.
- 검색 결과에서 특허 상세로 이동해 `meta[name="citation_pdf_url"]`로 PDF 직링크를 추출합니다.

주의: 본 스크립트는 구글 사이트 변경에 민감합니다. 차단(403/429) 시 진단 아티팩트를
      활성화(`--diagnostics`)해 캡처된 요청을 확인하고 지연(`--delay`)을 늘려주세요.

Google Patents 검색 문법 가이드 (생성형 AI용 체계적 매뉴얼):

=== 기본 구문 구조 ===

1. 연산자 우선순위 (높음 → 낮음):
   1순위: 괄호 ()
   2순위: 근접연산자 (NEAR, SAME, WITH, ADJ)  
   3순위: NOT (-)
   4순위: AND (기본값, 공백도 AND로 처리)
   5순위: OR

2. 기본 논리:
   - 기본 연산자: AND (공백으로도 표현)
   - 결합성: 좌결합 (A OR B C → (A OR B) AND C)
   - 그룹핑: 괄호 () 사용 필수

=== 검색 요소별 문법 ===

A. 키워드 검색:
   정확구문: "machine learning" (따옴표 필수)
   제외: -"deep learning" (NOT 연산)
   와일드카드: learn*, networ?, optim# (따옴표 외부에만)
   
B. 필드 제한:
   제목: TI=(keyword)
   초록: AB=(keyword)  
   청구항: CL=(keyword)
   전체텍스트: keyword (필드 미지정시 제목+초록+청구항+본문 검색)

C. 분류 코드:
   정확매칭: CPC=G06N3/08
   하위포함: CPC=G06N3
   
D. 메타데이터:
   출원인: assignee:"Company Name"
   발명자: inventor:"Last Name"
   날짜: after:2020, before:2024
   국가: country:US, country:(US OR EP)
   상태: status:grant, status:application

E. 근접 연산자 (랭킹용, 필터링 아님):
   거리지정: NEAR/5 (5단어 이내, 순서무관)
   인접: ADJ/2 (2단어 이내, 순서유지)
   문단내: SAME (200단어 이내)
   문장내: WITH (20단어 이내)

=== 올바른 구문 패턴 ===

✅ 단순 조합:
   machine learning AND neural network
   TI=(artificial intelligence) AND assignee:"Google"
   
✅ 근접 연산자:
   machine NEAR/3 learning SAME neural
   TI=(quantum ADJ/1 computing)
   
✅ 복잡한 그룹핑:
   (TI=(quantum) OR AB=(quantum)) AND CL=(computing)
   ((A NEAR/3 B) AND (C ADJ D)) OR (E WITH F)

✅ 메타데이터 조합:
   "machine learning" AND assignee:"Google" AND after:2020 AND CPC=G06N

=== 절대 금지 패턴 ===

❌ 연산자 혼용:
   word1 AND NEAR/5 word2    // AND 뒤에 근접연산자
   word1 OR SAME word2       // OR 뒤에 근접연산자
   
❌ 괄호 오류:
   ((((word1                 // 미매칭 괄호
   TI=(word1] AND [AB=word2   // 대괄호 혼용
   
❌ 인용부호 내 특수문자:
   "machine learn*"          // 인용부호 안에 와일드카드
   "word1 NEAR/3 word2"      // 인용부호 안에 연산자

=== 검색식 작성 알고리즘 ===

1. 핵심 키워드 식별 → 정확구문은 따옴표, 변형어는 와일드카드
2. 중요도별 필드 선택 → 제목 > 초록 > 청구항 순
3. 메타데이터 조건 추가 → 출원인, 날짜, 분류코드
4. 근접도 지정 → 관련성 높은 단어간 NEAR/ADJ 적용  
5. 논리 구조 설계 → 괄호로 명확한 그룹핑
6. 구문 검증 → 괄호 매칭, 연산자 조합 확인

=== 실용적 한계 (테스트 검증됨) ===

제한 없음: AND 조건 개수 (13개+ 성공)
제한 없음: 근접연산자 중첩 (6중+ 성공)  
제한 없음: 필드 조합 (TI+AB+CL+메타데이터)
제한 없음: 복잡한 중첩 괄호 구조

★ 검색 실패 규칙 (2025년 체계적 테스트 결과):

검색 실패 유형:
1. 구문 오류 → {"results":{"user_error":"invalid argument: query syntax error"}}
2. 0건 결과 → {"results":{"total_num_results":0, ...}} (정상 응답)

❌ 절대 안되는 구문 오류 패턴:
- 잘못된 연산자 조합: "word1 AND NEAR/5 word2", "word1 OR SAME word2"
- 괄호 매칭 오류: "((((word1", "word1))))"
- 대괄호 혼용: "TI=(word1] AND [AB=word2"
- 인용부호 내 특수문자: "training data*", "word1 NEAR/3 word2", "machine learn*"

✅ 올바른 구문 패턴:
- 근접연산자 연속: "word1 NEAR/5 word2 SAME word3"
- 괄호 그룹핑: "(word1 NEAR/3 word2) AND (word3 ADJ word4)"
- 정확한 인용부호: "exact phrase", pretraining OR "training data"
- 와일드카드: word* AND learn* (인용부호 외부에서만)

실제 한계 (테스트 완료):
- AND 조건 개수: 13개+ 성공 (무제한)
- 근접연산자 중첩: 6중+ 성공 (NEAR+SAME+WITH+ADJ+NEAR+NEAR)
- 필드 조합: TI+AB+CL+메타데이터 성공 (무제한)
- 복잡한 중첩 괄호: ((A AND B) OR (C WITH D)) AND E 성공
- 복잡도는 문제없음, 오직 구문 오류만이 실패 원인

오류 vs 0건 구분법:
if "user_error" in response: 
    # 구문 오류 → 쿼리 문법 수정 필요
elif response["results"]["total_num_results"] == 0:
    # 0건 결과 → 조건 완화 고려 (정상 동작)

결론: Google Patents는 복잡도에 매우 관대하지만 구문 오류에 매우 엄격함

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

    @staticmethod
    def _normalize_query_string(query: str) -> str:
        """사용자 쿼리를 Google Patents의 표준 구문으로 정규화한다.

        Google Patents는 명시적 필드 접두사(abstract:/title:/claims:)보다
        약어 구문(AB=/TI=/CL=)을 더 안정적으로 처리합니다.
        DE=(Description) 필드는 연산자 동작이 불안정하여 변환하지 않습니다.

        정규화 규칙:
        1. 필드 약어 변환 (대소문자 무관):
           - abstract: → AB=, title: → TI=, claims: → CL=
        2. 메타데이터 필드 정규화:
           - assignee: → assignee:, inventor: → inventor:
           - before:/after: → before:/after:, country: → country:
           - status: → status:, language: → language:

        이미 표준 구문을 사용 중이면 변환하지 않습니다.
        """

        # 필드 약어 치환
        field_replacements = {
            r"\babstract:\s*": "AB=",
            r"\btitle:\s*": "TI=", 
            r"\bclaims:\s*": "CL=",
        }
        
        # 메타데이터 필드 정규화 (일관성을 위해)
        metadata_replacements = {
            r"\bassignee\s*=\s*": "assignee:",
            r"\binventor\s*=\s*": "inventor:",
            r"\bcountry\s*=\s*": "country:",
            r"\bstatus\s*=\s*": "status:",
            r"\blanguage\s*=\s*": "language:",
        }

        normalized = query
        
        # 필드 약어 치환 적용
        for pattern, repl in field_replacements.items():
            normalized = re.sub(pattern, repl, normalized, flags=re.IGNORECASE)
            
        # 메타데이터 필드 정규화 적용
        for pattern, repl in metadata_replacements.items():
            normalized = re.sub(pattern, repl, normalized, flags=re.IGNORECASE)
            
        return normalized

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

        # 필드 별칭 정규화(abstract:/title:/claims: → AB=/TI=/CL=)
        effective_query = self._normalize_query_string(query)

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
                await page.locator("#searchInput").fill(effective_query)
            except Exception:
                await page.wait_for_selector("input")
                await page.locator("input").first.fill(effective_query)
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
                await page.wait_for_load_state("networkidle", timeout=1500)
            except Exception:
                pass
            await page.wait_for_timeout(250)

            try:
                search_results_html = await page.content()
            except Exception:
                search_results_html = ""
        except Exception:
            search_results_html = ""

        # 2차 폴백: 검색 URL 직접 이동
        if not search_results_html or "<article" not in search_results_html:
            from urllib.parse import quote_plus
            search_url = f"{GOOGLE_PATENTS_ORIGIN}/?q={quote_plus(effective_query)}&hl=en&num=100"
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
                    await page.wait_for_load_state("networkidle", timeout=1500)
                except Exception:
                    pass
                await page.wait_for_timeout(250)
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
    def _parse_results_from_xhr(content: str) -> Tuple[List[PatentSummary], Optional[int]]:
        """/xhr/query 응답 파싱.

        - JSON 본문: cluster → result[] → id, patent.publication_number, patent.title 사용
        - HTML fragment 본문: 기존 HTML 파서 재사용
        
        Returns:
            Tuple[List[PatentSummary], Optional[int]]: (특허 목록, 총 결과 개수)
        """

        if not content:
            return [], None

        # 1) JSON 응답 시
        try:
            if content.strip().startswith("{"):
                data = json.loads(content)
                results_node = data.get("results") or {}
                clusters = results_node.get("cluster") or []
                total_results = results_node.get("total_num_results")
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
                return results, total_results
        except Exception:
            # JSON 파싱 실패 시 HTML 로직으로 폴백
            pass

        # 2) HTML fragment 폴백 (총 결과 개수는 HTML에서 추출 불가능하므로 None)
        html_results = GooglePatentsXHRDownloader._parse_results_from_html(content)
        return html_results, None

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
    
    async def _fetch_all_results(
        self, 
        client: httpx.AsyncClient, 
        captured: CapturedRequest, 
        total_count: int,
        replay_headers: Dict[str, str],
        target_patent: Optional[str] = None
    ) -> List[PatentSummary]:
        """전체 검색 결과를 페이지별로 수집 (Seed Recall 계산용)"""
        all_results = []
        page_size = 100  # 한 번에 가져올 최대 결과 수
        
        # URL 파라미터 파싱
        from urllib.parse import urlparse, parse_qs
        parsed_url = urlparse(captured.url)
        base_params = parse_qs(parsed_url.query)
        
        # 기본 파라미터를 dict로 변환
        params = {}
        for k, v in base_params.items():
            params[k] = v[0] if v else ""
        
        pages_fetched = 0
        max_pages = (total_count + page_size - 1) // page_size  # 올림 계산
        
        logger.info(f"Fetching up to {max_pages} pages ({total_count} total results)")
        
        for page in range(max_pages):
            start_idx = page * page_size
            params["num"] = str(min(page_size, total_count - start_idx))
            params["start"] = str(start_idx)
            
            # URL 재구성
            query_string = "&".join([f"{k}={v}" for k, v in params.items() if v])
            fetch_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?{query_string}"
            
            try:
                logger.info(f"Fetching page {page + 1}/{max_pages} (results {start_idx + 1}-{start_idx + int(params['num'])})")
                
                resp = await client.get(fetch_url, headers=replay_headers, timeout=15.0)
                if resp.status_code >= 400:
                    logger.warning(f"Page {page + 1} failed with status {resp.status_code}")
                    break
                
                page_results, _ = self._parse_results_from_xhr(resp.text)
                all_results.extend(page_results)
                pages_fetched += 1
                
                # Early termination: 타겟 특허를 찾으면 중단
                if target_patent:
                    normalized_target = target_patent.upper().replace(" ", "").replace(",", "").replace("-", "")
                    for patent in page_results:
                        if patent.publication_number:
                            normalized_found = patent.publication_number.upper().replace(" ", "").replace(",", "").replace("-", "")
                            if normalized_found == normalized_target:
                                logger.info(f"🎯 Target patent {target_patent} found on page {page + 1}! Early termination.")
                                return all_results
                
                # 진행률 로그
                if pages_fetched % 5 == 0 or page == max_pages - 1:
                    logger.info(f"Progress: {len(all_results)}/{total_count} results fetched ({len(all_results)/total_count*100:.1f}%)")
                
                # 요청 간 지연
                await asyncio.sleep(0.5)
                
            except Exception as exc:
                logger.error(f"Failed to fetch page {page + 1}: {exc}")
                break
        
        logger.info(f"Collected {len(all_results)} results across {pages_fetched} pages")
        return all_results

    async def search_and_download(
        self, query: str, max_results: int, count_only: bool = False, full_recall: bool = False
    ) -> Tuple[List[Path], Optional[int], List[PatentSummary]]:
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
                total_count: Optional[int] = None
                if xhr_text:
                    results, total_count = self._parse_results_from_xhr(xhr_text)
                    logger.info(f"Initial XHR results: {len(results)}")
                    if total_count is not None:
                        logger.info(f"Total search results available: {total_count}")

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

                        resp = await client.get(captured.url, headers=replay_headers, timeout=10.0)
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
                            results, total_count = self._parse_results_from_xhr(resp.text)
                            logger.info(f"Replayed XHR results: {len(results)}")
                            if total_count is not None:
                                logger.info(f"Total search results available: {total_count}")
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
                    effective_query = self._normalize_query_string(query)
                    search_url = GOOGLE_PATENTS_ORIGIN + "/?q=" + quote_plus(effective_query) + "&hl=en&num=100"
                    try:
                        await page.goto(search_url)
                        await page.wait_for_load_state("domcontentloaded")
                        try:
                            await page.wait_for_load_state("networkidle", timeout=1500)
                        except Exception:
                            pass
                        await page.wait_for_timeout(250)
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

                # 추가 페이징/스크롤: 더 많은 결과가 필요하면 XHR 재요청, 스크롤 로드 또는 다음 페이지를 따라가며 수집
                if len(results) < max_results:
                    seen: set[str] = {r.detail_url for r in results}

                    # 0) XHR 기반 파라미터 페이지네이션(가능한 경우): page/start/num 조합 시도
                    if captured and "/xhr/query" in captured.url:
                        try:
                            from urllib.parse import (
                                urlsplit,
                                urlunsplit,
                                parse_qsl,
                                urlencode,
                                unquote,
                            )

                            split = urlsplit(captured.url)
                            params = dict(parse_qsl(split.query, keep_blank_values=True))

                            # 캡처된 쿼리는 상위 파라미터 url= 안에 실제 질의 파라미터가 존재함
                            # 예: /xhr/query?url=q=...&oq=...
                            inner_raw = params.get("url", "")
                            inner_qs = unquote(inner_raw)
                            inner_params = dict(parse_qsl(inner_qs, keep_blank_values=True))
                            # 한 페이지당 최대한 많이 가져오도록 시도
                            inner_params.setdefault("num", "100")

                            def build_url(updated_inner: dict[str, str]) -> str:
                                outer = dict(params)
                                outer["url"] = urlencode(updated_inner)
                                return urlunsplit(
                                    (
                                        split.scheme,
                                        split.netloc,
                                        split.path,
                                        urlencode(outer),
                                        split.fragment,
                                    )
                                )

                            # 공통 재생 헤더(최소 요구)
                            replay_headers = {
                                "X-Same-Domain": "1",
                                "Referer": captured.referer or GOOGLE_PATENTS_ORIGIN + "/",
                            }

                            # 우선 현재 파라미터로 한 번 더 최대 개수 요청 시도
                            try:
                                inner_params["num"] = str(min(max_results, 100))
                                resp0 = await client.get(build_url(inner_params), headers=replay_headers, timeout=10.0)
                                if resp0.status_code < 400 and resp0.text:
                                    more0, _ = self._parse_results_from_xhr(resp0.text)
                                    for m in more0:
                                        if m.detail_url and m.detail_url not in seen:
                                            results.append(m)
                                            seen.add(m.detail_url)
                                            if len(results) >= max_results:
                                                break
                            except Exception:
                                pass

                            # page=2..N 시도
                            page_try_max = 10
                            if len(results) < max_results:
                                for page_no in range(2, page_try_max + 1):
                                    params_page = dict(inner_params)
                                    params_page["page"] = str(page_no)
                                    try:
                                        params_page["num"] = str(min(max_results, 100))
                                        resp = await client.get(build_url(params_page), headers=replay_headers, timeout=10.0)
                                        if resp.status_code >= 400 or not resp.text:
                                            break
                                        add_items, _ = self._parse_results_from_xhr(resp.text)
                                        new_added = 0
                                        for m in add_items:
                                            if m.detail_url and m.detail_url not in seen:
                                                results.append(m)
                                                seen.add(m.detail_url)
                                                new_added += 1
                                                if len(results) >= max_results:
                                                    break
                                        if new_added == 0:
                                            # 동일 결과만 반복되면 중단
                                            break
                                    except Exception:
                                        break
                                    if len(results) >= max_results:
                                        break

                            # start=offset 시도(10 단위)
                            if len(results) < max_results:
                                for start_offset in range(10, 1000, 10):
                                    params_start = dict(inner_params)
                                    params_start["start"] = str(start_offset)
                                    try:
                                        params_start["num"] = str(min(max_results, 100))
                                        resp = await client.get(build_url(params_start), headers=replay_headers, timeout=10.0)
                                        if resp.status_code >= 400 or not resp.text:
                                            break
                                        add_items, _ = self._parse_results_from_xhr(resp.text)
                                        new_added = 0
                                        for m in add_items:
                                            if m.detail_url and m.detail_url not in seen:
                                                results.append(m)
                                                seen.add(m.detail_url)
                                                new_added += 1
                                                if len(results) >= max_results:
                                                    break
                                        if new_added == 0:
                                            break
                                    except Exception:
                                        break
                                    if len(results) >= max_results:
                                        break
                        except Exception:
                            pass

                    async def collect_from_current_page() -> int:
                        """현재 페이지에서 결과를 파싱해 results에 병합하고 새로 추가된 개수를 반환한다."""
                        added = 0
                        try:
                            html_now = await page.content()
                        except Exception:
                            html_now = ""
                        more = self._parse_results_from_html(html_now)
                        if not more:
                            try:
                                more = await self._parse_results_from_dom(page)
                            except Exception:
                                more = []
                        for item in more:
                            if item.detail_url and item.detail_url not in seen:
                                results.append(item)
                                seen.add(item.detail_url)
                                added += 1
                                if len(results) >= max_results:
                                    break
                        return added

                    # 1) 무한 스크롤 형태 지원: 스크롤을 내려 더 많은 article을 로드
                    try:
                        while len(results) < max_results:
                            prev_len = len(results)
                            try:
                                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            except Exception:
                                break
                            try:
                                await page.wait_for_load_state("networkidle", timeout=1500)
                            except Exception:
                                pass
                            await page.wait_for_timeout(250)
                            added = await collect_from_current_page()
                            if added == 0 and len(results) == prev_len:
                                break
                    except Exception:
                        pass

                    # 2) 다음 페이지 링크 탐색: 다양한 셀렉터 시도
                    next_selectors = [
                        "a[rel='next' i]",
                        "a[aria-label='Next' i]",
                        "a[aria-label*='Next' i]",
                        "a:has-text('Next')",
                        "a:has-text('다음')",
                        "button:has-text('Next')",
                        "[role='link']:has-text('Next')",
                        "a#pnnext",
                        "a:has-text('›')",
                        "a[aria-label*='›']",
                    ]

                    while len(results) < max_results:
                        try:
                            next_locator = None
                            # 셀렉터 후보를 순서대로 검사
                            for sel in next_selectors:
                                loc = page.locator(sel).first
                                try:
                                    if await loc.count():
                                        next_locator = loc
                                        break
                                except Exception:
                                    continue
                            if next_locator is None or not await next_locator.count():
                                break

                            href = await next_locator.get_attribute("href")
                            if not href:
                                # 링크가 버튼 형태인 경우 클릭 시도
                                try:
                                    await next_locator.click()
                                except Exception:
                                    break
                                try:
                                    await page.wait_for_load_state("networkidle", timeout=1500)
                                except Exception:
                                    pass
                                await page.wait_for_timeout(250)
                            else:
                                next_url = href if href.startswith("http") else GOOGLE_PATENTS_ORIGIN + href
                                await page.goto(next_url)
                                try:
                                    await page.wait_for_load_state("networkidle", timeout=1500)
                                except Exception:
                                    pass
                                await page.wait_for_timeout(250)

                            added = await collect_from_current_page()
                            if added == 0:
                                # 스크롤 보조 시도 후 종료
                                try:
                                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                except Exception:
                                    pass
                                await page.wait_for_timeout(200)
                                added2 = await collect_from_current_page()
                                if added2 == 0:
                                    break
                        except Exception:
                            break

                if not results:
                    logger.warning("검색 결과를 찾지 못했습니다.")
                    return [], total_count, []

                results = results[:max_results]
                logger.info(f"Parsed {len(results)} results")

                # count_only 모드에서는 PDF 다운로드 건너뛰고 특허 정보만 반환
                if count_only:
                    # full_recall 모드에서는 가능한 모든 결과 수집
                    if full_recall and total_count and total_count > len(results):
                        logger.info(f"Full recall mode: fetching all {total_count} results...")
                        # target_patent을 인자로 전달하여 early termination 활용
                        target_patent = getattr(self, '_target_patent', None)
                        results = await self._fetch_all_results(client, captured, total_count, replay_headers, target_patent)
                    return [], total_count, results

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
                        "effective_query": self._normalize_query_string(query),
                        "timestamp": datetime.now().isoformat(),
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

                return saved, total_count, results
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
        self, queries: List[str], max_results: int, count_only: bool = False, full_recall: bool = False
    ) -> Dict[str, Tuple[List[Path], Optional[int], List[PatentSummary]]]:
        """여러 쿼리를 순차 처리. 각 쿼리마다 보안 토큰을 재캡처한다."""

        results: Dict[str, Tuple[List[Path], Optional[int], List[PatentSummary]]] = {}
        for i, q in enumerate(queries, 1):
            logger.info(f"◇ Query {i}/{len(queries)}: {q}")
            try:
                saved, total_count, patents = await self.search_and_download(q, max_results, count_only, full_recall)
                results[q] = (saved, total_count, patents)
            except Exception as exc:
                logger.error(f"Query failed: {q} ({exc})")
                results[q] = ([], None, [])
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
    parser.add_argument(
        "--count-only",
        action="store_true",
        help="검색 결과 개수만 확인 (PDF 다운로드 건너뛰기)",
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
        saved, total_count, patents = await downloader.search_and_download(
            query=queries[0], max_results=args.max_results, count_only=args.count_only
        )
        if args.count_only:
            print(f"📊 검색 결과: {queries[0]}")
            print(f"   - 파싱된 결과: {len(patents)} 건")
            if total_count is not None:
                print(f"   - 전체 검색 결과: {total_count:,} 건")
            else:
                print("   - 전체 검색 결과: 알 수 없음")
        else:
            logger.info(f"총 {len(saved)}개 파일 저장 완료: {out_dir}")
    else:
        all_saved = await downloader.search_and_download_many(
            queries=queries, max_results=args.max_results, count_only=args.count_only
        )
        if args.count_only:
            print(f"📊 여러 쿼리 검색 결과:")
            for query, (files, total_count, patents) in all_saved.items():
                print(f"   🔍 {query}")
                print(f"      - 파싱된 결과: {len(patents)} 건")
                if total_count is not None:
                    print(f"      - 전체 검색 결과: {total_count:,} 건")
                else:
                    print("      - 전체 검색 결과: 알 수 없음")
        else:
            total = sum(len(files) for files, _, _ in all_saved.values())
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
