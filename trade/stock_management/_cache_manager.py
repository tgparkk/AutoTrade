#!/usr/bin/env python3
"""
Stock 캐시 관리 모듈

StockManager의 캐시 관련 기능을 분리한 내부 헬퍼 클래스
"""

import threading
from typing import Dict, Optional, TYPE_CHECKING
from utils.korean_time import now_kst
from utils.logger import setup_logger

if TYPE_CHECKING:
    from models.stock import Stock

logger = setup_logger(__name__)


class _StockCacheManager:
    """Stock 객체 캐시 관리 전담 클래스 (내부 전용)"""
    
    def __init__(self, 
                 cache_ttl_seconds: float = 2.0,
                 enable_cache_debug: bool = False):
        """캐시 매니저 초기화
        
        Args:
            cache_ttl_seconds: 캐시 TTL(초 단위)
            enable_cache_debug: 캐시 디버그 로그 활성화 여부
        """
        self._cache_ttl_seconds = cache_ttl_seconds
        self._enable_cache_debug = enable_cache_debug
        
        # 캐시 저장소
        self._stock_cache: Dict[str, "Stock"] = {}
        self._cache_timestamps: Dict[str, float] = {}
        
        # 캐시 전용 락
        self._cache_lock = threading.RLock()
        
        logger.info(f"StockCacheManager 초기화: TTL={cache_ttl_seconds}초, 디버그={enable_cache_debug}")
    
    def get_cached_stock(self, stock_code: str) -> Optional["Stock"]:
        """캐시에서 Stock 객체 조회
        
        Args:
            stock_code: 종목코드
            
        Returns:
            캐시된 Stock 객체 (유효하지 않으면 None)
        """
        try:
            current_time = now_kst().timestamp()
            
            with self._cache_lock:
                # 캐시 존재 여부 확인
                if (stock_code not in self._stock_cache or 
                    stock_code not in self._cache_timestamps):
                    return None
                
                # TTL 검사
                cache_age = current_time - self._cache_timestamps[stock_code]
                if cache_age >= self._cache_ttl_seconds:
                    # 만료된 캐시 제거
                    self._stock_cache.pop(stock_code, None)
                    self._cache_timestamps.pop(stock_code, None)
                    if self._enable_cache_debug:
                        logger.debug(f"캐시 만료 제거: {stock_code} (age: {cache_age:.2f}초)")
                    return None
                
                # 유효한 캐시 반환
                if self._enable_cache_debug:
                    logger.debug(f"캐시 적중: {stock_code} (age: {cache_age:.2f}초)")
                return self._stock_cache[stock_code]
                
        except Exception as e:
            logger.error(f"캐시 조회 오류 {stock_code}: {e}")
            return None
    
    def cache_stock(self, stock_code: str, stock: "Stock") -> None:
        """Stock 객체를 캐시에 저장
        
        Args:
            stock_code: 종목코드
            stock: Stock 객체
        """
        try:
            current_time = now_kst().timestamp()
            
            with self._cache_lock:
                self._stock_cache[stock_code] = stock
                self._cache_timestamps[stock_code] = current_time
                
                if self._enable_cache_debug:
                    logger.debug(f"캐시 저장: {stock_code}")
                    
        except Exception as e:
            logger.error(f"캐시 저장 오류 {stock_code}: {e}")
    
    def invalidate_cache(self, stock_code: str) -> None:
        """특정 종목의 캐시 무효화
        
        Args:
            stock_code: 종목코드
        """
        try:
            with self._cache_lock:
                removed_cache = self._stock_cache.pop(stock_code, None)
                removed_timestamp = self._cache_timestamps.pop(stock_code, None)
                
                if removed_cache and self._enable_cache_debug:
                    logger.debug(f"캐시 무효화: {stock_code}")
                    
        except Exception as e:
            logger.error(f"캐시 무효화 오류 {stock_code}: {e}")
    
    def clear_all_cache(self) -> None:
        """모든 캐시 삭제"""
        try:
            with self._cache_lock:
                cache_count = len(self._stock_cache)
                self._stock_cache.clear()
                self._cache_timestamps.clear()
                
                if cache_count > 0:
                    logger.info(f"전체 캐시 삭제: {cache_count}개 항목")
                    
        except Exception as e:
            logger.error(f"전체 캐시 삭제 오류: {e}")
    
    def get_cache_stats(self) -> Dict:
        """캐시 통계 정보 반환
        
        Returns:
            캐시 통계 딕셔너리
        """
        try:
            current_time = now_kst().timestamp()
            
            with self._cache_lock:
                total_count = len(self._stock_cache)
                valid_count = 0
                expired_count = 0
                
                for stock_code, timestamp in self._cache_timestamps.items():
                    cache_age = current_time - timestamp
                    if cache_age < self._cache_ttl_seconds:
                        valid_count += 1
                    else:
                        expired_count += 1
                
                return {
                    'total_cached': total_count,
                    'valid_cached': valid_count,
                    'expired_cached': expired_count,
                    'cache_ttl_seconds': self._cache_ttl_seconds,
                    'debug_enabled': self._enable_cache_debug
                }
                
        except Exception as e:
            logger.error(f"캐시 통계 조회 오류: {e}")
            return {}
    
    def cleanup_expired_cache(self) -> int:
        """만료된 캐시 정리
        
        Returns:
            정리된 캐시 항목 수
        """
        try:
            current_time = now_kst().timestamp()
            cleanup_count = 0
            
            with self._cache_lock:
                expired_codes = []
                
                for stock_code, timestamp in self._cache_timestamps.items():
                    cache_age = current_time - timestamp
                    if cache_age >= self._cache_ttl_seconds:
                        expired_codes.append(stock_code)
                
                for stock_code in expired_codes:
                    self._stock_cache.pop(stock_code, None)
                    self._cache_timestamps.pop(stock_code, None)
                    cleanup_count += 1
                
                if cleanup_count > 0 and self._enable_cache_debug:
                    logger.debug(f"만료 캐시 정리: {cleanup_count}개 항목")
                
                return cleanup_count
                
        except Exception as e:
            logger.error(f"만료 캐시 정리 오류: {e}")
            return 0
    
    def __str__(self) -> str:
        with self._cache_lock:
            return f"StockCacheManager(캐시: {len(self._stock_cache)}개, TTL: {self._cache_ttl_seconds}초)" 