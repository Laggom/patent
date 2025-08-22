"""특허 검색식 생성 프롬프트 관리 시스템

다양한 검색 전략에 따른 프롬프트 템플릿을 관리하고, 
특허 내용에 기반하여 최적의 프롬프트를 자동 선택하는 시스템입니다.

사용 예시:
  # 프롬프트 매니저 초기화
  manager = PromptManager()
  
  # 특정 전략 선택
  prompt = manager.get_prompt("technical_depth")
  
  # 자동 선택
  prompt = manager.auto_select_prompt(pdf_content_analysis)
  
  # 멀티 프롬프트 실행  
  prompts = manager.get_multi_prompts(["technical_depth", "competitor_analysis"])
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum

from loguru import logger


class PromptStrategy(Enum):
    """프롬프트 전략 유형"""
    BASE = "base_template"
    TECHNICAL_DEPTH = "technical_depth"
    APPLICATION_FOCUS = "application_focus" 
    COMPETITOR_ANALYSIS = "competitor_analysis"
    PRIOR_ART = "prior_art"
    EVOLUTION_TRACKING = "evolution_tracking"


@dataclass
class PromptMetadata:
    """프롬프트 메타데이터"""
    strategy: str
    name: str
    description: str
    best_for: List[str]  # 최적 사용 사례
    tech_domains: List[str]  # 적합한 기술 도메인
    query_count: int  # 생성할 검색식 수
    focus_areas: List[str]  # 주요 포커스 영역


@dataclass
class PerformanceRecord:
    """프롬프트 성과 기록"""
    strategy: str
    patent_number: str
    seed_recall_rate: float
    total_queries: int
    successful_queries: int
    found_target: bool
    timestamp: str


class PromptManager:
    """프롬프트 관리 및 선택 시스템"""
    
    def __init__(self, prompts_dir: Path = Path("./prompts")):
        self.prompts_dir = prompts_dir
        self.prompt_metadata = {}
        self.performance_history = []
        self._load_prompt_metadata()
        self._load_performance_history()
    
    def _load_prompt_metadata(self):
        """프롬프트 메타데이터 초기화"""
        self.prompt_metadata = {
            "base_template": PromptMetadata(
                strategy="base_template",
                name="기본 템플릿",
                description="표준 검색식 생성 (broad/medium/narrow)",
                best_for=["일반적 특허 분석", "기본 검색"],
                tech_domains=["모든 분야"],
                query_count=3,
                focus_areas=["키워드", "분류코드", "출원인"]
            ),
            "technical_depth": PromptMetadata(
                strategy="technical_depth", 
                name="기술적 세부사항 중심",
                description="기술 메커니즘과 구현 세부사항 중심 검색",
                best_for=["기술 분석", "R&D 검토", "기술적 무효 자료"],
                tech_domains=["화학", "반도체", "소재", "제조"],
                query_count=5,
                focus_areas=["메커니즘", "소재", "공정", "파라미터", "구조"]
            ),
            "application_focus": PromptMetadata(
                strategy="application_focus",
                name="응용 분야 중심", 
                description="기술의 응용 분야와 사용 사례 중심 검색",
                best_for=["시장 분석", "제품 개발", "라이센싱"],
                tech_domains=["소프트웨어", "의료기기", "자동차", "전자제품"],
                query_count=5,
                focus_areas=["산업", "제품", "용도", "시장", "사용자"]
            ),
            "competitor_analysis": PromptMetadata(
                strategy="competitor_analysis",
                name="경쟁사 분석 중심",
                description="경쟁사 및 경쟁 기술 발굴 중심 검색",
                best_for=["경쟁 분석", "FTO 분석", "침해 분석"],  
                tech_domains=["모든 분야"],
                query_count=6,
                focus_areas=["경쟁사", "대안기술", "우회설계", "시장점유"]
            ),
            "prior_art": PromptMetadata(
                strategy="prior_art",
                name="선행기술 발굴 중심",
                description="무효 자료 및 선행기술 발굴 특화 검색",
                best_for=["무효심판", "재심사", "특허 무효화"],
                tech_domains=["모든 분야"],
                query_count=7,
                focus_areas=["선행기술", "무효포인트", "청구항", "시간제한"]
            ),
            "evolution_tracking": PromptMetadata(
                strategy="evolution_tracking", 
                name="기술 진화 추적",
                description="기술의 시간적 진화와 발전 경로 추적",
                best_for=["기술 동향", "로드맵 수립", "미래 예측"],
                tech_domains=["모든 분야"],
                query_count=6,
                focus_areas=["시간축", "진화경로", "트렌드", "미래예측"]
            )
        }
    
    def _load_performance_history(self):
        """성과 이력 로드"""
        history_file = self.prompts_dir.parent / "performance_history.json"
        
        if not history_file.exists():
            logger.debug(f"Performance history file not found: {history_file}")
            return
            
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # JSON 데이터를 PerformanceRecord 객체로 변환
            self.performance_history = []
            for record_data in data.get("records", []):
                record = PerformanceRecord(
                    strategy=record_data["strategy"],
                    patent_number=record_data["patent_number"],
                    seed_recall_rate=record_data["seed_recall_rate"],
                    total_queries=record_data["total_queries"],
                    successful_queries=record_data["successful_queries"],
                    found_target=record_data["found_target"],
                    timestamp=record_data["timestamp"]
                )
                self.performance_history.append(record)
                
            logger.info(f"Loaded {len(self.performance_history)} performance records")
            
        except Exception as exc:
            logger.error(f"Failed to load performance history: {exc}")
            self.performance_history = []
    
    def get_available_strategies(self) -> List[str]:
        """사용 가능한 전략 목록 반환"""
        return list(self.prompt_metadata.keys())
    
    def get_strategy_info(self, strategy: str) -> Optional[PromptMetadata]:
        """특정 전략의 메타데이터 반환"""
        return self.prompt_metadata.get(strategy)
    
    def get_prompt(self, strategy: str) -> str:
        """특정 전략의 프롬프트 텍스트 반환"""
        prompt_file = self.prompts_dir / f"{strategy}.txt"
        
        if not prompt_file.exists():
            logger.error(f"Prompt file not found: {prompt_file}")
            # 기본 템플릿으로 fallback
            prompt_file = self.prompts_dir / "base_template.txt"
            
        if not prompt_file.exists():
            raise FileNotFoundError(f"No prompt files available in {self.prompts_dir}")
            
        return prompt_file.read_text(encoding='utf-8')
    
    def auto_select_prompt(self, patent_analysis: Dict[str, Any]) -> Tuple[str, str]:
        """특허 내용 분석 기반 최적 프롬프트 자동 선택"""
        
        # 기술 분야 기반 선택
        tech_field = patent_analysis.get("technology_field", "").lower()
        title = patent_analysis.get("title", "").lower()
        keywords = [k.lower() for k in patent_analysis.get("keywords", [])]
        
        # 키워드 기반 전략 점수 계산
        strategy_scores = {}
        
        # 화학/소재 관련 키워드
        chemistry_keywords = ["chemical", "polymer", "membrane", "catalyst", "synthesis", "composition", "material"]
        if any(keyword in tech_field + title + " ".join(keywords) for keyword in chemistry_keywords):
            strategy_scores["technical_depth"] = strategy_scores.get("technical_depth", 0) + 3
            
        # 응용/제품 관련 키워드  
        application_keywords = ["system", "device", "method", "apparatus", "application", "use"]
        if any(keyword in tech_field + title + " ".join(keywords) for keyword in application_keywords):
            strategy_scores["application_focus"] = strategy_scores.get("application_focus", 0) + 2
            
        # 경쟁 분석이 유용한 키워드
        competitive_keywords = ["improvement", "enhanced", "optimized", "advanced", "novel"]
        if any(keyword in tech_field + title + " ".join(keywords) for keyword in competitive_keywords):
            strategy_scores["competitor_analysis"] = strategy_scores.get("competitor_analysis", 0) + 2
            
        # 기본 점수 설정
        for strategy in self.prompt_metadata:
            if strategy not in strategy_scores:
                strategy_scores[strategy] = 1
                
        # 과거 성과 기반 보정 (있는 경우)
        for strategy in strategy_scores:
            performance = self.get_strategy_performance(strategy)
            if performance["count"] > 0:
                # 과거 성과가 좋은 전략에 가산점
                historical_boost = performance["avg_recall"] * 2  # 최대 2점 가산점
                strategy_scores[strategy] += historical_boost
                logger.debug(f"Historical boost for {strategy}: +{historical_boost:.2f} (avg recall: {performance['avg_recall']:.1%})")
                
        # 최고 점수 전략 선택
        best_strategy = max(strategy_scores.items(), key=lambda x: x[1])[0]
        
        logger.info(f"Auto-selected prompt strategy: {best_strategy}")
        logger.debug(f"Strategy scores: {strategy_scores}")
        
        return best_strategy, self.get_prompt(best_strategy)
    
    def get_multi_prompts(self, strategies: List[str]) -> Dict[str, str]:
        """다중 프롬프트 반환"""
        result = {}
        for strategy in strategies:
            try:
                result[strategy] = self.get_prompt(strategy)
            except Exception as exc:
                logger.error(f"Failed to load prompt '{strategy}': {exc}")
                
        return result
    
    def get_recommended_strategies(self, 
                                 patent_analysis: Dict[str, Any], 
                                 top_k: int = 3) -> List[Tuple[str, float]]:
        """추천 전략들을 점수순으로 반환"""
        
        tech_field = patent_analysis.get("technology_field", "").lower()
        title = patent_analysis.get("title", "").lower()
        content = tech_field + " " + title
        
        recommendations = []
        
        for strategy, metadata in self.prompt_metadata.items():
            score = 0.0
            
            # 기술 도메인 매칭
            if "모든 분야" in metadata.tech_domains:
                score += 1.0
            else:
                for domain in metadata.tech_domains:
                    if domain.lower() in content:
                        score += 2.0
                        
            # 과거 성과 기반 점수 (향후 구현)
            # historical_performance = self._get_historical_performance(strategy)
            # score += historical_performance
            
            recommendations.append((strategy, score))
            
        # 점수순 정렬 후 상위 k개 반환
        recommendations.sort(key=lambda x: x[1], reverse=True)
        return recommendations[:top_k]
    
    def record_performance(self, 
                          strategy: str,
                          patent_number: str, 
                          performance_data: Dict[str, Any]):
        """프롬프트 성과 기록"""
        record = PerformanceRecord(
            strategy=strategy,
            patent_number=patent_number,
            seed_recall_rate=performance_data.get("seed_recall_rate", 0.0),
            total_queries=performance_data.get("total_queries", 0),
            successful_queries=performance_data.get("successful_queries", 0), 
            found_target=performance_data.get("queries_found_patent", 0) > 0,
            timestamp=performance_data.get("timestamp", "")
        )
        
        self.performance_history.append(record)
        logger.info(f"Recorded performance for {strategy}: Seed Recall = {record.seed_recall_rate:.2%}")
        
        # 자동으로 히스토리 저장
        self._save_performance_history()
    
    def _save_performance_history(self):
        """성과 이력 저장"""
        history_file = self.prompts_dir.parent / "performance_history.json"
        
        try:
            # 디렉터리 생성
            history_file.parent.mkdir(parents=True, exist_ok=True)
            
            # 데이터를 JSON 형태로 변환
            data = {
                "metadata": {
                    "last_updated": datetime.now().isoformat(),
                    "total_records": len(self.performance_history),
                    "version": "1.0"
                },
                "records": []
            }
            
            for record in self.performance_history:
                data["records"].append({
                    "strategy": record.strategy,
                    "patent_number": record.patent_number,
                    "seed_recall_rate": record.seed_recall_rate,
                    "total_queries": record.total_queries,
                    "successful_queries": record.successful_queries,
                    "found_target": record.found_target,
                    "timestamp": record.timestamp
                })
            
            # JSON 파일로 저장
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
            logger.debug(f"Performance history saved to: {history_file}")
            
        except Exception as exc:
            logger.error(f"Failed to save performance history: {exc}")
    
    def get_strategy_performance(self, strategy: str) -> Dict[str, Any]:
        """특정 전략의 평균 성과 반환"""
        records = [r for r in self.performance_history if r.strategy == strategy]
        
        if not records:
            return {"count": 0, "avg_recall": 0.0, "success_rate": 0.0}
            
        avg_recall = sum(r.seed_recall_rate for r in records) / len(records)
        success_rate = sum(1 for r in records if r.found_target) / len(records)
        
        return {
            "count": len(records),
            "avg_recall": avg_recall,
            "success_rate": success_rate,
            "total_patents": len(set(r.patent_number for r in records))
        }
    
    def get_performance_summary(self) -> Dict[str, Dict[str, Any]]:
        """전체 전략별 성과 요약"""
        summary = {}
        for strategy in self.prompt_metadata:
            summary[strategy] = self.get_strategy_performance(strategy)
        return summary


def create_default_prompt_manager() -> PromptManager:
    """기본 프롬프트 매니저 생성"""
    return PromptManager()


# CLI 지원 함수들
def list_available_strategies():
    """사용 가능한 전략들 출력"""
    manager = create_default_prompt_manager()
    print("Available prompt strategies:")
    print("=" * 50)
    
    for strategy in manager.get_available_strategies():
        info = manager.get_strategy_info(strategy)
        print(f"• {strategy}")
        print(f"  Name: {info.name}")
        print(f"  Description: {info.description}")
        print(f"  Best for: {', '.join(info.best_for)}")
        print(f"  Queries: {info.query_count}")
        print()


if __name__ == "__main__":
    # 간단한 CLI 테스트
    import argparse
    
    parser = argparse.ArgumentParser(description="Prompt Manager CLI")
    parser.add_argument("--list", action="store_true", help="List available strategies")
    parser.add_argument("--test-auto", help="Test auto-selection with patent info JSON file")
    
    args = parser.parse_args()
    
    if args.list:
        list_available_strategies()
    elif args.test_auto:
        manager = create_default_prompt_manager()
        
        # 테스트 데이터
        test_analysis = {
            "technology_field": "Gas separation membrane technology",
            "title": "GAS SEPARATION MEMBRANES BASED ON PERFLUORINATED POLYMERS", 
            "keywords": ["gas separation", "membrane", "polymer", "perfluorinated"]
        }
        
        strategy, prompt = manager.auto_select_prompt(test_analysis)
        print(f"Auto-selected strategy: {strategy}")
        
        recommendations = manager.get_recommended_strategies(test_analysis)
        print(f"Top recommendations: {recommendations}")
    else:
        print("Use --list to see available strategies or --test-auto to test auto-selection")