## 변경 요약
- 무엇을 왜 변경했는지 한두 문장으로 설명해 주세요.

## 관련 이슈
- Closes #<번호> (있다면)

## 변경 유형
- [ ] 기능 추가
- [ ] 버그 수정
- [ ] 문서 변경
- [ ] 리팩터링/성능 개선
- [ ] 테스트/도구 설정

## 상세 내용
- 핵심 변경점, 아키텍처/옵션/플래그 설명, 역호환성 여부

## 테스트 방법
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && python -m playwright install
python google_patents_xhr_downloader.py --query "..." --out ./downloads --max-results 1 --headless --diagnostics
```

## 체크리스트
- [ ] 단위 테스트/로컬 검증 완료 (가능하면 오프라인)
- [ ] 문서/AGENTS.md 반영 필요 여부 확인
- [ ] 민감정보(쿠키/헤더/다운로드 파일) 커밋 금지 확인

## 스크린샷/로그 (선택)
<!-- 필요 시 첨부 -->

