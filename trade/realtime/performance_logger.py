"""performance_logger.py – 상태/최종 리포트 및 metrics 저장 (스캐폴드)"""

from typing import Any, Dict, TYPE_CHECKING

from utils.korean_time import now_kst
from utils.logger import setup_logger


logger = setup_logger(__name__)


class PerformanceLogger:
    """RealTimeMonitor 의 성과 및 상태 리포트를 담당하는 헬퍼"""

    def __init__(self, monitor: Any):
        if TYPE_CHECKING:
            from trade.realtime_monitor import RealTimeMonitor  # pragma: no cover

        self.monitor = monitor  # type: ignore

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def log_status_report(self, buy_result: Dict[str, int], sell_result: Dict[str, int]):
        """monitor._log_status_report 기능 이관"""
        try:
            current_time = now_kst().strftime("%H:%M:%S")
            market_phase = self.monitor.get_market_phase()

            websocket_status = self.monitor._get_websocket_status_summary()

            logger.info(
                f"🕐 {current_time} ({market_phase}) - "
                f"매수(확인:{buy_result['checked']}/신호:{buy_result['signaled']}/주문:{buy_result['ordered']}), "
                f"매도(확인:{sell_result['checked']}/신호:{sell_result['signaled']}/주문:{sell_result['ordered']}), "
                f"모니터링주기: {self.monitor.current_monitoring_interval}초, "
                f"웹소켓: {websocket_status}"
            )
        except Exception as e:
            logger.error(f"상태 리포트 로깅 오류: {e}")

    def log_final_performance(self):
        """monitor._log_final_performance 기능 이관"""
        try:
            logger.info("=" * 60)
            logger.info("📊 최종 성능 리포트")
            logger.info("=" * 60)
            st = self.monitor.stats_tracker
            logger.info(f"총 스캔 횟수: {st.market_scan_count:,}회")
            logger.info(f"매수 신호 감지: {st.buy_signals_detected}건")
            logger.info(f"매도 신호 감지: {st.sell_signals_detected}건")
            logger.info(f"주문 실행: {st.orders_executed}건")

            trade_stats = self.monitor.trade_executor.get_trade_statistics()
            logger.info(
                f"거래 성과: 승률 {trade_stats['win_rate']:.1f}%, "
                f"총 손익 {trade_stats['total_pnl']:+,.0f}원"
            )

            # metrics_daily 저장
            try:
                database = self.monitor.stock_manager._get_database()
            except AttributeError:
                database = None

            if database:
                metrics = {
                    'trade_date': now_kst().strftime('%Y%m%d'),
                    'trades': trade_stats['total_trades'],
                    'win_rate': trade_stats['win_rate'],
                    'total_pnl': trade_stats['total_pnl'],
                    'avg_pnl': (trade_stats['total_pnl'] / trade_stats['total_trades']) if trade_stats['total_trades'] else 0,
                    'max_drawdown': trade_stats.get('max_drawdown', 0),
                    'params': self.monitor.performance_config
                }
                database.save_daily_metrics(metrics)
                database.save_daily_summary(now_kst().date())
                logger.info("📈 metrics_daily / daily_summaries 저장 완료")

            logger.info("=" * 60)
        except Exception as e:
            logger.error(f"최종 성능 리포트 오류: {e}")

    def check_and_log_daily_report(self):
        """monitor._check_and_log_daily_report 기능 이관"""
        try:
            from datetime import time as dt_time

            current_dt = now_kst()
            if current_dt.time() >= dt_time(16, 0):
                today_str = current_dt.strftime("%Y%m%d")
                if getattr(self.monitor, "_daily_report_logged", None) != today_str:
                    self.log_final_performance()
                    self.monitor._daily_report_logged = today_str
        except Exception as e:
            logger.error(f"일일 리포트 자동 기록 오류: {e}") 