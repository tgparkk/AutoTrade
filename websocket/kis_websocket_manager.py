"""
KIS 웹소켓 매니저 (Facade 패턴)
"""
import asyncio
import threading
import time
from typing import Dict, List, Optional, Callable, Any
from utils.logger import setup_logger
from datetime import datetime

# 분리된 컴포넌트들
from websocket.kis_websocket_connection import KISWebSocketConnection
from websocket.kis_websocket_data_parser import KISWebSocketDataParser
from websocket.kis_websocket_subscription_manager import KISWebSocketSubscriptionManager
from websocket.kis_websocket_message_handler import KISWebSocketMessageHandler, KIS_WSReq

logger = setup_logger(__name__)


class KISWebSocketManager:
    """
    KIS 웹소켓 매니저 (Facade 패턴)
    """

    def __init__(self):
        """초기화"""
        # 분리된 컴포넌트들 초기화
        self.connection = KISWebSocketConnection()
        self.data_parser = KISWebSocketDataParser()
        self.subscription_manager = KISWebSocketSubscriptionManager()
        self.message_handler = KISWebSocketMessageHandler(
            self.data_parser,
            self.subscription_manager
        )

        # 백그라운드 작업 관리
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._websocket_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()

        # 통계
        self.stats = {
            'start_time': time.time(),
            'total_messages': 0,
            'connection_count': 0,
            'reconnect_count': 0,
            'ping_pong_count': 0,
            'last_error': None
        }

        logger.info("✅ KIS 웹소켓 매니저 초기화 완료")

    # ==========================================
    # 기본 속성들
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
    # 연결 관리
    # ==========================================

    def start_message_loop(self):
        """메시지 루프 시작"""
        try:
            if self._websocket_thread and self._websocket_thread.is_alive():
                logger.warning("메시지 루프가 이미 실행 중입니다")
                return

            logger.info("웹소켓 백그라운드 스레드 시작...")
            self._shutdown_event.clear()
            self._websocket_thread = threading.Thread(
                target=self._run_websocket_thread,
                name="WebSocketThread",
                daemon=True
            )
            self._websocket_thread.start()
            logger.info("✅ 웹소켓 백그라운드 스레드 시작 완료")

        except Exception as e:
            logger.error(f"메시지 루프 시작 실패: {e}")

    def start(self):
        """웹소켓 시작 (start_message_loop 별칭)"""
        self.start_message_loop()

    def connect(self) -> bool:
        """웹소켓 연결 (동기 방식)"""
        try:
            logger.debug("🔄 웹소켓 연결 시도")

            # 이미 연결되어 있으면 성공 반환
            if self.is_connected and self.connection.check_actual_connection_status():
                logger.debug("✅ 이미 연결된 상태")
                return True

            # 스레드가 실행 중이면 연결 대기
            if self._websocket_thread and self._websocket_thread.is_alive():
                logger.debug("웹소켓 스레드 실행 중 - 연결 대기")
                for i in range(10):  # 10초 대기
                    if self.is_connected and self.connection.check_actual_connection_status():
                        logger.debug(f"✅ 웹소켓 연결 확인됨 ({i}초 대기)")
                        return True
                    time.sleep(1)
                logger.warning("⚠️ 웹소켓 연결 대기 시간 초과")
                return False

            # 스레드 시작
            logger.debug("웹소켓 스레드 시작")
            self.start_message_loop()

            # 연결 완료 대기
            for i in range(15):  # 15초 대기
                if self.is_connected and self.connection.check_actual_connection_status():
                    logger.debug(f"✅ 웹소켓 연결 성공 ({i}초 대기)")
                    self.stats['connection_count'] += 1
                    return True
                time.sleep(1)

            logger.error("❌ 웹소켓 연결 시간 초과")
            return False

        except Exception as e:
            logger.error(f"웹소켓 연결 오류: {e}")
            return False

    def ensure_connection(self) -> bool:
        """웹소켓 연결 보장"""
        try:
            # 이미 연결되어 있고 건강하면 성공
            if self.is_connected and self.is_websocket_healthy():
                return True

            # 스레드가 없으면 시작
            if not self._websocket_thread or not self._websocket_thread.is_alive():
                logger.info("웹소켓 스레드 시작...")
                self.start_message_loop()
                
                # 연결 대기
                for i in range(10):
                    time.sleep(1)
                    if self.is_connected and self.is_websocket_healthy():
                        logger.info(f"✅ 웹소켓 연결 성공 ({i+1}초 대기)")
                        return True
                
                logger.warning("웹소켓 연결 시간 초과")
                return False
            
            return self.is_connected

        except Exception as e:
            logger.error(f"웹소켓 연결 보장 오류: {e}")
            return False

    def reconnect(self) -> bool:
        """웹소켓 재연결"""
        try:
            logger.info("🔄 웹소켓 재연결 시도...")
            
            # 기존 연결 정리
            self.safe_cleanup()
            time.sleep(2)
            
            # 새로운 연결 시도
            return self.ensure_connection()
            
        except Exception as e:
            logger.error(f"웹소켓 재연결 오류: {e}")
            return False

    def is_websocket_healthy(self) -> bool:
        """웹소켓 연결 건강성 체크"""
        return self.connection.is_healthy()

    # ==========================================
    # 웹소켓 스레드 실행
    # ==========================================

    def _run_websocket_thread(self):
        """웹소켓 스레드 실행"""
        try:
            # 기존 이벤트 루프 정리
            if hasattr(self, '_event_loop') and self._event_loop:
                try:
                    if not self._event_loop.is_closed():
                        pending_tasks = asyncio.all_tasks(self._event_loop)
                        for task in pending_tasks:
                            task.cancel()
                        if self._event_loop.is_running():
                            self._event_loop.call_soon_threadsafe(self._event_loop.stop)
                        self._event_loop.close()
                except Exception as e:
                    logger.debug(f"기존 이벤트 루프 정리 중 오류: {e}")

            # 새로운 이벤트 루프 생성
            self._event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._event_loop)

            # 메인 루프 실행
            try:
                self._event_loop.run_until_complete(self._websocket_main_loop())
            except asyncio.CancelledError:
                logger.info("웹소켓 스레드가 정상적으로 취소되었습니다")
            except Exception as e:
                logger.error(f"웹소켓 메인 루프 실행 오류: {e}")

        except Exception as e:
            logger.error(f"웹소켓 스레드 오류: {e}")
        finally:
            # 이벤트 루프 정리
            try:
                if hasattr(self, '_event_loop') and self._event_loop:
                    pending_tasks = asyncio.all_tasks(self._event_loop)
                    if pending_tasks:
                        logger.debug(f"미완료 작업 {len(pending_tasks)}개 취소 중...")
                        for task in pending_tasks:
                            task.cancel()
                        try:
                            self._event_loop.run_until_complete(
                                asyncio.gather(*pending_tasks, return_exceptions=True)
                            )
                        except Exception as e:
                            logger.debug(f"작업 취소 중 오류: {e}")
                    if not self._event_loop.is_closed():
                        self._event_loop.close()
            except Exception as e:
                logger.error(f"이벤트 루프 정리 오류: {e}")

    async def _websocket_main_loop(self):
        """웹소켓 메인 루프"""
        try:
            # 연결
            if not await self.connection.connect():
                logger.error("초기 웹소켓 연결 실패")
                return

            self.connection.is_running = True
            logger.info("✅ 웹소켓 메인 루프 시작")

            # 계좌 체결통보 구독
            await self._subscribe_account_notices()

            # 메시지 루프
            consecutive_errors = 0
            max_consecutive_errors = 5

            while self.connection.is_running and not self._shutdown_event.is_set():
                try:
                    # 연결 상태 확인
                    if not self.connection.check_actual_connection_status():
                        logger.warning("웹소켓 연결 끊어짐 - 재연결 시도")
                        if not await self._safe_reconnect():
                            await asyncio.sleep(5)
                            continue

                    # 메시지 수신
                    try:
                        message = await asyncio.wait_for(
                            self.connection.receive_message(),
                            timeout=30
                        )

                        if message:
                            self.stats['total_messages'] += 1
                            consecutive_errors = 0

                            # 메시지 처리
                            result = await self.message_handler.process_message(message)

                            # PINGPONG 응답 처리
                            if result and result[0] == 'PINGPONG':
                                await self.connection.send_pong(result[1])
                                self.stats['ping_pong_count'] += 1

                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        logger.info("메시지 수신이 취소되었습니다")
                        break
                    except Exception as recv_error:
                        consecutive_errors += 1
                        logger.error(f"메시지 수신 오류 (연속 {consecutive_errors}회): {recv_error}")

                        if consecutive_errors >= max_consecutive_errors:
                            logger.error(f"연속 오류 {max_consecutive_errors}회 발생 - 재연결 시도")
                            if not await self._safe_reconnect():
                                logger.error("재연결 실패 - 메인 루프 종료")
                                break
                            consecutive_errors = 0
                        else:
                            await asyncio.sleep(1)

                except asyncio.CancelledError:
                    logger.info("웹소켓 메인 루프가 취소되었습니다")
                    break
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"메시지 루프 오류 (연속 {consecutive_errors}회): {e}")

                    if consecutive_errors >= max_consecutive_errors:
                        logger.error("치명적 오류 발생 - 메인 루프 종료")
                        break

                    await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"웹소켓 메인 루프 치명적 오류: {e}")
        finally:
            # 연결 정리
            try:
                if self.connection:
                    if hasattr(self.connection, 'websocket') and self.connection.websocket:
                        try:
                            if not getattr(self.connection.websocket, 'closed', True):
                                self.connection.websocket.close()
                        except Exception as e:
                            logger.debug(f"웹소켓 종료 중 오류: {e}")
                    
                    self.connection.is_connected = False
                    self.connection.is_running = False
            except Exception as e:
                logger.debug(f"연결 해제 중 오류: {e}")

            logger.info("🛑 웹소켓 메인 루프 종료")

    async def _safe_reconnect(self) -> bool:
        """안전한 재연결"""
        try:
            logger.info("🔄 웹소켓 재연결 시도...")
            self.stats['reconnect_count'] += 1

            # 기존 연결 정리
            await self.connection.disconnect()
            await asyncio.sleep(2)

            # 새로운 연결
            success = await self.connection.connect()
            if success:
                logger.info("✅ 웹소켓 재연결 성공")
                # 계좌 체결통보 재구독
                await self._subscribe_account_notices()
                return True
            else:
                logger.error("❌ 웹소켓 재연결 실패")
                return False

        except Exception as e:
            logger.error(f"❌ 재연결 과정 오류: {e}")
            self.stats['last_error'] = str(e)
            return False

    async def _subscribe_account_notices(self):
        """계좌 체결통보 구독"""
        try:
            from api import kis_auth as kis
            hts_id = kis.get_hts_id()

            if not hts_id:
                logger.error("❌ HTS ID가 설정되지 않음 - 계좌 체결통보 구독 불가")
                return False

            notice_msg = self.connection.build_message(
                KIS_WSReq.NOTICE.value,
                hts_id,
                "1"
            )
            await self.connection.send_message(notice_msg)

            logger.info(f"✅ 계좌 체결통보 구독 성공 (H0STCNI0) - HTS ID: {hts_id}")
            return True

        except Exception as e:
            logger.error(f"계좌 체결통보 구독 실패: {e}")
            return False

    # ==========================================
    # 구독 관리
    # ==========================================

    async def subscribe_stock(self, stock_code: str, callback: Optional[Callable] = None) -> bool:
        """종목 구독"""
        try:
            # 이미 구독된 종목인지 확인
            if self.subscription_manager.is_subscribed(stock_code):
                logger.debug(f"📡 {stock_code} 이미 구독됨 - 콜백만 추가")
                if callback:
                    self.subscription_manager.add_stock_callback(stock_code, callback)
                return True

            # 구독 가능 여부 확인
            if not self.subscription_manager.can_subscribe(stock_code):
                return False

            # 체결가 구독
            contract_msg = self.connection.build_message(
                KIS_WSReq.CONTRACT.value, stock_code, '1'
            )
            await self.connection.send_message(contract_msg)

            # 호가 구독
            bid_ask_msg = self.connection.build_message(
                KIS_WSReq.BID_ASK.value, stock_code, '1'
            )
            await self.connection.send_message(bid_ask_msg)

            # 구독 등록
            if self.subscription_manager.add_subscription(stock_code):
                if callback:
                    self.subscription_manager.add_stock_callback(stock_code, callback)
                logger.info(f"✅ 종목 구독 성공: {stock_code}")
                return True
            else:
                logger.warning(f"❌ 구독 등록 실패: {stock_code}")
                return False

        except Exception as e:
            error_msg = str(e)
            if "ALREADY IN SUBSCRIBE" in error_msg:
                self.subscription_manager.add_subscription(stock_code)
                if callback:
                    self.subscription_manager.add_stock_callback(stock_code, callback)
                return True
            else:
                logger.error(f"❌ 종목 구독 실패 ({stock_code}): {e}")
                return False

    def subscribe_stock_sync(self, stock_code: str, callback: Optional[Callable] = None) -> bool:
        """종목 구독 (동기 방식)"""
        try:
            # 웹소켓 연결 상태 확인
            if not self.connection.is_connected:
                logger.error(f"웹소켓 연결 상태 불량")
                return False

            # 이미 구독된 종목인지 확인
            if self.subscription_manager.is_subscribed(stock_code):
                if callback:
                    self.subscription_manager.add_stock_callback(stock_code, callback)
                logger.debug(f"이미 구독됨: {stock_code}")
                return True

            # 구독 가능 여부 확인
            if not self.subscription_manager.can_subscribe(stock_code):
                logger.warning(f"구독 한계 도달: {stock_code}")
                return False

            # 이벤트 루프가 있으면 비동기 방식 사용
            if self._event_loop and not self._event_loop.is_closed():
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self.subscribe_stock(stock_code, callback),
                        self._event_loop
                    )
                    result = future.result(timeout=10)
                    logger.info(f"✅ 이벤트 루프 구독 성공: {stock_code}")
                    return result
                except Exception as e:
                    logger.error(f"이벤트 루프 구독 오류 ({stock_code}): {e}")
            
            # 이벤트 루프가 없으면 새로운 이벤트 루프에서 실행
            logger.warning(f"이벤트 루프 없음 - 새 루프에서 구독: {stock_code}")
            try:
                # 새로운 이벤트 루프 생성 및 실행
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    result = loop.run_until_complete(self.subscribe_stock(stock_code, callback))
                    if result:
                        logger.info(f"✅ 새 루프 구독 성공: {stock_code}")
                    else:
                        logger.warning(f"⚠️ 새 루프 구독 실패: {stock_code}")
                    return result
                finally:
                    loop.close()
                    
            except Exception as e:
                logger.error(f"새 루프 구독 오류 ({stock_code}): {e}")
                return False

        except Exception as e:
            logger.error(f"동기 구독 오류 ({stock_code}): {e}")
            self.subscription_manager.remove_subscription(stock_code)
            return False

    async def unsubscribe_stock(self, stock_code: str) -> bool:
        """종목 구독 해제"""
        try:
            # 체결가 구독 해제
            contract_msg = self.connection.build_message(
                KIS_WSReq.CONTRACT.value, stock_code, '2'
            )
            await self.connection.send_message(contract_msg)

            # 호가 구독 해제
            bid_ask_msg = self.connection.build_message(
                KIS_WSReq.BID_ASK.value, stock_code, '2'
            )
            await self.connection.send_message(bid_ask_msg)

            # 구독 제거
            self.subscription_manager.remove_subscription(stock_code)
            logger.info(f"✅ 종목 구독 해제: {stock_code}")
            return True

        except Exception as e:
            logger.error(f"종목 구독 해제 실패 ({stock_code}): {e}")
            return False

    # ==========================================
    # 콜백 관리
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
        connection_status = self.connection.get_status()
        subscription_status = self.subscription_manager.get_status()
        handler_stats = self.message_handler.get_stats()
        parser_stats = self.data_parser.get_stats()

        return {
            'connection': connection_status,
            'subscriptions': subscription_status,
            'message_handler': handler_stats,
            'data_parser': parser_stats,
            'total_stats': self.stats.copy(),
            'uptime': time.time() - self.stats['start_time']
        }

    def get_status_summary(self) -> Dict:
        """웹소켓 상태 요약 정보"""
        try:
            return {
                'connected': self.is_connected,
                'healthy': self.is_websocket_healthy(),
                'subscribed_stocks': len(self.get_subscribed_stocks()),
                'subscription_capacity': self.subscription_manager.has_subscription_capacity(),
                'usage': self.get_websocket_usage(),
                'last_check_time': datetime.now().strftime('%H:%M:%S')
            }
        except Exception as e:
            logger.error(f"웹소켓 상태 요약 오류: {e}")
            return {
                'connected': False,
                'healthy': False,
                'subscribed_stocks': 0,
                'subscription_capacity': False,
                'usage': '0/0',
                'last_check_time': datetime.now().strftime('%H:%M:%S'),
                'error': str(e)
            }

    # ==========================================
    # 정리 및 종료
    # ==========================================

    async def cleanup(self):
        """정리 작업"""
        try:
            logger.info("웹소켓 매니저 정리 시작...")

            # 종료 신호 설정
            self._shutdown_event.set()

            # 웹소켓 연결 해제
            await self.connection.disconnect()

            # 구독 정리
            self.subscription_manager.clear_all_subscriptions()

            # 스레드 종료 대기
            if self._websocket_thread and self._websocket_thread.is_alive():
                self._websocket_thread.join(timeout=5)

            logger.info("✅ 웹소켓 매니저 정리 완료")

        except Exception as e:
            logger.error(f"웹소켓 매니저 정리 오류: {e}")

    def safe_cleanup(self):
        """동기식 안전한 정리"""
        try:
            logger.info("웹소켓 매니저 동기식 정리 시작...")

            # 종료 신호
            self._shutdown_event.set()

            # 연결 정리
            try:
                if self.connection:
                    if hasattr(self.connection, 'websocket') and self.connection.websocket:
                        try:
                            if not getattr(self.connection.websocket, 'closed', True):
                                self.connection.websocket.close()
                        except Exception as e:
                            logger.debug(f"웹소켓 종료 중 오류: {e}")

                    self.connection.is_connected = False
                    self.connection.is_running = False
            except Exception as e:
                logger.debug(f"연결 해제 중 오류: {e}")

            # 구독 정리
            self.subscription_manager.clear_all_subscriptions()

            # 스레드 정리
            if self._websocket_thread and self._websocket_thread.is_alive():
                self._websocket_thread.join(timeout=3)

            logger.info("✅ 웹소켓 매니저 동기식 정리 완료")

        except Exception as e:
            logger.error(f"웹소켓 매니저 동기식 정리 오류: {e}")

    def __del__(self):
        """소멸자"""
        try:
            self.safe_cleanup()
        except Exception:
            pass
