"""
KIS 웹소켓 매니저 (Facade 패턴)
"""
import asyncio
import threading
import time
from typing import Dict, List, Optional, Callable
from utils.logger import setup_logger
from utils.korean_time import now_kst

# 분리된 컴포넌트들
from websocket.kis_websocket_connection import KISWebSocketConnection
from websocket.kis_websocket_data_parser import KISWebSocketDataParser
from websocket.kis_websocket_subscription_manager import KISWebSocketSubscriptionManager
from websocket.kis_websocket_message_handler import KISWebSocketMessageHandler, KIS_WSReq

logger = setup_logger(__name__)


class KISWebSocketManager:
    """KIS 웹소켓 매니저 (Facade 패턴)"""

    def __init__(self):
        """초기화"""
        # 컴포넌트 초기화
        self.connection = KISWebSocketConnection()
        self.data_parser = KISWebSocketDataParser()
        self.subscription_manager = KISWebSocketSubscriptionManager()
        self.message_handler = KISWebSocketMessageHandler(
            self.data_parser, self.subscription_manager
        )

        # 스레드 관리
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._websocket_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()

        # 통계 (한국시간 기준)
        self.stats = {
            'start_time': now_kst().timestamp(),
            'total_messages': 0,
            'connection_count': 0,
            'reconnect_count': 0,
            'ping_pong_count': 0,
            'last_error': None
        }

        logger.info("✅ KIS 웹소켓 매니저 초기화 완료")

    # ==========================================
    # 속성들
    # ==========================================

    @property
    def is_connected(self) -> bool:
        """연결 상태"""
        return self.connection.is_connected

    @property
    def is_running(self) -> bool:
        """실행 상태"""
        return self.connection.is_running

    @property
    def websocket(self):
        """웹소켓 객체"""
        return self.connection.websocket

    @property
    def subscribed_stocks(self) -> set:
        """구독 중인 종목 목록"""
        return set(self.subscription_manager.get_subscribed_stocks())

    # ==========================================
    # 연결 관리 (통합된 메서드들)
    # ==========================================

    def start(self):
        """웹소켓 시작"""
        if self._websocket_thread and self._websocket_thread.is_alive():
            logger.warning("웹소켓이 이미 실행 중입니다")
            return

        logger.info("웹소켓 백그라운드 스레드 시작...")
        self._shutdown_event.clear()
        self._websocket_thread = threading.Thread(
            target=self._run_websocket_thread,
            name="WebSocketThread",
            daemon=True
        )
        self._websocket_thread.start()
        logger.info("✅ 웹소켓 스레드 시작 완료")

    def connect(self) -> bool:
        """웹소켓 연결"""
        # 이미 연결되어 있으면 성공
        if self.is_connected and self.connection.check_actual_connection_status():
            return True

        # 스레드가 없으면 시작
        if not self._websocket_thread or not self._websocket_thread.is_alive():
            self.start()

        # 연결 대기
        for i in range(15):
            if self.is_connected and self.connection.check_actual_connection_status():
                logger.info(f"✅ 웹소켓 연결 성공 ({i+1}초 대기)")
                self.stats['connection_count'] += 1
                return True
            time.sleep(1)

        logger.error("❌ 웹소켓 연결 시간 초과")
        return False

    def reconnect(self) -> bool:
        """웹소켓 재연결"""
        logger.info("🔄 웹소켓 재연결 시도...")
        self.safe_cleanup()
        time.sleep(2)
        return self.connect()

    def is_websocket_healthy(self) -> bool:
        """웹소켓 건강성 체크"""
        return self.connection.is_healthy()

    # ==========================================
    # 웹소켓 스레드 실행
    # ==========================================

    def _run_websocket_thread(self):
        """웹소켓 스레드 실행"""
        try:
            # 이벤트 루프 생성
            self._event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._event_loop)

            # 메인 루프 실행
            self._event_loop.run_until_complete(self._websocket_main_loop())

        except Exception as e:
            logger.error(f"웹소켓 스레드 오류: {e}")
        finally:
            self._cleanup_event_loop()

    def _cleanup_event_loop(self):
        """이벤트 루프 정리"""
        try:
            if self._event_loop and not self._event_loop.is_closed():
                # 미완료 작업 취소
                pending_tasks = asyncio.all_tasks(self._event_loop)
                for task in pending_tasks:
                    task.cancel()
                
                # 작업 완료 대기
                if pending_tasks:
                    self._event_loop.run_until_complete(
                        asyncio.gather(*pending_tasks, return_exceptions=True)
                    )
                
                self._event_loop.close()
        except Exception as e:
            logger.debug(f"이벤트 루프 정리 오류: {e}")

    async def _websocket_main_loop(self):
        """웹소켓 메인 루프"""
        try:
            # 초기 연결
            if not await self.connection.connect():
                logger.error("초기 웹소켓 연결 실패")
                return

            self.connection.is_running = True
            logger.info("✅ 웹소켓 메인 루프 시작")

            # 계좌 체결통보 구독
            await self._subscribe_account_notices()

            # 메시지 처리 루프
            consecutive_errors = 0
            max_errors = 5

            while self.connection.is_running and not self._shutdown_event.is_set():
                try:
                    # 연결 상태 확인
                    if not self.connection.check_actual_connection_status():
                        if not await self._handle_reconnect():
                            break

                    # 메시지 수신 및 처리
                    message = await asyncio.wait_for(
                        self.connection.receive_message(), timeout=30
                    )

                    if message:
                        self.stats['total_messages'] += 1
                        consecutive_errors = 0

                        # 메시지 처리
                        result = await self.message_handler.process_message(message)
                        
                        # PINGPONG 처리
                        if result and result[0] == 'PINGPONG':
                            await self.connection.send_pong(result[1])
                            self.stats['ping_pong_count'] += 1

                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"메시지 처리 오류 (연속 {consecutive_errors}회): {e}")

                    if consecutive_errors >= max_errors:
                        logger.error("연속 오류 한계 도달 - 재연결 시도")
                        if not await self._handle_reconnect():
                            break
                        consecutive_errors = 0
                    else:
                        await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"웹소켓 메인 루프 오류: {e}")
        finally:
            await self._cleanup_connection()
            logger.info("🛑 웹소켓 메인 루프 종료")

    async def _handle_reconnect(self) -> bool:
        """재연결 처리"""
        logger.info("🔄 웹소켓 재연결 시도...")
        self.stats['reconnect_count'] += 1

        await self.connection.disconnect()
        await asyncio.sleep(2)

        if await self.connection.connect():
            logger.info("✅ 웹소켓 재연결 성공")
            await self._subscribe_account_notices()
            return True
        else:
            logger.error("❌ 웹소켓 재연결 실패")
            return False

    async def _subscribe_account_notices(self):
        """계좌 체결통보 구독"""
        try:
            from api import kis_auth as kis
            hts_id = kis.get_hts_id()

            if not hts_id:
                logger.error("❌ HTS ID 없음 - 계좌 체결통보 구독 불가")
                return False

            notice_msg = self.connection.build_message(
                KIS_WSReq.NOTICE.value, hts_id, "1"
            )
            await self.connection.send_message(notice_msg)
            logger.info(f"✅ 계좌 체결통보 구독 성공 - HTS ID: {hts_id}")
            return True

        except Exception as e:
            logger.error(f"계좌 체결통보 구독 실패: {e}")
            return False

    async def _cleanup_connection(self):
        """연결 정리"""
        try:
            if self.connection:
                if hasattr(self.connection, 'websocket') and self.connection.websocket:
                    if not getattr(self.connection.websocket, 'closed', True):
                        await self.connection.websocket.close()
                
                self.connection.is_connected = False
                self.connection.is_running = False
        except Exception as e:
            logger.debug(f"연결 정리 오류: {e}")

    # ==========================================
    # 구독 관리
    # ==========================================

    async def subscribe_stock(self, stock_code: str, callback: Optional[Callable] = None) -> bool:
        """종목 구독"""
        try:
            # 이미 구독된 경우
            if self.subscription_manager.is_subscribed(stock_code):
                if callback:
                    self.subscription_manager.add_stock_callback(stock_code, callback)
                return True

            # 구독 가능 여부 확인
            if not self.subscription_manager.can_subscribe(stock_code):
                return False

            # 체결가 + 호가 구독
            messages = [
                self.connection.build_message(KIS_WSReq.CONTRACT.value, stock_code, '1'),
                self.connection.build_message(KIS_WSReq.BID_ASK.value, stock_code, '1')
            ]
            
            for msg in messages:
                await self.connection.send_message(msg)

            # 구독 등록
            if self.subscription_manager.add_subscription(stock_code):
                if callback:
                    self.subscription_manager.add_stock_callback(stock_code, callback)
                logger.info(f"✅ 종목 구독 성공: {stock_code}")
                return True

        except Exception as e:
            if "ALREADY IN SUBSCRIBE" in str(e):
                self.subscription_manager.add_subscription(stock_code)
                if callback:
                    self.subscription_manager.add_stock_callback(stock_code, callback)
                return True
            logger.error(f"❌ 종목 구독 실패 ({stock_code}): {e}")

        return False

    def subscribe_stock_sync(self, stock_code: str, callback: Optional[Callable] = None) -> bool:
        """종목 구독 (동기 방식)"""
        if not self.connection.is_connected:
            return False

        # 이미 구독된 경우
        if self.subscription_manager.is_subscribed(stock_code):
            if callback:
                self.subscription_manager.add_stock_callback(stock_code, callback)
            return True

        # 구독 가능 여부 확인
        if not self.subscription_manager.can_subscribe(stock_code):
            return False

        # 이벤트 루프를 통한 비동기 실행
        if self._event_loop and not self._event_loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.subscribe_stock(stock_code, callback),
                    self._event_loop
                )
                return future.result(timeout=10)
            except Exception as e:
                logger.error(f"동기 구독 오류 ({stock_code}): {e}")

        return False

    async def unsubscribe_stock(self, stock_code: str) -> bool:
        """종목 구독 해제"""
        try:
            # 구독 해제 메시지 전송
            messages = [
                self.connection.build_message(KIS_WSReq.CONTRACT.value, stock_code, '2'),
                self.connection.build_message(KIS_WSReq.BID_ASK.value, stock_code, '2')
            ]
            
            for msg in messages:
                await self.connection.send_message(msg)

            # 구독 제거
            self.subscription_manager.remove_subscription(stock_code)
            logger.info(f"✅ 종목 구독 해제: {stock_code}")
            return True

        except Exception as e:
            logger.error(f"종목 구독 해제 실패 ({stock_code}): {e}")
            return False

    # ==========================================
    # 콜백 관리 (통합된 메서드들)
    # ==========================================

    def add_stock_callback(self, stock_code: str, callback: Callable):
        """종목별 콜백 추가"""
        self.subscription_manager.add_stock_callback(stock_code, callback)

    def remove_stock_callback(self, stock_code: str, callback: Callable):
        """종목별 콜백 제거"""
        self.subscription_manager.remove_stock_callback(stock_code, callback)

    def add_global_callback(self, data_type: str, callback: Callable):
        """글로벌 콜백 추가"""
        self.subscription_manager.add_global_callback(data_type, callback)

    def register_callback(self, tr_id: str, callback: Callable):
        """콜백 등록 (TR_ID 기반 - StockManager 연동용)"""
        self.subscription_manager.add_tr_id_callback(tr_id, callback)
        logger.debug(f"TR_ID 콜백 등록: {tr_id}")

    def remove_global_callback(self, data_type: str, callback: Callable):
        """글로벌 콜백 제거"""
        self.subscription_manager.remove_global_callback(data_type, callback)

    # ==========================================
    # 상태 조회
    # ==========================================

    def get_subscribed_stocks(self) -> List[str]:
        """구독 중인 종목 목록"""
        return self.subscription_manager.get_subscribed_stocks()

    def get_subscription_count(self) -> int:
        """구독 수 조회"""
        return self.subscription_manager.get_subscription_count()

    def has_subscription_capacity(self) -> bool:
        """구독 가능 여부"""
        return self.subscription_manager.has_subscription_capacity()

    def get_websocket_usage(self) -> str:
        """웹소켓 사용량"""
        return self.subscription_manager.get_websocket_usage()

    def is_subscribed(self, stock_code: str) -> bool:
        """구독 여부 확인"""
        return self.subscription_manager.is_subscribed(stock_code)

    def get_status(self) -> Dict:
        """전체 상태 조회"""
        return {
            'connection': self.connection.get_status(),
            'subscriptions': self.subscription_manager.get_status(),
            'message_handler': self.message_handler.get_stats(),
            'data_parser': self.data_parser.get_stats(),
            'total_stats': self.stats.copy(),
            'uptime': now_kst().timestamp() - self.stats['start_time']
        }

    def get_status_summary(self) -> Dict:
        """웹소켓 상태 요약"""
        try:
            return {
                'connected': self.is_connected,
                'healthy': self.is_websocket_healthy(),
                'subscribed_stocks': len(self.get_subscribed_stocks()),
                'subscription_capacity': self.subscription_manager.has_subscription_capacity(),
                'usage': self.get_websocket_usage(),
                'last_check_time': now_kst().strftime('%H:%M:%S')
            }
        except Exception as e:
            return {
                'connected': False,
                'healthy': False,
                'subscribed_stocks': 0,
                'subscription_capacity': False,
                'usage': '0/0',
                'last_check_time': now_kst().strftime('%H:%M:%S'),
                'error': str(e)
            }

    def get_health_status(self) -> Dict:
        """웹소켓 연결 상태 및 헬스 체크"""
        try:
            return {
                'is_connected': self.is_connected,
                'is_running': self.is_running,
                'subscribed_stocks': len(self.subscription_manager.subscribed_stocks),
                'message_stats': self.message_handler.get_stats(),
                'last_check_time': now_kst().strftime('%H:%M:%S')
            }
        except Exception as e:
            logger.error(f"헬스 상태 조회 오류: {e}")
            return {
                'error': str(e),
                'last_check_time': now_kst().strftime('%H:%M:%S'),
                'is_connected': False,
                'is_running': False
            }

    # ==========================================
    # 정리 및 종료
    # ==========================================

    async def cleanup(self):
        """비동기 정리"""
        logger.info("웹소켓 매니저 정리 시작...")
        
        self._shutdown_event.set()
        await self.connection.disconnect()
        self.subscription_manager.clear_all_subscriptions()
        
        if self._websocket_thread and self._websocket_thread.is_alive():
            self._websocket_thread.join(timeout=5)
        
        logger.info("✅ 웹소켓 매니저 정리 완료")

    def safe_cleanup(self):
        """동기 정리"""
        logger.info("웹소켓 매니저 동기 정리 시작...")
        
        self._shutdown_event.set()
        
        # 연결 정리
        if self.connection:
            try:
                if hasattr(self.connection, 'websocket') and self.connection.websocket:
                    if not getattr(self.connection.websocket, 'closed', True):
                        self.connection.websocket.close()
                self.connection.is_connected = False
                self.connection.is_running = False
            except Exception as e:
                logger.debug(f"연결 해제 오류: {e}")

        # 구독 정리
        self.subscription_manager.clear_all_subscriptions()
        
        # 스레드 정리
        if self._websocket_thread and self._websocket_thread.is_alive():
            self._websocket_thread.join(timeout=3)
        
        logger.info("✅ 웹소켓 매니저 동기 정리 완료")

    def __del__(self):
        """소멸자"""
        try:
            self.safe_cleanup()
        except Exception:
            pass
