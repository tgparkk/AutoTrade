#!/usr/bin/env python3
"""
KIS 웹소켓 구독 관리 전담 클래스
"""
import threading
from typing import Set, Dict, List, Callable, Optional
from utils.logger import setup_logger

logger = setup_logger(__name__)


class KISWebSocketSubscriptionManager:
    """KIS 웹소켓 구독 관리 전담 클래스"""

    def __init__(self, max_stocks: int = 19):
        # 웹소켓 제한
        self.WEBSOCKET_LIMIT = 41
        self.MAX_STOCKS = max_stocks

        # 구독 관리
        self.subscribed_stocks: Set[str] = set()
        self.subscription_lock = threading.Lock()

        # 다중 콜백 시스템
        self.stock_callbacks: Dict[str, List[Callable]] = {}  # 종목별 콜백들
        self.global_callbacks: Dict[str, List[Callable]] = {   # 데이터 타입별 글로벌 콜백들
            'stock_price': [],
            'stock_orderbook': [],
            'stock_execution': [],
            'market_index': []
        }

        # 통계
        self.stats = {
            'subscriptions': 0
        }

    def add_global_callback(self, data_type: str, callback: Callable[[Dict], None]):
        """글로벌 콜백 함수 추가"""
        if data_type in self.global_callbacks:
            self.global_callbacks[data_type].append(callback)
            logger.debug(f"글로벌 콜백 추가: {data_type}")

    def remove_global_callback(self, data_type: str, callback: Callable[[Dict], None]):
        """글로벌 콜백 함수 제거"""
        if data_type in self.global_callbacks and callback in self.global_callbacks[data_type]:
            self.global_callbacks[data_type].remove(callback)
            logger.debug(f"글로벌 콜백 제거: {data_type}")

    def add_stock_callback(self, stock_code: str, callback: Callable):
        """종목별 콜백 함수 추가"""
        if stock_code not in self.stock_callbacks:
            self.stock_callbacks[stock_code] = []
        self.stock_callbacks[stock_code].append(callback)
        logger.debug(f"종목별 콜백 추가: {stock_code}")

    def remove_stock_callback(self, stock_code: str, callback: Callable):
        """종목별 콜백 함수 제거"""
        if stock_code in self.stock_callbacks and callback in self.stock_callbacks[stock_code]:
            self.stock_callbacks[stock_code].remove(callback)
            if not self.stock_callbacks[stock_code]:
                del self.stock_callbacks[stock_code]
            logger.debug(f"종목별 콜백 제거: {stock_code}")

    def can_subscribe(self, stock_code: str) -> bool:
        """구독 가능 여부 확인"""
        with self.subscription_lock:
            # 이미 구독 중인지 확인
            if stock_code in self.subscribed_stocks:
                return True  # 이미 구독 중이므로 문제없음

            # 구독 한계 확인
            return len(self.subscribed_stocks) < self.MAX_STOCKS

    def add_subscription(self, stock_code: str) -> bool:
        """구독 목록에 추가"""
        with self.subscription_lock:
            if len(self.subscribed_stocks) >= self.MAX_STOCKS:
                logger.warning(f"구독 한계 도달: {len(self.subscribed_stocks)}/{self.MAX_STOCKS}")
                return False

            if stock_code in self.subscribed_stocks:
                logger.debug(f"이미 구독 중인 종목: {stock_code}")
                return True

            self.subscribed_stocks.add(stock_code)
            self.stats['subscriptions'] += 1
            return True

    def remove_subscription(self, stock_code: str):
        """구독 목록에서 제거"""
        with self.subscription_lock:
            self.subscribed_stocks.discard(stock_code)
            # 콜백도 제거
            self.stock_callbacks.pop(stock_code, None)

    def is_subscribed(self, stock_code: str) -> bool:
        """구독 여부 확인"""
        with self.subscription_lock:
            return stock_code in self.subscribed_stocks

    def get_subscribed_stocks(self) -> List[str]:
        """구독 중인 종목 목록"""
        with self.subscription_lock:
            return list(self.subscribed_stocks)

    def get_subscription_count(self) -> int:
        """구독 수 조회"""
        with self.subscription_lock:
            return len(self.subscribed_stocks)

    def has_subscription_capacity(self) -> bool:
        """구독 가능 여부 확인"""
        with self.subscription_lock:
            return len(self.subscribed_stocks) < self.MAX_STOCKS

    def get_websocket_usage(self) -> str:
        """웹소켓 사용량 문자열"""
        with self.subscription_lock:
            return f"{len(self.subscribed_stocks) * 2}/{self.WEBSOCKET_LIMIT}"

    def clear_all_subscriptions(self):
        """모든 구독 정보 정리"""
        with self.subscription_lock:
            self.subscribed_stocks.clear()
            self.stock_callbacks.clear()

    def get_callbacks_for_stock(self, stock_code: str) -> List[Callable]:
        """특정 종목의 콜백 목록 반환"""
        return self.stock_callbacks.get(stock_code, [])

    def get_global_callbacks(self, data_type: str) -> List[Callable]:
        """특정 데이터 타입의 글로벌 콜백 목록 반환"""
        return self.global_callbacks.get(data_type, [])

    def get_status(self) -> Dict:
        """구독 관리자 상태 반환"""
        with self.subscription_lock:
            return {
                'subscribed_count': len(self.subscribed_stocks),
                'max_stocks': self.MAX_STOCKS,
                'websocket_usage': f"{len(self.subscribed_stocks) * 2}/{self.WEBSOCKET_LIMIT}",
                'subscribed_stocks': list(self.subscribed_stocks),
                'stats': self.stats.copy()
            }
