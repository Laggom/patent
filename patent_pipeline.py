"""특허 분석 완전 자동화 파이프라인

PDF 특허 입력부터 Seed Recall 분석까지 원클릭으로 실행하는 통합 파이프라인입니다.

사용 예시:
  # 기본 파이프라인 실행
  python patent_pipeline.py --pdf patent.pdf --output full_analysis.json
  
  # 전체 Seed Recall 계산 (더 정확하지만 오래 걸림)
  python patent_pipeline.py --pdf patent.pdf --full-recall --output detailed_analysis.json
  
  # 커스텀 설정으로 실행
  python patent_pipeline.py --pdf patent.pdf --prompt custom_prompt.txt --delay 2.0 --max-results 20

워크플로우:
1. PDF 분석 → Gemini API → 검색식 생성 (query_generator.py)
2. 검색식 → Google Patents 검색 → 결과 수집 (recall_analyzer.py)  
3. Seed Recall 계산 및 통합 결과 생성

주요 기능:
- 메모리상 데이터 전달로 중간 파일 생성 최소화
- 단계별 진행 상황 실시간 로깅
- 에러 발생시 중단점부터 재시작 가능
- 기존 개별 모듈들의 모든 옵션 지원
- 통합 JSON 결과 + 개별 단계 결과 저장

필수 설정:
1. pip install -r requirements.txt
2. python -m playwright install  
3. Google AI Studio에서 API 키 발급
4. .env 파일에 GOOGLE_API_KEY 설정 또는 --api-key 사용
"""

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from loguru import logger

# 기존 모듈들 임포트
from query_generator import PatentQueryGenerator, load_prompt_template, extract_patent_number_from_filename
from recall_analyzer import RecallAnalyzer
from prompt_manager import PromptManager, create_default_prompt_manager


