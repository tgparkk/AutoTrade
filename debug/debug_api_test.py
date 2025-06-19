#!/usr/bin/env python3
"""
디버그: API 연결 및 기능 테스트
"""

import sys
import os

# 프로젝트 루트를 Python 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)


def debug_api_connection():
    """API 연결 테스트"""
    print("=" * 60)
    print("🌐 API 연결 테스트")
    print("=" * 60)
    
    try:
        # 1. API 설정 확인
        print("🔧 1단계: API 설정 확인")
        print("-" * 40)
        
        try:
            from api import kis_auth
            print("✅ kis_auth 모듈 import 성공")
        except ImportError as e:
            print(f"❌ kis_auth 모듈 import 실패: {e}")
            return False
        
        try:
            from api import kis_market_api
            print("✅ kis_market_api 모듈 import 성공")
        except ImportError as e:
            print(f"❌ kis_market_api 모듈 import 실패: {e}")
            return False
        
        try:
            from api import kis_order_api
            print("✅ kis_order_api 모듈 import 성공")
        except ImportError as e:
            print(f"❌ kis_order_api 모듈 import 실패: {e}")
            return False
        
        try:
            from api import kis_account_api
            print("✅ kis_account_api 모듈 import 성공")
        except ImportError as e:
            print(f"❌ kis_account_api 모듈 import 실패: {e}")
            return False
        
        # 2. 인증 테스트
        print(f"\n🔐 2단계: 인증 테스트")
        print("-" * 40)
        
        # 브레이크포인트 설정 위치 (인증 전)
        # breakpoint()
        
        # 인증 상태 확인 (실제 토큰 발급은 하지 않고 설정만 확인)
        print("📋 API 설정 정보 확인 중...")
        
        # 설정 파일 존재 여부 확인
        import configparser
        config = configparser.ConfigParser()
        
        try:
            config.read('config/key.ini', encoding='utf-8')
            
            # 필수 설정 확인
            required_keys = ['APP_KEY', 'APP_SECRET', 'ACCOUNT_NO', 'ACCOUNT_CODE']
            missing_keys = []
            
            for key in required_keys:
                try:
                    value = config.get('KIS_API', key)
                    if value:
                        print(f"✅ {key}: 설정됨")
                    else:
                        print(f"❌ {key}: 값이 없음")
                        missing_keys.append(key)
                except:
                    print(f"❌ {key}: 설정되지 않음")
                    missing_keys.append(key)
            
            if missing_keys:
                print(f"⚠️ 누락된 설정: {', '.join(missing_keys)}")
                print("   실제 API 호출은 건너뜁니다.")
                skip_api_calls = True
            else:
                print("✅ 모든 필수 설정이 확인되었습니다.")
                skip_api_calls = False
                
        except Exception as e:
            print(f"❌ 설정 파일 읽기 실패: {e}")
            skip_api_calls = True
        
        # 3. 시장 데이터 API 테스트
        print(f"\n📊 3단계: 시장 데이터 API 테스트")
        print("-" * 40)
        
        if not skip_api_calls:
            try:
                # 삼성전자 현재가 조회 테스트
                print("📈 삼성전자(005930) 현재가 조회 중...")
                
                current_data = kis_market_api.get_inquire_price(
                    div_code="J",
                    itm_no="005930"
                )
                
                if current_data is not None and not current_data.empty:
                    price = current_data.iloc[0]['stck_prpr']
                    print(f"✅ 현재가 조회 성공: {price:,}원")
                    
                    # 추가 정보 출력
                    change_rate = current_data.iloc[0].get('prdy_ctrt', '0')
                    volume = current_data.iloc[0].get('acml_vol', '0')
                    print(f"   전일대비: {change_rate}%")
                    print(f"   거래량: {volume:,}주")
                    
                else:
                    print("❌ 현재가 조회 실패 (데이터 없음)")
                    
            except Exception as e:
                print(f"❌ 현재가 조회 실패: {e}")
        else:
            print("⚠️ API 설정이 불완전하여 건너뜀")
        
        # 4. 계좌 API 테스트
        print(f"\n💰 4단계: 계좌 API 테스트")
        print("-" * 40)
        
        if not skip_api_calls:
            try:
                # 계좌 잔고 조회 테스트
                print("💰 계좌 잔고 조회 중...")
                
                # 실제 API 호출 대신 함수 존재 여부만 확인
                if hasattr(kis_account_api, 'get_inquire_balance'):
                    print("✅ 계좌 잔고 조회 함수 존재")
                else:
                    print("❌ 계좌 잔고 조회 함수 없음")
                
                if hasattr(kis_account_api, 'get_psbl_order'):
                    print("✅ 주문 가능 조회 함수 존재")
                else:
                    print("❌ 주문 가능 조회 함수 없음")
                    
            except Exception as e:
                print(f"❌ 계좌 API 테스트 실패: {e}")
        else:
            print("⚠️ API 설정이 불완전하여 건너뜀")
        
        # 5. 주문 API 테스트
        print(f"\n📋 5단계: 주문 API 테스트")
        print("-" * 40)
        
        if not skip_api_calls:
            try:
                # 주문 함수 존재 여부 확인
                if hasattr(kis_order_api, 'send_order_cash'):
                    print("✅ 현금 주문 함수 존재")
                else:
                    print("❌ 현금 주문 함수 없음")
                
                if hasattr(kis_order_api, 'inquire_balance'):
                    print("✅ 잔고 조회 함수 존재")
                else:
                    print("❌ 잔고 조회 함수 없음")
                    
                print("ℹ️ 실제 주문은 테스트하지 않습니다 (안전상)")
                    
            except Exception as e:
                print(f"❌ 주문 API 테스트 실패: {e}")
        else:
            print("⚠️ API 설정이 불완전하여 건너뜀")
        
        # 6. 종목 데이터 로더 테스트
        print(f"\n📚 6단계: 종목 데이터 로더 테스트")
        print("-" * 40)
        
        try:
            from utils.stock_data_loader import get_stock_data_loader
            
            stock_loader = get_stock_data_loader()
            print(f"✅ 종목 데이터 로더 초기화 성공")
            print(f"   총 종목 수: {len(stock_loader)}")
            
            # 샘플 종목 검색
            test_codes = ["005930", "000660", "035420"]
            for code in test_codes:
                name = stock_loader.get_stock_name(code)
                if name:
                    print(f"   {code}: {name}")
                else:
                    print(f"   {code}: 종목명 없음")
                    
        except Exception as e:
            print(f"❌ 종목 데이터 로더 테스트 실패: {e}")
        
        print(f"\n🔧 7단계: 웹소켓 연결 테스트")
        print("-" * 40)
        
        try:
            from websocket import kis_websocket_manager
            print("✅ 웹소켓 매니저 모듈 import 성공")
            
            # 웹소켓 클래스 존재 여부 확인
            if hasattr(kis_websocket_manager, 'WebSocketManager'):
                print("✅ WebSocketManager 클래스 존재")
            else:
                print("⚠️ WebSocketManager 클래스 없음 (정상 - 구현 중)")
                
        except Exception as e:
            print(f"ℹ️ 웹소켓 테스트 건너뜀: {e}")
        
    except Exception as e:
        logger.error(f"API 테스트 중 오류: {e}")
        print(f"❌ 오류 발생: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("🎯 API 연결 테스트 완료")
    print("=" * 60)
    
    return True


def debug_network_connection():
    """네트워크 연결 테스트"""
    print(f"\n🌐 네트워크 연결 테스트")
    print("-" * 40)
    
    try:
        import requests
        
        # 한국투자증권 API 서버 연결 테스트
        test_url = "https://openapi.koreainvestment.com"
        
        print(f"🔗 {test_url} 연결 테스트 중...")
        
        response = requests.get(test_url, timeout=5)
        
        if response.status_code == 200:
            print("✅ 한국투자증권 API 서버 연결 성공")
        else:
            print(f"⚠️ 응답 코드: {response.status_code}")
            
    except requests.exceptions.Timeout:
        print("❌ 연결 시간 초과")
        return False
    except requests.exceptions.ConnectionError:
        print("❌ 연결 실패")
        return False
    except Exception as e:
        print(f"❌ 네트워크 테스트 실패: {e}")
        return False
    
    return True


if __name__ == "__main__":
    print("🌐 API 연결 및 기능 테스트를 시작합니다...")
    
    # 브레이크포인트 설정 위치 (시작 전)
    # breakpoint()
    
    try:
        # 네트워크 연결 테스트
        network_ok = debug_network_connection()
        
        if network_ok:
            # API 기능 테스트
            api_ok = debug_api_connection()
            
            if api_ok:
                print("\n✅ 모든 테스트 완료")
            else:
                print("\n❌ API 테스트 실패")
                sys.exit(1)
        else:
            print("\n❌ 네트워크 연결 실패")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n👋 테스트를 중단합니다.")
    except Exception as e:
        print(f"\n❌ 테스트 오류: {e}")
        sys.exit(1) 