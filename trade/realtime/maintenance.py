from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trade.realtime_monitor import RealTimeMonitor

from utils.logger import setup_logger

logger = setup_logger(__name__)


class MaintenanceManager:
    """모니터링 과정의 유지보수(주문 복구, 메모리 정리 등)를 담당."""

    def __init__(self, monitor: "RealTimeMonitor") -> None:  # type: ignore[name-defined]
        # 순환 import 우회
        self.monitor = monitor

    # -------------------------------------------------
    # Public API
    # -------------------------------------------------
    def check_stuck_orders(self) -> None:
        """OrderRecoveryManager 를 통해 지연된 주문을 복구하고 이상 상태를 검증."""
        try:
            recovered = self.monitor.order_recovery_manager.auto_recover_stuck_orders()
            if recovered:
                logger.warning(f"⚠️ 정체된 주문 {recovered}건 자동 복구 완료")

            issues = self.monitor.order_recovery_manager.validate_stock_transitions()
            if issues:
                logger.warning("🚨 비정상 상태 전환 감지:")
                for issue in issues[:5]:
                    logger.warning(f"   - {issue}")
        except Exception as exc:
            logger.error(f"정체 주문 검사 오류: {exc}")

    def cleanup(self) -> None:
        """알림·구독 대기열·스레드 등 메모리 정리를 수행한다."""
        try:
            cleanup_count = 0

            # 1. alert_sent
            if self.monitor.alert_sent:
                self.monitor.alert_sent.clear()
                cleanup_count += 1

            # 2. SubscriptionManager cleanup
            cleanup_count += self.monitor.sub_manager.cleanup()

            # 3. ScanWorker thread 상태
            if (
                self.monitor.scan_worker._scan_thread  # pylint: disable=protected-access
                and not self.monitor.scan_worker._scan_thread.is_alive()
            ):
                self.monitor.scan_worker._scan_thread = None
                cleanup_count += 1

            if cleanup_count:
                logger.info(f"🧹 메모리 정리 완료: {cleanup_count}개 항목 정리")
        except Exception as exc:
            logger.error(f"메모리 정리 오류: {exc}") 