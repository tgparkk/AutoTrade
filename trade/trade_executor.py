#!/usr/bin/env python3
"""
실제 매매 주문 실행을 담당하는 TradeExecutor 클래스

주요 기능:
- 매수/매도 주문 실행
- 주문 체결 확인 
- 리스크 관리 (포지션 크기, 손절/익절)
- 거래 통계 관리
"""

import time
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
from collections import defaultdict
from models.stock import Stock, StockStatus
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

logger = setup_logger(__name__)


class TradeExecutor:
    """거래 주문 실행 및 관리 클래스"""
    
    def __init__(self):
        """TradeExecutor 초기화"""
        logger.info("=== TradeExecutor 초기화 시작 ===")
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.risk_config = self.config_loader.load_risk_management_config()
        
        # 거래 통계
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        
        # 리스크 관리
        self.max_daily_loss = self.risk_config.get('max_daily_loss', -100000)  # 일일 최대 손실
        self.daily_trade_count = 0
        
        # 성능 메트릭
        self.execution_times = []  # 주문 실행 시간
        self.avg_execution_time = 0.0
        self.hourly_trades = defaultdict(int)  # 시간대별 거래 수
        
        # 캐시
        self.last_price_cache = {}
        self.max_position_size = self.risk_config.get('max_position_size', 1000000)  # 최대 포지션 크기
        self.emergency_stop = False  # 비상 정지 플래그
        
        logger.info("TradeExecutor 초기화 완료 (장시간 최적화 버전)")
    
    def execute_buy_order(self, stock: Stock, price: float, 
                         quantity: int, order_id: str = None, 
                         current_positions_count: int) -> bool:
        """매수 주문 실행 (장시간 최적화)
        
        Args:
            stock: 주식 객체
            price: 매수가
            quantity: 수량
            order_id: 주문번호
            current_positions_count: 현재 보유 포지션 수
            
        Returns:
            실행 성공 여부
        """
        start_time = time.time()
        
        try:
            if not stock:
                logger.error("주식 객체가 없습니다")
                return False
            
            # 비상 정지 체크
            if self.emergency_stop:
                logger.warning("비상 정지 상태 - 매수 주문 차단")
                return False
            
            # 일일 거래 한도 체크
            if self.daily_trade_count >= self.risk_config.get('max_daily_trades', 20):
                logger.warning(f"일일 거래 한도 초과: {self.daily_trade_count}")
                return False
            
            # 포지션 크기 체크
            total_amount = price * quantity
            max_positions = self.risk_config.get('max_positions', 5)
            
            if current_positions_count >= max_positions:
                logger.warning(f"최대 포지션 수 초과: {current_positions_count}/{max_positions}")
                return False
            
            # 포지션 크기 제한 체크
            if total_amount > self.max_position_size:
                logger.warning(f"포지션 크기 초과: {total_amount:,}원 > {self.max_position_size:,}원")
                # 수량 조정
                quantity = int(self.max_position_size / price)
                total_amount = price * quantity
                logger.info(f"수량 조정: {quantity}주로 변경")
            
            # 일일 손실 한도 체크
            if self.total_pnl <= self.max_daily_loss:
                logger.error(f"일일 손실 한도 도달: {self.total_pnl:,}원 <= {self.max_daily_loss:,}원")
                self.emergency_stop = True
                return False
            
            # 매수 정보 설정
            stock.status = StockStatus.BUY_ORDERED
            stock.buy_price = price
            stock.buy_quantity = quantity
            stock.buy_amount = total_amount
            stock.buy_order_id = order_id or f"BUY_{int(time.time())}"
            stock.order_time = now_kst()
            
            # 손절가, 익절가 설정 (시장 상황에 따른 동적 조정)
            stop_loss_rate = self._get_dynamic_stop_loss_rate()
            take_profit_rate = self._get_dynamic_take_profit_rate()
            
            stock.stop_loss_price = price * (1 + stop_loss_rate)
            stock.target_price = price * (1 + take_profit_rate)
            
            # 실행 시간 기록
            execution_time = time.time() - start_time
            self.execution_times.append(execution_time)
            self._update_execution_stats()
            
            # 시간대별 거래 수 증가
            current_hour = now_kst().hour
            self.hourly_trades[current_hour] += 1
            
            logger.info(f"✅ 매수 주문 실행: {stock.stock_code} {quantity}주 @{price:,}원 "
                       f"(손절: {stock.stop_loss_price:,.0f}, 익절: {stock.target_price:,.0f}) "
                       f"실행시간: {execution_time:.3f}초")
            
            return True
            
        except Exception as e:
            logger.error(f"매수 주문 실행 오류 {stock.stock_code}: {e}")
            return False
    
    def _get_dynamic_stop_loss_rate(self) -> float:
        """동적 손절률 계산 (시장 상황 반영)
        
        Returns:
            손절률 (음수)
        """
        base_rate = self.risk_config.get('stop_loss_rate', -0.02)
        
        # 최근 거래 성과에 따른 조정
        if len(self.execution_times) > 0:
            recent_win_rate = self._calculate_recent_win_rate()
            
            if recent_win_rate < 0.3:  # 승률 30% 미만이면 더 보수적
                return base_rate * 0.7  # 1.4% 손절
            elif recent_win_rate > 0.7:  # 승률 70% 이상이면 더 공격적
                return base_rate * 1.2  # 2.4% 손절
        
        return base_rate
    
    def _get_dynamic_take_profit_rate(self) -> float:
        """동적 익절률 계산 (시장 상황 반영)
        
        Returns:
            익절률 (양수)
        """
        base_rate = self.risk_config.get('take_profit_rate', 0.015)
        
        # 현재 시간대에 따른 조정
        current_hour = now_kst().hour
        
        if 9 <= current_hour <= 10:  # 장 초반
            return base_rate * 1.3  # 2% 익절
        elif 14 <= current_hour <= 15:  # 장 마감 전
            return base_rate * 0.8  # 1.2% 익절
        
        return base_rate
    
    def _calculate_recent_win_rate(self, recent_count: int = 10) -> float:
        """최근 거래의 승률 계산
        
        Args:
            recent_count: 최근 거래 수
            
        Returns:
            최근 승률
        """
        if self.total_trades < recent_count:
            return self.winning_trades / max(self.total_trades, 1)
        
        # TODO: 실제로는 최근 거래 기록을 저장해서 계산해야 함
        return self.winning_trades / self.total_trades
    
    def _update_execution_stats(self):
        """실행 통계 업데이트"""
        if self.execution_times:
            self.avg_execution_time = sum(self.execution_times) / len(self.execution_times)
            
            # 최근 100개 실행 시간만 유지
            if len(self.execution_times) > 100:
                self.execution_times = self.execution_times[-100:]
    
    def confirm_buy_execution(self, stock: Stock, executed_price: float = None) -> bool:
        """매수 체결 확인 (장시간 최적화)
        
        Args:
            stock: 주식 객체
            executed_price: 체결가 (None이면 주문가 사용)
            
        Returns:
            확인 성공 여부
        """
        try:
            if not stock or stock.status != StockStatus.BUY_ORDERED:
                return False
            
            if executed_price and executed_price != stock.buy_price:
                # 체결가가 다르면 손절가, 익절가 재계산
                price_diff_rate = (executed_price - stock.buy_price) / stock.buy_price
                logger.info(f"체결가 차이: {price_diff_rate:+.2%} ({stock.buy_price:,} → {executed_price:,})")
                
                stock.buy_price = executed_price
                stock.buy_amount = executed_price * stock.buy_quantity
                
                # 손절가, 익절가 재계산
                stop_loss_rate = self._get_dynamic_stop_loss_rate()
                take_profit_rate = self._get_dynamic_take_profit_rate()
                stock.stop_loss_price = executed_price * (1 + stop_loss_rate)
                stock.target_price = executed_price * (1 + take_profit_rate)
            
            stock.status = StockStatus.BOUGHT
            stock.execution_time = now_kst()
            
            # 가격 캐시 업데이트
            self.last_price_cache[stock.stock_code] = stock.buy_price
            
            # 일일 거래 수 증가
            self.daily_trade_count += 1
            
            logger.info(f"✅ 매수 체결 확인: {stock.stock_code} {stock.buy_quantity}주 @{stock.buy_price:,}원")
            return True
            
        except Exception as e:
            logger.error(f"매수 체결 확인 오류 {stock.stock_code}: {e}")
            return False
    
    def execute_sell_order(self, stock: Stock, price: float = None, 
                          reason: str = "manual", order_id: str = None) -> bool:
        """매도 주문 실행 (장시간 최적화)
        
        Args:
            stock: 주식 객체
            price: 매도가 (None이면 마지막 알려진 가격 사용)
            reason: 매도 사유
            order_id: 주문번호
            
        Returns:
            실행 성공 여부
        """
        try:
            if not stock or stock.status != StockStatus.BOUGHT:
                logger.warning(f"매도 불가 상태: {stock.stock_code if stock else 'None'}")
                return False
            
            # 가격이 없으면 캐시에서 조회
            if price is None:
                price = self.last_price_cache.get(stock.stock_code, stock.buy_price)
            
            stock.status = StockStatus.SELL_ORDERED
            stock.sell_order_id = order_id or f"SELL_{int(time.time())}"
            stock.sell_order_time = now_kst()
            stock.sell_reason = reason
            
            # 시간대별 거래 수 증가
            current_hour = now_kst().hour
            self.hourly_trades[current_hour] += 1
            
            logger.info(f"✅ 매도 주문 실행: {stock.stock_code} @{price:,}원 (사유: {reason}) "
                       f"매수가: {stock.buy_price:,}원")
            return True
            
        except Exception as e:
            logger.error(f"매도 주문 실행 오류 {stock.stock_code}: {e}")
            return False
    
    def confirm_sell_execution(self, stock: Stock, executed_price: float) -> bool:
        """매도 체결 확인 (장시간 최적화)
        
        Args:
            stock: 주식 객체
            executed_price: 체결가
            
        Returns:
            확인 성공 여부
        """
        try:
            if not stock or stock.status != StockStatus.SELL_ORDERED:
                return False
            
            # 손익 계산
            if stock.buy_price and stock.buy_quantity:
                total_buy = stock.buy_price * stock.buy_quantity
                total_sell = executed_price * stock.buy_quantity
                stock.realized_pnl = total_sell - total_buy
                stock.realized_pnl_rate = (executed_price - stock.buy_price) / stock.buy_price * 100
                
                # 수수료 반영 (간단히 0.3%)
                commission_rate = 0.003
                total_commission = (total_buy + total_sell) * commission_rate
                stock.realized_pnl -= total_commission
            
            stock.status = StockStatus.SOLD
            stock.sell_execution_time = now_kst()
            stock.sell_price = executed_price
            
            # 통계 업데이트
            self.total_trades += 1
            self.total_pnl += stock.realized_pnl or 0
            
            if stock.realized_pnl and stock.realized_pnl > 0:
                self.winning_trades += 1
            else:
                self.losing_trades += 1
            
            # 일일 거래 수 증가
            self.daily_trade_count += 1
            
            # 가격 캐시 업데이트
            self.last_price_cache[stock.stock_code] = executed_price
            
            # 연속 손실 체크 (비상 정지 조건)
            if self.losing_trades >= 3 and self.winning_trades == 0:
                logger.warning("연속 손실 발생 - 비상 정지 활성화")
                self.emergency_stop = True
            
            logger.info(f"✅ 매도 체결 확인: {stock.stock_code} "
                       f"손익: {stock.realized_pnl:+,.0f}원 ({stock.realized_pnl_rate:+.2f}%) "
                       f"사유: {stock.sell_reason}")
            
            return True
            
        except Exception as e:
            logger.error(f"매도 체결 확인 오류 {stock.stock_code}: {e}")
            return False
    
    def get_positions_to_sell(self, stocks: List[Stock], 
                             current_prices: Dict[str, float] = None) -> List[Tuple[Stock, str]]:
        """매도할 포지션들을 선별 (장시간 최적화)
        
        Args:
            stocks: 보유 주식 리스트
            current_prices: 현재가 딕셔너리 {종목코드: 가격}
            
        Returns:
            매도할 (주식, 사유) 튜플 리스트
        """
        positions_to_sell = []
        current_time = now_kst()
        
        for stock in stocks:
            if stock.status != StockStatus.BOUGHT:
                continue
            
            # 현재가 확인
            if current_prices and stock.stock_code in current_prices:
                current_price = current_prices[stock.stock_code]
                # 실시간 데이터 업데이트
                stock.update_realtime_data(current_price=current_price)
            else:
                current_price = stock.realtime_data.current_price
            
            if current_price <= 0:
                continue
            
            # 손익 계산
            stock.calculate_unrealized_pnl(current_price)
            
            # 매도 조건 체크
            sell_reason = None
            
            # 1. 익절 조건
            if stock.should_take_profit(current_price):
                sell_reason = "익절"
            
            # 2. 손절 조건
            elif stock.should_stop_loss(current_price):
                sell_reason = "손절"
            
            # 3. 보유기간 초과
            elif stock.is_holding_period_exceeded():
                sell_reason = "보유기간초과"
            
            # 4. 급락 감지
            elif self._detect_sudden_drop(stock, current_price):
                sell_reason = "급락감지"
            
            # 5. 장마감 30분 전 강제 매도 (15:00 이후)
            elif current_time.hour >= 15:
                if stock.unrealized_pnl and stock.unrealized_pnl > 0:
                    sell_reason = "장마감전익절"
                elif stock.unrealized_pnl and stock.unrealized_pnl < -50000:  # 5만원 이상 손실
                    sell_reason = "장마감전손절"
            
            if sell_reason:
                positions_to_sell.append((stock, sell_reason))
        
        return positions_to_sell
    
    def _detect_sudden_drop(self, stock: Stock, current_price: float) -> bool:
        """급락 감지 (단순 버전)
        
        Args:
            stock: 주식 객체
            current_price: 현재가
            
        Returns:
            급락 여부
        """
        if not stock.buy_price:
            return False
        
        # 5분 내 5% 이상 하락
        drop_rate = (current_price - stock.buy_price) / stock.buy_price
        
        if drop_rate <= -0.05:  # 5% 이상 하락
            # 추가로 거래량 급증도 확인할 수 있음
            volume_spike = stock.realtime_data.volume_spike_ratio
            if volume_spike > 3.0:  # 평소 거래량의 3배 이상
                logger.warning(f"급락 감지: {stock.stock_code} {drop_rate:.2%} 하락, 거래량 {volume_spike:.1f}배")
                return True
        
        return False
    
    def get_trade_statistics(self) -> Dict:
        """거래 통계 조회
        
        Returns:
            거래 통계 딕셔너리
        """
        win_rate = (self.winning_trades / max(self.total_trades, 1)) * 100
        
        return {
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': win_rate,
            'total_pnl': self.total_pnl,
            'total_realized_pnl': self.total_pnl,  # 호환성
            'avg_execution_time': self.avg_execution_time,
            'daily_trade_count': self.daily_trade_count,
            'emergency_stop': self.emergency_stop
        }
    
    def reset_statistics(self):
        """통계 초기화 (일일 리셋)"""
        logger.info("거래 통계 초기화")
        self.daily_trade_count = 0
        self.emergency_stop = False
        self.hourly_trades.clear()
        
        # 전체 통계는 유지 (누적)
        # self.total_trades = 0
        # self.winning_trades = 0
        # self.losing_trades = 0
        # self.total_pnl = 0.0
    
    def get_performance_summary(self) -> str:
        """성과 요약 문자열 생성
        
        Returns:
            성과 요약 문자열
        """
        stats = self.get_trade_statistics()
        return (f"거래 성과: {stats['total_trades']}건 "
                f"(승률 {stats['win_rate']:.1f}%, "
                f"손익 {stats['total_pnl']:+,.0f}원)")
    
    def __str__(self) -> str:
        """문자열 표현"""
        return f"TradeExecutor(거래수: {self.total_trades}, 손익: {self.total_pnl:+,.0f}원)" 