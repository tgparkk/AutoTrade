#!/usr/bin/env python3
"""
KIS 웹소켓 데이터 파싱 전담 클래스
"""
from typing import Dict, Optional
from utils.korean_time import now_kst
from utils.logger import setup_logger
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from base64 import b64decode

logger = setup_logger(__name__)

# Crypto 라이브러리가 필수이므로 항상 True 로 설정
CRYPTO_AVAILABLE = True

class KISWebSocketDataParser:
    """KIS 웹소켓 데이터 파싱 전담 클래스"""

    def __init__(self):
        # 체결통보용 복호화 키
        # 원본 문자열 키/IV (디버깅용)
        self._aes_key_str: Optional[str] = None
        self._aes_iv_str: Optional[str] = None

        # 실제 AES 복호화에 사용할 바이트 배열
        self._aes_key_bytes: Optional[bytes] = None
        self._aes_iv_bytes: Optional[bytes] = None

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
        """
        KIS 시스템 메시지에서 전달되는 KEY/IV 값은 Base64(또는 ASCII HEX)
        형태일 수 있으므로, 실제 AES 복호화에 사용할 수 있도록 안전하게
        디코딩한 뒤 바이트 배열로 저장한다.
        """

        import base64, binascii

        def _to_bytes(value: str) -> Optional[bytes]:
            """입력 문자열을 Base64 → HEX → UTF-8 순서로 디코딩 시도"""
            if not value:
                return None

            # 1) ASCII 그대로 사용 (길이가 16/24/32 글자면 이미 충분)
            if len(value) in (16, 24, 32):
                try:
                    return value.encode('utf-8')
                except Exception:
                    pass

            # 2) HEX 시도 (32/48/64 글자)
            try:
                if all(c in '0123456789abcdefABCDEF' for c in value) and len(value) % 2 == 0:
                    decoded = binascii.unhexlify(value)
                    if len(decoded) in (16, 24, 32):
                        return decoded
            except Exception:
                pass

            # 3) Base64 시도 (보통 24/32/44 글자)
            try:
                decoded = base64.b64decode(value)
                if len(decoded) in (16, 24, 32):
                    return decoded
            except Exception:
                pass

            # 4) 그래도 안 되면 UTF-8 bytes 그대로 사용 (마지막 수단)
            try:
                utf8_bytes = value.encode("utf-8")
                if len(utf8_bytes) in (16, 24, 32):
                    return utf8_bytes
            except Exception:
                pass

            logger.warning(f"⚠️ AES 키/IV 길이가 16/24/32바이트가 아님 - 원본 사용: {len(value)} bytes")
            return value.encode("utf-8")[:32]

        # 원본 보관
        self._aes_key_str = aes_key
        self._aes_iv_str = aes_iv

        # 안전 디코딩
        self._aes_key_bytes = _to_bytes(aes_key)
        self._aes_iv_bytes = _to_bytes(aes_iv)

        if self._aes_key_bytes and self._aes_iv_bytes:
            logger.info("✅ 체결통보 암호화 키/IV 디코딩 및 설정 완료")
        else:
            logger.error("❌ 체결통보 암호화 키/IV 설정 실패 - 복호화 불가")

    def parse_contract_data(self, data: str) -> Dict:
        """실시간 체결 데이터 파싱 (H0STCNT0) - KIS 공식 문서 기준"""
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
            
            # 🔥 KIS 공식 문서 순서에 맞는 필드 매핑 (wikidocs 참조)
            # 순서: 유가증권단축종목코드|주식체결시간|주식현재가|전일대비부호|전일대비|전일대비율|...
            stock_code = parts[0]  # 유가증권단축종목코드  
            contract_time = parts[1]  # 주식체결시간
            current_price = self._safe_int(parts[2])  # 주식현재가
            change_sign = parts[3]  # 전일대비부호 (1:상한, 2:상승, 3:보합, 4:하한, 5:하락)
            change_amount = self._safe_int(parts[4])  # 전일대비
            change_rate = self._safe_float(parts[5])  # 전일대비율
            weighted_avg_price = self._safe_float(parts[6])  # 가중평균주식가격
            open_price = self._safe_int(parts[7])  # 주식시가
            high_price = self._safe_int(parts[8])  # 주식최고가
            low_price = self._safe_int(parts[9])  # 주식최저가
            ask_price1 = self._safe_int(parts[10])  # 매도호가1
            bid_price1 = self._safe_int(parts[11])  # 매수호가1
            contract_volume = self._safe_int(parts[12])  # 체결거래량
            acc_volume = self._safe_int(parts[13])  # 누적거래량
            acc_trade_amount = self._safe_int(parts[14])  # 누적거래대금
            sell_contract_count = self._safe_int(parts[15])  # 매도체결건수
            buy_contract_count = self._safe_int(parts[16])  # 매수체결건수
            net_buy_contract_count = self._safe_int(parts[17])  # 순매수체결건수
            contract_strength = self._safe_float(parts[18])  # 체결강도
            total_ask_qty = self._safe_int(parts[19])  # 총매도수량
            total_bid_qty = self._safe_int(parts[20])  # 총매수수량
            contract_type = parts[21]  # 체결구분 (1:매수, 3:장전, 5:매도)
            buy_ratio = self._safe_float(parts[22])  # 매수비율
            prev_volume_ratio = self._safe_float(parts[23])  # 전일거래량대비등락율
            
            # 🆕 시간 관련 정보들
            open_time = parts[24]  # 시가시간
            open_vs_current_sign = parts[25]  # 시가대비구분
            open_vs_current = self._safe_int(parts[26])  # 시가대비
            high_time = parts[27]  # 최고가시간
            high_vs_current_sign = parts[28]  # 고가대비구분  
            high_vs_current = self._safe_int(parts[29])  # 고가대비
            low_time = parts[30]  # 최저가시간
            low_vs_current_sign = parts[31]  # 저가대비구분
            low_vs_current = self._safe_int(parts[32])  # 저가대비
            business_date = parts[33]  # 영업일자
            market_operation_code = parts[34]  # 신장운영구분코드
            trading_halt = parts[35] == 'Y'  # 거래정지여부 (Y/N)
            ask_qty1 = self._safe_int(parts[36])  # 매도호가잔량
            bid_qty1 = self._safe_int(parts[37])  # 매수호가잔량
            total_ask_qty_alt = self._safe_int(parts[38])  # 총매도호가잔량
            total_bid_qty_alt = self._safe_int(parts[39])  # 총매수호가잔량
            volume_turnover_rate = self._safe_float(parts[40])  # 거래량회전율
            prev_same_time_volume = self._safe_int(parts[41])  # 전일동시간누적거래량
            prev_same_time_volume_rate = self._safe_float(parts[42])  # 전일동시간누적거래량비율
            hour_cls_code = parts[43]  # 시간구분코드 (0:장중, 기타:장외)
            market_closing_code = parts[44] if len(parts) > 44 else ''  # 임의종료구분코드
            vi_standard_price = self._safe_int(parts[45]) if len(parts) > 45 else 0  # 정적VI발동기준가
            
            # 🆕 시장압력 계산 (체결구분 + 매수비율 기반)
            if contract_type == '1':  # 매수 체결
                market_pressure = 'BUY'
            elif contract_type == '5':  # 매도 체결
                market_pressure = 'SELL'
            else:  # 장전 또는 기타
                market_pressure = 'NEUTRAL'
            
            # 🆕 매수비율 기반 압력 보정
            if buy_ratio > 60.0:
                market_pressure = 'BUY'
            elif buy_ratio < 40.0:
                market_pressure = 'SELL'
            
            parsed_data = {
                # 🔥 StockManager 호환 필드명 사용 (정확한 순서)
                'stock_code': stock_code,
                'current_price': current_price,
                'acc_volume': acc_volume,  # 누적 거래량
                'contract_volume': contract_volume,  # 체결 거래량
                
                # 기본 가격 정보 (정확한 순서)
                'open_price': open_price,
                'high_price': high_price,
                'low_price': low_price,
                
                # 🆕 KIS 공식 문서 기반 고급 지표들
                'contract_strength': contract_strength,
                'buy_ratio': buy_ratio,
                'market_pressure': market_pressure,
                'vi_standard_price': vi_standard_price,
                'trading_halt': trading_halt,
                
                # 전일 대비 정보 (정확한 순서)
                'change_sign': change_sign,
                'change_amount': change_amount,
                'change_rate': change_rate,
                
                # 체결 정보 (정확한 순서)
                'weighted_avg_price': weighted_avg_price,
                'sell_contract_count': sell_contract_count,
                'buy_contract_count': buy_contract_count,
                'net_buy_contract_count': net_buy_contract_count,
                
                # 호가 정보 (정확한 순서)
                'ask_price1': ask_price1,
                'bid_price1': bid_price1,
                'ask_qty1': ask_qty1,
                'bid_qty1': bid_qty1,
                
                # 호가 잔량 정보 (정확한 순서) 
                'total_ask_qty': total_ask_qty_alt,  # 총매도호가잔량 사용
                'total_bid_qty': total_bid_qty_alt,  # 총매수호가잔량 사용
                
                # 거래량 관련 (정확한 순서)
                'volume_turnover_rate': volume_turnover_rate,
                'prev_same_time_volume': prev_same_time_volume,
                'prev_same_time_volume_rate': prev_same_time_volume_rate,
                'prev_volume_ratio': prev_volume_ratio,
                
                # 시간 구분 정보 (정확한 순서)
                'hour_cls_code': hour_cls_code,
                'market_operation_code': market_operation_code,
                'market_closing_code': market_closing_code,
                
                # 기존 필드들 (하위 호환성)
                'contract_time': contract_time,
                'acc_trade_amount': acc_trade_amount,
                'business_date': business_date,
                
                # 시가/고가/저가 대비 정보 (정확한 순서)
                'open_time': open_time,
                'open_vs_current_sign': open_vs_current_sign,
                'open_vs_current': open_vs_current,
                'high_time': high_time,
                'high_vs_current_sign': high_vs_current_sign,
                'high_vs_current': high_vs_current,
                'low_time': low_time,
                'low_vs_current_sign': low_vs_current_sign,
                'low_vs_current': low_vs_current,
                
                # 메타 정보
                'timestamp': now_kst(),
                'source': 'websocket',
                'type': 'contract',
                'total_data_count': total_records,
                
                # 🆕 거래 참고 지표 (계산된 값들)
                'is_market_time': hour_cls_code == '0',
                'is_trading_halt': trading_halt,
                'price_momentum': 'UP' if change_sign in ['1', '2'] else 'DOWN' if change_sign in ['4', '5'] else 'FLAT',
                'volume_activity': 'HIGH' if prev_volume_ratio > 150.0 else 'LOW' if prev_volume_ratio < 50.0 else 'NORMAL',
                'contract_imbalance': (buy_contract_count - sell_contract_count) / max(buy_contract_count + sell_contract_count, 1),
                'strength_level': 'STRONG' if contract_strength > 120 else 'WEAK' if contract_strength < 80 else 'NORMAL'
            }
            
            self.stats['data_processed'] += 1
            
            logger.debug(f"체결 파싱 성공: {parsed_data['stock_code']} "
                        f"{parsed_data['current_price']:,}원 "
                        f"({parsed_data['change_sign']}{parsed_data['change_amount']:,}원/{parsed_data['change_rate']:.2f}%) "
                        f"거래량:{parsed_data['contract_volume']:,}주 "
                        f"체결강도:{parsed_data['contract_strength']:.1f} "
                        f"매수비율:{parsed_data['buy_ratio']:.1f}% "
                        f"시장압력:{parsed_data['market_pressure']}")
            
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
                'timestamp': now_kst(),
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
        if not CRYPTO_AVAILABLE or not self._aes_key_bytes or not self._aes_iv_bytes:
            return ""

        try:
            # 웹소켓에서 전달된 데이터는 Base64 인코딩이며 종종 '=' 패딩이 제거돼 온다.
            from base64 import b64decode

            def _b64decode_padded(data: str) -> bytes:
                missing = (-len(data)) % 4
                if missing:
                    data += "=" * missing
                return b64decode(data)

            cipher = AES.new(self._aes_key_bytes, AES.MODE_CBC, self._aes_iv_bytes)
            decrypted = unpad(cipher.decrypt(_b64decode_padded(encrypted_data)), AES.block_size)
            return decrypted.decode('utf-8', errors='ignore')

        except Exception as e:
            logger.error(f"체결통보 복호화 오류: {e}")
            return ""

    def get_stats(self) -> Dict:
        """파싱 통계 반환"""
        return self.stats.copy()
