#!/usr/bin/env python3
"""
KIS 웹소켓 메시지 처리 전담 클래스
"""
import asyncio
import json
from typing import Dict, Callable, TYPE_CHECKING, Optional
from datetime import datetime
from enum import Enum
from utils.logger import setup_logger

if TYPE_CHECKING:
    from .kis_websocket_data_parser import KISWebSocketDataParser
    from .kis_websocket_subscription_manager import KISWebSocketSubscriptionManager

logger = setup_logger(__name__)


class KIS_WSReq(Enum):
    """웹소켓 요청 타입"""
    BID_ASK = 'H0STASP0'     # 실시간 국내주식 호가
    CONTRACT = 'H0STCNT0'    # 실시간 국내주식 체결
    NOTICE = 'H0STCNI0'      # 실시간 계좌체결발생통보 (실전)
    NOTICE_DEMO = 'H0STCNI9' # 실시간 계좌체결발생통보 (모의)
    MARKET_INDEX = 'H0UPCNT0' # 실시간 시장지수


class DataType(Enum):
    """데이터 타입"""
    STOCK_PRICE = 'stock_price'          # 주식체결가
    STOCK_ORDERBOOK = 'stock_orderbook'  # 주식호가
    STOCK_EXECUTION = 'stock_execution'  # 주식체결통보
    MARKET_INDEX = 'market_index'        # 시장지수


