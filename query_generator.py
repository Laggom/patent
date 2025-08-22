"""Google Patents 검색식 생성 및 확장 도구

Google Gemini API를 사용하여 특허 PDF를 분석하고,
유사 특허 검색을 위한 검색식을 자동 생성합니다.

사용 예시:
  # 기본 검색식 생성
  python query_generator.py --pdf patent.pdf --output temp_results/queries.json
  
  # 커스텀 프롬프트 사용
  python query_generator.py --pdf patent.pdf --prompt custom_prompt.txt --output queries.json
  
  # API 키 직접 지정
  python query_generator.py --pdf patent.pdf --api-key YOUR_API_KEY --output queries.json

주요 기능:
- PDF를 Gemini API에 직접 업로드하여 분석
- AI 기반 다중 검색 전략 생성 (broad, medium, narrow)
- Google Patents 검색 문법에 최적화된 쿼리 생성
- JSON 형태의 구조화된 검색식 출력

필수 설정:
1. pip install -r requirements.txt
2. Google AI Studio에서 API 키 발급
3. .env 파일에 GOOGLE_API_KEY 설정 또는 --api-key 사용
"""

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import google.generativeai as genai
from dotenv import load_dotenv
from loguru import logger


def extract_patent_number_from_filename(pdf_path: Path) -> str:
    """PDF 파일명에서 특허번호 추출 (확장자 제거)
    
    예: US8771637B2.pdf → US8771637B2
    """
    return pdf_path.stem


class PatentQueryGenerator:
    """특허 PDF 기반 검색식 생성 클래스"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        
        # Gemini 설정
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash-exp")
        
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
    
    async def generate_queries_from_pdf(
        self, 
        pdf_path: Path,
        prompt_template: str
    ) -> Dict[str, Any]:
        """PDF에서 검색식 생성하는 전체 파이프라인"""
        logger.info(f"Starting query generation for: {pdf_path}")
        
        # 1. PDF 업로드
        uploaded_file_name = await self.upload_pdf_to_gemini(pdf_path)
        
        try:
            # 2. 검색식 생성
            queries_data = await self.generate_queries(uploaded_file_name, prompt_template)
            
            # 3. 메타데이터 추가
            patent_number = extract_patent_number_from_filename(pdf_path)
            final_result = {
                "metadata": {
                    "pdf_file": str(pdf_path),
                    "patent_number": patent_number,
                    "generation_timestamp": datetime.now().isoformat(),
                    "model_used": "gemini-2.0-flash-exp"
                },
                "patent_info": queries_data.get("patent_info", {}),
                "search_queries": queries_data.get("search_queries", [])
            }
            
            logger.info(f"Query generation completed successfully")
            return final_result
            
        finally:
            # 업로드된 파일 정리
            try:
                genai.delete_file(uploaded_file_name)
                logger.debug(f"Deleted uploaded file: {uploaded_file_name}")
            except Exception as cleanup_exc:
                logger.warning(f"Failed to cleanup uploaded file: {cleanup_exc}")


def load_prompt_template(prompt_path: Path) -> str:
    """프롬프트 템플릿 로드"""
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")
    
    return prompt_path.read_text(encoding='utf-8')


def save_results(results: Dict[str, Any], output_path: Path) -> None:
    """결과를 JSON 파일로 저장"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Results saved to: {output_path}")


def setup_api_key(args) -> str:
    """API 키 설정"""
    # 명령줄 인자에서 API 키 확인
    if args.api_key:
        return args.api_key
    
    # 환경 변수에서 API 키 확인
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
        description="PDF 특허에서 Google Patents 검색식 생성",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--pdf", 
        type=Path,
        required=True,
        help="분석할 특허 PDF 파일 경로"
    )
    
    parser.add_argument(
        "--prompt",
        type=Path,
        default=Path("analyzer_prompt.txt"),
        help="Gemini용 프롬프트 템플릿 파일 (기본값: analyzer_prompt.txt)"
    )
    
    parser.add_argument(
        "--output", 
        type=Path,
        default=Path("temp_results/queries.json"),
        help="결과 저장 경로 (기본값: temp_results/queries.json)"
    )
    
    parser.add_argument(
        "--api-key",
        type=str,
        help="Google Gemini API 키 (환경변수 GOOGLE_API_KEY 대신 사용)"
    )
    
    return parser


async def main() -> int:
    """메인 실행 함수"""
    parser = build_cli_parser()
    args = parser.parse_args()
    
    try:
        # API 키 설정
        api_key = setup_api_key(args)
        
        # PDF 파일 확인
        if not args.pdf.exists():
            logger.error(f"PDF file not found: {args.pdf}")
            return 1
        
        # 프롬프트 템플릿 로드
        prompt_template = load_prompt_template(args.prompt)
        
        # 검색식 생성기 초기화
        generator = PatentQueryGenerator(api_key)
        
        # 검색식 생성 실행
        results = await generator.generate_queries_from_pdf(
            args.pdf,
            prompt_template
        )
        
        # 결과 저장
        save_results(results, args.output)
        
        # 간단한 요약 출력
        queries = results.get("search_queries", [])
        patent_info = results.get("patent_info", {})
        
        print(f"✅ 검색식 생성 완료")
        print(f"📄 특허: {patent_info.get('title', '제목 없음')}")
        print(f"🔍 생성된 검색식: {len(queries)}개")
        print(f"💾 결과 저장: {args.output}")
        
        for i, q in enumerate(queries, 1):
            print(f"  {i}. {q.get('strategy', 'Unknown')}: {q.get('description', 'No description')}")
        
        return 0
        
    except Exception as exc:
        logger.error(f"Query generation failed: {exc}")
        return 1


if __name__ == "__main__":
    import os
    exit_code = asyncio.run(main())
    exit(exit_code)