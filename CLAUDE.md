# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 개발 환경 및 실행

### 환경 설정
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install

# Linux/macOS  
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install
```

### API 키 설정
```bash
# .env.example을 복사하여 .env 파일 생성
cp .env.example .env
# .env 파일에 Google Gemini API 키 입력
# GOOGLE_API_KEY=your_actual_api_key_here
```

### 기본 실행
```bash
python patent_downloader.py --query "machine learning" --out ./downloads --max-results 3 --headless
```

### 진단 모드 (디버깅용)
```bash
python patent_downloader.py --query "complex query" --out ./downloads --diagnostics
# 진단 아티팩트: ./downloads/diagnostics/query_slug/
```

### 특허 분석 (AI 기반 검색식 생성)
```bash
# 완전 자동화 파이프라인 (권장)
python patent_pipeline.py --pdf patent.pdf --output ./temp_results/full_analysis.json

# 전체 Seed Recall 계산 (더 정확하지만 오래 걸림)
python patent_pipeline.py --pdf patent.pdf --full-recall --output ./temp_results/detailed_analysis.json

# 커스텀 설정으로 실행
python patent_pipeline.py --pdf patent.pdf --prompt custom_prompt.txt --delay 2.0 --max-results 20 --output analysis.json

# 개별 모듈 실행 (고급 사용자)
# 1단계: 검색식 생성
python query_generator.py --pdf patent.pdf --output ./temp_results/queries.json

# 2단계: Seed Recall 분석  
python recall_analyzer.py --queries ./temp_results/queries.json --pdf patent.pdf --output ./temp_results/recall.json
```

### 테스트 실행
```bash
# 테스트 의존성 설치 (필요시)
pip install pytest

# 테스트 실행 (현재는 테스트 파일 없음, 향후 추가 예정)
pytest -q tests/
```

### 환경 검증
```bash
# 가상환경 활성화 확인
python --version  # Python 3.8+ 필요
pip list | grep playwright  # playwright==1.54.0 확인

# 브라우저 설치 확인
python -c "from playwright.sync_api import sync_playwright; print('Playwright 설치 완료')"
```

## 아키텍처 이해

### 하이브리드 접근법
이 프로젝트의 핵심은 **Playwright + httpx 하이브리드** 아키텍처입니다:

1. **Playwright 단계**: Google Patents 초기 접속하여 세션/쿠키/보안토큰/XHR 요청 패턴 캡처
2. **httpx 단계**: 캡처된 인증 정보로 고속 HTTP/2 요청하여 검색/다운로드 수행

### 주요 컴포넌트

**GooglePatentsXHRDownloader 클래스** (`patent_downloader.py`):
- `_capture_xhr_request()`: Playwright로 XHR 요청 패턴 캡처
- `_build_client_with_cookies()`: 캡처된 인증으로 httpx 클라이언트 구성  
- `_parse_results_from_xhr()`: XHR JSON 응답 파싱
- `_fetch_detail_and_pdf()`: 특허 상세페이지에서 PDF URL 추출
- `_download_pdf()`: 스트리밍 PDF 다운로드

**PatentQueryGenerator 클래스** (`query_generator.py`):
- `upload_pdf_to_gemini()`: PDF를 Gemini API에 업로드
- `generate_queries()`: AI 기반 검색식 생성
- `generate_queries_from_pdf()`: PDF 분석부터 검색식 생성까지 전체 파이프라인

**RecallAnalyzer 클래스** (`recall_analyzer.py`):
- `execute_searches()`: 검색식들을 Google Patents에서 실행
- `calculate_seed_recall()`: 원본 특허 발견 여부 기반 성능 측정
- `analyze_recall()`: 검색부터 성능 분석까지 전체 파이프라인

**워크플로우**:
```
완전 자동화 파이프라인 (patent_pipeline.py):
PDF 입력 → Gemini API → 검색식 생성 → Google Patents 검색 → Seed Recall 분석 → 통합 결과