class KISWebSocketMessageHandler:
    """KIS 웹소켓 메시지 처리 전담 클래스"""

    def __init__(self, data_parser: "KISWebSocketDataParser",
                 subscription_manager: "KISWebSocketSubscriptionManager"):
        self.data_parser = data_parser
        self.subscription_manager = subscription_manager

        # 통계
        self.stats = {
            'messages_received': 0,
            'last_message_time': None,
            'ping_pong_count': 0,
            'last_ping_pong_time': None,
            'errors': 0
        }

    async def handle_realtime_data(self, data: str):
        """실시간 데이터 처리"""
        try:
            parts = data.split('|')
            if len(parts) < 4:
                logger.debug(f"⚠️ 데이터 파트 수 부족: {len(parts)}")
                return

            # KIS 공식 구조: 암호화유무|TR_ID|데이터건수|응답데이터
            encryption_flag = parts[0]  # 0: 암호화없음, 1: 암호화됨
            tr_id = parts[1]
            data_count = parts[2] if len(parts) > 2 else "001"
            raw_data = parts[3]

            if tr_id == KIS_WSReq.CONTRACT.value:
                # 실시간 체결가
                is_encrypted = encryption_flag == '1'
                
                if is_encrypted:
                    decrypted_data = self.data_parser.decrypt_notice_data(raw_data)
                    if decrypted_data:
                        parsed_data = self.data_parser.parse_contract_data(decrypted_data)
                    else:
                        parsed_data = None
                else:
                    parsed_data = self.data_parser.parse_contract_data(raw_data)

                if parsed_data:
                    await self._execute_callbacks(DataType.STOCK_PRICE.value, parsed_data)

            elif tr_id == KIS_WSReq.BID_ASK.value:
                # 실시간 호가
                is_encrypted = encryption_flag == '1'
                
                if is_encrypted:
                    decrypted_data = self.data_parser.decrypt_notice_data(raw_data)
                    if decrypted_data:
                        parsed_data = self.data_parser.parse_bid_ask_data(decrypted_data)
                    else:
                        parsed_data = None
                else:
                    parsed_data = self.data_parser.parse_bid_ask_data(raw_data)

                if parsed_data:
                    await self._execute_callbacks(DataType.STOCK_ORDERBOOK.value, parsed_data)

            elif tr_id in [KIS_WSReq.NOTICE.value]:
                # 체결통보 - 간단하게 처리
                logger.info(f"📢 체결통보 수신: {tr_id}")
                
                decrypted_data = self.data_parser.decrypt_notice_data(raw_data)
                if decrypted_data:
                    logger.info(f"✅ 체결통보 복호화 성공")
                    await self._execute_callbacks(DataType.STOCK_EXECUTION.value,
                                                {'data': decrypted_data, 'timestamp': datetime.now()})
                else:
                    logger.warning(f"❌ 체결통보 복호화 실패 - 원본 데이터로 처리")
                    await self._execute_callbacks(DataType.STOCK_EXECUTION.value,
                                                {'data': raw_data, 'timestamp': datetime.now()})

            else:
                logger.warning(f"⚠️ 알 수 없는 TR_ID: {tr_id}")

        except Exception as e:
            logger.error(f"실시간 데이터 처리 오류: {e}")
            self.stats['errors'] += 1

    async def handle_system_message(self, data: str):
        """시스템 메시지 처리"""
        try:
            # 이벤트 루프 안전성 미리 확인
            try:
                current_loop = asyncio.get_running_loop()
                if current_loop.is_closed():
                    logger.debug("시스템 메시지 처리 - 이벤트 루프가 닫혀있음, 건너뜀")
                    return
            except RuntimeError:
                logger.debug("시스템 메시지 처리 - 실행 중인 이벤트 루프가 없음, 건너뜀")
                return

            json_data = json.loads(data)
            tr_id = json_data.get('header', {}).get('tr_id', '')

            if tr_id == "PINGPONG":
                # PINGPONG 처리
                logger.debug(f"### RECV [PINGPONG]")
                self.stats['ping_pong_count'] = self.stats.get('ping_pong_count', 0) + 1
                self.stats['last_ping_pong_time'] = datetime.now()
                return 'PINGPONG', data
            else:
                body = json_data.get('body', {})
                rt_cd = body.get('rt_cd', '')
                msg = body.get('msg1', '')

                if rt_cd == '0':  # 성공
                    logger.debug(f"시스템 메시지: {msg}")

                    # 체결통보 암호화 키 저장
                    output = body.get('output', {})
                    if 'KEY' in output and 'IV' in output:
                        self.data_parser.set_encryption_keys(output['KEY'], output['IV'])
                        logger.info("✅ 체결통보 암호화 키 설정 완료")
                else:
                    logger.warning(f"시스템 메시지 오류: {rt_cd} - {msg}")

        except Exception as e:
            logger.error(f"시스템 메시지 처리 오류: {e}")

    async def process_message(self, message: str):
        """메시지 분류 및 처리"""
        try:
            self.stats['messages_received'] += 1
            self.stats['last_message_time'] = datetime.now()

            if message.startswith('{'):
                # JSON 형태 - 시스템 메시지
                return await self.handle_system_message(message)
            else:
                # 파이프 구분자 - 실시간 데이터
                await self.handle_realtime_data(message)
                return None

        except Exception as e:
            logger.error(f"메시지 처리 오류: {e}")
            self.stats['errors'] += 1
            return None

    async def _execute_callbacks(self, data_type: str, data: Dict):
        """콜백 함수들 실행"""
        try:
            # 글로벌 콜백 실행
            global_callbacks = self.subscription_manager.get_global_callbacks(data_type)
            for callback in global_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(data_type, data)
                    else:
                        callback(data_type, data)
                except Exception as e:
                    logger.error(f"글로벌 콜백 실행 오류 ({data_type}): {e}")

            # 종목별 콜백 실행 (stock_code가 있는 경우)
            stock_code = data.get('stock_code')
            if stock_code:
                stock_callbacks = self.subscription_manager.get_callbacks_for_stock(stock_code)
                for callback in stock_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(data_type, stock_code, data)
                        else:
                            callback(data_type, stock_code, data)
                    except Exception as e:
                        logger.error(f"종목별 콜백 실행 오류 ({stock_code}): {e}")

        except Exception as e:
            logger.error(f"콜백 실행 오류: {e}")

    def get_stats(self) -> Dict:
        """메시지 처리 통계 반환"""
        return self.stats.copy()
