"""특허 PDF 기반 검색식 생성 및 Seed Recall 측정 도구

Google Gemini 2.5 Pro를 사용하여 특허 PDF를 분석하고,
유사 특허 검색을 위한 검색식을 자동 생성한 후,
생성된 검색식들의 성능을 측정합니다.

사용 예시:
  # 기본 사용법 (환경변수에서 API 키 로드)
  python patent_analyzer.py \
    --pdf patent.pdf \
    --prompt analyzer_prompt.txt \
    --output temp_results/analysis.json

  # 전체 Seed Recall 계산 (더 정확하지만 오래 걸림)
  python patent_analyzer.py \
    --pdf patent.pdf \
    --output temp_results/full_analysis.json \
    --full-recall

  # API 키를 직접 지정
  python patent_analyzer.py \
    --pdf patent.pdf \
    --api-key YOUR_API_KEY \
    --output temp_results/results.json

  # 커스텀 프롬프트와 상세 옵션
  python patent_analyzer.py \
    --pdf patent.pdf \
    --prompt custom_prompt.txt \
    --output temp_results/custom_results.json \
    --max-results 10 \
    --delay 2.0

주요 기능:
- PDF를 Gemini API에 직접 업로드하여 분석
- AI 기반 다중 검색 전략 생성
- Google Patents에서 실제 검색 수행 및 결과 수집
- Seed Recall 계산 (원본 특허가 검색 결과에 포함되는지)
- JSON/CSV 형태의 상세한 성능 보고서 생성

필수 설정:
1. pip install -r requirements.txt
2. Google AI Studio에서 API 키 발급
3. .env 파일에 GOOGLE_API_KEY 설정 또는 --api-key 사용
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import google.generativeai as genai
from dotenv import load_dotenv
from loguru import logger

# 기존 downloader 임포트
sys.path.append(str(Path(__file__).parent))
from patent_downloader import GooglePatentsXHRDownloader, PatentSummary


def extract_patent_number_from_filename(pdf_path: Path) -> str:
    """PDF 파일명에서 특허번호 추출 (확장자 제거)
    
    예: US8771637B2.pdf → US8771637B2
    """
    return pdf_path.stem


def normalize_patent_number(patent_number: str) -> str:
    """특허번호 정규화 (공백, 쉼표, 하이픈 제거 및 대문자 변환)
    
    예: "US 8,771,637 B2" → "US8771637B2"
    """
    if not patent_number:
        return ""
    
    # 공백, 쉼표, 하이픈 제거 후 대문자 변환
    normalized = re.sub(r'[\s,\-]', '', patent_number).upper()
    return normalized


class PatentQueryGenerator:
    """특허 PDF 기반 검색식 생성 및 성능 측정 클래스"""
    
    def __init__(
        self,
        api_key: str,
        download_dir: Path,
        max_results: int = 10,
        delay: float = 1.0
    ):
        self.api_key = api_key
        self.download_dir = download_dir
        self.max_results = max_results
        self.delay = delay
        
        # Gemini 설정
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash-exp")
        
        # Google Patents downloader 초기화
        self.downloader = GooglePatentsXHRDownloader(
            download_dir=download_dir,
            headless=True,
            delay=delay,
            timeout=30
        )
        
    async def upload_pdf_to_gemini(self, pdf_path: Path) -> str:
        """PDF 파일을 Gemini API에 업로드"""
        logger.info(f"Uploading PDF to Gemini API: {pdf_path}")
        
        try:
            # 파일 업로드
            uploaded_file = genai.upload_file(
                path=str(pdf_path),
                display_name=pdf_path.name
            )
            
            # 업로드 완료 대기
            while uploaded_file.state.name == "PROCESSING":
                await asyncio.sleep(1)
                uploaded_file = genai.get_file(uploaded_file.name)
            
            if uploaded_file.state.name == "FAILED":
                raise Exception(f"File upload failed: {uploaded_file.state}")
            
            logger.info(f"PDF uploaded successfully: {uploaded_file.name}")
            return uploaded_file.name
            
        except Exception as exc:
            logger.error(f"PDF upload failed: {exc}")
            raise
    
    async def generate_queries(
        self, 
        pdf_file_name: str, 
        prompt_template: str
    ) -> Dict[str, Any]:
        """Gemini를 사용하여 검색식 생성"""
        logger.info("Generating search queries with Gemini...")
        
        try:
            # 업로드된 파일 객체 가져오기
            uploaded_file = genai.get_file(pdf_file_name)
            
            # Gemini에 프롬프트와 파일 전송
            response = self.model.generate_content([
                uploaded_file,
                prompt_template
            ])
            
            # JSON 응답 파싱
            response_text = response.text.strip()
            logger.debug(f"Gemini response: {response_text}")
            
            # JSON 추출 (마크다운 코드 블록 제거)
            json_match = re.search(r'```json\n(.*?)\n```', response_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
            else:
                # 코드 블록이 없으면 전체 응답에서 JSON 찾기
                json_text = response_text
            
            queries_data = json.loads(json_text)
            logger.info(f"Generated {len(queries_data.get('search_queries', []))} search queries")
            
            return queries_data
            
        except Exception as exc:
            logger.error(f"Query generation failed: {exc}")
            raise
    
    async def execute_searches(
        self, 
        queries_data: Dict[str, Any],
        target_patent: Optional[str] = None,
        full_recall: bool = False
    ) -> List[Dict[str, Any]]:
        """생성된 검색식들을 실행하고 결과 수집"""
        logger.info(f"Executing search queries (full_recall={full_recall})...")
        
        # target_patent 설정 (early termination용)
        if target_patent:
            self.downloader._target_patent = target_patent
            logger.info(f"Target patent for early termination: {target_patent}")
        
        search_results = []
        queries = queries_data.get("search_queries", [])
        
        for i, query_info in enumerate(queries, 1):
            query = query_info.get("query", "")
            strategy = query_info.get("strategy", f"Query {i}")
            
            logger.info(f"[{i}/{len(queries)}] Executing: {strategy}")
            logger.info(f"Query: {query}")
            
            try:
                # count-only 모드로 검색 실행 (full_recall 옵션 포함)
                saved_files, total_count, patents = await self.downloader.search_and_download(
                    query=query,
                    max_results=self.max_results,
                    count_only=True,
                    full_recall=full_recall
                )
                
                result = {
                    "query_index": i,
                    "strategy": strategy,
                    "query": query,
                    "expected_scope": query_info.get("expected_scope", "unknown"),
                    "total_results": total_count,
                    "parsed_results": len(patents),
                    "found_patents": [p.publication_number for p in patents if p.publication_number],
                    "success": True,
                    "error": None
                }
                
                logger.info(f"Results: {total_count} total, {len(patents)} parsed")
                if patents:
                    logger.info(f"Found patents: {[p.publication_number for p in patents[:3]]}")  # 처음 3개만 로그
                
            except Exception as exc:
                logger.error(f"Search failed: {exc}")
                result = {
                    "query_index": i,
                    "strategy": strategy,
                    "query": query,
                    "expected_scope": query_info.get("expected_scope", "unknown"),
                    "total_results": None,
                    "parsed_results": 0,
                    "found_patents": [],
                    "success": False,
                    "error": str(exc)
                }
            
            search_results.append(result)
            
            # 요청 간 지연
            if i < len(queries):
                await asyncio.sleep(self.delay)
        
        return search_results
    
    def calculate_seed_recall(
        self, 
        original_patent_number: str,
        search_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """파일명 기반 Seed Recall 계산"""
        logger.info(f"Calculating Seed Recall for patent: {original_patent_number}")
        
        # 특허번호 정규화
        normalized_original = normalize_patent_number(original_patent_number)
        
        # 각 검색식별로 원본 특허 발견 여부 확인
        query_recalls = []
        found_in_queries = 0
        
        for result in search_results:
            if not result["success"]:
                query_recalls.append({
                    "query_index": result["query_index"],
                    "strategy": result["strategy"], 
                    "found": False,
                    "reason": "Query failed"
                })
                continue
            
            found_patents = result.get("found_patents", [])
            normalized_found = [normalize_patent_number(p) for p in found_patents if p]
            
            is_found = normalized_original in normalized_found
            if is_found:
                found_in_queries += 1
                
            query_recalls.append({
                "query_index": result["query_index"],
                "strategy": result["strategy"],
                "found": is_found,
                "total_results": result["total_results"],
                "parsed_results": result["parsed_results"],
                "found_patents_sample": found_patents[:3] if found_patents else []
            })
            
            logger.info(f"Query {result['query_index']}: {'✅ Found' if is_found else '❌ Not found'} - {result['parsed_results']} results")
        
        # 통계 계산
        total_queries = len(search_results)
        successful_queries = len([r for r in search_results if r["success"]])
        avg_total_results = sum(
            r["total_results"] or 0 for r in search_results if r["success"]
        ) / max(successful_queries, 1)
        avg_parsed_results = sum(
            r["parsed_results"] for r in search_results
        ) / max(total_queries, 1)
        
        seed_recall_rate = found_in_queries / total_queries if total_queries > 0 else 0
        
        return {
            "original_patent": original_patent_number,
            "normalized_patent": normalized_original,
            "total_queries": total_queries,
            "successful_queries": successful_queries,
            "success_rate": successful_queries / total_queries if total_queries > 0 else 0,
            "avg_total_results": round(avg_total_results, 1),
            "avg_parsed_results": round(avg_parsed_results, 1),
            "seed_recall_rate": seed_recall_rate,
            "queries_found_patent": found_in_queries,
            "query_details": query_recalls
        }
    
    async def generate_and_evaluate(
        self, 
        pdf_path: Path,
        prompt_template: str
    ) -> Dict[str, Any]:
        """전체 파이프라인 실행: PDF 분석 → 검색식 생성 → 검색 실행 → 평가"""
        logger.info(f"Starting analysis pipeline for: {pdf_path}")
        
        # 1. PDF 업로드
        uploaded_file_name = await self.upload_pdf_to_gemini(pdf_path)
        
        try:
            # 2. 검색식 생성
            queries_data = await self.generate_queries(uploaded_file_name, prompt_template)
            
            # 3. 검색 실행 
            original_patent_from_file = extract_patent_number_from_filename(pdf_path)
            search_results = await self.execute_searches(
                queries_data, 
                target_patent=original_patent_from_file,
                full_recall=getattr(self, 'full_recall_mode', False)
            )
            
            # 4. 성능 평가
            performance = self.calculate_seed_recall(original_patent_from_file, search_results)
            
            # 5. 결과 종합
            final_result = {
                "metadata": {
                    "pdf_file": str(pdf_path),
                    "analysis_time": datetime.now().isoformat(),
                    "gemini_file_name": uploaded_file_name
                },
                "patent_info": queries_data.get("patent_info", {}),
                "search_results": search_results,
                "performance_summary": performance
            }
            
            return final_result
            
        finally:
            # 업로드된 파일 정리
            try:
                genai.delete_file(uploaded_file_name)
                logger.info("Cleaned up uploaded file")
            except Exception as exc:
                logger.warning(f"Failed to cleanup uploaded file: {exc}")


def load_prompt_template(prompt_path: Path) -> str:
    """프롬프트 템플릿 파일 로드"""
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    
    return prompt_path.read_text(encoding="utf-8")


def save_results(results: Dict[str, Any], output_path: Path) -> None:
    """결과를 JSON 파일로 저장"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Results saved to: {output_path}")