개별 모듈 사용:
1. 검색식 생성: PDF → Gemini API → 검색식 JSON (query_generator.py)
2. 성능 분석: 검색식 JSON → Google Patents → Seed Recall (recall_analyzer.py)  
3. PDF 다운로드: 검색쿼리 → Playwright+httpx → PDF 파일 (patent_downloader.py)
```

### 쿼리 정규화
`_normalize_query_string()` 함수가 사용자 친화적 문법을 Google Patents 내부 문법으로 변환:
- `title:` → `TI=`
- `abstract:` → `AB=` 
- `claims:` → `CL=`
- 메타데이터 필드 정규화 (assignee=, inventor= 등)

## Google Patents 검색 문법

이 프로젝트는 체계적 테스트를 통해 Google Patents 검색 문법의 정확한 한계를 규명했습니다.

### 검증된 한계
- **AND 조건**: 13개+ 무제한
- **근접연산자 중첩**: 6중+ 무제한  
- **필드 조합**: TI+AB+CL+메타데이터 무제한
- **복잡한 중첩 괄호**: 무제한

### 실패 패턴 (구문 오류만 실패)
```bash
# ❌ 금지된 패턴
"word1 AND NEAR/5 word2"     # AND 뒤 근접연산자
"machine learn*"             # 인용부호 내 와일드카드
((((word1                    # 괄호 미매칭

# ✅ 올바른 패턴  
word1 NEAR/5 word2 SAME word3
(word1 NEAR/3 word2) AND (word3 ADJ word4)
```

### 오류 vs 0건 구분
```python
if "user_error" in response:
    # 구문 오류 → 쿼리 문법 수정 필요
elif response["results"]["total_num_results"] == 0:
    # 0건 결과 → 조건 완화 고려 (정상 동작)
```

## 프로젝트 구조

### 핵심 파일들
- **`patent_pipeline.py`**: 완전 자동화 파이프라인 (PDF → AI 검색식 → Seed Recall)
- **`patent_downloader.py`**: Google Patents 검색 및 PDF 다운로드 엔진
- **`query_generator.py`**: AI 기반 검색식 생성 도구 (개별 사용)
- **`recall_analyzer.py`**: Seed Recall 계산 및 성능 분석 도구 (개별 사용)
- **`analyzer_prompt.txt`**: Gemini AI용 검색식 생성 프롬프트 템플릿
- **`requirements.txt`**: 핵심 의존성 (Playwright, httpx, BeautifulSoup, loguru, Gemini)

### 지원 파일들
- **`CLAUDE.md`**: 개발 가이드라인 (현재 파일)
- **`AGENTS.md`**: 상세한 개발 가이드라인 및 코딩 스타일
- **`archive/`**: 실험적 구현들 (배포 대상 아님)
- **`temp_results/`**: AI 분석 결과 및 테스트 파일 임시 저장소
- **`temp_downloads/`**: 임시 다운로드 파일들

## 개발 시 주의사항

### 임시 파일 관리 정책
테스트 및 개발 중 생성되는 임시 파일들은 별도 폴더에서 관리:
- **테스트 결과 파일**: `./temp_results/` 폴더에 저장
- **임시 다운로드**: `./temp_downloads/` 폴더 사용  
- **분석 결과**: `./temp_results/` 폴더에 JSON/CSV 저장
- **PDF 다운로드**: 사용자 지정 `--out` 경로 (기본값: `./downloads/`)
- **.gitignore**에 임시 폴더들 추가하여 커밋 방지

### 의존성 충돌 해결
httpx 버전 충돌 발생 시:
```bash
# 격리된 환경에서만 실행
pip install --force-reinstall httpx==0.27.0
# 또는 별도 가상환경 사용 권장
```

### 기능 추가 후 주석 업데이트 필수
새로운 CLI 옵션이나 주요 기능을 추가한 후에는 반드시:
- 파일 상단 "사용 예시" 섹션에 새 옵션 사용법 추가
- 기능별 예시 명령어를 명확하게 기입
- 이후 개발자가 쉽게 참조할 수 있도록 주석 유지

### 사이트 차단 대응
Google Patents는 과도한 요청을 차단할 수 있으므로:
- `--delay` 옵션으로 요청 간격 조정
- `--diagnostics`로 차단 시 캡처된 요청 분석  
- 429/403 응답 시 대기 시간 증가

### 언어 규칙
- 사용자 대화/문서: 한국어 사용
- 코드/식별자/로그: 영문 유지
- 커밋 메시지: 영문 명령형 (feat:, fix:, docs: 등)

### 테스트 철학
유닛 테스트는 Google에 직접 요청하지 않고 오프라인 픽스처 사용:
- HTML/XHR 파싱 로직
- 파일명 슬러그화  
- PDF URL 추출 헬퍼 함수들