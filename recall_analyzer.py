"""Seed Recall 계산 및 검색 성능 분석 도구

주어진 검색식들의 성능을 평가하고, 원본 특허가 검색 결과에 
포함되는지 확인하여 Seed Recall을 계산합니다.

사용 예시:
  # JSON 검색식으로 Seed Recall 분석
  python recall_analyzer.py --queries queries.json --pdf US1234567.pdf --output temp_results/recall.json
  
  # 전체 검색 결과 수집 (early termination 비활성화)
  python recall_analyzer.py --queries queries.json --pdf patent.pdf --full-recall --output recall.json
  
  # 검색 지연시간 설정
  python recall_analyzer.py --queries queries.json --pdf patent.pdf --delay 2.0 --output results.json

주요 기능:
- 검색식별 Google Patents 검색 실행
- 원본 특허 발견 여부 확인 (Seed Recall)
- 검색 성능 통계 계산
- Early termination 지원 (원본 특허 발견시 조기 종료)
- JSON/CSV 형태의 상세한 성능 보고서 생성

필수 설정:
1. pip install -r requirements.txt
2. python -m playwright install
"""

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

# patent_downloader 임포트
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


class RecallAnalyzer:
    """검색 성능 분석 및 Seed Recall 계산 클래스"""
    
    def __init__(
        self,
        download_dir: Path,
        max_results: int = 10,
        delay: float = 1.5,
        full_recall: bool = False
    ):
        self.download_dir = download_dir
        self.max_results = max_results
        self.delay = delay
        self.full_recall = full_recall
        
        # Google Patents downloader 초기화
        self.downloader = GooglePatentsXHRDownloader(
            download_dir=download_dir,
            headless=True,
            delay=delay,
            timeout=30
        )
        
    async def execute_searches(
        self, 
        queries_data: Dict[str, Any],
        target_patent: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """검색식들을 실행하고 결과 수집"""
        logger.info(f"Executing search queries (full_recall={self.full_recall})...")
        
        # target_patent 설정 (early termination용)
        if target_patent and not self.full_recall:
            self.downloader._target_patent = target_patent
            logger.info(f"Target patent for early termination: {target_patent}")
        
        search_results = []
        queries = queries_data.get("search_queries", [])
        
        for i, query_info in enumerate(queries, 1):
            query = query_info.get("query", "")
            strategy = query_info.get("strategy", f"Query {i}")
            
            logger.info(f"Executing query {i}/{len(queries)}: {strategy}")
            logger.info(f"Query: {query}")
            
            try:
                # 단일 쿼리 실행
                saved_files, total_count, patents = await self.downloader.search_and_download(
                    query=query,
                    max_results=self.max_results,
                    count_only=True,  # PDF는 다운로드하지 않고 검색만
                    full_recall=self.full_recall
                )
                
                # 특허 번호 추출
                found_patents = []
                if patents:
                    found_patents = [
                        patent.publication_number 
                        for patent in patents 
                        if patent.publication_number
                    ]
                
                search_results.append({
                    "query_index": i,
                    "strategy": strategy,
                    "query": query,
                    "success": True,
                    "total_results": total_count,
                    "parsed_results": len(patents) if patents else 0,
                    "found_patents": found_patents,
                    "execution_time": 0  # 현재 측정하지 않음
                })
                
                logger.info(f"✅ Query {i} completed: {len(found_patents)} patents found")
                    
            except Exception as exc:
                # 검색 실패
                search_results.append({
                    "query_index": i,
                    "strategy": strategy,
                    "query": query,
                    "success": False,
                    "total_results": None,
                    "parsed_results": 0,
                    "found_patents": [],
                    "error": str(exc)
                })
                
                logger.error(f"❌ Query {i} failed with error: {exc}")
        
        return search_results
    
    def calculate_seed_recall(
        self, 
        original_patent_number: str,
        search_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Seed Recall 계산"""
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
                    "query": result["query"],
                    "found": False,
                    "reason": result.get("error", "Query failed")
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
                "query": result["query"],
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
    
    async def analyze_recall(
        self,
        queries_data: Dict[str, Any],
        target_patent_number: str
    ) -> Dict[str, Any]:
        """전체 Recall 분석 파이프라인"""
        logger.info(f"Starting recall analysis for patent: {target_patent_number}")
        
        # 1. 검색 실행
        search_results = await self.execute_searches(queries_data, target_patent_number)
        
        # 2. Seed Recall 계산
        performance = self.calculate_seed_recall(target_patent_number, search_results)
        
        # 3. 결과 종합
        final_result = {
            "metadata": {
                "target_patent": target_patent_number,
                "analysis_timestamp": datetime.now().isoformat(),
                "full_recall_mode": self.full_recall,
                "max_results_per_query": self.max_results,
                "search_delay": self.delay
            },
            "search_results": search_results,
            "performance_analysis": performance,
            "queries_metadata": queries_data.get("metadata", {}),
            "patent_info": queries_data.get("patent_info", {})
        }
        
        logger.info(f"Recall analysis completed successfully")
        return final_result


def load_queries_data(queries_path: Path) -> Dict[str, Any]:
    """검색식 데이터 로드"""
    if not queries_path.exists():
        raise FileNotFoundError(f"Queries file not found: {queries_path}")
    
    with open(queries_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_results(results: Dict[str, Any], output_path: Path) -> None:
    """결과를 JSON 파일로 저장"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Results saved to: {output_path}")


def build_cli_parser() -> argparse.ArgumentParser:
    """CLI 파서 구성"""
    parser = argparse.ArgumentParser(
        description="검색식 성능 분석 및 Seed Recall 계산",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--queries", 
        type=Path,
        required=True,
        help="검색식이 포함된 JSON 파일 경로"
    )
    
    parser.add_argument(
        "--pdf", 
        type=Path,
        required=True,
        help="대상 특허 PDF 파일 경로 (특허번호 추출용)"
    )
    
    parser.add_argument(
        "--output", 
        type=Path,
        default=Path("temp_results/recall_analysis.json"),
        help="결과 저장 경로 (기본값: temp_results/recall_analysis.json)"
    )
    
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=Path("./temp_downloads"),
        help="다운로드 디렉터리 (기본값: ./temp_downloads)"
    )
    
    parser.add_argument(
        "--max-results",
        type=int,
        default=10,
        help="검색식당 최대 결과 수 (기본값: 10)"
    )
    
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="검색 간 지연시간(초) (기본값: 1.5)"
    )
    
    parser.add_argument(
        "--full-recall",
        action="store_true",
        help="전체 검색 수행 (early termination 비활성화)"
    )
    
    return parser


