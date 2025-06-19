"""
종목 관리를 전담하는 StockManager 클래스
"""

import threading
from typing import Dict, List, Optional
from datetime import datetime
from models.position import Position, PositionStatus
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

logger = setup_logger(__name__)


class StockManager:
    """선정된 종목들을 관리하는 클래스 (멀티스레딩 안전)"""
    
    def __init__(self):
        """StockManager 초기화"""
        self.selected_stocks: Dict[str, Position] = {}  # 선정된 종목들 {종목코드: Position}
        self.candidate_stocks: List[str] = []  # 후보 종목 리스트
        self.max_selected_stocks = 15  # 최대 선정 종목 수
        
        # 🔒 멀티스레딩 안전성을 위한 락
        self._lock = threading.RLock()  # RLock: 같은 스레드에서 여러번 acquire 가능
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        
        logger.info("StockManager 초기화 완료 (멀티스레딩 안전)")
    
    def add_selected_stock(self, stock_code: str, stock_name: str, 
                          open_price: float, high_price: float, 
                          low_price: float, close_price: float, 
                          volume: int, selection_score: float) -> bool:
        """선정된 종목 추가
        
        Args:
            stock_code: 종목코드
            stock_name: 종목명
            open_price: 시가
            high_price: 고가
            low_price: 저가
            close_price: 종가
            volume: 거래량
            selection_score: 선정 점수
            
        Returns:
            추가 성공 여부
        """
        with self._lock:
            if len(self.selected_stocks) >= self.max_selected_stocks:
                logger.warning(f"최대 선정 종목 수 초과: {len(self.selected_stocks)}/{self.max_selected_stocks}")
                return False
            
            if stock_code in self.selected_stocks:
                logger.warning(f"이미 선정된 종목입니다: {stock_code}")
                return False
            
            position = Position(
                stock_code=stock_code,
                stock_name=stock_name,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
                volume=volume,
                total_pattern_score=selection_score,
                status=PositionStatus.WATCHING,
                max_holding_period=self.strategy_config.get('max_holding_days', 1)
            )
            
            self.selected_stocks[stock_code] = position
            logger.info(f"선정 종목 추가: {stock_code}[{stock_name}] (점수: {selection_score:.2f})")
            return True
    
    def remove_selected_stock(self, stock_code: str) -> bool:
        """선정된 종목 제거
        
        Args:
            stock_code: 종목코드
            
        Returns:
            제거 성공 여부
        """
        with self._lock:
            if stock_code in self.selected_stocks:
                position = self.selected_stocks[stock_code]
                del self.selected_stocks[stock_code]
                logger.info(f"선정 종목 제거: {stock_code}[{position.stock_name}]")
                return True
            return False
    
    def get_selected_stock(self, stock_code: str) -> Optional[Position]:
        """선정된 종목 조회
        
        Args:
            stock_code: 종목코드
            
        Returns:
            Position 객체 또는 None
        """
        with self._lock:
            return self.selected_stocks.get(stock_code)
    
    def get_all_selected_stocks(self) -> List[Position]:
        """모든 선정된 종목 반환"""
        with self._lock:
            return list(self.selected_stocks.values())
    
    def get_all_positions(self) -> List[Position]:
        """모든 포지션 반환 (get_all_selected_stocks의 별칭)"""
        return self.get_all_selected_stocks()
    
    def get_stocks_by_status(self, status: PositionStatus) -> List[Position]:
        """특정 상태의 종목들 반환
        
        Args:
            status: 조회할 상태
            
        Returns:
            해당 상태의 Position 리스트
        """
        with self._lock:
            return [pos for pos in self.selected_stocks.values() if pos.status == status]
    
    def update_stock_price(self, stock_code: str, current_price: float, 
                          today_volume: Optional[int] = None, price_change_rate: Optional[float] = None):
        """종목 가격 및 거래 정보 업데이트
        
        Args:
            stock_code: 종목코드
            current_price: 현재가
            today_volume: 오늘 거래량
            price_change_rate: 시가대비 상승률
        """
        with self._lock:
            position = self.get_selected_stock(stock_code)
            if position:
                position.close_price = current_price
                if today_volume is not None:
                    position.volume = today_volume
                position.update_timestamp()
                
                # 미실현 손익 계산 (매수 완료된 경우)
                if position.status == PositionStatus.BOUGHT:
                    position.calculate_unrealized_pnl(current_price)
    
    def change_stock_status(self, stock_code: str, new_status: PositionStatus, 
                           reason: str = "") -> bool:
        """종목 상태 변경
        
        Args:
            stock_code: 종목코드
            new_status: 새로운 상태
            reason: 변경 사유
            
        Returns:
            변경 성공 여부
        """
        with self._lock:
            position = self.get_selected_stock(stock_code)
            if not position:
                return False
            
            old_status = position.status
            position.status = new_status
            position.update_timestamp()
            
            logger.info(f"종목 상태 변경: {stock_code} {old_status.value} → {new_status.value}" + 
                       (f" (사유: {reason})" if reason else ""))
            return True
    
    def get_buy_ready_stocks(self) -> List[Position]:
        """매수 준비 상태의 종목들 반환"""
        return self.get_stocks_by_status(PositionStatus.BUY_READY)
    
    def get_sell_ready_stocks(self) -> List[Position]:
        """매도 준비 상태의 종목들 반환"""
        return self.get_stocks_by_status(PositionStatus.SELL_READY)
    
    def get_watching_stocks(self) -> List[Position]:
        """관찰 중인 종목들 반환"""
        return self.get_stocks_by_status(PositionStatus.WATCHING)
    
    def get_bought_stocks(self) -> List[Position]:
        """매수 완료된 종목들 반환"""
        return self.get_stocks_by_status(PositionStatus.BOUGHT)
    
    def clear_all_stocks(self):
        """모든 선정 종목 초기화"""
        with self._lock:
            count = len(self.selected_stocks)
            self.selected_stocks.clear()
            logger.info(f"모든 선정 종목 초기화: {count}개 종목 제거")
    
    def get_stock_summary(self) -> Dict:
        """종목 관리 요약 정보"""
        with self._lock:
            status_counts = {}
            for status in PositionStatus:
                status_counts[status.value] = len(self.get_stocks_by_status(status))
            
            return {
                'total_selected': len(self.selected_stocks),
                'max_capacity': self.max_selected_stocks,
                'status_breakdown': status_counts,
                'utilization_rate': len(self.selected_stocks) / self.max_selected_stocks * 100
            }
    
    def validate_stock_transitions(self) -> List[str]:
        """비정상적인 상태 전환 감지
        
        Returns:
            문제가 있는 종목들의 리스트
        """
        with self._lock:
            issues = []
            current_time = now_kst()
            
            for stock_code, position in self.selected_stocks.items():
                # 주문 상태에서 너무 오래 머물러 있는 경우
                if position.status in [PositionStatus.BUY_ORDERED, PositionStatus.SELL_ORDERED]:
                    if position.order_time:
                        minutes_since_order = (current_time - position.order_time).total_seconds() / 60
                        if minutes_since_order > 3:  # 3분 초과
                            issues.append(f"{stock_code}: {position.status.value} 상태 3분 초과")
            
            return issues
    
    def auto_recover_stuck_orders(self) -> int:
        """정체된 주문들 자동 복구
        
        Returns:
            복구된 종목 수
        """
        with self._lock:
            recovered_count = 0
            current_time = now_kst()
            
            for stock_code, position in self.selected_stocks.items():
                if position.status == PositionStatus.BUY_ORDERED and position.order_time:
                    minutes_since_order = (current_time - position.order_time).total_seconds() / 60
                    if minutes_since_order > 3:
                        # BUY_ORDERED → BUY_READY로 복구
                        self.change_stock_status(stock_code, PositionStatus.BUY_READY, "3분 타임아웃")
                        recovered_count += 1
                
                elif position.status == PositionStatus.SELL_ORDERED and position.sell_order_time:
                    minutes_since_order = (current_time - position.sell_order_time).total_seconds() / 60
                    if minutes_since_order > 3:
                        # SELL_ORDERED → BOUGHT로 복구
                        self.change_stock_status(stock_code, PositionStatus.BOUGHT, "3분 타임아웃")
                        recovered_count += 1
            
            if recovered_count > 0:
                logger.info(f"정체된 주문 자동 복구: {recovered_count}개 종목")
            
            return recovered_count
    
    def __str__(self) -> str:
        """문자열 표현"""
        with self._lock:
            summary = self.get_stock_summary()
            return f"StockManager(선정: {summary['total_selected']}/{summary['max_capacity']}, 활용률: {summary['utilization_rate']:.1f}%)"

    # ==========================================
    # 🆕 웹소켓 실시간 데이터 처리
    # ==========================================

    def handle_realtime_price(self, data_type: str, stock_code: str, data: Dict):
        """웹소켓 실시간 체결가 데이터 처리 (종목별 콜백) - 스레드 안전"""
        # 빠른 체크 (락 없이)
        if stock_code not in self.selected_stocks:
            return
        
        try:
            # 데이터 검증 및 추출
            current_price = data.get('current_price', 0)
            contract_volume = data.get('contract_volume', 0)
            acc_volume = data.get('acc_volume', 0)
            
            if current_price <= 0:
                return
            
            # 락 내에서 Position 업데이트
            with self._lock:
                position = self.selected_stocks.get(stock_code)
                if not position:
                    return
                
                old_price = position.close_price
                
                # 원자적 업데이트
                position.close_price = current_price
                if acc_volume > 0:
                    position.volume = acc_volume
                position.update_timestamp()
                
                # 미실현 손익 계산 (매수 완료된 경우만)
                if position.status == PositionStatus.BOUGHT and position.buy_price:
                    position.calculate_unrealized_pnl(current_price)
                
                # 시가 대비 상승률 계산
                change_rate = 0
                if position.open_price > 0:
                    change_rate = ((current_price - position.open_price) / position.open_price) * 100
                
                # 로그는 락 밖에서 (성능 최적화)
                should_log = abs(current_price - old_price) / old_price > 0.001  # 0.1% 이상 변동시만
                
            # 락 밖에서 로깅 (락 보유 시간 최소화)
            if should_log:
                logger.debug(f"📈 실시간 체결가: {stock_code} {old_price:,}→{current_price:,}원 "
                           f"(체결량:{contract_volume:,}, 누적:{acc_volume:,}, 상승률:{change_rate:+.2f}%)")
                    
        except Exception as e:
            logger.error(f"실시간 체결가 처리 오류 ({stock_code}): {e}")

    def handle_realtime_orderbook(self, data_type: str, stock_code: str, data: Dict):
        """웹소켓 실시간 호가 데이터 처리 (종목별 콜백) - 스레드 안전"""
        # 빠른 체크 (락 없이)
        if stock_code not in self.selected_stocks:
            return
            
        try:
            # 데이터 추출 및 검증
            ask_price = data.get('ask_price1', 0)
            bid_price = data.get('bid_price1', 0)
            ask_qty = data.get('ask_qty1', 0)
            bid_qty = data.get('bid_qty1', 0)
            total_ask_qty = data.get('total_ask_qty', 0)
            total_bid_qty = data.get('total_bid_qty', 0)
            
            if ask_price <= 0 or bid_price <= 0:
                return
            
            # 스프레드 및 매수세 계산 (락 없이)
            spread = ask_price - bid_price
            spread_rate = (spread / ask_price) * 100
            total_qty = total_ask_qty + total_bid_qty
            bid_ratio = (total_bid_qty / total_qty * 100) if total_qty > 0 else 50
            
            # 중요한 호가 변동만 로깅 (스프레드가 0.5% 미만이고 매수세가 40~60% 범위가 아닌 경우)
            should_log = spread_rate > 0.5 or bid_ratio < 40 or bid_ratio > 60
            
            if should_log:
                logger.debug(f"📊 실시간 호가: {stock_code} 매수:{bid_price:,}({bid_qty:,}) "
                           f"매도:{ask_price:,}({ask_qty:,}) 스프레드:{spread_rate:.2f}% "
                           f"매수세:{bid_ratio:.1f}%")
                    
        except Exception as e:
            logger.error(f"실시간 호가 처리 오류 ({stock_code}): {e}")

    def handle_execution_notice(self, data_type: str, data: Dict):
        """웹소켓 체결통보 처리 (글로벌 콜백)"""
        with self._lock:
            try:
                # 체결통보에서 종목코드 추출
                raw_data = data.get('data', '')
                if not raw_data:
                    return
                
                # 간단한 파싱으로 종목코드 추출
                parts = raw_data.split('^')
                if len(parts) > 8:
                    stock_code = parts[8]  # 종목코드 위치
                    
                    if stock_code in self.selected_stocks:
                        logger.info(f"🔔 체결통보 수신: {stock_code}")
                        # 체결통보 상세 처리는 TradeExecutor에서 담당
                        # 여기서는 로깅만 수행
                        
            except Exception as e:
                logger.error(f"체결통보 처리 오류: {e}")

    def setup_websocket_callbacks(self, websocket_manager):
        """웹소켓 콜백 설정"""
        with self._lock:
            try:
                logger.info("🔗 StockManager 웹소켓 콜백 설정 시작")
                
                # 선정된 종목들에 대해 실시간 데이터 콜백 등록
                for stock_code in self.selected_stocks.keys():
                    # 체결가 콜백 등록
                    websocket_manager.add_stock_callback(stock_code, self.handle_realtime_price)
                    # 호가 콜백 등록  
                    websocket_manager.add_stock_callback(stock_code, self.handle_realtime_orderbook)
                    logger.debug(f"📞 {stock_code} 실시간 콜백 등록 완료")
                
                # 체결통보 글로벌 콜백 등록
                websocket_manager.add_global_callback('stock_execution', self.handle_execution_notice)
                
                logger.info(f"✅ StockManager 웹소켓 콜백 설정 완료 - {len(self.selected_stocks)}개 종목")
                
            except Exception as e:
                logger.error(f"웹소켓 콜백 설정 오류: {e}") 