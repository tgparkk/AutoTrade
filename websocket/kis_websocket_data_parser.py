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

    def set_encryption_keys(self, aes_key: str, aes_iv: str):
        """체결통보 암호화 키 설정"""
        self.aes_key = aes_key
        self.aes_iv = aes_iv
        logger.info("체결통보 암호화 키 설정 완료")

    def parse_contract_data(self, data: str) -> Dict:
        """실시간 체결 데이터 파싱 - 🎯 KIS 공식 문서 H0STCNT0 기준"""
        
        def safe_int(value: str) -> int:
            """🔧 안전한 정수 변환 - 소수점 포함 문자열 처리"""
            if not value or value.strip() == '':
                return 0
            try:
                # 소수점이 포함된 경우 float로 변환 후 int로 변환
                return int(float(value))
            except (ValueError, TypeError):
                return 0
                
        def safe_float(value: str) -> float:
            """🔧 안전한 실수 변환"""
            if not value or value.strip() == '':
                return 0.0
            try:
                return float(value)
            except (ValueError, TypeError):
                return 0.0
        
        try:
            # 🔍 KIS 공식: 다중 데이터 건수 처리 가능
            # 예시: 005930^123929^73100^5^...^005930^123930^73200^2^... (2건의 체결 데이터)
            
            # 전체 데이터를 '^'로 분리
            all_parts = data.split('^')
            
            # 🎯 KIS 공식: 정확히 46개 필드가 1건의 체결 데이터
            field_count_per_record = 46
            
            if len(all_parts) < field_count_per_record:
                logger.warning(f"⚠️ 체결 데이터 필드 부족: {len(all_parts)}개 (최소 {field_count_per_record}개 필요)")
                return {}
            
            # 🔢 데이터 건수 계산
            total_records = len(all_parts) // field_count_per_record
            logger.debug(f"📊 체결 데이터 건수: {total_records}건")
            
            # 🎯 가장 최근 데이터(마지막 레코드) 사용
            start_idx = (total_records - 1) * field_count_per_record
            parts = all_parts[start_idx:start_idx + field_count_per_record]
            
            # 🎯 KIS 공식 문서 순서대로 정확한 필드 매핑 (46개 필드)
            parsed_data = {
                # 기본 정보 (0~2)
                'stock_code': parts[0],                                    # MKSC_SHRN_ISCD: 유가증권 단축 종목코드
                'contract_time': parts[1],                                 # STCK_CNTG_HOUR: 주식 체결 시간
                'current_price': safe_int(parts[2]),                       # STCK_PRPR: 주식 현재가 (체결가격) - 🔧 안전한 변환
                
                # 전일 대비 정보 (3~5)
                'change_sign': parts[3],                                   # PRDY_VRSS_SIGN: 전일 대비 부호
                'change_amount': safe_int(parts[4]),                       # PRDY_VRSS: 전일 대비 - 🔧 안전한 변환
                'change_rate': safe_float(parts[5]),                       # PRDY_CTRT: 전일 대비율 - 🔧 안전한 변환
                
                # 가격 정보 (6~9)
                'weighted_avg_price': safe_int(parts[6]),                  # WGHN_AVRG_STCK_PRC: 가중 평균 주식 가격 - 🔧 안전한 변환
                'open_price': safe_int(parts[7]),                          # STCK_OPRC: 주식 시가 - 🔧 안전한 변환
                'high_price': safe_int(parts[8]),                          # STCK_HGPR: 주식 최고가 - 🔧 안전한 변환
                'low_price': safe_int(parts[9]),                           # STCK_LWPR: 주식 최저가 - 🔧 안전한 변환
                
                # 호가 정보 (10~11)
                'ask_price1': safe_int(parts[10]),                         # ASKP1: 매도호가1 - 🔧 안전한 변환
                'bid_price1': safe_int(parts[11]),                         # BIDP1: 매수호가1 - 🔧 안전한 변환
                
                # 거래량 정보 (12~14)
                'contract_volume': safe_int(parts[12]),                    # CNTG_VOL: 체결 거래량 - 🔧 안전한 변환
                'acc_volume': safe_int(parts[13]),                         # ACML_VOL: 누적 거래량 - 🔧 안전한 변환
                'acc_trade_amount': safe_int(parts[14]),                   # ACML_TR_PBMN: 누적 거래 대금 - 🔧 안전한 변환
                
                # 체결 건수 정보 (15~17)
                'sell_contract_count': safe_int(parts[15]),                # SELN_CNTG_CSNU: 매도 체결 건수 - 🔧 안전한 변환
                'buy_contract_count': safe_int(parts[16]),                 # SHNU_CNTG_CSNU: 매수 체결 건수 - 🔧 안전한 변환
                'net_buy_contract_count': safe_int(parts[17]),             # NTBY_CNTG_CSNU: 순매수 체결 건수 - 🔧 안전한 변환
                
                # 체결강도 및 수량 정보 (18~20)
                'contract_strength': safe_float(parts[18]),                # CTTR: 체결강도 - 🔧 안전한 변환
                'total_sell_qty': safe_int(parts[19]),                     # SELN_CNTG_SMTN: 총 매도 수량 - 🔧 안전한 변환
                'total_buy_qty': safe_int(parts[20]),                      # SHNU_CNTG_SMTN: 총 매수 수량 - 🔧 안전한 변환
                
                # 체결구분 및 비율 (21~23)
                'contract_type': parts[21],                                # CCLD_DVSN: 체결구분 (1:매수+, 3:장전, 5:매도-)
                'buy_ratio': safe_float(parts[22]),                        # SHNU_RATE: 매수비율 - 🔧 안전한 변환
                'volume_change_rate': safe_float(parts[23]),               # PRDY_VOL_VRSS_ACML_VOL_RATE: 전일 거래량 대비 등락율 - 🔧 안전한 변환
                
                # 시가 관련 정보 (24~26)
                'open_time': parts[24],                                    # OPRC_HOUR: 시가 시간
                'open_vs_current_sign': parts[25],                         # OPRC_VRSS_PRPR_SIGN: 시가대비구분
                'open_vs_current': safe_int(parts[26]),                    # OPRC_VRSS_PRPR: 시가대비 - 🔧 안전한 변환
                
                # 고가 관련 정보 (27~29)
                'high_time': parts[27],                                    # HGPR_HOUR: 최고가 시간
                'high_vs_current_sign': parts[28],                         # HGPR_VRSS_PRPR_SIGN: 고가대비구분
                'high_vs_current': safe_int(parts[29]),                    # HGPR_VRSS_PRPR: 고가대비 - 🔧 안전한 변환
                
                # 저가 관련 정보 (30~32)
                'low_time': parts[30],                                     # LWPR_HOUR: 최저가 시간
                'low_vs_current_sign': parts[31],                          # LWPR_VRSS_PRPR_SIGN: 저가대비구분
                'low_vs_current': safe_int(parts[32]),                     # LWPR_VRSS_PRPR: 저가대비 - 🔧 안전한 변환
                
                # 영업일자 및 장운영 정보 (33~35)
                'business_date': parts[33],                                # BSOP_DATE: 영업 일자
                'market_operation_code': parts[34],                        # NEW_MKOP_CLS_CODE: 신 장운영 구분 코드
                'trading_halt': parts[35],                                 # TRHT_YN: 거래정지 여부 (Y:정지, N:정상)
                
                # 호가 잔량 정보 (36~39)
                'ask_qty1': safe_int(parts[36]),                           # ASKP_RSQN1: 매도호가 잔량1 - 🔧 안전한 변환
                'bid_qty1': safe_int(parts[37]),                           # BIDP_RSQN1: 매수호가 잔량1 - 🔧 안전한 변환
                'total_ask_qty': safe_int(parts[38]),                      # TOTAL_ASKP_RSQN: 총 매도호가 잔량 - 🔧 안전한 변환
                'total_bid_qty': safe_int(parts[39]),                      # TOTAL_BIDP_RSQN: 총 매수호가 잔량 - 🔧 안전한 변환
                
                # 거래량 회전율 및 전일 동시간 비교 (40~42)
                'volume_turnover_rate': safe_float(parts[40]),             # VOL_TNRT: 거래량 회전율 - 🔧 안전한 변환
                'prev_same_time_volume': safe_int(parts[41]),              # PRDY_SMNS_HOUR_ACML_VOL: 전일 동시간 누적 거래량 - 🔧 안전한 변환
                'prev_same_time_volume_rate': safe_float(parts[42]),       # PRDY_SMNS_HOUR_ACML_VOL_RATE: 전일 동시간 누적 거래량 비율 - 🔧 안전한 변환
                
                # 시간구분 및 VI 정보 (43~45)
                'hour_cls_code': parts[43],                                # HOUR_CLS_CODE: 시간 구분 코드
                'market_closing_code': parts[44],                          # MRKT_TRTM_CLS_CODE: 임의종료구분코드
                'vi_standard_price': safe_int(parts[45]) if len(parts) > 45 else 0, # VI_STND_PRC: 정적VI발동기준가 - 🔧 안전한 변환
                
                # 메타 정보
                'timestamp': datetime.now(),
                'source': 'websocket',
                'type': 'contract',
                'total_data_count': total_records,
                
                # 🎯 거래 참고용 주요 지표들
                'is_market_time': parts[43] == '0',  # 0: 장중, A: 장후예상, B: 장전예상
                'is_trading_halt': parts[35] == 'Y',
                'market_pressure': 'BUY' if parts[21] == '1' else 'SELL' if parts[21] == '5' else 'NEUTRAL',
                'price_momentum': 'UP' if parts[3] in ['1', '2'] else 'DOWN' if parts[3] in ['4', '5'] else 'FLAT',
                'volume_activity': 'HIGH' if safe_float(parts[23]) > 150.0 else 'LOW' if safe_float(parts[23]) < 50.0 else 'NORMAL'
            }
            
            # 통계 업데이트
            self.stats['data_processed'] += 1
            
            logger.debug(f"✅ 체결 파싱 성공: {parsed_data['stock_code']} "
                        f"{parsed_data['current_price']:,}원 "
                        f"({parsed_data['change_sign']}{parsed_data['change_amount']:,}원/{parsed_data['change_rate']:.2f}%) "
                        f"거래량:{parsed_data['contract_volume']:,}주")
            
            return parsed_data

        except Exception as e:
            self.stats['errors'] += 1
            logger.error(f"❌ 체결 데이터 파싱 오류: {e}")
            logger.error(f"데이터 길이: {len(data.split('^')) if data else 0}")
            logger.debug(f"🔍 파싱 실패 데이터: {data[:200]}..." if data and len(data) > 200 else data)
            return {}

    def parse_bid_ask_data(self, data: str) -> Dict:
        """실시간 호가 데이터 파싱 - 🎯 KIS 공식 문서 H0STASP0 기준"""
        
        def safe_int(value: str) -> int:
            """🔧 안전한 정수 변환 - 소수점 포함 문자열 처리"""
            if not value or value.strip() == '':
                return 0
            try:
                # 소수점이 포함된 경우 float로 변환 후 int로 변환
                return int(float(value))
            except (ValueError, TypeError):
                return 0
                
        def safe_float(value: str) -> float:
            """🔧 안전한 실수 변환"""
            if not value or value.strip() == '':
                return 0.0
            try:
                return float(value)
            except (ValueError, TypeError):
                return 0.0
        
        try:
            parts = data.split('^')
            
            # 🔍 KIS 공식: 정확히 57개 필드
            if len(parts) < 57:
                logger.warning(f"⚠️ 호가 데이터 필드 부족: {len(parts)}개 (필요: 57개)")
                return {}

            # 🎯 KIS 공식 문서 순서대로 정확한 필드 매핑
            parsed_data = {
                # 기본 정보
                'stock_code': parts[0],                      # MKSC_SHRN_ISCD: 유가증권 단축 종목코드
                'business_hour': parts[1],                   # BSOP_HOUR: 영업 시간
                'hour_cls_code': parts[2],                   # HOUR_CLS_CODE: 시간 구분 코드
                
                # 매도호가 1~10 (ASKP1~ASKP10) - 🔧 안전한 변환 적용
                'ask_price1': safe_int(parts[3]),
                'ask_price2': safe_int(parts[4]),
                'ask_price3': safe_int(parts[5]),
                'ask_price4': safe_int(parts[6]),
                'ask_price5': safe_int(parts[7]),
                'ask_price6': safe_int(parts[8]),
                'ask_price7': safe_int(parts[9]),
                'ask_price8': safe_int(parts[10]),
                'ask_price9': safe_int(parts[11]),
                'ask_price10': safe_int(parts[12]),
                
                # 매수호가 1~10 (BIDP1~BIDP10) - 🔧 안전한 변환 적용
                'bid_price1': safe_int(parts[13]),
                'bid_price2': safe_int(parts[14]),
                'bid_price3': safe_int(parts[15]),
                'bid_price4': safe_int(parts[16]),
                'bid_price5': safe_int(parts[17]),
                'bid_price6': safe_int(parts[18]),
                'bid_price7': safe_int(parts[19]),
                'bid_price8': safe_int(parts[20]),
                'bid_price9': safe_int(parts[21]),
                'bid_price10': safe_int(parts[22]),
                
                # 매도호가 잔량 1~10 (ASKP_RSQN1~ASKP_RSQN10) - 🔧 안전한 변환 적용
                'ask_qty1': safe_int(parts[23]),
                'ask_qty2': safe_int(parts[24]),
                'ask_qty3': safe_int(parts[25]),
                'ask_qty4': safe_int(parts[26]),
                'ask_qty5': safe_int(parts[27]),
                'ask_qty6': safe_int(parts[28]),
                'ask_qty7': safe_int(parts[29]),
                'ask_qty8': safe_int(parts[30]),
                'ask_qty9': safe_int(parts[31]),
                'ask_qty10': safe_int(parts[32]),
                
                # 매수호가 잔량 1~10 (BIDP_RSQN1~BIDP_RSQN10) - 🔧 안전한 변환 적용
                'bid_qty1': safe_int(parts[33]),
                'bid_qty2': safe_int(parts[34]),
                'bid_qty3': safe_int(parts[35]),
                'bid_qty4': safe_int(parts[36]),
                'bid_qty5': safe_int(parts[37]),
                'bid_qty6': safe_int(parts[38]),
                'bid_qty7': safe_int(parts[39]),
                'bid_qty8': safe_int(parts[40]),
                'bid_qty9': safe_int(parts[41]),
                'bid_qty10': safe_int(parts[42]),
                
                # 총 잔량 및 시간외 잔량 - 🔧 안전한 변환 적용
                'total_ask_qty': safe_int(parts[43]),          # TOTAL_ASKP_RSQN
                'total_bid_qty': safe_int(parts[44]),          # TOTAL_BIDP_RSQN
                'overtime_total_ask_qty': safe_int(parts[45]), # OVTM_TOTAL_ASKP_RSQN
                'overtime_total_bid_qty': safe_int(parts[46]), # OVTM_TOTAL_BIDP_RSQN
                
                # 예상 체결 정보 - 🔧 안전한 변환 적용
                'expected_price': safe_int(parts[47]),         # ANTC_CNPR: 예상 체결가
                'expected_qty': safe_int(parts[48]),           # ANTC_CNQN: 예상 체결량
                'expected_volume': safe_int(parts[49]),        # ANTC_VOL: 예상 거래량
                'expected_change': safe_int(parts[50]),        # ANTC_CNTG_VRSS: 예상 체결 대비
                'expected_change_sign': parts[51],             # ANTC_CNTG_VRSS_SIGN: 예상 체결 대비 부호
                'expected_change_rate': safe_float(parts[52]), # ANTC_CNTG_PRDY_CTRT: 예상 체결 전일 대비율
                
                # 누적 거래량 및 증감 - 🔧 안전한 변환 적용
                'acc_volume': safe_int(parts[53]),             # ACML_VOL: 누적 거래량
                'total_ask_change': safe_int(parts[54]),       # TOTAL_ASKP_RSQN_ICDC: 총 매도호가 잔량 증감
                'total_bid_change': safe_int(parts[55]),       # TOTAL_BIDP_RSQN_ICDC: 총 매수호가 잔량 증감
                'overtime_ask_change': safe_int(parts[56]) if len(parts) > 56 else 0, # OVTM_TOTAL_ASKP_ICDC
                'overtime_bid_change': safe_int(parts[57]) if len(parts) > 57 else 0, # OVTM_TOTAL_BIDP_ICDC
                
                # 추가 메타 정보
                'timestamp': datetime.now(),
                'source': 'websocket',
                'type': 'bid_ask',
                'is_market_time': parts[2] == '0',  # 0: 장중, A: 장후예상, B: 장전예상
                
                # 🎯 거래 참고용 주요 지표들 - 🔧 안전한 변환 적용
                'bid_ask_spread': (safe_int(parts[3]) - safe_int(parts[13])) if parts[3] and parts[13] else 0,
                'bid_ask_ratio': (safe_int(parts[44]) / max(safe_int(parts[43]), 1)) if parts[43] and parts[44] else 0.0,
                'market_pressure': 'BUY' if (safe_int(parts[44]) > safe_int(parts[43])) else 'SELL' if parts[43] and parts[44] else 'NEUTRAL'
            }

            # 통계 업데이트
            self.stats['data_processed'] += 1
            
            logger.debug(f"✅ 호가 파싱 성공: {parsed_data['stock_code']} "
                        f"매수1호가={parsed_data['bid_price1']:,}원({parsed_data['bid_qty1']:,}주) "
                        f"매도1호가={parsed_data['ask_price1']:,}원({parsed_data['ask_qty1']:,}주)")
            
            return parsed_data

        except Exception as e:
            self.stats['errors'] += 1
            logger.error(f"❌ 호가 데이터 파싱 오류: {e}")
            logger.error(f"데이터 길이: {len(data.split('^')) if data else 0}")
            logger.debug(f"🔍 파싱 실패 데이터: {data[:200]}..." if data and len(data) > 200 else data)
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
