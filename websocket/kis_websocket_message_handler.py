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
from utils.korean_time import now_kst

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
                    # TR_ID 기반 콜백 실행 (StockManager 연동용)
                    await self._execute_tr_id_callbacks(tr_id, parsed_data)

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
                    # TR_ID 기반 콜백 실행 (StockManager 연동용) 
                    await self._execute_tr_id_callbacks(tr_id, parsed_data)

            elif tr_id == KIS_WSReq.NOTICE.value:
                # 체결통보 수신 (계좌 체결/접수)
                logger.info(f"📢 체결통보 수신: {tr_id}")

                decrypted_data: Optional[str] = None
                if encryption_flag == '1':
                    # 암호화된 경우 복호화 시도
                    decrypted_data = self.data_parser.decrypt_notice_data(raw_data)
                    if not decrypted_data:
                        logger.warning("❌ 체결통보 복호화 실패 - KEY/IV 설정 전일 수 있음, 건너뜀")
                        return  # 복호화 실패 시 콜백 실행하지 않음
                else:
                    # 암호화되지 않은 경우 그대로 사용
                    decrypted_data = raw_data

                # 콜백 실행 (StockManager)
                execution_data = {'data': decrypted_data, 'timestamp': now_kst()}
                await self._execute_tr_id_callbacks(tr_id, execution_data)

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
                # 🔥 KIS 공식 PINGPONG 처리 방식
                logger.debug(f"🏓 RECV [PINGPONG]: {data[:50]}...")
                self.stats['ping_pong_count'] = self.stats.get('ping_pong_count', 0) + 1
                self.stats['last_ping_pong_time'] = now_kst()
                

                
                # KIS 공식: 받은 데이터를 그대로 websocket.pong()에 전달
                return 'PINGPONG', data
            else:
                body = json_data.get('body', {})
                rt_cd = body.get('rt_cd', '')
                msg = body.get('msg1', '')

                if rt_cd == '0':  # 성공
                    logger.debug(f"시스템 메시지: {msg}")

                    # output 정보는 body 안이나 최상위에 위치할 수 있다.
                    output = body.get('output') if isinstance(body, dict) else None
                    if output is None and 'output' in json_data:
                        output = json_data['output']
                    if output is None:
                        output = {}

                    # 일부 계정에서는 key/iv가 소문자 또는 다른 필드명으로 올 수 있으므로
                    # 대소문자를 무시하고 검색한다.
                    def _find_key(d: dict, *candidates):
                        for c in candidates:
                            if c in d:
                                return d[c]
                        return None

                    key_val = _find_key(output, 'KEY', 'key', 'aes_key', 'AES_KEY')
                    iv_val = _find_key(output, 'IV', 'iv', 'aes_iv', 'AES_IV')

                    if key_val and iv_val:
                        self.data_parser.set_encryption_keys(key_val, iv_val)
                        logger.info("✅ 체결통보 암호화 키 설정 완료")
                        logger.debug(f"SYSTEM MSG RAW: {json_data}")
                    else:
                        logger.warning(f"⚠️ KEY/IV 필드 미발견 - output keys: {list(output.keys())}")
                        # 디버깅용 전체 메시지 로그 (민감 정보 주의)
                        logger.warning(f"SYSTEM MSG RAW: {json_data}")
                else:
                    logger.warning(f"시스템 메시지 오류: {rt_cd} - {msg}")

        except Exception as e:
            logger.error(f"시스템 메시지 처리 오류: {e}")
            self.stats['errors'] += 1

    async def process_message(self, message: str):
        """메시지 분류 및 처리"""
        try:
            self.stats['messages_received'] += 1
            self.stats['last_message_time'] = now_kst()

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

    async def _execute_tr_id_callbacks(self, tr_id: str, data: Dict):
        """TR_ID 기반 콜백 함수들 실행 (StockManager 연동용)"""
        try:
            tr_id_callbacks = self.subscription_manager.get_tr_id_callbacks(tr_id)
            stock_code = data.get('stock_code', '')
            
            for callback in tr_id_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        if tr_id == 'H0STCNI0':  # 체결통보는 data_type, data 형태
                            await callback('execution_notice', data)
                        else:  # 체결가, 호가는 data_type, stock_code, data 형태
                            data_type = 'stock_price' if tr_id == 'H0STCNT0' else 'stock_orderbook'
                            await callback(data_type, stock_code, data)
                    else:
                        if tr_id == 'H0STCNI0':  # 체결통보는 data_type, data 형태
                            callback('execution_notice', data)
                        else:  # 체결가, 호가는 data_type, stock_code, data 형태
                            data_type = 'stock_price' if tr_id == 'H0STCNT0' else 'stock_orderbook'
                            callback(data_type, stock_code, data)
                except Exception as e:
                    logger.error(f"TR_ID 콜백 실행 오류 ({tr_id}): {e}")

        except Exception as e:
            logger.error(f"TR_ID 콜백 실행 오류: {e}")

    def get_stats(self) -> Dict:
        """메시지 처리 통계 반환"""
        return self.stats.copy()
