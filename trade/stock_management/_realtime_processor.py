#!/usr/bin/env python3
"""
실시간 데이터 처리 모듈

StockManager의 실시간 데이터 처리 로직을 분리한 내부 헬퍼 클래스
"""

import threading
from typing import Dict, Optional, TYPE_CHECKING
from utils.korean_time import now_kst
from utils.logger import setup_logger

if TYPE_CHECKING:
    from models.stock import StockStatus, RealtimeData, ReferenceData

logger = setup_logger(__name__)


class _RealtimeProcessor:
    """실시간 데이터 처리 전담 클래스 (내부 전용)"""
    
    def __init__(self,
                 realtime_data: Dict[str, "RealtimeData"],
                 trading_status: Dict[str, "StockStatus"],
                 trade_info: Dict[str, dict],
                 reference_stocks: Dict[str, "ReferenceData"],
                 realtime_lock: threading.RLock,
                 status_lock: threading.RLock,
                 data_updated: threading.Condition,
                 cache_invalidator_func,
                 strategy_config: Dict,
                 trade_executor = None):
        """실시간 처리기 초기화
        
        Args:
            realtime_data: 실시간 데이터 딕셔너리
            trading_status: 거래 상태 딕셔너리
            trade_info: 거래 정보 딕셔너리
            reference_stocks: 참조 데이터 딕셔너리
            realtime_lock: 실시간 데이터용 락
            status_lock: 상태 데이터용 락
            data_updated: 데이터 업데이트 조건 변수
            cache_invalidator_func: 캐시 무효화 함수
            strategy_config: 전략 설정 딕셔너리
            trade_executor: 거래 실행기 (옵션)
        """
        # 데이터 저장소 참조
        self._realtime_data = realtime_data
        self._trading_status = trading_status
        self._trade_info = trade_info
        self._reference_stocks = reference_stocks
        
        # 락 참조
        self._realtime_lock = realtime_lock
        self._status_lock = status_lock
        self._data_updated = data_updated
        
        # 기능 참조
        self._cache_invalidator = cache_invalidator_func
        self._strategy_config = strategy_config
        self._trade_executor = trade_executor
        
        logger.info("RealtimeProcessor 초기화 완료")
    
    def update_stock_price(self, stock_code: str, current_price: float, 
                          today_volume: Optional[int] = None, 
                          price_change_rate: Optional[float] = None):
        """종목 가격 업데이트 (스레드 안전성 개선)"""
        try:
            # Import를 메서드 내부에서 수행 (순환 import 방지)
            from models.stock import StockStatus
            
            # 🔥 락 순서 일관성 보장: realtime → status → cache 순서로 고정
            with self._realtime_lock:
                if stock_code not in self._realtime_data:
                    return
                
                realtime = self._realtime_data[stock_code]
                
                # 모든 업데이트를 원자적으로 수행
                old_price = realtime.current_price
                realtime.current_price = current_price
                if today_volume is not None:
                    realtime.today_volume = today_volume
                if price_change_rate is not None:
                    realtime.price_change_rate = price_change_rate
                realtime.update_timestamp()
                
                # 🆕 조건 변수로 데이터 업데이트 알림 (메모리 가시성 보장)
                with self._data_updated:
                    self._data_updated.notify_all()
                
                # 디버그 로그 (큰 가격 변동 감지)
                if old_price > 0:
                    price_change = abs((current_price - old_price) / old_price)
                    if price_change > 0.05:  # 5% 이상 변동
                        logger.info(f"⚡ 큰 가격 변동 감지: {stock_code} "
                                   f"{old_price:,}원 → {current_price:,}원 ({price_change:.1%})")
                
                # 🔥 미실현 손익 계산을 동일한 락 블록 내에서 처리 (원자성 보장)
                with self._status_lock:
                    if (self._trading_status.get(stock_code) == StockStatus.BOUGHT and
                        stock_code in self._trade_info):
                        trade_info = self._trade_info[stock_code]
                        buy_price = trade_info.get('buy_price')
                        buy_quantity = trade_info.get('buy_quantity')
                        
                        if buy_price and buy_quantity:
                            pnl = (current_price - buy_price) * buy_quantity
                            pnl_rate = (current_price - buy_price) / buy_price * 100
                            trade_info['unrealized_pnl'] = pnl
                            trade_info['unrealized_pnl_rate'] = pnl_rate
                            trade_info['updated_at'] = now_kst()
                
                # 🔥 캐시 무효화를 락 내부에서 처리 (원자성 보장)
                self._cache_invalidator(stock_code)
            
        except Exception as e:
            logger.error(f"가격 업데이트 오류 {stock_code}: {e}")
    
    def handle_realtime_price(self, data_type: str, stock_code: str, data: Dict):
        """실시간 가격 데이터 처리 (KIS 공식 문서 기반 고급 지표 포함) - 필드 매핑 개선"""
        try:
            # Import를 메서드 내부에서 수행 (순환 import 방지)
            from models.stock import StockStatus
            
            # 빠른 존재 확인 (락 없이)
            if stock_code not in self._realtime_data:
                return
            
            # 🔥 KIS 공식 문서 기반 핵심 데이터 추출 (안전한 변환)
            current_price = float(data.get('current_price', 0))
            acc_volume = int(data.get('acc_volume', 0))
            
            # 기본 가격 정보
            open_price = float(data.get('open_price', 0))
            high_price = float(data.get('high_price', 0))
            low_price = float(data.get('low_price', 0))
            contract_volume = int(data.get('contract_volume', 0))
            
            # 🆕 KIS 공식 문서 기반 고급 지표들 (안전한 변환)
            contract_strength = float(data.get('contract_strength', 100.0))
            buy_ratio = float(data.get('buy_ratio', 50.0))
            market_pressure = data.get('market_pressure', 'NEUTRAL')
            vi_standard_price = float(data.get('vi_standard_price', 0))
            
            # 🔥 거래정지 필드 안전 처리 (다양한 형태 지원)
            trading_halt_raw = data.get('trading_halt', False)
            if isinstance(trading_halt_raw, str):
                trading_halt = trading_halt_raw.upper() in ['Y', 'TRUE', '1']
            elif isinstance(trading_halt_raw, bool):
                trading_halt = trading_halt_raw
            else:
                trading_halt = False
            
            # 전일 대비 정보
            change_sign = data.get('change_sign', '3')
            change_amount = float(data.get('change_amount', 0))
            change_rate = float(data.get('change_rate', 0.0))
            
            # 체결 정보
            weighted_avg_price = float(data.get('weighted_avg_price', 0))
            sell_contract_count = int(data.get('sell_contract_count', 0))
            buy_contract_count = int(data.get('buy_contract_count', 0))
            net_buy_contract_count = int(data.get('net_buy_contract_count', 0))
            
            # 호가 잔량 정보
            total_ask_qty = int(data.get('total_ask_qty', 0))
            total_bid_qty = int(data.get('total_bid_qty', 0))
            
            # 거래량 관련
            volume_turnover_rate = float(data.get('volume_turnover_rate', 0.0))
            prev_same_time_volume = int(data.get('prev_same_time_volume', 0))
            prev_same_time_volume_rate = float(data.get('prev_same_time_volume_rate', 0.0))
            
            # 시간 구분 정보
            hour_cls_code = data.get('hour_cls_code', '0')
            market_operation_code = data.get('market_operation_code', '20')
            
            if current_price <= 0:
                return
            
            # 🔥 실시간 데이터 전체 업데이트 (원자적 처리)
            with self._realtime_lock:
                if stock_code not in self._realtime_data:
                    return
                
                realtime = self._realtime_data[stock_code]
                old_price = realtime.current_price
                
                # 기본 가격 정보 업데이트
                realtime.current_price = current_price
                realtime.today_volume = acc_volume
                realtime.contract_volume = contract_volume
                if high_price > 0:
                    realtime.today_high = max(realtime.today_high, high_price)
                if low_price > 0:
                    realtime.today_low = min(realtime.today_low, low_price) if realtime.today_low > 0 else low_price
                
                # 🆕 KIS 공식 문서 기반 고급 지표 업데이트
                realtime.contract_strength = contract_strength
                realtime.buy_ratio = buy_ratio
                realtime.market_pressure = market_pressure
                realtime.vi_standard_price = vi_standard_price  # is_vi False이면 0 저장
                realtime.trading_halt = trading_halt
                
                # 전일 대비 정보 업데이트
                realtime.change_sign = change_sign
                realtime.change_amount = change_amount
                realtime.change_rate = change_rate
                
                # 체결 정보 업데이트
                realtime.weighted_avg_price = weighted_avg_price
                realtime.sell_contract_count = sell_contract_count
                realtime.buy_contract_count = buy_contract_count
                realtime.net_buy_contract_count = net_buy_contract_count
                
                # 호가 잔량 정보 업데이트
                realtime.total_ask_qty = total_ask_qty
                realtime.total_bid_qty = total_bid_qty
                
                # 거래량 관련 업데이트
                realtime.volume_turnover_rate = volume_turnover_rate
                realtime.prev_same_time_volume = prev_same_time_volume
                realtime.prev_same_time_volume_rate = prev_same_time_volume_rate
                
                # 시간 구분 정보 업데이트
                realtime.hour_cls_code = hour_cls_code
                realtime.market_operation_code = market_operation_code
                
                # 🆕 호가 정보 업데이트 (웹소켓 체결가 데이터에서 추출)
                ask_price1 = float(data.get('ask_price1', 0))
                bid_price1 = float(data.get('bid_price1', 0))
                if ask_price1 > 0:
                    realtime.ask_price = ask_price1
                if bid_price1 > 0:
                    realtime.bid_price = bid_price1
                
                # 계산 지표 업데이트
                if self._reference_stocks.get(stock_code):
                    ref_data = self._reference_stocks[stock_code]
                    if ref_data.yesterday_close > 0:
                        realtime.price_change_rate = (current_price - ref_data.yesterday_close) / ref_data.yesterday_close * 100
                    if ref_data.avg_daily_volume > 0:
                        realtime.volume_spike_ratio = acc_volume / ref_data.avg_daily_volume
                
                # 🔥 price_change_rate 백업 계산 (웹소켓 데이터 누락 시에만)
                if realtime.price_change_rate == 0 and self._reference_stocks.get(stock_code):
                    ref_data = self._reference_stocks[stock_code] 
                    if ref_data.yesterday_close > 0:
                        calculated_rate = (current_price - ref_data.yesterday_close) / ref_data.yesterday_close * 100
                        realtime.price_change_rate = calculated_rate
                        logger.debug(f"price_change_rate 백업 계산: {stock_code} = {calculated_rate:.2f}%")
                
                # 변동성 계산 (일중 고저 기준)
                if realtime.today_high > 0 and realtime.today_low > 0:
                    realtime.volatility = (realtime.today_high - realtime.today_low) / realtime.today_low * 100
                
                realtime.update_timestamp()
                
                # 🆕 트레일링 스탑 즉시 매도 로직 (stock_manager 참조 필요로 일시 비활성화)
                # TODO: stock_manager 참조를 추가한 후 활성화
                # if (self._trade_executor is not None and
                #     self._strategy_config.get('trailing_stop_enabled', True)):
                #     try:
                #         from trade.utils.trailing_stop import trailing_stop_check
                #         trail_ratio = self._strategy_config.get('trailing_stop_ratio', 1.0)
                #         trailing_stop_check(
                #             stock_manager=stock_manager_ref,  # 참조 필요
                #             trade_executor=self._trade_executor,
                #             stock_code=stock_code,
                #             current_price=current_price,
                #             trail_ratio=trail_ratio,
                #         )
                #     except ImportError:
                #         pass  # trailing_stop 모듈이 없는 경우 무시
                
                # 디버그 로그 (큰 가격 변동 또는 특이 상황 감지)
                if old_price > 0:
                    price_change = abs((current_price - old_price) / old_price)
                    if price_change > 0.05:  # 5% 이상 변동
                        logger.info(f"⚡ 큰 가격 변동: {stock_code} "
                                   f"{old_price:,}원 → {current_price:,}원 ({price_change:.1%}) "
                                   f"체결강도:{contract_strength:.1f} 매수비율:{buy_ratio:.1f}%")
                
                # 특이 상황 로그
                if trading_halt:
                    logger.warning(f"🚨 거래정지: {stock_code}")
                
                # 🆕 VI 발동 여부 재판정 (HOUR_CLS_CODE 51/52 or NEW_MKOP_CLS_CODE 30/31)
                is_vi = (hour_cls_code in ['51', '52']) or (market_operation_code in ['30', '31'])
                if not is_vi:
                    # 실제 VI 상태가 아닌 경우 기준가 무효화
                    vi_standard_price = 0
                
                # 로그는 실제 VI 발생시에만 출력
                if is_vi and vi_standard_price > 0:
                    logger.warning(f"⚠️ VI 발동: {stock_code} 기준가:{vi_standard_price:,}원")
            
            # 미실현 손익 계산 (별도 락으로 분리하여 성능 최적화)
            with self._status_lock:
                if (self._trading_status.get(stock_code) == StockStatus.BOUGHT and
                    stock_code in self._trade_info):
                    trade_info = self._trade_info[stock_code]
                    buy_price = trade_info.get('buy_price')
                    buy_quantity = trade_info.get('buy_quantity')
                    
                    if buy_price and buy_quantity:
                        pnl = (current_price - buy_price) * buy_quantity
                        pnl_rate = (current_price - buy_price) / buy_price * 100
                        trade_info['unrealized_pnl'] = pnl
                        trade_info['unrealized_pnl_rate'] = pnl_rate
                        trade_info['updated_at'] = now_kst()
            
            # 캐시 무효화 (마지막에 수행)
            self._cache_invalidator(stock_code)
            
            # 🆕 유동성 추적기 기록 (체결 데이터)
            try:
                from websocket.liquidity_tracker import liquidity_tracker
                if liquidity_tracker is not None:
                    liquidity_tracker.record(stock_code, 'contract', contract_volume)
            except (ImportError, Exception):
                pass  # 유동성 추적기가 없거나 오류 시 무시
            
        except Exception as e:
            logger.error(f"실시간 가격 처리 오류 [{stock_code}]: {e}")
            logger.debug(f"처리 실패 데이터: {data}")
            # 🆕 데이터 구조 디버깅 정보 추가
            if data:
                logger.debug(f"데이터 키들: {list(data.keys())}")
                logger.debug(f"current_price 타입: {type(data.get('current_price'))}")
                logger.debug(f"trading_halt 타입: {type(data.get('trading_halt'))}")
    
    def handle_realtime_orderbook(self, data_type: str, stock_code: str, data: Dict):
        """실시간 호가 데이터 처리 (필드명 매핑 수정)"""
        try:
            if stock_code not in self._realtime_data:
                return
            
            # 🔥 웹소켓 파서 필드명과 매핑 (ask_price1, bid_price1 등)
            bid_prices = []
            ask_prices = []
            bid_volumes = []
            ask_volumes = []
            
            for i in range(1, 6):
                # 웹소켓 파서가 제공하는 실제 필드명 사용
                bid_price = float(data.get(f'bid_price{i}', 0))
                ask_price = float(data.get(f'ask_price{i}', 0))
                bid_volume = int(data.get(f'bid_qty{i}', 0))
                ask_volume = int(data.get(f'ask_qty{i}', 0))
                
                bid_prices.append(bid_price)
                ask_prices.append(ask_price)
                bid_volumes.append(bid_volume)
                ask_volumes.append(ask_volume)
            
            # 빠른 호가 업데이트
            with self._realtime_lock:
                if stock_code in self._realtime_data:
                    realtime = self._realtime_data[stock_code]
                    realtime.bid_prices = bid_prices
                    realtime.ask_prices = ask_prices
                    realtime.bid_volumes = bid_volumes
                    realtime.ask_volumes = ask_volumes
                    realtime.bid_price = bid_prices[0] if bid_prices[0] > 0 else realtime.bid_price
                    realtime.ask_price = ask_prices[0] if ask_prices[0] > 0 else realtime.ask_price
                    
                    # 🆕 추가 호가 정보 업데이트 (웹소켓 파서 호환)
                    realtime.total_ask_qty = int(data.get('total_ask_qty', 0))
                    realtime.total_bid_qty = int(data.get('total_bid_qty', 0))
                    
                    realtime.update_timestamp()
            
            # 캐시 무효화
            self._cache_invalidator(stock_code)
            
            # 🆕 유동성 추적기 기록 (호가 데이터)
            try:
                from websocket.liquidity_tracker import liquidity_tracker
                if liquidity_tracker is not None:
                    liquidity_tracker.record(stock_code, 'bidask', 0)
            except (ImportError, Exception):
                pass  # 유동성 추적기가 없거나 오류 시 무시
            
        except Exception as e:
            logger.error(f"실시간 호가 처리 오류 [{stock_code}]: {e}")
            logger.debug(f"호가 데이터 구조: {data}")
    
    def set_trade_executor_ref(self, trade_executor):
        """TradeExecutor 참조 설정"""
        self._trade_executor = trade_executor
        logger.info("✅ RealtimeProcessor TradeExecutor 참조 설정 완료")
    
    def get_processor_stats(self) -> Dict:
        """실시간 처리기 통계 정보 반환
        
        Returns:
            처리기 통계 딕셔너리
        """
        try:
            with self._realtime_lock:
                total_realtime = len(self._realtime_data)
            
            with self._status_lock:
                total_status = len(self._trading_status)
                total_trade_info = len(self._trade_info)
            
            return {
                'total_realtime_data': total_realtime,
                'total_trading_status': total_status,
                'total_trade_info': total_trade_info,
                'has_trade_executor': self._trade_executor is not None,
                'trailing_stop_enabled': self._strategy_config.get('trailing_stop_enabled', False)
            }
            
        except Exception as e:
            logger.error(f"실시간 처리기 통계 조회 오류: {e}")
            return {}
    
    def __str__(self) -> str:
        with self._realtime_lock:
            total_realtime = len(self._realtime_data)
        return f"RealtimeProcessor(실시간 종목: {total_realtime}개)" 