async def main() -> int:
    """메인 실행 함수"""
    parser = build_cli_parser()
    args = parser.parse_args()
    
    try:
        # 입력 파일 확인
        if not args.queries.exists():
            logger.error(f"Queries file not found: {args.queries}")
            return 1
        
        if not args.pdf.exists():
            logger.error(f"PDF file not found: {args.pdf}")
            return 1
        
        # 검색식 데이터 로드
        queries_data = load_queries_data(args.queries)
        
        # 대상 특허번호 추출
        target_patent = extract_patent_number_from_filename(args.pdf)
        
        # Recall 분석기 초기화
        analyzer = RecallAnalyzer(
            download_dir=args.download_dir,
            max_results=args.max_results,
            delay=args.delay,
            full_recall=args.full_recall
        )
        
        # Recall 분석 실행
        results = await analyzer.analyze_recall(queries_data, target_patent)
        
        # 결과 저장
        save_results(results, args.output)
        
        # 간단한 요약 출력
        performance = results.get("performance_analysis", {})
        search_results = results.get("search_results", [])
        
        print(f"✅ Seed Recall 분석 완료")
        print(f"🎯 대상 특허: {target_patent}")
        print(f"🔍 실행된 검색식: {len(search_results)}개")
        print(f"📊 Seed Recall Rate: {performance.get('seed_recall_rate', 0):.2%}")
        print(f"✅ 성공한 검색: {performance.get('successful_queries', 0)}/{performance.get('total_queries', 0)}")
        print(f"💾 결과 저장: {args.output}")
        
        # 각 검색식 결과 요약
        for detail in performance.get("query_details", []):
            status = "✅" if detail["found"] else "❌"
            print(f"  {detail['query_index']}. {detail['strategy']}: {status}")
        
        return 0
        
    except Exception as exc:
        logger.error(f"Recall analysis failed: {exc}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)