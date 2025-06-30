"""IntradayScanWorker – 장중 추가 종목 스캔을 담당.

RealTimeMonitor 에서 호출되며, 종목 스캔을 별도 스레드에서 수행하고
결과를 메인 스레드가 안전하게 수신·처리할 수 있게 해 준다.
"""

from __future__ import annotations

import threading
import time
import queue
from datetime import time as dt_time
from typing import Any, Optional, Tuple, List, TYPE_CHECKING

from utils.korean_time import now_kst
from utils.logger import setup_logger

# 순환 참조 방지를 위한 타입 힌트 전용 import
if TYPE_CHECKING:
    from trade.realtime_monitor import RealTimeMonitor

logger = setup_logger(__name__)


class IntradayScanWorker:
    def __init__(self, monitor: "RealTimeMonitor"):
        """IntradayScanWorker 초기화

        Parameters
        ----------
        monitor : RealTimeMonitor
            RealTimeMonitor 인스턴스 (순환 import 방지를 위해 TYPE_CHECKING 사용)
        """
        self.monitor: "RealTimeMonitor" = monitor

        # 내부 상태
        self._market_scanner_instance = None
        self._result_queue: Optional["queue.Queue[Tuple[str, Any]]"] = None
        self._scan_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def check_and_run_scan(self):
        """RealTimeMonitor.monitor_cycle 에서 호출"""
        try:
            cfg = self.monitor.performance_config

            current_time = now_kst()

            # 마감시간 전이면 수행 금지
            #if current_time.time() >= self.monitor.pre_close_time:
            #    return

            # 종목 수 한도 체크
            websocket_max = cfg.get('websocket_max_connections', 41)
            connections_per_stock = cfg.get('websocket_connections_per_stock', 2)
            system_connections = cfg.get('websocket_system_connections', 1)

            current_total_stocks = len(self.monitor.stock_manager.get_all_positions())
            max_manageable_stocks = (websocket_max - system_connections) // connections_per_stock
            configured_max = cfg.get('max_total_observable_stocks', 20)
            effective_max = min(configured_max, max_manageable_stocks)

            if current_total_stocks >= effective_max:
                return

            # 스캔 주기 체크
            should_scan = False
            if getattr(self, 'last_scan_time', None) is None:
                first_scan_time = dt_time(8, 40)
                if current_time.time() >= first_scan_time:
                    should_scan = True
            else:
                elapsed = (current_time - self.last_scan_time).total_seconds()
                if elapsed >= self.monitor.intraday_scan_interval:
                    should_scan = True

            if not should_scan:
                return

            remaining_slots = effective_max - current_total_stocks
            max_new = min(self.monitor.max_additional_stocks, remaining_slots)

            logger.info(f"🔍 장중 추가 종목 스캔 시작 (추가가능:{max_new}개)")

            self._result_queue = queue.Queue()

            self._scan_thread = threading.Thread(
                target=self._background_scan,
                args=(max_new,),
                name=f"IntradayScan-{current_time.strftime('%H%M%S')}",
                daemon=True,
            )
            self._scan_thread.start()

            self.last_scan_time = current_time

        except Exception as e:
            logger.error(f"IntradayScanWorker.check_and_run_scan 오류: {e}")

    def process_background_results(self):
        """메인 루프에서 주기적으로 호출 – 결과가 준비되면 처리"""
        if not self._result_queue:
            return

        try:
            status, result = self._result_queue.get_nowait()
        except queue.Empty:
            return

        # queue consumed → reset
        self._result_queue = None
        self._scan_thread = None

        if status == 'success':
            self._process_scan_results(result)
        else:
            logger.error(f"백그라운드 장중 스캔 실패: {result}")

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _background_scan(self, max_new_stocks: int):
        """별도 스레드: MarketScanner.intraday_scan_additional_stocks 수행"""
        try:
            if self._market_scanner_instance is None:
                from trade.market_scanner import MarketScanner
                self._market_scanner_instance = MarketScanner(self.monitor.stock_manager)

            additional = self._market_scanner_instance.intraday_scan_additional_stocks(max_stocks=max_new_stocks)
            self._result_queue.put(('success', additional))  # type: ignore[arg-type]
        except Exception as e:
            logger.error(f"백그라운드 장중 스캔 오류: {e}")
            self._result_queue.put(('error', str(e)))  # type: ignore[arg-type]

    def _process_scan_results(self, additional_stocks: List[Tuple[str, float, str]]):
        """스캔 이후 메인 스레드 처리"""
        try:
            if not additional_stocks:
                logger.info("📊 장중 추가 종목 스캔: 조건 만족 종목 없음")
                return

            logger.info(f"🎯 장중 추가 종목 후보 {len(additional_stocks)}개 발견:")

            added_cnt = 0
            for i, (code, score, reasons) in enumerate(additional_stocks, 1):
                try:
                    from utils.stock_data_loader import get_stock_data_loader
                    name = get_stock_data_loader().get_stock_name(code)

                    logger.info(f"  {i}. {code}[{name}] - 점수:{score:.1f} ({reasons})")

                    db = self.monitor.stock_manager._get_database()
                    if db:
                        db.save_intraday_scan_result(code, name, score, reasons)

                    success = self.monitor._add_intraday_stock_safely(code, name, score, reasons)
                    if success:
                        added_cnt += 1
                except Exception as inner_e:
                    logger.error(f"장중 종목 추가 오류 {code}: {inner_e}")

            if added_cnt:
                summary = self.monitor.stock_manager.get_intraday_summary()
                logger.info(
                    f"🎉 장중 종목 추가 완료: {added_cnt}/{len(additional_stocks)}개, "
                    f"총 {summary.get('total_count',0)}개, 평균점수 {summary.get('average_score',0):.1f}"
                )
            else:
                logger.warning("❌ 장중 종목 추가 실패: 모든 후보 종목 추가 불가")

        except Exception as e:
            logger.error(f"IntradayScanWorker._process_scan_results 오류: {e}") 