class PatentAnalysisPipeline:
    """특허 분석 완전 자동화 파이프라인"""
    
    def __init__(
        self,
        api_key: str,
        download_dir: Path = Path("./temp_downloads"),
        max_results: int = 10,
        delay: float = 1.5,
        full_recall: bool = False,
        save_intermediate: bool = True,
        prompt_manager: Optional[PromptManager] = None
    ):
        self.api_key = api_key
        self.download_dir = download_dir
        self.max_results = max_results
        self.delay = delay
        self.full_recall = full_recall
        self.save_intermediate = save_intermediate
        
        # 프롬프트 매니저 초기화
        self.prompt_manager = prompt_manager or create_default_prompt_manager()
        
        # 모듈들 초기화
        self.query_generator = PatentQueryGenerator(api_key)
        self.recall_analyzer = RecallAnalyzer(
            download_dir=download_dir,
            max_results=max_results,
            delay=delay,
            full_recall=full_recall
        )
        
        logger.info(f"Pipeline initialized (full_recall={full_recall}, max_results={max_results})")
    
    async def run_complete_analysis(
        self,
        pdf_path: Path,
        prompt_template: str,
        output_dir: Path = Path("./temp_results")
    ) -> Dict[str, Any]:
        """완전 자동화 파이프라인 실행"""
        logger.info(f"Starting complete patent analysis pipeline for: {pdf_path.name}")
        
        # 출력 디렉터리 생성
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 특허 번호 추출
        patent_number = extract_patent_number_from_filename(pdf_path)
        
        try:
            # STEP 1: 검색식 생성
            logger.info("STEP 1: Generating search queries with AI...")
            queries_data = await self.query_generator.generate_queries_from_pdf(
                pdf_path, prompt_template
            )
            
            # 중간 결과 저장 (선택적)
            if self.save_intermediate:
                queries_file = output_dir / f"{patent_number}_queries.json"
                self._save_json(queries_data, queries_file)
                logger.info(f"Queries saved to: {queries_file}")
            
            # 생성된 검색식 수 확인
            search_queries = queries_data.get("search_queries", [])
            logger.info(f"Generated {len(search_queries)} search queries")
            
            # STEP 2: Seed Recall 분석
            logger.info("STEP 2: Executing searches and analyzing recall...")
            recall_results = await self.recall_analyzer.analyze_recall(
                queries_data, patent_number
            )
            
            # 중간 결과 저장 (선택적)
            if self.save_intermediate:
                recall_file = output_dir / f"{patent_number}_recall.json"
                self._save_json(recall_results, recall_file)
                logger.info(f"Recall analysis saved to: {recall_file}")
            
            # STEP 3: 결과 통합
            logger.info("STEP 3: Combining results...")
            integrated_result = self._integrate_results(
                pdf_path, queries_data, recall_results
            )
            
            # 성공 로깅
            performance = recall_results.get("performance_analysis", {})
            logger.info(f"Pipeline completed successfully!")
            logger.info(f"Seed Recall Rate: {performance.get('seed_recall_rate', 0):.2%}")
            logger.info(f"Successful queries: {performance.get('successful_queries', 0)}/{performance.get('total_queries', 0)}")
            
            # 성과 기록 (프롬프트 전략이 있는 경우)
            strategy = getattr(self, '_current_strategy', None)
            if strategy and strategy != 'custom':
                prompt_manager = PromptManager()
                prompt_manager.record_performance(
                    strategy=strategy,
                    patent_number=patent_number,
                    performance_data={
                        "seed_recall_rate": performance.get("seed_recall_rate", 0),
                        "total_queries": performance.get("total_queries", 0),
                        "successful_queries": performance.get("successful_queries", 0),
                        "queries_found_patent": performance.get("queries_found_patent", 0),
                        "timestamp": datetime.now().isoformat()
                    }
                )
            
            return integrated_result
            
        except Exception as exc:
            logger.error(f"Complete analysis pipeline failed: {exc}")
            raise
    
    async def run_multi_prompt_analysis(
        self,
        pdf_path: Path,
        strategies: List[str],
        output_dir: Path = Path("./temp_results")
    ) -> Dict[str, Any]:
        """다중 프롬프트로 병렬 분석 실행"""
        logger.info(f"Starting multi-prompt analysis for: {pdf_path.name}")
        logger.info(f"Strategies: {strategies}")
        
        # 출력 디렉터리 생성
        output_dir.mkdir(parents=True, exist_ok=True)
        patent_number = extract_patent_number_from_filename(pdf_path)
        
        # 각 전략별 결과 저장
        strategy_results = {}
        best_result = None
        best_recall_rate = -1.0
        
        try:
            for i, strategy in enumerate(strategies, 1):
                logger.info(f"STRATEGY {i}/{len(strategies)}: {strategy}")
                
                # 프롬프트 로드
                try:
                    prompt_template = self.prompt_manager.get_prompt(strategy)
                except Exception as exc:
                    logger.error(f"Failed to load prompt '{strategy}': {exc}")
                    continue
                
                # 1. 검색식 생성
                logger.info(f"Generating queries with {strategy} strategy...")
                queries_data = await self.query_generator.generate_queries_from_pdf(
                    pdf_path, prompt_template
                )
                
                # 2. Recall 분석
                logger.info(f"Analyzing recall for {strategy}...")
                recall_results = await self.recall_analyzer.analyze_recall(
                    queries_data, patent_number
                )
                
                # 3. 결과 통합 (단일 전략 결과)
                integrated_result = self._integrate_results(
                    pdf_path, queries_data, recall_results
                )
                
                strategy_results[strategy] = integrated_result
                
                # 중간 결과 저장
                if self.save_intermediate:
                    result_file = output_dir / f"{patent_number}_{strategy}.json"
                    self._save_json(integrated_result, result_file)
                    logger.info(f"Strategy result saved: {result_file}")
                
                # 최고 성과 추적
                performance = recall_results.get("performance_analysis", {})
                recall_rate = performance.get("seed_recall_rate", 0)
                
                if recall_rate > best_recall_rate:
                    best_recall_rate = recall_rate
                    best_result = integrated_result
                    
                # 프롬프트 성과 기록
                self.prompt_manager.record_performance(
                    strategy, patent_number, performance
                )
                
                logger.info(f"Strategy {strategy}: Seed Recall = {recall_rate:.2%}")
                
                # 전략간 지연
                if i < len(strategies):
                    await asyncio.sleep(1.0)
                    
            # 통합 결과 생성
            # 전략별 쿼리 데이터와 리콜 결과 분리
            multi_queries_data = {}
            multi_recall_results = {}
            
            for strategy, result in strategy_results.items():
                multi_queries_data[strategy] = result.get("query_generation", {})
                # 리콜 결과는 전체 성과 분석을 포함한 딕셔너리 형태로 전달
                multi_recall_results[strategy] = {
                    "performance_analysis": result.get("performance_analysis", {}),
                    "search_results": result.get("search_execution", {}).get("search_results", [])
                }
                
            multi_result = self._integrate_results_multi(
                pdf_path, multi_queries_data, multi_recall_results
            )
            
            # 최고 성과 전략 기록
            if best_result:
                best_strategy = None
                for strategy, result in strategy_results.items():
                    recall_rate = result.get("performance_analysis", {}).get("seed_recall_rate", 0)
                    if recall_rate == best_recall_rate:
                        best_strategy = strategy
                        break
                        
                multi_result["metadata"]["best_strategy"] = best_strategy
                multi_result["best_result"] = best_result
                
            logger.info(f"Multi-prompt analysis completed!")
            logger.info(f"Best strategy: {best_strategy} (Recall: {best_recall_rate:.2%})")
            
            return multi_result
            
        except Exception as exc:
            logger.error(f"Multi-prompt analysis failed: {exc}")
            raise
            
        except Exception as exc:
            logger.error(f"❌ Pipeline failed: {exc}")
            
            # 부분 결과라도 반환
            partial_result = {
                "metadata": {
                    "pdf_file": str(pdf_path),
                    "patent_number": patent_number,
                    "analysis_timestamp": datetime.now().isoformat(),
                    "pipeline_status": "failed",
                    "error": str(exc)
                },
                "queries_data": locals().get("queries_data", {}),
                "recall_results": locals().get("recall_results", {}),
                "integrated_analysis": {}
            }
            
            return partial_result
    
    def _integrate_results(
        self, 
        pdf_path: Path, 
        queries_data: Dict[str, Any], 
        recall_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """검색식 생성 결과와 Recall 분석 결과를 통합"""
        patent_number = extract_patent_number_from_filename(pdf_path)
        
        # 성능 메트릭 계산
        performance = recall_results.get("performance_analysis", {})
        search_results = recall_results.get("search_results", [])
        
        # 각 검색식별 성과 요약
        query_performance = []
        for detail in performance.get("query_details", []):
            query_performance.append({
                "strategy": detail.get("strategy", "Unknown"),
                "query": detail.get("query", ""),
                "found_target": detail.get("found", False),
                "total_results": detail.get("total_results", 0),
                "parsed_results": detail.get("parsed_results", 0)
            })
        
        # 통합 결과 구성
        integrated_result = {
            "metadata": {
                "pdf_file": str(pdf_path),
                "patent_number": patent_number,
                "analysis_timestamp": datetime.now().isoformat(),
                "pipeline_version": "1.0",
                "pipeline_status": "completed",
                "settings": {
                    "max_results": self.max_results,
                    "delay": self.delay,
                    "full_recall": self.full_recall
                }
            },
            
            # 1단계: 검색식 생성 결과
            "query_generation": {
                "patent_info": queries_data.get("patent_info", {}),
                "search_queries": queries_data.get("search_queries", []),
                "generation_metadata": queries_data.get("metadata", {})
            },
            
            # 2단계: 검색 실행 결과
            "search_execution": {
                "search_results": search_results,
                "execution_metadata": recall_results.get("metadata", {})
            },
            
            # 3단계: 성능 분석 결과
            "performance_analysis": {
                **performance,
                "query_performance": query_performance
            },
            
            # 종합 결론
            "summary": {
                "total_queries_generated": len(queries_data.get("search_queries", [])),
                "successful_searches": performance.get("successful_queries", 0),
                "seed_recall_rate": performance.get("seed_recall_rate", 0),
                "queries_found_target": performance.get("queries_found_patent", 0),
                "avg_results_per_query": performance.get("avg_parsed_results", 0),
                "analysis_success": performance.get("seed_recall_rate", 0) > 0
            }
        }
        
        return integrated_result
    
    def _integrate_results_multi(
        self, 
        pdf_path: Path, 
        multi_queries_data: Dict[str, Dict[str, Any]], 
        multi_recall_results: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """다중 프롬프트 결과 통합"""
        patent_number = extract_patent_number_from_filename(pdf_path)
        
        # 전략별 성과 요약
        strategy_performances = {}
        all_queries = []
        
        for strategy, recall_data in multi_recall_results.items():
            performance = recall_data.get("performance_analysis", {})
            queries_data = multi_queries_data.get(strategy, {})
            
            strategy_performances[strategy] = {
                "strategy_name": strategy,
                "total_queries": len(queries_data.get("search_queries", [])),
                "successful_searches": performance.get("successful_queries", 0),
                "seed_recall_rate": performance.get("seed_recall_rate", 0),
                "queries_found_target": performance.get("queries_found_patent", 0),
                "avg_results_per_query": performance.get("avg_parsed_results", 0)
            }
            
            # 모든 쿼리 수집
            for query_detail in performance.get("query_details", []):
                all_queries.append({
                    **query_detail,
                    "strategy": strategy
                })
        
        # 최고 성과 전략 식별
        best_strategy, best_performance = self._get_best_strategy(strategy_performances)
        
        # 전략 비교 분석
        comparison = self._compare_strategies(strategy_performances)
        
        # 추천 사항 생성
        recommendations = self._generate_recommendations(strategy_performances, comparison)
        
        # 통합 결과 구성
        integrated_result = {
            "metadata": {
                "pdf_file": str(pdf_path),
                "patent_number": patent_number,
                "analysis_timestamp": datetime.now().isoformat(),
                "pipeline_version": "1.0-multi",
                "pipeline_status": "completed",
                "analysis_type": "multi_prompt",
                "strategies_used": list(multi_queries_data.keys()),
                "settings": {
                    "max_results": self.max_results,
                    "delay": self.delay,
                    "full_recall": self.full_recall
                }
            },
            
            # 전략별 상세 결과
            "strategy_results": {
                strategy: {
                    "query_generation": multi_queries_data.get(strategy, {}),
                    "recall_analysis": recall_data,
                    "performance_summary": strategy_performances[strategy]
                }
                for strategy, recall_data in multi_recall_results.items()
            },
            
            # 전략 비교 및 분석
            "strategy_comparison": {
                "best_strategy": {
                    "name": best_strategy,
                    "performance": best_performance
                },
                "performance_ranking": comparison["ranking"],
                "comparative_analysis": comparison["analysis"],
                "recommendations": recommendations
            },
            
            # 종합 성과 요약  
            "summary": {
                "total_strategies": len(multi_queries_data),
                "total_queries_generated": sum(len(data.get("search_queries", [])) for data in multi_queries_data.values()),
                "best_seed_recall_rate": best_performance.get("seed_recall_rate", 0),
                "strategies_found_target": sum(1 for perf in strategy_performances.values() if perf["queries_found_target"] > 0),
                "analysis_success": best_performance.get("seed_recall_rate", 0) > 0,
                "recommended_strategy": recommendations.get("primary_recommendation")
            }
        }
        
        return integrated_result
    
    def _get_best_strategy(self, strategy_performances: Dict[str, Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
        """최고 성과 전략 식별"""
        if not strategy_performances:
            return "none", {}
        
        # 1차: Seed Recall Rate 기준
        # 2차: 타겟 발견 쿼리 수 기준
        # 3차: 성공 쿼리 수 기준
        best_strategy = max(
            strategy_performances.items(),
            key=lambda x: (
                x[1]["seed_recall_rate"],
                x[1]["queries_found_target"],
                x[1]["successful_searches"]
            )
        )
        
        return best_strategy[0], best_strategy[1]
    
    def _compare_strategies(self, strategy_performances: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """전략간 성과 비교 분석"""
        # 성과순 순위
        ranking = sorted(
            strategy_performances.items(),
            key=lambda x: (
                x[1]["seed_recall_rate"],
                x[1]["queries_found_target"],
                x[1]["successful_searches"]
            ),
            reverse=True
        )
        
        # 성과 분석
        recall_rates = [perf["seed_recall_rate"] for perf in strategy_performances.values()]
        target_found_counts = [perf["queries_found_target"] for perf in strategy_performances.values()]
        
        analysis = {
            "recall_rate_range": {
                "min": min(recall_rates) if recall_rates else 0,
                "max": max(recall_rates) if recall_rates else 0,
                "avg": sum(recall_rates) / len(recall_rates) if recall_rates else 0
            },
            "strategies_found_target": sum(1 for count in target_found_counts if count > 0),
            "most_effective": ranking[0][0] if ranking else None,
            "performance_gap": (max(recall_rates) - min(recall_rates)) if recall_rates else 0
        }
        
        return {
            "ranking": [(name, perf) for name, perf in ranking],
            "analysis": analysis
        }
    
    def _generate_recommendations(
        self, 
        strategy_performances: Dict[str, Dict[str, Any]], 
        comparison: Dict[str, Any]
    ) -> Dict[str, Any]:
        """전략별 분석 결과 기반 추천 생성"""
        analysis = comparison["analysis"]
        ranking = comparison["ranking"]
        
        recommendations = {
            "primary_recommendation": ranking[0][0] if ranking else None,
            "reasoning": [],
            "alternative_strategies": [],
            "optimization_suggestions": []
        }
        
        if not ranking:
            return recommendations
        
        best_strategy, best_perf = ranking[0]
        
        # 1차 추천 근거
        if best_perf["seed_recall_rate"] > 0:
            recommendations["reasoning"].append(
                f"{best_strategy}가 {best_perf['seed_recall_rate']:.1%} Seed Recall Rate로 최고 성과"
            )
        else:
            recommendations["reasoning"].append(
                "모든 전략에서 타겟 특허를 발견하지 못함. 검색 조건 재검토 필요"
            )
        
        # 대안 전략 제안
        if len(ranking) > 1:
            second_best = ranking[1]
            if second_best[1]["seed_recall_rate"] > 0:
                recommendations["alternative_strategies"].append({
                    "strategy": second_best[0],
                    "reason": f"두 번째 높은 성과 ({second_best[1]['seed_recall_rate']:.1%})"
                })
        
        # 최적화 제안
        if analysis["performance_gap"] > 0.2:  # 20% 이상 차이
            recommendations["optimization_suggestions"].append(
                "전략간 성과 차이가 큼. 최고 성과 전략 기반 하이브리드 접근 고려"
            )
        
        if analysis["strategies_found_target"] < len(strategy_performances) / 2:
            recommendations["optimization_suggestions"].append(
                "대부분 전략에서 타겟 미발견. 기본 검색 조건 완화 권장"
            )
        
        return recommendations
    
    def _save_json(self, data: Dict[str, Any], file_path: Path) -> None:
        """JSON 데이터를 파일로 저장"""
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def setup_api_key(args) -> str:
    """API 키 설정"""
    if args.api_key:
        return args.api_key
    
    load_dotenv()
    api_key = os.getenv('GOOGLE_API_KEY')
    
    if not api_key:
        raise ValueError(
            "Google API Key가 필요합니다. "
            "1) --api-key 옵션 사용 또는 "
            "2) .env 파일에 GOOGLE_API_KEY 설정"
        )
    
    return api_key


def build_cli_parser() -> argparse.ArgumentParser:
    """CLI 파서 구성"""
    parser = argparse.ArgumentParser(
        description="특허 PDF 완전 자동화 분석 파이프라인",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    # 필수 인자
    parser.add_argument(
        "--pdf", 
        type=Path,
        required=True,
        help="분석할 특허 PDF 파일 경로"
    )
    
    parser.add_argument(
        "--output", 
        type=Path,
        required=True,
        help="최종 결과 저장 경로 (JSON 파일)"
    )
    
    # 선택적 인자
    # 프롬프트 전략 선택 (단일 또는 다중)
    prompt_group = parser.add_mutually_exclusive_group()
    
    prompt_group.add_argument(
        "--prompt",
        type=Path,
        help="단일 프롬프트 템플릿 파일 경로"
    )
    
    prompt_group.add_argument(
        "--strategy",
        type=str,
        choices=["base_template", "technical_depth", "application_focus", 
                "competitor_analysis", "prior_art", "evolution_tracking", "auto"],
        help="프롬프트 전략 선택 (auto: 자동 선택)"
    )
    
    prompt_group.add_argument(
        "--multi-strategy",
        nargs='+',
        choices=["base_template", "technical_depth", "application_focus", 
                "competitor_analysis", "prior_art", "evolution_tracking"],
        help="다중 프롬프트 전략 병렬 실행 (예: --multi-strategy technical_depth prior_art)"
    )
    
    parser.add_argument(
        "--api-key",
        type=str,
        help="Google Gemini API 키 (환경변수 GOOGLE_API_KEY 대신 사용)"
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
        help="전체 검색 수행 (early termination 비활성화, 더 정확하지만 오래 걸림)"
    )
    
    parser.add_argument(
        "--no-intermediate",
        action="store_true",
        help="중간 결과 파일 저장 안함 (최종 결과만 저장)"
    )
    
    return parser


async def main() -> int:
    """메인 실행 함수"""
    parser = build_cli_parser()
    args = parser.parse_args()
    
    try:
        # 입력 검증
        if not args.pdf.exists():
            logger.error(f"PDF file not found: {args.pdf}")
            return 1
        
        # API 키 설정
        api_key = setup_api_key(args)
        
        # 파이프라인 초기화
        pipeline = PatentAnalysisPipeline(
            api_key=api_key,
            download_dir=args.download_dir,
            max_results=args.max_results,
            delay=args.delay,
            full_recall=args.full_recall,
            save_intermediate=not args.no_intermediate
        )
        
        # 출력 디렉터리 설정
        output_dir = args.output.parent
        if output_dir != Path("."):
            output_dir.mkdir(parents=True, exist_ok=True)
        
        # 프롬프트 전략에 따른 실행 분기
        if args.multi_strategy:
            # 다중 전략 병렬 실행
            logger.info(f"🎯 Starting multi-strategy analysis with: {', '.join(args.multi_strategy)}")
            results = await pipeline.run_multi_prompt_analysis(
                args.pdf, 
                args.multi_strategy,
                output_dir
            )
            
            # 다중 전략 결과 요약 출력
            summary = results.get("summary", {})
            metadata = results.get("metadata", {})
            comparison = results.get("strategy_comparison", {})
            
            print(f"다중 전략 분석 완료!")
            print(f"특허: {metadata.get('patent_number', 'Unknown')}")
            print(f"실행 전략: {summary.get('total_strategies', 0)}개")
            print(f"총 검색식: {summary.get('total_queries_generated', 0)}개")
            print(f"최고 Seed Recall Rate: {summary.get('best_seed_recall_rate', 0):.2%}")
            print(f"추천 전략: {summary.get('recommended_strategy', 'None')}")
            
            if comparison.get("best_strategy"):
                best = comparison["best_strategy"]
                print(f"최고 성과 전략: {best['name']} ({best['performance'].get('seed_recall_rate', 0):.2%})")
            
        else:
            # 단일 전략 실행
            prompt_manager = PromptManager()
            
            if args.strategy:
                if args.strategy == "auto":
                    # PDF 분석 후 자동 선택
                    logger.info("Auto-selecting prompt strategy based on patent content...")
                    
                    # 기본 PDF 분석을 위해 임시로 base template 사용
                    temp_template = prompt_manager.get_prompt("base_template")
                    temp_queries = await pipeline.query_generator.generate_queries_from_pdf(
                        args.pdf, temp_template
                    )
                    
                    # 자동 선택
                    strategy, prompt_template = prompt_manager.auto_select_prompt(
                        temp_queries.get("patent_info", {})
                    )
                    logger.info(f"Auto-selected strategy: {strategy}")
                    
                else:
                    # 지정된 전략 사용
                    strategy = args.strategy
                    prompt_template = prompt_manager.get_prompt(strategy)
                    
            elif args.prompt:
                # 직접 프롬프트 파일 지정
                if not args.prompt.exists():
                    logger.error(f"Prompt template not found: {args.prompt}")
                    return 1
                prompt_template = load_prompt_template(args.prompt)
                strategy = "custom"
                
            else:
                # 기본값: base_template 사용
                strategy = "base_template" 
                prompt_template = prompt_manager.get_prompt(strategy)
                
            logger.info(f"🎯 Starting single-strategy analysis with: {strategy}")
            # 전략 추적을 위해 pipeline 객체에 전략 설정
            pipeline._current_strategy = strategy
            results = await pipeline.run_complete_analysis(
                args.pdf, 
                prompt_template, 
                output_dir
            )
            
            # 단일 전략 결과 요약 출력
            summary = results.get("summary", {})
            metadata = results.get("metadata", {})
            
            print(f"Pipeline 분석 완료!")
            print(f"전략: {strategy}")
            print(f"특허: {metadata.get('patent_number', 'Unknown')}")
            print(f"생성된 검색식: {summary.get('total_queries_generated', 0)}개")
            print(f"성공한 검색: {summary.get('successful_searches', 0)}개")
            print(f"Seed Recall Rate: {summary.get('seed_recall_rate', 0):.2%}")
            print(f"타겟 발견 검색식: {summary.get('queries_found_target', 0)}개")
        
        # 최종 결과 저장
        pipeline._save_json(results, args.output)
        print(f"최종 결과: {args.output}")
        
        # 성공 여부 판단
        if args.multi_strategy:
            analysis_success = results.get("summary", {}).get("analysis_success", False)
        else:
            analysis_success = results.get("summary", {}).get("analysis_success", False)
            
        if not analysis_success:
            logger.warning("⚠️  타겟 특허가 검색 결과에서 발견되지 않았습니다.")
            return 2
        
        return 0
        
    except Exception as exc:
        logger.error(f"Pipeline execution failed: {exc}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)