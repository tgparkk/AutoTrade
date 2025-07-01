"""buy_processor.py – 매수 조건 분석·수량 계산·주문 실행 담당

RealTimeMonitor 에서 분리된 BuyProcessor 클래스. 
단일 책임 원칙에 따라 매수 판단 로직과 주문 실행을 담당한다.

• analyze_and_buy(stock, realtime_data): 조건 분석→수량 계산→주문 실행
• analyze_buy_conditions(): TradingConditionAnalyzer 위임 래퍼
• calculate_buy_quantity(): TradingConditionAnalyzer 위임 래퍼

주의: 이 모듈은 RealTimeMonitor 내부에서 호출된다는 가정하에
stock_manager, trade_executor, condition_analyzer, config 들을 전달받아
사용한다. (의존성 주입)
"""

from __future__ import annotations

from typing import Dict, Optional, TYPE_CHECKING, Any
from datetime import datetime

from models.stock import Stock, StockStatus
from utils.korean_time import now_kst
from utils.logger import setup_logger

if TYPE_CHECKING:
    from trade.stock_manager import StockManager
    from trade.trade_executor import TradeExecutor
    from trade.trading_condition_analyzer import TradingConditionAnalyzer

logger = setup_logger(__name__)


class BuyProcessor:
    """매수 조건 분석 + 주문 실행 전담 클래스"""

    # ------------------------------------------------------------------
    # 초기화
    # ------------------------------------------------------------------
    def __init__(
        self,
        stock_manager: "StockManager",
        trade_executor: "TradeExecutor", 
        condition_analyzer: "TradingConditionAnalyzer",
        performance_config: Dict[str, Any],
        risk_config: Dict[str, Any],
        duplicate_buy_cooldown: int = 10,
    ):
        self.stock_manager: "StockManager" = stock_manager
        self.trade_executor: "TradeExecutor" = trade_executor
        self.condition_analyzer: "TradingConditionAnalyzer" = condition_analyzer

        # 설정
        self.performance_config: Dict[str, Any] = performance_config
        self.risk_config: Dict[str, Any] = risk_config
        self.duplicate_buy_cooldown: int = max(1, duplicate_buy_cooldown)

        # 내부 상태
        self._recent_buy_times: Dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # 공개 메서드
    # ------------------------------------------------------------------
    def analyze_buy_conditions(
        self,
        stock: Stock,
        realtime_data: Dict[str, Any],
        market_phase: Optional[str] = None,
    ) -> bool:
        """TradingConditionAnalyzer 래퍼"""
        return self.condition_analyzer.analyze_buy_conditions(
            stock, realtime_data, market_phase
        )

    def calculate_buy_quantity(self, stock: Stock) -> int:
        """TradingConditionAnalyzer 래퍼"""
        return self.condition_analyzer.calculate_buy_quantity(stock)

    def analyze_and_buy(
        self,
        stock: Stock,
        realtime_data: Dict[str, Any],
        current_positions_count: int,
        market_phase: Optional[str] = None,
    ) -> bool:
        """매수 조건 분석 → 주문 실행 전체 프로세스.

        Args:
            stock: 매수 후보 Stock 객체
            realtime_data: 실시간 데이터 dict (current_price 등)
            current_positions_count: 현재 보유 포지션 수 (한도 체크용)
            market_phase: 이미 계산된 시장 단계(옵션)

        Returns:
            bool: 주문 접수 성공 여부
        """
        try:
            # -----------------------------------------------------------
            # 선행 체크 (쿨다운, 마감 임박, 포지션 한도)
            # -----------------------------------------------------------
            if not self._pre_checks(stock, realtime_data, current_positions_count):
                return False

            # -----------------------------------------------------------
            # 조건 분석
            # -----------------------------------------------------------
            buy_signal = self.analyze_buy_conditions(stock, realtime_data, market_phase)
            if not buy_signal:
                return False

            # -----------------------------------------------------------
            # 수량 계산
            # -----------------------------------------------------------
            quantity = self.calculate_buy_quantity(stock)
            if quantity <= 0:
                logger.debug(f"{stock.stock_code} 매수수량 0 – 주문 건너뜀")
                return False

            # -----------------------------------------------------------
            # 주문 실행
            # -----------------------------------------------------------
            price = realtime_data.get("current_price") or 0
            if price <= 0:
                logger.debug(f"{stock.stock_code} 현재가 없음 – 주문 건너뜀")
                return False

            success = self.trade_executor.execute_buy_order(
                stock=stock,
                price=price,
                quantity=quantity,
                current_positions_count=current_positions_count,
            )

            if success:
                # 최근 매수 시각 기록 (중복 방지)
                self._recent_buy_times[stock.stock_code] = now_kst()
                logger.info(
                    f"✅ 매수 주문 성공: {stock.stock_code} {quantity}주 @{price:,}원"
                )
            else:
                logger.warning(
                    f"❌ 매수 주문 실패: {stock.stock_code} {quantity}주 @{price:,}원"
                )
            return success

        except Exception as e:
            logger.error(f"analyze_and_buy 오류 {stock.stock_code}: {e}")
            return False

    # ------------------------------------------------------------------
    # 내부 메서드
    # ------------------------------------------------------------------
    def _pre_checks(
        self,
        stock: Stock,
        realtime_data: Dict[str, Any],
        current_positions_count: int,
    ) -> bool:
        """매수 전 공통 선행 체크"""
        try:
            # 1) 이미 보유 중이거나 매수 주문이 진행중인 종목은 패스
            if stock.status in (
                StockStatus.BOUGHT,          # 이미 매수 완료
                StockStatus.BUY_ORDERED,     # 매수 주문 접수 후 체결 대기
                StockStatus.PARTIAL_BOUGHT   # 일부 체결된 상태
            ):
                return False

            # 2) 중복 매수 쿨다운
            last_buy_time = self._recent_buy_times.get(stock.stock_code)
            if last_buy_time and (now_kst() - last_buy_time).total_seconds() < self.duplicate_buy_cooldown:
                logger.debug(
                    f"쿨다운 미지남 - 중복 매수 스킵: {stock.stock_code}"
                )
                return False

            # 3) 장 마감 임박 시간 체크 (performance_config 에서 임계값 가져오기)
            from datetime import datetime, time as dt_time

            now_dt: datetime = now_kst()
            pre_close_hour = self.performance_config.get("pre_close_hour", 14)
            pre_close_minute = self.performance_config.get("pre_close_minute", 50)
            market_close_time = dt_time(pre_close_hour, pre_close_minute)
            if now_dt.time() >= market_close_time:
                return False

            # 4) 포지션 최대 보유 수
            max_positions = self.risk_config.get("max_open_positions", 10)
            if current_positions_count >= max_positions:
                logger.debug("포지션 한도 초과 – 신규 매수 제한")
                return False

            # 5) 호가/현재가 필수 값 존재
            if realtime_data.get("current_price", 0) <= 0:
                return False

            return True

        except Exception as e:
            logger.error(f"_pre_checks 오류 {stock.stock_code}: {e}")
            return False

    # fast/standard 별도 로직은 TradingConditionAnalyzer에 포함되어 있어
    # BuyProcessor 자체에는 별도 구현이 없다. 