def setup_api_key(args) -> str:
    """API 키 설정 및 반환"""
    # 1. 명령행 인자 우선
    if args.api_key:
        return args.api_key
    
    # 2. 환경변수에서 로드
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    
    if not api_key:
        raise ValueError(
            "Google API key not found. Please provide it via:\n"
            "1. --api-key argument\n"
            "2. GOOGLE_API_KEY environment variable\n"
            "3. .env file with GOOGLE_API_KEY=your_key"
        )
    
    return api_key


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="특허 PDF 기반 검색식 생성 및 성능 측정"
    )
    
    parser.add_argument(
        "--pdf",
        type=Path,
        required=True,
        help="분석할 특허 PDF 파일"
    )
    
    parser.add_argument(
        "--prompt",
        type=Path,
        default=Path("analyzer_prompt.txt"),
        help="검색식 생성 프롬프트 파일 (기본값: analyzer_prompt.txt)"
    )
    
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("temp_results/results.json"),
        help="결과 저장 파일 (기본값: temp_results/results.json)"
    )
    
    parser.add_argument(
        "--api-key",
        help="Google Gemini API 키 (환경변수 GOOGLE_API_KEY 사용 가능)"
    )
    
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=Path("./temp_downloads"),
        help="임시 다운로드 디렉터리 (기본값: ./temp_downloads)"
    )
    
    parser.add_argument(
        "--max-results",
        type=int,
        default=10,
        help="검색 시 최대 결과 수 (기본값: 10)"
    )
    
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="검색 요청 간 지연 시간(초) (기본값: 1.5)"
    )
    
    parser.add_argument(
        "--full-recall",
        action="store_true",
        help="전체 검색 결과를 수집하여 정확한 Seed Recall 계산 (더 오래 걸림)"
    )
    
    return parser


