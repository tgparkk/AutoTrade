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
from collections import defaultdict, deque
from models.stock import Stock, StockStatus
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

# 🆕 데이터베이스 저장 기능 추가
try:
    from database.trade_database import TradeDatabase
    DATABASE_AVAILABLE = True
except ImportError:
    TradeDatabase = None
    DATABASE_AVAILABLE = False

logger = setup_logger(__name__)


class TradeExecutor:
    """거래 주문 실행 및 관리 클래스"""
    
    def __init__(self):
        """TradeExecutor 초기화"""
        logger.info("=== TradeExecutor 초기화 시작 ===")
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.risk_config = self.config_loader.load_risk_management_config()
        # 🆕 전략 설정 로드 (트레일링 스탑 등)
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        
        # 거래 통계
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        
        # 🆕 최근 거래 기록 저장 (승률 계산용)
        self.recent_trades = deque(maxlen=50)  # 최근 50건 거래 기록 저장
        
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
        
        # 🆕 데이터베이스 초기화
        self.database = None
        if DATABASE_AVAILABLE:
            try:
                self.database = TradeDatabase()
                logger.info("✅ 거래 데이터베이스 연결 완료")
            except Exception as e:
                logger.warning(f"⚠️ 거래 데이터베이스 연결 실패: {e}")
                self.database = None
        else:
            logger.info("📊 데이터베이스 라이브러리 없음 - 메모리에만 저장")
        
        # 🆕 손익 곡선 및 MDD 추적
        self.equity_curve = []          # 누적 손익 값 리스트
        self.running_max_equity = 0.0   # 손익곡선 최고점
        self.max_drawdown = 0.0         # 최대 낙폭
        
        logger.info("TradeExecutor 초기화 완료 (장시간 최적화 버전)")
    
    def execute_buy_order(self, stock: Stock, price: float, 
                         quantity: int, order_id: Optional[str] = None, 
                         current_positions_count: int = 0) -> bool:
        """매수 주문 실행 (실제 KIS API 호출 포함)
        
        Args:
            stock: 주식 객체
            price: 매수가
            quantity: 수량
            order_id: 주문번호
            current_positions_count: 현재 보유 포지션 수
            
        Returns:
            실행 성공 여부
        """
        start_time = now_kst().timestamp()
        
        try:
            # 이미 매수 주문(접수/일부체결) 또는 매수 완료 상태라면 중복 주문 방지
            if stock.status in (
                StockStatus.BUY_ORDERED,
                StockStatus.PARTIAL_BOUGHT,
                StockStatus.BOUGHT,
            ):
                logger.warning(
                    f"중복 매수 시도 차단: {stock.stock_code} 현재 상태 {stock.status.value}"
                )
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
            
            # 🔥 실제 KIS API 매수 주문 실행
            logger.info(f"📤 KIS API 매수 주문 요청: {stock.stock_code} {quantity}주 @{price:,}원")
            
            try:
                from api.kis_order_api import get_order_cash
                
                # KIS API 매수 주문 실행
                order_result = get_order_cash(
                    ord_dv="buy",           # 매수
                    itm_no=stock.stock_code, # 종목코드
                    qty=quantity,           # 수량
                    unpr=int(price)         # 주문가격 (정수)
                )
                
                # 모의투자/일부 상황에서는 빈 DataFrame 이 반환되지만 즉시 체결통보가 오는 경우가 있음
                if order_result is None or order_result.empty:
                    logger.warning(
                        f"⚠️ KIS API 응답이 비어있습니다 – 모의투자/네트워크 지연일 수 있으므로 임시 성공으로 간주"
                    )
                    order_data = {
                        'rt_cd': '0',
                        'msg_cd': 'SIM',
                        'msg1': 'EMPTY RESPONSE (SIMULATED)',
                        'ODNO': order_id or f"BUY_{int(now_kst().timestamp())}",
                        'KRX_FWDG_ORD_ORGNO': '',
                        'ORD_TMD': now_kst().strftime("%H%M%S")
                    }
                else:
                    order_data = order_result.iloc[0]
                
                rt_cd = str(order_data.get('rt_cd', '')).strip()
                msg_cd = str(order_data.get('msg_cd', '')).strip()
                msg1 = order_data.get('msg1', '')
                
                # 🆕 성공 여부 판정 로직 완화  
                # - 일부 브로커/모의투자는 rt_cd 공백이거나 '00' 으로 오는 경우가 있음  
                # - 주문번호(ODNO)가 존재하면 일단 접수 성공으로 간주하고 체결통보에서 최종 확인  
                is_success = False
                if rt_cd in ('0', '00'):
                    is_success = True
                elif rt_cd == '' and str(msg_cd) == '':
                    is_success = True  # 공백 → 성공 간주
                
                # 주문번호가 있으면 성공으로 간주 (예: 모의투자 응답 비어 있음)
                if not is_success:
                    odno_present = bool(order_data.get('ODNO'))
                    if odno_present:
                        is_success = True
                        logger.debug(f"주문번호 존재로 성공 간주: rt_cd='{rt_cd}', msg_cd='{msg_cd}'")
                
                # 성공 여부 확인
                if not is_success:
                    logger.error(f"❌ KIS API 매수 주문 실패: {stock.stock_code} [{rt_cd}/{msg_cd}] {msg1}")
                    return False
                
                # 주문 정보 추출
                actual_order_id = str(order_data.get('ODNO', order_id or f"BUY_{int(now_kst().timestamp())}"))
                krx_orgno = str(order_data.get('KRX_FWDG_ORD_ORGNO', ''))
                ord_tmd = str(order_data.get('ORD_TMD', ''))
                
                logger.info(f"✅ KIS API 매수 주문 접수 성공: {stock.stock_code} "
                           f"주문번호: {actual_order_id}, 거래소코드: {krx_orgno}, 주문시간: {ord_tmd} "
                           f"응답: [{msg_cd}] {msg1}")
                
            except Exception as api_error:
                logger.error(f"❌ KIS API 매수 주문 오류 {stock.stock_code}: {api_error}")
                return False
            
            # 🔥 주문 성공 시에만 Stock 객체 상태 업데이트
            stock.status = StockStatus.BUY_ORDERED
            stock.buy_price = price
            stock.buy_quantity = quantity
            stock.buy_amount = total_amount
            stock.buy_order_id = actual_order_id
            stock.buy_order_orgno = krx_orgno
            stock.buy_order_time = ord_tmd
            stock.order_time = now_kst()
            
            # 손절가, 익절가 설정 (시장 상황에 따른 동적 조정)
            stop_loss_rate = self._get_dynamic_stop_loss_rate()
            take_profit_rate = self._get_dynamic_take_profit_rate()
            
            stock.stop_loss_price = price * (1 + stop_loss_rate)
            stock.target_price = price * (1 + take_profit_rate)
            
            # 🆕 트레일링 스탑 초기화 (설정에 따라)
            if self.strategy_config.get('trailing_stop_enabled', False):
                trail_ratio = self.strategy_config.get('trailing_stop_ratio', 1.0)
                stock.dynamic_peak_price = price
                stock.dynamic_target_price = price * (1 - trail_ratio / 100)
            
            # 실행 시간 기록
            execution_time = now_kst().timestamp() - start_time
            self.execution_times.append(execution_time)
            self._update_execution_stats()
            
            # 시간대별 거래 수 증가
            current_hour = now_kst().hour
            self.hourly_trades[current_hour] += 1
            
            # 🔥 주문 단계에서는 DB 저장하지 않음 (체결 시점에 저장)
            # 실제 체결은 _handle_buy_execution에서 처리
            
            logger.info(f"✅ 매수 주문 실행 완료: {stock.stock_code} {quantity}주 @{price:,}원 "
                       f"주문번호: {actual_order_id}, 거래소코드: {krx_orgno} "
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
                base_rate = base_rate * 0.7  # 1.4% 손절
            elif recent_win_rate > 0.7:  # 승률 70% 이상이면 더 공격적
                base_rate = base_rate * 1.2  # 2.4% 손절
        
        # 🆕 시장 변동성에 따른 추가 조정
        try:
            # KOSPI 변동성이 높으면 더 보수적인 손절
            market_volatility = self._get_market_volatility()
            if market_volatility > 2.0:  # 2% 이상 변동성
                base_rate = base_rate * 0.8  # 더 타이트한 손절
                logger.debug(f"고변동성 시장으로 손절률 조정: {base_rate:.3f}")
        except Exception as e:
            logger.debug(f"시장 변동성 조회 실패, 기본값 사용: {e}")
        
        return base_rate
    
    def _get_dynamic_take_profit_rate(self) -> float:
        """동적 익절률 계산 (시장 상황 반영)
        
        Returns:
            익절률 (양수)
        """
        base_rate = self.risk_config.get('take_profit_rate', 0.03)

        trading_mode = str(self.strategy_config.get('trading_mode', 'day')).lower()
        current_hour = now_kst().hour

        # 데이트레이딩 모드에서는 시간대 보정을 적용하지 않는다.
        if trading_mode not in ('day', 'daytrade', 'day_trading'):
            if 9 <= current_hour <= 10:  # 장 초반
                base_rate *= 1.15
            elif 14 <= current_hour <= 15:  # 장 마감 전
                base_rate *= 0.8

        # 🆕 시장 변동성에 따른 추가 조정 (모드는 무관하게 유지)
        try:
            market_volatility = self._get_market_volatility()
            if market_volatility > 2.0:
                base_rate *= 1.15
                logger.debug(f"고변동성 시장으로 익절률 조정: {base_rate:.3f}")
            elif market_volatility < 0.5:
                base_rate *= 1.10
                logger.debug(f"저변동성 시장으로 익절률 조정: {base_rate:.3f}")
        except Exception as e:
            logger.debug(f"시장 변동성 조회 실패, 기본값 사용: {e}")

        return base_rate
    
    def _get_current_market_phase(self) -> str:
        """현재 시장 단계 반환"""
        from datetime import time as dt_time
        
        current_time = now_kst().time()
        
        if current_time <= dt_time(9, 30):
            return 'opening'
        elif current_time <= dt_time(12, 0):
            return 'active'
        elif current_time <= dt_time(13, 0):
            return 'lunch'
        elif current_time <= dt_time(14, 50):
            return 'active'
        elif current_time <= dt_time(15, 0):
            return 'pre_close'
        else:
            return 'closing'
    
    def _get_market_volatility(self) -> float:
        """시장 변동성 계산 (KOSPI 기준)
        
        Returns:
            시장 변동성 (%)
        """
        try:
            # KOSPI 지수의 일중 변동성 계산
            from api.kis_market_api import get_inquire_daily_itemchartprice
            
            # KOSPI 지수 코드로 일봉 데이터 조회 (최근 5일)
            kospi_data = get_inquire_daily_itemchartprice(
                output_dv="2",
                itm_no="0001",  # KOSPI 지수
                period_code="D",
                adj_prc="1"
            )
            
            if kospi_data is None or len(kospi_data) < 5:
                return 1.0  # 기본값
            
            # 최근 5일 변동성 계산
            volatilities = []
            for i in range(min(5, len(kospi_data))):
                row = kospi_data.iloc[i]
                high = float(row.get('stck_hgpr', 0))
                low = float(row.get('stck_lwpr', 0))
                close = float(row.get('stck_clpr', 0))
                
                if close > 0:
                    daily_volatility = (high - low) / close * 100
                    volatilities.append(daily_volatility)
            
            if volatilities:
                avg_volatility = sum(volatilities) / len(volatilities)
                logger.debug(f"시장 변동성 계산: {avg_volatility:.2f}% (최근 {len(volatilities)}일)")
                return avg_volatility
            
            return 1.0  # 기본값
            
        except Exception as e:
            logger.debug(f"시장 변동성 계산 오류: {e}")
            return 1.0  # 기본값
    
    def _calculate_recent_win_rate(self, recent_count: int = 10) -> float:
        """최근 거래의 승률 계산
        
        Args:
            recent_count: 최근 거래 수
            
        Returns:
            최근 승률 (0.0 ~ 1.0)
        """
        if not self.recent_trades:
            # 거래 기록이 없으면 전체 승률 반환
            return self.winning_trades / max(self.total_trades, 1)
        
        # 최근 거래 기록에서 승률 계산
        recent_trades_list = list(self.recent_trades)
        
        # 요청된 수만큼만 사용 (최신 거래부터)
        trades_to_analyze = recent_trades_list[-recent_count:] if len(recent_trades_list) >= recent_count else recent_trades_list
        
        if not trades_to_analyze:
            return 0.5  # 기본값
        
        # 승리한 거래 수 계산
        winning_count = sum(1 for trade in trades_to_analyze if trade['is_winning'])
        
        recent_win_rate = winning_count / len(trades_to_analyze)
        
        logger.debug(f"최근 승률 계산: {winning_count}/{len(trades_to_analyze)} = {recent_win_rate:.3f} "
                    f"(분석 대상: 최근 {len(trades_to_analyze)}건)")
        
        return recent_win_rate
    
    def _update_execution_stats(self):
        """실행 통계 업데이트"""
        if self.execution_times:
            self.avg_execution_time = sum(self.execution_times) / len(self.execution_times)
            
            # 최근 100개 실행 시간만 유지
            if len(self.execution_times) > 100:
                self.execution_times = self.execution_times[-100:]
    
    def confirm_buy_execution(self, stock: Stock, executed_price: Optional[float] = None) -> bool:
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
            
            if executed_price and stock.buy_price and executed_price != stock.buy_price:
                # 체결가가 다르면 손절가, 익절가 재계산
                price_diff_rate = (executed_price - stock.buy_price) / stock.buy_price
                logger.info(f"체결가 차이: {price_diff_rate:+.2%} ({stock.buy_price:,} → {executed_price:,})")
                
                stock.buy_price = executed_price
                if stock.buy_quantity:
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
            
            # 🆕 손익 곡선 및 MDD 업데이트
            self._update_equity_and_drawdown()
            
            logger.info(f"✅ 매수 체결 확인: {stock.stock_code} {stock.buy_quantity}주 @{stock.buy_price:,}원")
            return True
            
        except Exception as e:
            logger.error(f"매수 체결 확인 오류 {stock.stock_code}: {e}")
            return False
    
    def execute_sell_order(self, stock: Stock, price: Optional[float] = None, 
                          reason: str = "manual", order_id: Optional[str] = None) -> bool:
        """매도 주문 실행 (실제 KIS API 호출 포함)
        
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
                logger.warning(f"매도 불가 상태: {stock.stock_code if stock else 'None'} "
                             f"상태: {stock.status.value if stock else 'None'}")
                return False
            
            # 가격이 없으면 캐시에서 조회
            if price is None:
                price = self.last_price_cache.get(stock.stock_code, stock.buy_price)
                if price is None or price <= 0:
                    logger.error(f"유효하지 않은 매도가: {stock.stock_code} 가격: {price}")
                    return False
            
            # 매도 수량 확인 (남은 수량 기반)
            sell_quantity = stock.buy_quantity or 0
            
            if not sell_quantity or sell_quantity <= 0:
                logger.error(f"유효하지 않은 매도 수량: {stock.stock_code} 수량: {sell_quantity}")
                return False
            
            # 🔥 실제 KIS API 매도 주문 실행
            logger.info(f"📤 KIS API 매도 주문 요청: {stock.stock_code} {sell_quantity}주 @{price:,}원 (사유: {reason})")
            
            try:
                from api.kis_order_api import get_order_cash
                
                # KIS API 매도 주문 실행
                order_result = get_order_cash(
                    ord_dv="sell",          # 매도
                    itm_no=stock.stock_code, # 종목코드
                    qty=sell_quantity,      # 수량
                    unpr=int(price)         # 주문가격 (정수)
                )
                
                # 주문 결과 확인
                if order_result is None or order_result.empty:
                    logger.error("❌ 매도 주문 실패 – 응답 없음 (수량 초과 등)")
                    return False
                else:
                    order_data = order_result.iloc[0]
                
                rt_cd = str(order_data.get('rt_cd', '')).strip()
                msg_cd = str(order_data.get('msg_cd', '')).strip()
                msg1 = order_data.get('msg1', '')
                
                # 🆕 성공 여부 판정 로직 완화  
                # - 일부 브로커/모의투자는 rt_cd 공백이거나 '00' 으로 오는 경우가 있음  
                # - 주문번호(ODNO)가 존재하면 일단 접수 성공으로 간주하고 체결통보에서 최종 확인  
                is_success = False
                if rt_cd in ('0', '00'):
                    is_success = True
                elif rt_cd == '' and str(msg_cd) == '':
                    is_success = True  # 공백 → 성공 간주
                
                # 주문번호가 있으면 성공으로 간주 (예: 모의투자 응답 비어 있음)
                if not is_success:
                    odno_present = bool(order_data.get('ODNO'))
                    if odno_present:
                        is_success = True
                        logger.debug(f"주문번호 존재로 성공 간주: rt_cd='{rt_cd}', msg_cd='{msg_cd}'")
                
                # 성공 여부 확인
                if not is_success:
                    logger.error(f"❌ KIS API 매도 주문 실패: {stock.stock_code} [{rt_cd}/{msg_cd}] {msg1}")
                    return False
                
                # 주문 정보 추출
                actual_order_id = str(order_data.get('ODNO', order_id or f"SELL_{int(now_kst().timestamp())}"))
                krx_orgno = str(order_data.get('KRX_FWDG_ORD_ORGNO', ''))
                ord_tmd = str(order_data.get('ORD_TMD', ''))
                
                logger.info(f"✅ KIS API 매도 주문 접수 성공: {stock.stock_code} "
                           f"주문번호: {actual_order_id}, 거래소코드: {krx_orgno}, 주문시간: {ord_tmd} "
                           f"응답: [{msg_cd}] {msg1}")
                
            except Exception as api_error:
                logger.error(f"❌ KIS API 매도 주문 오류 {stock.stock_code}: {api_error}")
                return False
            
            # 🔥 주문 성공 시에만 Stock 객체 상태 업데이트
            stock.status = StockStatus.SELL_ORDERED
            stock.sell_order_id = actual_order_id
            stock.sell_order_orgno = krx_orgno
            stock.sell_order_time_api = ord_tmd
            stock.sell_order_time = now_kst()
            stock.sell_reason = reason
            
            # 시간대별 거래 수 증가
            current_hour = now_kst().hour
            self.hourly_trades[current_hour] += 1
            
            # 🔥 주문 단계에서는 DB 저장하지 않음 (체결 시점에 저장)
            # 실제 체결은 _handle_sell_execution에서 처리
            
            logger.info(f"✅ 매도 주문 실행 완료: {stock.stock_code} {sell_quantity}주 @{price:,}원 "
                       f"주문번호: {actual_order_id}, 거래소코드: {krx_orgno} (사유: {reason}) "
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
            
            # 🆕 거래 기록 저장 (승률 계산용)
            is_winning = stock.realized_pnl and stock.realized_pnl > 0
            trade_record = {
                'stock_code': stock.stock_code,
                'stock_name': stock.stock_name,
                'buy_price': stock.buy_price,
                'sell_price': executed_price,
                'quantity': stock.buy_quantity,
                'realized_pnl': stock.realized_pnl or 0,
                'realized_pnl_rate': stock.realized_pnl_rate or 0,
                'is_winning': is_winning,
                'sell_reason': stock.sell_reason or 'manual',
                'buy_time': stock.order_time,
                'sell_time': stock.sell_execution_time,
                'holding_minutes': (stock.sell_execution_time - stock.order_time).total_seconds() / 60 if stock.order_time else 0,
                'timestamp': now_kst()
            }
            
            self.recent_trades.append(trade_record)
            
            # 통계 업데이트
            self.total_trades += 1
            self.total_pnl += stock.realized_pnl or 0
            
            if stock.realized_pnl and stock.realized_pnl > 0:
                self.winning_trades += 1
            else:
                self.losing_trades += 1
            
            # 일일 거래 수 증가
            self.daily_trade_count += 1
            
            # 🆕 손익 곡선 및 MDD 업데이트
            self._update_equity_and_drawdown()
            
            # 가격 캐시 업데이트
            self.last_price_cache[stock.stock_code] = executed_price
            
            # 연속 손실 체크 (비상 정지 조건)
            if self.losing_trades >= 3 and self.winning_trades == 0:
                logger.warning("연속 손실 발생 - 비상 정지 활성화")
                self.emergency_stop = True
            
            logger.info(f"✅ 매도 체결 확인: {stock.stock_code} "
                       f"손익: {stock.realized_pnl:+,.0f}원 ({stock.realized_pnl_rate:+.2f}%) "
                       f"사유: {stock.sell_reason} "
                       f"보유시간: {trade_record['holding_minutes']:.1f}분")
            
            return True
            
        except Exception as e:
            logger.error(f"매도 체결 확인 오류 {stock.stock_code}: {e}")
            return False
    
    def get_positions_to_sell(self, stocks: List[Stock], 
                             current_prices: Optional[Dict[str, float]] = None) -> List[Tuple[Stock, str]]:
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
        recent_win_rate = self._calculate_recent_win_rate() * 100  # 최근 10건 승률
        recent_win_rate_5 = self._calculate_recent_win_rate(5) * 100  # 최근 5건 승률
        
        return {
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': win_rate,
            'recent_win_rate_10': recent_win_rate,  # 최근 10건 승률
            'recent_win_rate_5': recent_win_rate_5,   # 최근 5건 승률
            'total_pnl': self.total_pnl,
            'total_realized_pnl': self.total_pnl,  # 호환성
            'avg_execution_time': self.avg_execution_time,
            'daily_trade_count': self.daily_trade_count,
            'emergency_stop': self.emergency_stop,
            'recent_trades_count': len(self.recent_trades),  # 저장된 거래 기록 수
            'max_drawdown': self.max_drawdown
        }
    
    def get_recent_trades_summary(self, count: int = 10) -> Dict:
        """최근 거래 요약 정보 조회
        
        Args:
            count: 조회할 최근 거래 수
            
        Returns:
            최근 거래 요약 딕셔너리
        """
        if not self.recent_trades:
            return {
                'trades': [],
                'summary': {
                    'count': 0,
                    'win_count': 0,
                    'lose_count': 0,
                    'win_rate': 0.0,
                    'total_pnl': 0.0,
                    'avg_pnl': 0.0,
                    'avg_holding_minutes': 0.0
                }
            }
        
        # 최근 거래 추출
        recent_trades_list = list(self.recent_trades)
        trades_to_show = recent_trades_list[-count:] if len(recent_trades_list) >= count else recent_trades_list
        
        # 요약 통계 계산
        win_count = sum(1 for trade in trades_to_show if trade['is_winning'])
        lose_count = len(trades_to_show) - win_count
        win_rate = (win_count / len(trades_to_show)) * 100 if trades_to_show else 0.0
        total_pnl = sum(trade['realized_pnl'] for trade in trades_to_show)
        avg_pnl = total_pnl / len(trades_to_show) if trades_to_show else 0.0
        avg_holding_minutes = sum(trade['holding_minutes'] for trade in trades_to_show) / len(trades_to_show) if trades_to_show else 0.0
        
        return {
            'trades': trades_to_show,
            'summary': {
                'count': len(trades_to_show),
                'win_count': win_count,
                'lose_count': lose_count,
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'avg_pnl': avg_pnl,
                'avg_holding_minutes': avg_holding_minutes
            }
        }
    
    def get_performance_analysis(self) -> Dict:
        """성과 분석 정보 조회
        
        Returns:
            성과 분석 딕셔너리
        """
        recent_summary = self.get_recent_trades_summary(20)
        
        # 승률 추세 분석
        recent_5_win_rate = self._calculate_recent_win_rate(5) * 100
        recent_10_win_rate = self._calculate_recent_win_rate(10) * 100
        recent_20_win_rate = self._calculate_recent_win_rate(20) * 100
        
        # 매도 사유별 통계
        sell_reason_stats = {}
        if self.recent_trades:
            for trade in self.recent_trades:
                reason = trade['sell_reason']
                if reason not in sell_reason_stats:
                    sell_reason_stats[reason] = {'count': 0, 'win_count': 0, 'total_pnl': 0.0}
                
                sell_reason_stats[reason]['count'] += 1
                if trade['is_winning']:
                    sell_reason_stats[reason]['win_count'] += 1
                sell_reason_stats[reason]['total_pnl'] += trade['realized_pnl']
        
        # 각 사유별 승률 계산
        for reason in sell_reason_stats:
            stats = sell_reason_stats[reason]
            stats['win_rate'] = (stats['win_count'] / stats['count']) * 100 if stats['count'] > 0 else 0.0
            stats['avg_pnl'] = stats['total_pnl'] / stats['count'] if stats['count'] > 0 else 0.0
        
        return {
            'recent_summary': recent_summary['summary'],
            'win_rate_trend': {
                'recent_5': recent_5_win_rate,
                'recent_10': recent_10_win_rate,
                'recent_20': recent_20_win_rate,
                'overall': (self.winning_trades / max(self.total_trades, 1)) * 100
            },
            'sell_reason_analysis': sell_reason_stats,
            'risk_metrics': {
                'emergency_stop': self.emergency_stop,
                'daily_trades': self.daily_trade_count,
                'max_daily_loss': self.max_daily_loss,
                'current_pnl': self.total_pnl
            }
        }
    
    def reset_statistics(self, reset_trade_history: bool = False):
        """통계 초기화 (일일 리셋)
        
        Args:
            reset_trade_history: 거래 기록도 함께 초기화할지 여부
        """
        logger.info(f"거래 통계 초기화 (거래기록 초기화: {reset_trade_history})")
        self.daily_trade_count = 0
        self.emergency_stop = False
        self.hourly_trades.clear()
        
        # 거래 기록 초기화 (선택적)
        if reset_trade_history:
            self.recent_trades.clear()
            logger.info("거래 기록도 함께 초기화됨")
        
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
        recent_win_rate = stats.get('recent_win_rate_10', 0)
        
        summary = (f"거래 성과: {stats['total_trades']}건 "
                  f"(전체승률 {stats['win_rate']:.1f}%, 최근승률 {recent_win_rate:.1f}%, "
                  f"손익 {stats['total_pnl']:+,.0f}원)")
        
        # 거래 기록이 있으면 추가 정보 포함
        if self.recent_trades:
            recent_summary = self.get_recent_trades_summary(5)
            avg_holding = recent_summary['summary']['avg_holding_minutes']
            summary += f" [최근5건: 평균보유 {avg_holding:.1f}분]"
        
        return summary
    
    def __str__(self) -> str:
        """문자열 표현"""
        return f"TradeExecutor(거래수: {self.total_trades}, 손익: {self.total_pnl:+,.0f}원)"
    
    def cancel_order(self, stock: Stock, order_type: str = "buy") -> bool:
        """주문 취소 (KIS API 활용)
        
        Args:
            stock: 주식 객체
            order_type: 주문 타입 ("buy" 또는 "sell")
            
        Returns:
            취소 성공 여부
        """
        try:
            if order_type == "buy":
                if stock.status != StockStatus.BUY_ORDERED:
                    logger.warning(f"매수 주문 상태가 아님: {stock.stock_code} 상태: {stock.status.value}")
                    return False
                
                order_id = stock.buy_order_id
                orgno = stock.buy_order_orgno
                
            elif order_type == "sell":
                if stock.status != StockStatus.SELL_ORDERED:
                    logger.warning(f"매도 주문 상태가 아님: {stock.stock_code} 상태: {stock.status.value}")
                    return False
                
                order_id = stock.sell_order_id
                orgno = stock.sell_order_orgno
                
            else:
                logger.error(f"잘못된 주문 타입: {order_type}")
                return False
            
            if not order_id or not orgno:
                logger.error(f"주문 정보 부족: {stock.stock_code} order_id={order_id}, orgno={orgno}")
                return False
            
            # 🔥 KIS API 주문 취소 실행 (거래소코드 활용)
            logger.info(f"📤 KIS API 주문 취소 요청: {stock.stock_code} {order_type} "
                       f"주문번호: {order_id}, 거래소코드: {orgno}")
            
            try:
                from api.kis_order_api import get_order_rvsecncl
                
                # KIS API 주문 취소 실행
                cancel_result = get_order_rvsecncl(
                    ord_orgno=orgno,                    # 거래소코드 (KRX_FWDG_ORD_ORGNO)
                    orgn_odno=order_id,                 # 원주문번호 (ODNO)
                    ord_dvsn="00",                      # 주문구분 (지정가)
                    rvse_cncl_dvsn_cd="02",            # 취소구분 (02: 취소)
                    ord_qty=0,                          # 취소수량 (0: 전량취소)
                    ord_unpr=0,                         # 취소단가 (취소시 0)
                    qty_all_ord_yn="Y"                  # 잔량전부주문여부 (Y: 전량)
                )
                
                if cancel_result is None or cancel_result.empty:
                    logger.error(f"❌ KIS API 주문 취소 실패: {stock.stock_code}")
                    return False
                
                # 🔥 KIS API 응답 구조 활용
                cancel_data = cancel_result.iloc[0]
                rt_cd = cancel_data.get('rt_cd', '')
                msg_cd = cancel_data.get('msg_cd', '')
                msg1 = cancel_data.get('msg1', '')
                
                # 성공 여부 확인
                if rt_cd != '0':
                    logger.error(f"❌ KIS API 주문 취소 실패: {stock.stock_code} [{msg_cd}] {msg1}")
                    return False
                
                logger.info(f"✅ KIS API 주문 취소 성공: {stock.stock_code} {order_type} "
                           f"주문번호: {order_id} 응답: [{msg_cd}] {msg1}")
                
                # 주문 취소 성공 시 상태 복원
                if order_type == "buy":
                    stock.status = StockStatus.WATCHING
                    stock.buy_order_id = None
                    stock.buy_order_orgno = None
                    stock.buy_order_time = None
                elif order_type == "sell":
                    stock.status = StockStatus.BOUGHT
                    stock.sell_order_id = None
                    stock.sell_order_orgno = None
                    stock.sell_order_time_api = None
                
                return True
                
            except Exception as api_error:
                logger.error(f"❌ KIS API 주문 취소 오류 {stock.stock_code}: {api_error}")
                return False
            
        except Exception as e:
            logger.error(f"주문 취소 오류 {stock.stock_code}: {e}")
            return False
    
    # ---------------------------
    #  Equity / Drawdown 관리
    # ---------------------------
    def _update_equity_and_drawdown(self):
        """누적 손익 곡선 및 최대 낙폭 갱신"""
        equity = self.total_pnl
        self.equity_curve.append(equity)

        # 최고점 갱신
        if equity > self.running_max_equity:
            self.running_max_equity = equity

        # 현재 낙폭
        drawdown = self.running_max_equity - equity
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown 