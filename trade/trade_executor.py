"""
매매 실행을 전담하는 TradeExecutor 클래스 (장시간 최적화 버전)
"""

import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
from models.position import Position, PositionStatus
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

logger = setup_logger(__name__)


class TradeExecutor:
    """매매 실행을 담당하는 클래스 (장시간 최적화 버전)"""
    
    def __init__(self):
        """TradeExecutor 초기화"""
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.risk_config = self.config_loader.load_risk_management_config()
        
        # 통계 정보
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        
        # 장시간 최적화를 위한 추가 통계
        self.execution_times = []  # 주문 실행 시간 추적
        self.order_success_rate = 0.0
        self.avg_execution_time = 0.0
        self.daily_trade_count = 0
        self.hourly_trades = defaultdict(int)  # 시간대별 거래 수
        
        # 빠른 실행을 위한 캐시
        self.last_price_cache = {}  # 최근 가격 캐시
        self.execution_queue = []   # 실행 대기열
        
        # 리스크 관리 최적화
        self.max_daily_loss = self.risk_config.get('max_daily_loss', -50000)  # 일일 최대 손실
        self.max_position_size = self.risk_config.get('max_position_size', 1000000)  # 최대 포지션 크기
        self.emergency_stop = False  # 비상 정지 플래그
        
        logger.info("TradeExecutor 초기화 완료 (장시간 최적화 버전)")
    
    def execute_buy_order(self, position: Position, price: float, 
                         quantity: int, order_id: str = None, 
                         current_positions_count: int = 0) -> bool:
        """매수 주문 실행 (장시간 최적화)
        
        Args:
            position: 포지션 객체
            price: 매수가
            quantity: 수량
            order_id: 주문번호
            current_positions_count: 현재 보유 포지션 수
            
        Returns:
            실행 성공 여부
        """
        start_time = time.time()
        
        try:
            if not position:
                logger.error("포지션 객체가 없습니다")
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
            position.status = PositionStatus.BUY_ORDERED
            position.buy_price = price
            position.buy_quantity = quantity
            position.buy_amount = total_amount
            position.buy_order_id = order_id or f"BUY_{int(time.time())}"
            position.order_time = now_kst()
            
            # 손절가, 익절가 설정 (시장 상황에 따른 동적 조정)
            stop_loss_rate = self._get_dynamic_stop_loss_rate()
            take_profit_rate = self._get_dynamic_take_profit_rate()
            
            position.stop_loss_price = price * (1 + stop_loss_rate)
            position.target_price = price * (1 + take_profit_rate)
            
            # 실행 시간 기록
            execution_time = time.time() - start_time
            self.execution_times.append(execution_time)
            self._update_execution_stats()
            
            # 시간대별 거래 수 증가
            current_hour = now_kst().hour
            self.hourly_trades[current_hour] += 1
            
            logger.info(f"✅ 매수 주문 실행: {position.stock_code} {quantity}주 @{price:,}원 "
                       f"(손절: {position.stop_loss_price:,.0f}, 익절: {position.target_price:,.0f}) "
                       f"실행시간: {execution_time:.3f}초")
            
            return True
            
        except Exception as e:
            logger.error(f"매수 주문 실행 오류 {position.stock_code}: {e}")
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
    
    def confirm_buy_execution(self, position: Position, executed_price: float = None) -> bool:
        """매수 체결 확인 (장시간 최적화)
        
        Args:
            position: 포지션 객체
            executed_price: 체결가 (None이면 주문가 사용)
            
        Returns:
            확인 성공 여부
        """
        try:
            if not position or position.status != PositionStatus.BUY_ORDERED:
                return False
            
            if executed_price and executed_price != position.buy_price:
                # 체결가가 다르면 손절가, 익절가 재계산
                price_diff_rate = (executed_price - position.buy_price) / position.buy_price
                logger.info(f"체결가 차이: {price_diff_rate:+.2%} ({position.buy_price:,} → {executed_price:,})")
                
                position.buy_price = executed_price
                position.buy_amount = executed_price * position.buy_quantity
                
                # 손절가, 익절가 재계산
                stop_loss_rate = self._get_dynamic_stop_loss_rate()
                take_profit_rate = self._get_dynamic_take_profit_rate()
                position.stop_loss_price = executed_price * (1 + stop_loss_rate)
                position.target_price = executed_price * (1 + take_profit_rate)
            
            position.status = PositionStatus.BOUGHT
            position.execution_time = now_kst()
            
            # 가격 캐시 업데이트
            self.last_price_cache[position.stock_code] = position.buy_price
            
            # 일일 거래 수 증가
            self.daily_trade_count += 1
            
            logger.info(f"✅ 매수 체결 확인: {position.stock_code} {position.buy_quantity}주 @{position.buy_price:,}원")
            return True
            
        except Exception as e:
            logger.error(f"매수 체결 확인 오류 {position.stock_code}: {e}")
            return False
    
    def execute_sell_order(self, position: Position, price: float = None, 
                          reason: str = "manual", order_id: str = None) -> bool:
        """매도 주문 실행 (장시간 최적화)
        
        Args:
            position: 포지션 객체
            price: 매도가 (None이면 시장가)
            reason: 매도 사유
            order_id: 주문번호
            
        Returns:
            실행 성공 여부
        """
        start_time = time.time()
        
        try:
            if not position or position.status != PositionStatus.BOUGHT:
                logger.warning(f"매도 불가 상태: {position.stock_code if position else 'None'}")
                return False
            
            # 매도가 설정 (None이면 현재 캐시된 가격 사용)
            if price is None:
                price = self.last_price_cache.get(position.stock_code, position.buy_price)
            
            position.status = PositionStatus.SELL_ORDERED
            position.sell_order_id = order_id or f"SELL_{int(time.time())}"
            position.sell_order_time = now_kst()
            position.sell_reason = reason
            
            # 실행 시간 기록
            execution_time = time.time() - start_time
            self.execution_times.append(execution_time)
            self._update_execution_stats()
            
            logger.info(f"✅ 매도 주문 실행: {position.stock_code} @{price:,}원 (사유: {reason}) "
                       f"실행시간: {execution_time:.3f}초")
            return True
            
        except Exception as e:
            logger.error(f"매도 주문 실행 오류 {position.stock_code}: {e}")
            return False
    
    def confirm_sell_execution(self, position: Position, executed_price: float) -> bool:
        """매도 체결 확인 (장시간 최적화)
        
        Args:
            position: 포지션 객체
            executed_price: 체결가
            
        Returns:
            확인 성공 여부
        """
        try:
            if not position or position.status != PositionStatus.SELL_ORDERED:
                return False
            
            # 실현 손익 계산
            if position.buy_price and position.buy_quantity:
                total_buy = position.buy_price * position.buy_quantity
                total_sell = executed_price * position.buy_quantity
                position.realized_pnl = total_sell - total_buy
                position.realized_pnl_rate = (executed_price - position.buy_price) / position.buy_price * 100
                
                # 수수료 고려 (간단 계산)
                commission_rate = 0.0015  # 0.15%
                total_commission = (total_buy + total_sell) * commission_rate
                position.realized_pnl -= total_commission
            
            position.status = PositionStatus.SOLD
            position.sell_execution_time = now_kst()
            position.sell_price = executed_price
            
            # 통계 업데이트
            self.total_trades += 1
            self.total_pnl += position.realized_pnl or 0
            
            if position.realized_pnl and position.realized_pnl > 0:
                self.winning_trades += 1
            else:
                self.losing_trades += 1
            
            # 승률 업데이트
            self.order_success_rate = self.winning_trades / self.total_trades if self.total_trades > 0 else 0
            
            # 가격 캐시 업데이트
            self.last_price_cache[position.stock_code] = executed_price
            
            # 비상 정지 해제 조건 확인
            if self.emergency_stop and self.total_pnl > self.max_daily_loss * 0.8:
                self.emergency_stop = False
                logger.info("비상 정지 해제")
            
            logger.info(f"✅ 매도 체결 확인: {position.stock_code} "
                       f"손익: {position.realized_pnl:+,.0f}원 ({position.realized_pnl_rate:+.2f}%) "
                       f"사유: {position.sell_reason}")
            
            return True
            
        except Exception as e:
            logger.error(f"매도 체결 확인 오류 {position.stock_code}: {e}")
            return False
    
    def get_positions_to_sell(self, positions: List[Position], current_prices: Dict[str, float] = None) -> List[Tuple[Position, str]]:
        """매도해야 할 포지션들과 사유 반환 (장시간 최적화)
        
        Args:
            positions: 보유 포지션 리스트
            current_prices: 현재 가격 딕셔너리 (종목코드: 가격)
            
        Returns:
            (Position, 사유) 튜플 리스트
        """
        to_sell = []
        
        try:
            for position in positions:
                if position.status != PositionStatus.BOUGHT:
                    continue
                
                # 현재 가격 결정 (우선순위: 파라미터 > 캐시 > 포지션 종가)
                current_price = position.close_price
                if current_prices and position.stock_code in current_prices:
                    current_price = current_prices[position.stock_code]
                elif position.stock_code in self.last_price_cache:
                    current_price = self.last_price_cache[position.stock_code]
                
                # 기본 매도 조건들
                if position.should_stop_loss(current_price):
                    to_sell.append((position, "stop_loss"))
                    continue
                
                if position.should_take_profit(current_price):
                    to_sell.append((position, "take_profit"))
                    continue
                
                if position.is_holding_period_exceeded():
                    to_sell.append((position, "holding_period"))
                    continue
                
                # 장시간 최적화 추가 조건들
                
                # 1. 급락 감지 매도
                if self._detect_sudden_drop(position, current_price):
                    to_sell.append((position, "sudden_drop"))
                    continue
                
                # 2. 시간 기반 매도 (마감 전)
                current_time = now_kst()
                if current_time.hour >= 14 and current_time.minute >= 50:
                    # 14:50 이후에는 수익이 있으면 매도
                    if position.get_unrealized_pnl(current_price) > 0:
                        to_sell.append((position, "pre_close_profit"))
                        continue
                
                # 3. 비상 정지 상황에서는 모든 포지션 매도
                if self.emergency_stop:
                    to_sell.append((position, "emergency_stop"))
                    continue
            
            return to_sell
            
        except Exception as e:
            logger.error(f"매도 대상 포지션 검색 오류: {e}")
            return []
    
    def _detect_sudden_drop(self, position: Position, current_price: float) -> bool:
        """급락 감지
        
        Args:
            position: 포지션
            current_price: 현재 가격
            
        Returns:
            급락 여부
        """
        try:
            # 매수가 대비 급락률 체크
            drop_rate = (position.buy_price - current_price) / position.buy_price
            
            # 5분 이내에 3% 이상 급락하면 손절
            if drop_rate >= 0.03:
                holding_minutes = (now_kst() - position.execution_time).total_seconds() / 60
                if holding_minutes <= 5:
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"급락 감지 오류 {position.stock_code}: {e}")
            return False
    
    def get_trade_statistics(self) -> Dict:
        """거래 통계 정보 반환 (장시간 최적화)"""
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        
        # 시간대별 거래 분석
        most_active_hour = max(self.hourly_trades.items(), key=lambda x: x[1]) if self.hourly_trades else (0, 0)
        
        return {
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': win_rate,
            'total_pnl': self.total_pnl,
            'avg_pnl_per_trade': self.total_pnl / max(self.total_trades, 1),
            'daily_trade_count': self.daily_trade_count,
            'avg_execution_time': self.avg_execution_time,
            'order_success_rate': self.order_success_rate,
            'emergency_stop': self.emergency_stop,
            'most_active_hour': most_active_hour[0],
            'most_active_hour_count': most_active_hour[1],
            'hourly_distribution': dict(self.hourly_trades)
        }
    
    def reset_statistics(self):
        """통계 초기화 (장시간 최적화)"""
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        self.daily_trade_count = 0
        self.execution_times.clear()
        self.hourly_trades.clear()
        self.last_price_cache.clear()
        self.execution_queue.clear()
        self.emergency_stop = False
        self.order_success_rate = 0.0
        self.avg_execution_time = 0.0
        
        logger.info("거래 통계 초기화 완료")
    
    def get_performance_summary(self) -> str:
        """성능 요약 문자열 반환"""
        stats = self.get_trade_statistics()
        
        return (f"거래 성과: {stats['total_trades']}건 "
                f"(승률: {stats['win_rate']:.1f}%, "
                f"손익: {stats['total_pnl']:+,.0f}원, "
                f"평균실행: {stats['avg_execution_time']:.3f}초)")
    
    def __str__(self) -> str:
        """문자열 표현"""
        return (f"TradeExecutor(거래: {self.total_trades}건, "
                f"승률: {self.winning_trades/max(self.total_trades,1)*100:.1f}%, "
                f"손익: {self.total_pnl:+,.0f}원, "
                f"비상정지: {self.emergency_stop})") 