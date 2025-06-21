#!/usr/bin/env python3
"""
KIS 웹소켓 데이터 파싱 전담 클래스
"""
from typing import Dict, Optional
from datetime import datetime
from utils.logger import setup_logger

# AES 복호화 (체결통보용)
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    from base64 import b64decode
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

logger = setup_logger(__name__)


class KISWebSocketDataParser:
    """KIS 웹소켓 데이터 파싱 전담 클래스"""

    def __init__(self):
        # 체결통보용 복호화 키
        self.aes_key: Optional[str] = None
        self.aes_iv: Optional[str] = None

        # 통계
        self.stats = {
            'data_processed': 0,
            'errors': 0
        }

    def _safe_int(self, value: str) -> int:
        """안전한 정수 변환"""
        if not value or value.strip() == '':
            return 0
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return 0

    def _safe_float(self, value: str) -> float:
        """안전한 실수 변환"""
        if not value or value.strip() == '':
            return 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    def set_encryption_keys(self, aes_key: str, aes_iv: str):
        """체결통보 암호화 키 설정"""
        self.aes_key = aes_key
        self.aes_iv = aes_iv
        logger.info("체결통보 암호화 키 설정 완료")

    def parse_contract_data(self, data: str) -> Dict:
        """실시간 체결 데이터 파싱 (H0STCNT0)"""
        try:
            # 다중 데이터 건수 처리
            all_parts = data.split('^')
            field_count_per_record = 46
            
            if len(all_parts) < field_count_per_record:
                logger.warning(f"체결 데이터 필드 부족: {len(all_parts)}개 (최소 {field_count_per_record}개 필요)")
                return {}
            
            # 가장 최근 데이터 사용
            total_records = len(all_parts) // field_count_per_record
            start_idx = (total_records - 1) * field_count_per_record
            parts = all_parts[start_idx:start_idx + field_count_per_record]
            
            # 핵심 데이터 파싱
            parsed_data = {
                # 기본 정보
                'stock_code': parts[0],
                'contract_time': parts[1],
                'current_price': self._safe_int(parts[2]),
                
                # 전일 대비
                'change_sign': parts[3],
                'change_amount': self._safe_int(parts[4]),
                'change_rate': self._safe_float(parts[5]),
                
                # 가격 정보
                'weighted_avg_price': self._safe_int(parts[6]),
                'open_price': self._safe_int(parts[7]),
                'high_price': self._safe_int(parts[8]),
                'low_price': self._safe_int(parts[9]),
                
                # 호가 정보
                'ask_price1': self._safe_int(parts[10]),
                'bid_price1': self._safe_int(parts[11]),
                
                # 거래량 정보
                'contract_volume': self._safe_int(parts[12]),
                'acc_volume': self._safe_int(parts[13]),
                'acc_trade_amount': self._safe_int(parts[14]),
                
                # 체결 건수
                'sell_contract_count': self._safe_int(parts[15]),
                'buy_contract_count': self._safe_int(parts[16]),
                'net_buy_contract_count': self._safe_int(parts[17]),
                
                # 체결강도 및 수량
                'contract_strength': self._safe_float(parts[18]),
                'total_sell_qty': self._safe_int(parts[19]),
                'total_buy_qty': self._safe_int(parts[20]),
                
                # 체결구분 및 비율
                'contract_type': parts[21],
                'buy_ratio': self._safe_float(parts[22]),
                'volume_change_rate': self._safe_float(parts[23]),
                
                # 시가 관련
                'open_time': parts[24],
                'open_vs_current_sign': parts[25],
                'open_vs_current': self._safe_int(parts[26]),
                
                # 고가 관련
                'high_time': parts[27],
                'high_vs_current_sign': parts[28],
                'high_vs_current': self._safe_int(parts[29]),
                
                # 저가 관련
                'low_time': parts[30],
                'low_vs_current_sign': parts[31],
                'low_vs_current': self._safe_int(parts[32]),
                
                # 영업일자 및 장운영
                'business_date': parts[33],
                'market_operation_code': parts[34],
                'trading_halt': parts[35],
                
                # 호가 잔량
                'ask_qty1': self._safe_int(parts[36]),
                'bid_qty1': self._safe_int(parts[37]),
                'total_ask_qty': self._safe_int(parts[38]),
                'total_bid_qty': self._safe_int(parts[39]),
                
                # 거래량 회전율
                'volume_turnover_rate': self._safe_float(parts[40]),
                'prev_same_time_volume': self._safe_int(parts[41]),
                'prev_same_time_volume_rate': self._safe_float(parts[42]),
                
                # 시간구분 및 VI
                'hour_cls_code': parts[43],
                'market_closing_code': parts[44],
                'vi_standard_price': self._safe_int(parts[45]) if len(parts) > 45 else 0,
                
                # 메타 정보
                'timestamp': datetime.now(),
                'source': 'websocket',
                'type': 'contract',
                'total_data_count': total_records,
                
                # 거래 참고 지표
                'is_market_time': parts[43] == '0',
                'is_trading_halt': parts[35] == 'Y',
                'market_pressure': 'BUY' if parts[21] == '1' else 'SELL' if parts[21] == '5' else 'NEUTRAL',
                'price_momentum': 'UP' if parts[3] in ['1', '2'] else 'DOWN' if parts[3] in ['4', '5'] else 'FLAT',
                'volume_activity': 'HIGH' if self._safe_float(parts[23]) > 150.0 else 'LOW' if self._safe_float(parts[23]) < 50.0 else 'NORMAL'
            }
            
            self.stats['data_processed'] += 1
            
            logger.debug(f"체결 파싱 성공: {parsed_data['stock_code']} "
                        f"{parsed_data['current_price']:,}원 "
                        f"({parsed_data['change_sign']}{parsed_data['change_amount']:,}원/{parsed_data['change_rate']:.2f}%) "
                        f"거래량:{parsed_data['contract_volume']:,}주")
            
            return parsed_data

        except Exception as e:
            self.stats['errors'] += 1
            logger.error(f"체결 데이터 파싱 오류: {e}")
            logger.error(f"데이터 길이: {len(data.split('^')) if data else 0}")
            logger.debug(f"파싱 실패 데이터: {data[:200]}..." if data and len(data) > 200 else data)
            return {}

    def parse_bid_ask_data(self, data: str) -> Dict:
        """실시간 호가 데이터 파싱 (H0STASP0)"""
        try:
            parts = data.split('^')
            
            if len(parts) < 57:
                logger.warning(f"호가 데이터 필드 부족: {len(parts)}개 (필요: 57개)")
                return {}

            # 호가 데이터 파싱
            parsed_data = {
                # 기본 정보
                'stock_code': parts[0],
                'business_hour': parts[1],
                'hour_cls_code': parts[2],
                
                # 매도호가 1~10
                'ask_price1': self._safe_int(parts[3]),
                'ask_price2': self._safe_int(parts[4]),
                'ask_price3': self._safe_int(parts[5]),
                'ask_price4': self._safe_int(parts[6]),
                'ask_price5': self._safe_int(parts[7]),
                'ask_price6': self._safe_int(parts[8]),
                'ask_price7': self._safe_int(parts[9]),
                'ask_price8': self._safe_int(parts[10]),
                'ask_price9': self._safe_int(parts[11]),
                'ask_price10': self._safe_int(parts[12]),
                
                # 매수호가 1~10
                'bid_price1': self._safe_int(parts[13]),
                'bid_price2': self._safe_int(parts[14]),
                'bid_price3': self._safe_int(parts[15]),
                'bid_price4': self._safe_int(parts[16]),
                'bid_price5': self._safe_int(parts[17]),
                'bid_price6': self._safe_int(parts[18]),
                'bid_price7': self._safe_int(parts[19]),
                'bid_price8': self._safe_int(parts[20]),
                'bid_price9': self._safe_int(parts[21]),
                'bid_price10': self._safe_int(parts[22]),
                
                # 매도호가 잔량 1~10
                'ask_qty1': self._safe_int(parts[23]),
                'ask_qty2': self._safe_int(parts[24]),
                'ask_qty3': self._safe_int(parts[25]),
                'ask_qty4': self._safe_int(parts[26]),
                'ask_qty5': self._safe_int(parts[27]),
                'ask_qty6': self._safe_int(parts[28]),
                'ask_qty7': self._safe_int(parts[29]),
                'ask_qty8': self._safe_int(parts[30]),
                'ask_qty9': self._safe_int(parts[31]),
                'ask_qty10': self._safe_int(parts[32]),
                
                # 매수호가 잔량 1~10
                'bid_qty1': self._safe_int(parts[33]),
                'bid_qty2': self._safe_int(parts[34]),
                'bid_qty3': self._safe_int(parts[35]),
                'bid_qty4': self._safe_int(parts[36]),
                'bid_qty5': self._safe_int(parts[37]),
                'bid_qty6': self._safe_int(parts[38]),
                'bid_qty7': self._safe_int(parts[39]),
                'bid_qty8': self._safe_int(parts[40]),
                'bid_qty9': self._safe_int(parts[41]),
                'bid_qty10': self._safe_int(parts[42]),
                
                # 총 잔량 및 시간외 잔량
                'total_ask_qty': self._safe_int(parts[43]),
                'total_bid_qty': self._safe_int(parts[44]),
                'overtime_total_ask_qty': self._safe_int(parts[45]),
                'overtime_total_bid_qty': self._safe_int(parts[46]),
                
                # 예상 체결 정보
                'expected_price': self._safe_int(parts[47]),
                'expected_qty': self._safe_int(parts[48]),
                'expected_volume': self._safe_int(parts[49]),
                'expected_change': self._safe_int(parts[50]),
                'expected_change_sign': parts[51],
                'expected_change_rate': self._safe_float(parts[52]),
                
                # 누적 거래량 및 증감
                'acc_volume': self._safe_int(parts[53]),
                'total_ask_change': self._safe_int(parts[54]),
                'total_bid_change': self._safe_int(parts[55]),
                'overtime_ask_change': self._safe_int(parts[56]) if len(parts) > 56 else 0,
                'overtime_bid_change': self._safe_int(parts[57]) if len(parts) > 57 else 0,
                
                # 메타 정보
                'timestamp': datetime.now(),
                'source': 'websocket',
                'type': 'bid_ask',
                'is_market_time': parts[2] == '0',
                
                # 거래 참고 지표
                'bid_ask_spread': (self._safe_int(parts[3]) - self._safe_int(parts[13])) if parts[3] and parts[13] else 0,
                'bid_ask_ratio': (self._safe_int(parts[44]) / max(self._safe_int(parts[43]), 1)) if parts[43] and parts[44] else 0.0,
                'market_pressure': 'BUY' if (self._safe_int(parts[44]) > self._safe_int(parts[43])) else 'SELL' if parts[43] and parts[44] else 'NEUTRAL'
            }

            self.stats['data_processed'] += 1
            
            logger.debug(f"호가 파싱 성공: {parsed_data['stock_code']} "
                        f"매수1호가={parsed_data['bid_price1']:,}원({parsed_data['bid_qty1']:,}주) "
                        f"매도1호가={parsed_data['ask_price1']:,}원({parsed_data['ask_qty1']:,}주)")
            
            return parsed_data

        except Exception as e:
            self.stats['errors'] += 1
            logger.error(f"호가 데이터 파싱 오류: {e}")
            logger.error(f"데이터 길이: {len(data.split('^')) if data else 0}")
            logger.debug(f"파싱 실패 데이터: {data[:200]}..." if data and len(data) > 200 else data)
            return {}

    def decrypt_notice_data(self, encrypted_data: str) -> str:
        """체결통보 데이터 복호화"""
        if not CRYPTO_AVAILABLE or not self.aes_key or not self.aes_iv:
            return ""

        try:
            cipher = AES.new(self.aes_key.encode('utf-8'), AES.MODE_CBC, self.aes_iv.encode('utf-8'))
            decrypted = unpad(cipher.decrypt(b64decode(encrypted_data)), AES.block_size)
            return decrypted.decode('utf-8')

        except Exception as e:
            logger.error(f"체결통보 복호화 오류: {e}")
            return ""

    def get_stats(self) -> Dict:
        """파싱 통계 반환"""
        return self.stats.copy()
