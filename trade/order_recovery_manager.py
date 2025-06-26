#!/usr/bin/env python3
"""
주문 복구 및 관리 전담 클래스

주요 기능:
- 정체된 주문 자동 복구 (KIS API 취소 포함)
- 주문 상태 검증
- 비정상적인 상태 전환 감지
- 강제 주문 취소
"""

from typing import List, Dict, Optional, TYPE_CHECKING
from datetime import datetime
from models.stock import Stock, StockStatus
from utils.korean_time import now_kst
from utils.logger import setup_logger

if TYPE_CHECKING:
    from .stock_manager import StockManager
    from .trade_executor import TradeExecutor

logger = setup_logger(__name__)


class OrderRecoveryManager:
    """주문 복구 및 관리 전담 클래스"""
    
    def __init__(self, stock_manager: "StockManager", trade_executor: "TradeExecutor"):
        """OrderRecoveryManager 초기화
        
        Args:
            stock_manager: 종목 관리자 인스턴스
            trade_executor: 매매 실행자 인스턴스
        """
        self.stock_manager = stock_manager
        self.trade_executor = trade_executor
        
        # 설정
        self.stuck_order_timeout_minutes = 3  # 정체된 주문 타임아웃 (분)
        self.max_recovery_attempts = 3        # 최대 복구 시도 횟수
        
        # 통계
        self.total_recoveries = 0
        self.successful_api_cancels = 0
        self.failed_api_cancels = 0
        
        logger.info("OrderRecoveryManager 초기화 완료")
    
    def auto_recover_stuck_orders(self) -> int:
        """정체된 주문들 자동 복구 (실제 KIS API 주문 취소 포함)
        
        Returns:
            복구된 주문 수
        """
        recovered = 0
        current_time = now_kst()
        
        # 1단계: 정체된 주문들 식별
        stuck_orders = self._identify_stuck_orders(current_time)
        
        if not stuck_orders:
            return 0
        
        logger.warning(f"⏰ {len(stuck_orders)}개의 정체된 주문 발견")
        
        # 2단계: 정체된 주문들 처리
        for stuck_order in stuck_orders:
            if self._recover_stuck_order(stuck_order):
                recovered += 1
        
        # 3단계: 통계 업데이트
        self.total_recoveries += recovered
        
        if recovered > 0:
            logger.info(f"🔧 총 {recovered}개 정체 주문 복구 완료 (KIS API 취소 포함)")
        
        return recovered
    
    def _identify_stuck_orders(self, current_time: datetime) -> List[Dict]:
        """정체된 주문들 식별
        
        Args:
            current_time: 현재 시간
            
        Returns:
            정체된 주문 정보 리스트
        """
        stuck_orders = []
        
        # StockManager에서 주문 상태 조회
        with self.stock_manager._status_lock:
            for stock_code, status in self.stock_manager.trading_status.items():
                trade_info = self.stock_manager.trade_info.get(stock_code, {})
                
                if status in [StockStatus.BUY_ORDERED, StockStatus.PARTIAL_BOUGHT]:
                    order_time = trade_info.get('order_time')
                    if order_time and self._is_order_stuck(current_time, order_time):
                        stuck_orders.append({
                            'stock_code': stock_code,
                            'order_type': 'buy',
                            'order_time': order_time,
                            'minutes_elapsed': (current_time - order_time).total_seconds() / 60,
                            'status': status,
                            'trade_info': trade_info.copy()
                        })
                
                elif status in [StockStatus.SELL_ORDERED, StockStatus.PARTIAL_SOLD]:
                    sell_order_time = trade_info.get('sell_order_time')
                    if sell_order_time and self._is_order_stuck(current_time, sell_order_time):
                        stuck_orders.append({
                            'stock_code': stock_code,
                            'order_type': 'sell',
                            'order_time': sell_order_time,
                            'minutes_elapsed': (current_time - sell_order_time).total_seconds() / 60,
                            'status': status,
                            'trade_info': trade_info.copy()
                        })
        
        return stuck_orders
    
    def _is_order_stuck(self, current_time: datetime, order_time: datetime) -> bool:
        """주문이 정체되었는지 확인
        
        Args:
            current_time: 현재 시간
            order_time: 주문 시간
            
        Returns:
            정체 여부
        """
        elapsed_minutes = (current_time - order_time).total_seconds() / 60
        return elapsed_minutes > self.stuck_order_timeout_minutes
    
    def _recover_stuck_order(self, stuck_order: Dict) -> bool:
        """개별 정체된 주문 복구
        
        Args:
            stuck_order: 정체된 주문 정보
            
        Returns:
            복구 성공 여부
        """
        stock_code = stuck_order['stock_code']
        order_type = stuck_order['order_type']
        minutes_elapsed = stuck_order['minutes_elapsed']
        
        try:
            logger.warning(f"⏰ 정체된 {order_type} 주문 복구 시작: {stock_code} "
                         f"(경과: {minutes_elapsed:.1f}분)")
            
            # Stock 객체 조회
            stock = self.stock_manager.get_selected_stock(stock_code)
            if not stock:
                logger.error(f"❌ Stock 객체를 찾을 수 없음: {stock_code}")
                return False
            
            # 🔥 실제 KIS API 주문 취소 시도
            cancel_success = self._attempt_api_cancel(stock, order_type)
            
            # 상태 복구 (API 취소 성공 여부와 관계없이 실행)
            recovery_success = self._recover_order_state(stock_code, order_type, cancel_success)
            
            if recovery_success:
                logger.info(f"✅ 정체된 주문 복구 완료: {stock_code} {order_type}")
                return True
            else:
                logger.error(f"❌ 정체된 주문 상태 복구 실패: {stock_code} {order_type}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 정체된 주문 복구 오류 {stock_code}: {e}")
            return False
    
    def _attempt_api_cancel(self, stock: Stock, order_type: str) -> bool:
        """KIS API 주문 취소 시도
        
        Args:
            stock: 주식 객체
            order_type: 주문 타입 ('buy' 또는 'sell')
            
        Returns:
            취소 성공 여부
        """
        try:
            cancel_success = self.trade_executor.cancel_order(stock, order_type)
            
            if cancel_success:
                logger.info(f"✅ KIS API 주문 취소 성공: {stock.stock_code} {order_type}")
                self.successful_api_cancels += 1
                return True
            else:
                logger.warning(f"⚠️ KIS API 주문 취소 실패: {stock.stock_code} {order_type}")
                self.failed_api_cancels += 1
                return False
                
        except Exception as e:
            logger.error(f"❌ KIS API 주문 취소 오류 {stock.stock_code}: {e}")
            self.failed_api_cancels += 1
            return False
    
    def _recover_order_state(self, stock_code: str, order_type: str, api_cancel_success: bool) -> bool:
        """주문 상태 복구
        
        Args:
            stock_code: 종목 코드
            order_type: 주문 타입
            api_cancel_success: API 취소 성공 여부
            
        Returns:
            상태 복구 성공 여부
        """
        try:
            reason = f"3분 타임아웃 복구 (API취소: {'성공' if api_cancel_success else '실패'})"
            
            if order_type == "buy":
                return self.stock_manager.change_stock_status(
                    stock_code=stock_code,
                    new_status=StockStatus.WATCHING,
                    reason=reason,
                    buy_order_id=None,
                    buy_order_orgno=None,
                    buy_order_time=None,
                    order_time=None
                )
            elif order_type == "sell":
                return self.stock_manager.change_stock_status(
                    stock_code=stock_code,
                    new_status=StockStatus.BOUGHT,
                    reason=reason,
                    sell_order_id=None,
                    sell_order_orgno=None,
                    sell_order_time=None,
                    sell_order_time_api=None
                )
            else:
                logger.error(f"알 수 없는 주문 타입: {order_type}")
                return False
                
        except Exception as e:
            logger.error(f"주문 상태 복구 오류 {stock_code}: {e}")
            return False
    
    def validate_stock_transitions(self) -> List[str]:
        """비정상적인 상태 전환 감지
        
        Returns:
            발견된 문제점 리스트
        """
        issues = []
        current_time = now_kst()
        
        with self.stock_manager._status_lock:
            for stock_code, status in self.stock_manager.trading_status.items():
                if status in [StockStatus.BUY_ORDERED, StockStatus.SELL_ORDERED]:
                    trade_info = self.stock_manager.trade_info.get(stock_code, {})
                    
                    # 매수 주문 상태 검증
                    if status == StockStatus.BUY_ORDERED:
                        order_time = trade_info.get('order_time')
                        if order_time:
                            minutes_since_order = (current_time - order_time).total_seconds() / 60
                            if minutes_since_order > self.stuck_order_timeout_minutes:
                                issues.append(f"{stock_code}: 매수 주문 상태 {minutes_since_order:.1f}분 초과")
                    
                    # 매도 주문 상태 검증
                    elif status == StockStatus.SELL_ORDERED:
                        sell_order_time = trade_info.get('sell_order_time')
                        if sell_order_time:
                            minutes_since_order = (current_time - sell_order_time).total_seconds() / 60
                            if minutes_since_order > self.stuck_order_timeout_minutes:
                                issues.append(f"{stock_code}: 매도 주문 상태 {minutes_since_order:.1f}분 초과")
        
        return issues
    
    def force_cancel_all_pending_orders(self) -> int:
        """모든 미체결 주문 강제 취소
        
        Returns:
            취소된 주문 수
        """
        cancelled = 0
        
        try:
            # 매수 주문 상태인 종목들 취소
            buy_ordered_stocks = self.stock_manager.get_stocks_by_status(StockStatus.BUY_ORDERED)
            for stock in buy_ordered_stocks:
                try:
                    if self.trade_executor.cancel_order(stock, "buy"):
                        self.stock_manager.change_stock_status(
                            stock.stock_code, 
                            StockStatus.WATCHING, 
                            "강제 취소"
                        )
                        cancelled += 1
                        logger.info(f"강제 매수 주문 취소: {stock.stock_code}")
                except Exception as e:
                    logger.error(f"강제 매수 주문 취소 실패 {stock.stock_code}: {e}")
            
            # 매도 주문 상태인 종목들 취소
            sell_ordered_stocks = self.stock_manager.get_stocks_by_status(StockStatus.SELL_ORDERED)
            for stock in sell_ordered_stocks:
                try:
                    if self.trade_executor.cancel_order(stock, "sell"):
                        self.stock_manager.change_stock_status(
                            stock.stock_code, 
                            StockStatus.BOUGHT, 
                            "강제 취소"
                        )
                        cancelled += 1
                        logger.info(f"강제 매도 주문 취소: {stock.stock_code}")
                except Exception as e:
                    logger.error(f"강제 매도 주문 취소 실패 {stock.stock_code}: {e}")
            
            if cancelled > 0:
                logger.info(f"🚨 총 {cancelled}개 주문 강제 취소 완료")
            
            return cancelled
            
        except Exception as e:
            logger.error(f"강제 주문 취소 오류: {e}")
            return cancelled
    
    def get_recovery_statistics(self) -> Dict:
        """복구 통계 정보 반환
        
        Returns:
            복구 통계 딕셔너리
        """
        return {
            'total_recoveries': self.total_recoveries,
            'successful_api_cancels': self.successful_api_cancels,
            'failed_api_cancels': self.failed_api_cancels,
            'api_cancel_success_rate': (
                self.successful_api_cancels / (self.successful_api_cancels + self.failed_api_cancels) * 100
                if (self.successful_api_cancels + self.failed_api_cancels) > 0 else 0
            ),
            'stuck_order_timeout_minutes': self.stuck_order_timeout_minutes
        }
    
    def set_stuck_order_timeout(self, minutes: int):
        """정체된 주문 타임아웃 설정
        
        Args:
            minutes: 타임아웃 시간 (분)
        """
        if minutes > 0:
            self.stuck_order_timeout_minutes = minutes
            logger.info(f"정체된 주문 타임아웃 설정: {minutes}분")
        else:
            logger.warning("타임아웃은 0보다 커야 합니다")
    
    def __str__(self) -> str:
        """문자열 표현"""
        return (f"OrderRecoveryManager(복구완료: {self.total_recoveries}, "
                f"API취소성공: {self.successful_api_cancels}, "
                f"API취소실패: {self.failed_api_cancels}, "
                f"타임아웃: {self.stuck_order_timeout_minutes}분)") 