async def main() -> int:
    parser = build_cli_parser()
    args = parser.parse_args()
    
    # 입력 검증
    if not args.pdf.exists():
        logger.error(f"PDF file not found: {args.pdf}")
        return 1
    
    # API 키 설정
    try:
        api_key = setup_api_key(args)
    except ValueError as exc:
        logger.error(str(exc))
        return 1
    
    # 프롬프트 로드
    try:
        prompt_template = load_prompt_template(args.prompt)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        return 1
    
    # 다운로드 디렉터리 생성
    args.download_dir.mkdir(parents=True, exist_ok=True)
    
    # 분석 실행
    generator = PatentQueryGenerator(
        api_key=api_key,
        download_dir=args.download_dir,
        max_results=args.max_results,
        delay=args.delay
    )
    generator.full_recall_mode = args.full_recall
    
    try:
        results = await generator.generate_and_evaluate(
            pdf_path=args.pdf,
            prompt_template=prompt_template
        )
        
        # 결과 저장
        save_results(results, args.output)
        
        # 요약 출력
        performance = results["performance_summary"]
        patent_info = results["patent_info"]
        
        recall_mode = "전체 결과" if args.full_recall else "부분 결과"
        print("\n" + "="*70)
        print(f"🔍 특허 검색식 생성 및 Seed Recall 분석 완료 ({recall_mode})")
        print("="*70)
        print(f"📄 분석 대상: {patent_info.get('title', 'Unknown')}")
        print(f"🔢 파일 특허번호: {performance['original_patent']}")
        print(f"📊 생성된 검색식: {performance['total_queries']}개")
        print(f"✅ 성공적 검색: {performance['successful_queries']}개 ({performance['success_rate']:.1%})")
        print(f"📈 평균 검색 결과: {performance['avg_total_results']:.0f}건")
        print()
        print("🎯 Seed Recall 결과:")
        print(f"   📍 원본 특허 발견율: {performance['seed_recall_rate']:.1%} ({performance['queries_found_patent']}/{performance['total_queries']})")
        
        # 각 검색식별 상세 결과
        for detail in performance.get('query_details', []):
            status = "✅" if detail['found'] else "❌"
            if 'reason' in detail:
                print(f"   {status} Query {detail['query_index']}: {detail['reason']}")
            else:
                print(f"   {status} Query {detail['query_index']}: {detail.get('total_results', 0)}건 중 {detail.get('parsed_results', 0)}건 파싱")
        
        print()
        print(f"💾 결과 저장: {args.output}")
        print("="*70)
        
        return 0
        
    except Exception as exc:
        logger.error(f"Analysis failed: {exc}")
        return 1


if __name__ == "__main__":
    asyncio.run(main())