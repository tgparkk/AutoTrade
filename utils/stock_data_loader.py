"""
종목 데이터 로딩 유틸리티
"""

import json
import os
from typing import Dict, List, Optional
from utils.logger import setup_logger

logger = setup_logger(__name__)


class StockDataLoader:
    """종목 데이터를 로딩하고 관리하는 클래스"""
    
    def __init__(self, data_file: str = "data/stock_list.json"):
        """StockDataLoader 초기화
        
        Args:
            data_file: 종목 데이터 JSON 파일 경로
        """
        self.data_file = data_file
        self.stock_data: Dict[str, str] = {}  # {종목코드: 종목명}
        self.stock_list: List[Dict] = []  # 전체 종목 리스트
        self.total_stocks = 0
        
        self.load_stock_data()
    
    def load_stock_data(self) -> bool:
        """JSON 파일에서 종목 데이터 로드
        
        Returns:
            로드 성공 여부
        """
        try:
            if not os.path.exists(self.data_file):
                logger.error(f"종목 데이터 파일이 없습니다: {self.data_file}")
                return False
            
            with open(self.data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.stock_list = data.get('stocks', [])
            self.total_stocks = data.get('total_stocks', 0)
            
            # 빠른 검색을 위한 딕셔너리 생성
            self.stock_data = {
                stock['code']: stock['name'] 
                for stock in self.stock_list
            }
            
            logger.info(f"종목 데이터 로드 완료: {len(self.stock_data)}개 종목")
            return True
            
        except Exception as e:
            logger.error(f"종목 데이터 로드 실패: {e}")
            return False
    
    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """종목코드로 종목명 조회
        
        Args:
            stock_code: 종목코드
            
        Returns:
            종목명 또는 None
        """
        return self.stock_data.get(stock_code)
    
    def get_stock_code_by_name(self, stock_name: str) -> Optional[str]:
        """종목명으로 종목코드 조회 (부분 매칭)
        
        Args:
            stock_name: 종목명 (부분 검색 가능)
            
        Returns:
            종목코드 또는 None
        """
        for code, name in self.stock_data.items():
            if stock_name in name:
                return code
        return None
    
    def search_stocks(self, keyword: str) -> List[Dict]:
        """키워드로 종목 검색
        
        Args:
            keyword: 검색 키워드 (종목명에 포함된 키워드)
            
        Returns:
            매칭되는 종목들의 리스트
        """
        matches = []
        for stock in self.stock_list:
            if keyword in stock['name'] or keyword in stock['code']:
                matches.append(stock)
        return matches
    
    def get_stocks_by_prefix(self, prefix: str) -> List[Dict]:
        """특정 접두사로 시작하는 종목들 조회
        
        Args:
            prefix: 종목코드 접두사 (예: "005" → 삼성 계열)
            
        Returns:
            해당 접두사로 시작하는 종목들
        """
        matches = []
        for stock in self.stock_list:
            if stock['code'].startswith(prefix):
                matches.append(stock)
        return matches
    
    def get_random_stocks(self, count: int) -> List[Dict]:
        """랜덤 종목 선택
        
        Args:
            count: 선택할 종목 수
            
        Returns:
            랜덤하게 선택된 종목들
        """
        import random
        return random.sample(self.stock_list, min(count, len(self.stock_list)))
    
    def validate_stock_code(self, stock_code: str) -> bool:
        """종목코드 유효성 검증
        
        Args:
            stock_code: 검증할 종목코드
            
        Returns:
            유효한 종목코드 여부
        """
        return stock_code in self.stock_data
    
    def get_stock_info(self, stock_code: str) -> Optional[Dict]:
        """종목 상세 정보 조회
        
        Args:
            stock_code: 종목코드
            
        Returns:
            종목 정보 딕셔너리
        """
        stock_name = self.get_stock_name(stock_code)
        if stock_name:
            return {
                'code': stock_code,
                'name': stock_name,
                'is_preferred': 'K' in stock_code or '우' in stock_name,
                'is_special': any(c.isalpha() for c in stock_code),
                'code_length': len(stock_code)
            }
        return None
    
    def get_statistics(self) -> Dict:
        """종목 데이터 통계 정보
        
        Returns:
            통계 정보 딕셔너리
        """
        preferred_count = sum(1 for stock in self.stock_list if '우' in stock['name'])
        special_count = sum(1 for stock in self.stock_list if any(c.isalpha() for c in stock['code']))
        
        return {
            'total_stocks': self.total_stocks,
            'loaded_stocks': len(self.stock_data),
            'preferred_stocks': preferred_count,
            'special_stocks': special_count,
            'regular_stocks': len(self.stock_data) - preferred_count - special_count
        }
    
    def __len__(self) -> int:
        """로드된 종목 수 반환"""
        return len(self.stock_data)
    
    def __contains__(self, stock_code: str) -> bool:
        """종목코드 포함 여부 확인"""
        return stock_code in self.stock_data
    
    def __str__(self) -> str:
        """문자열 표현"""
        return f"StockDataLoader({len(self.stock_data)}개 종목)"


# 전역 인스턴스 (싱글톤 패턴)
_stock_loader = None

def get_stock_data_loader() -> StockDataLoader:
    """StockDataLoader 싱글톤 인스턴스 반환"""
    global _stock_loader
    if _stock_loader is None:
        _stock_loader = StockDataLoader()
    return _stock_loader 