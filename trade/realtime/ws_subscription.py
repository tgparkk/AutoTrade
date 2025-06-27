"""ws_subscription.py – 웹소켓 구독 관리 모듈

SubscriptionManager 는 RealTimeMonitor 로부터 전달받은 종목을
대기열에 보관했다가, 메인 스레드(모니터링 사이클)에서 일정
배치 단위로 웹소켓 매니저에 안전하게 구독을 시도한다.

• add_pending(code): 구독 대기열 추가
• process_pending(): 한 배치 처리 – 기존 로직에서 가져옴
• cleanup(): retry 3회 초과 항목 정리
"""

from __future__ import annotations

import time
from typing import Set, Dict, Any, List

from utils.logger import setup_logger

logger = setup_logger(__name__)

class SubscriptionManager:
    def __init__(self, monitor: "Any"):
        self.monitor = monitor  # RealTimeMonitor 참조 (타입 회피)

        self.pending: Set[str] = set()
        self.retry_count: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def add_pending(self, stock_code: str):
        self.pending.add(stock_code)

    def process_pending(self):
        if not self.pending:
            return

        cfg = self.monitor.performance_config
        max_batch_size = cfg.get('websocket_subscription_batch_size', 3)
        batch = list(self.pending)[:max_batch_size]
        for code in batch:
            self.pending.discard(code)

        if not batch:
            return

        logger.debug(
            f"📡 웹소켓 구독 배치 처리: {len(batch)}개 (대기: {len(self.pending)}개)"
        )

        success_cnt = 0
        failed: List[str] = []

        for code in batch:
            start = time.time()
            ok = self._add_subscription_safely(code)
            elapsed = time.time() - start
            if elapsed > 2.0:
                logger.warning(f"⏰ 웹소켓 구독 처리 시간 초과: {code} ({elapsed:.1f}s)")

            if ok:
                success_cnt += 1
            else:
                failed.append(code)

        self._handle_failures(failed)

        if success_cnt:
            logger.info(
                f"📡 웹소켓 구독 배치 완료: {success_cnt}/{len(batch)}개 성공"
            )

    def cleanup(self):
        """retry 3회 초과 실패 항목 정리. 반환: 정리된 수"""
        expired = [c for c, n in self.retry_count.items() if n >= 3]
        for c in expired:
            self.retry_count.pop(c, None)
        return len(expired)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _handle_failures(self, failed: List[str]):
        if not failed:
            return
        for code in failed:
            n = self.retry_count.get(code, 0)
            if n < 3:
                self.pending.add(code)
                self.retry_count[code] = n + 1
                logger.debug(
                    f"🔄 웹소켓 구독 재시도 대기열 추가: {code} ({n + 1}/3)"
                )
            else:
                logger.error(f"❌ 웹소켓 구독 최대 재시도 초과: {code} – 포기")
                self.retry_count.pop(code, None)

    def _add_subscription_safely(self, stock_code: str) -> bool:
        """실제 subscribe_stock_sync 호출 로직 (기존 코드 이동)"""
        try:
            websocket_manager = getattr(self.monitor.stock_manager, 'websocket_manager', None)
            if not websocket_manager:
                logger.debug(f"웹소켓 매니저 없음 – 구독 생략: {stock_code}")
                return False

            if not websocket_manager.is_websocket_healthy():
                logger.warning("웹소켓 상태 불량 – 구독 실패: %s", stock_code)
                return False

            if not websocket_manager.is_connected:
                logger.warning("웹소켓 연결되지 않음 – 구독 실패: %s", stock_code)
                return False

            if websocket_manager.is_subscribed(stock_code):
                logger.debug("이미 구독된 종목: %s", stock_code)
                return True

            if not websocket_manager.has_subscription_capacity():
                logger.warning("구독 한도 초과 – 구독 실패: %s", stock_code)
                return False

            # 이벤트 루프 확인
            if not getattr(websocket_manager, '_event_loop', None) or websocket_manager._event_loop.is_closed():
                logger.warning("이벤트 루프 없음/종료 – 구독 실패: %s", stock_code)
                return False

            try:
                if websocket_manager.subscribe_stock_sync(stock_code):
                    logger.info("📡 웹소켓 구독 추가 성공: %s", stock_code)
                    return True
                logger.warning("웹소켓 구독 실패: %s", stock_code)
                return False
            except Exception as e:
                logger.error("웹소켓 구독 오류 %s: %s", stock_code, e)
                return False
        except Exception as e:
            logger.error("웹소켓 구독 추가 오류 %s: %s", stock_code, e)
            return False 