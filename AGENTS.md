# Repository Guidelines

## 에이전트 언어 규칙 (Korean Responses)
- 모든 사용자 대화와 문서는 기본적으로 한국어로 작성합니다.
- 코드·식별자·명령어·경로·로그 메시지는 원문(영문) 표기를 유지합니다.
- 외부 문서 인용은 원문을 유지하되, 요약/설명은 한국어로 제공합니다.
- 이슈·PR 본문은 한국어 사용을 권장합니다.

## 프로젝트 구조 및 모듈 구성
- `google_patents_xhr_downloader.py`: Playwright + httpx 기반 비동기 특허 검색/다운로더(메인 엔트리).
- `requirements.txt`: 런타임 의존성 목록(Playwright, httpx[http2], bs4, lxml, loguru).
- `archive/`: 실험/벤치마크 및 과거 스크립트 모음(배포 대상 아님).
- `.venv/`, `__pycache__/`: 로컬 환경/캐시(무시됨).

## 빌드·테스트·개발 명령
- 가상환경: `python -m venv .venv && source .venv/bin/activate`
- 의존성 설치: `pip install -r requirements.txt && python -m playwright install`
- 실행 예:
  - `python google_patents_xhr_downloader.py --query "machine learning" --out ./downloads --max-results 3 --headless`
  - 진단 아티팩트 저장: `--diagnostics` (경로: `./downloads/diagnostics/`).
- 포매팅/린트(선택): Black/Ruff 사용 시 `black . && ruff .` 후 커밋 권장.

## 코딩 스타일 및 네이밍
- Python 3, PEP 8, 4칸 들여쓰기; 타입 힌트와 모듈 상수 사용 권장.
- 함수/변수: `snake_case`, 클래스: `PascalCase`, 파일: `snake_case.py`.
- 파일 I/O는 사용자 지정 출력 경로(`--out`) 내에서만 수행합니다.
- 네트워크 흐름은 `loguru`의 INFO/WARNING 레벨로 기록하고, 과도한 디버그 로그는 지양합니다.

## 테스트 가이드라인
- 프레임워크: `pytest`(개발 의존성으로 추가). 실행: `pip install pytest && pytest -q`.
- 위치/이름: `tests/` 디렉터리, `test_*.py` (예: `tests/test_parsing.py`).
- 범위: HTML/XHR 파싱 헬퍼, 슬러그화, PDF URL 추출 로직. 작은 고정 픽스처 사용.
- 유닛 테스트는 오프라인으로 수행하고 Google에 직접 요청하지 않습니다.

## 커밋·PR 가이드라인
- 커밋 메시지: 짧은 명령형 제목(≤72자), 필요 시 본문 추가.
  - 예: `feat: add diagnostics directory per query`
- PR: 목적/접근/테스트 요약, 관련 이슈 링크, 재현 명령/출력 첨부. 대용량 PDF는 커밋하지 않습니다.

## 보안 및 구성 팁
- 사이트 약관을 준수하고, 429/403 발생 시 `--delay`로 요청 속도를 제한합니다(기본은 headless 권장).
- 쿠키/민감 헤더/다운로드한 PDF는 저장소에 커밋하지 않습니다.
- 재현성: 사용한 명령, Python/Playwright 버전, 브라우저 채널을 PR 본문에 명시합니다.
