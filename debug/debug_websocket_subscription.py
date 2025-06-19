#!/usr/bin/env python3
"""
웹소켓 구독 문제 디버그용 스크립트
"""

import sys
import time
import asyncio
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import setup_logger
from websocket.kis_websocket_manager import KISWebSocketManager

logger = setup_logger(__name__)


def test_websocket_subscription():
    """웹소켓 구독 테스트"""
    print("🔧 웹소켓 구독 테스트 시작")
    print("=" * 50)
    
    try:
        # 1. 웹소켓 매니저 생성
        print("1️⃣ 웹소켓 매니저 생성...")
        websocket_manager = KISWebSocketManager()
        print(f"   ✅ 웹소켓 매니저 생성 완료")
        
        # 2. 연결 시도
        print("2️⃣ 웹소켓 연결 시도...")
        if websocket_manager.connect():
            print(f"   ✅ 웹소켓 연결 성공")
            print(f"   - 연결 상태: {'연결됨' if websocket_manager.is_connected else '연결안됨'}")
        else:
            print(f"   ❌ 웹소켓 연결 실패")
            return False
        
        # 3. 메시지 루프 시작
        print("3️⃣ 메시지 루프 시작...")
        websocket_manager.start_message_loop()
        
        # 메시지 루프가 시작될 때까지 대기
        time.sleep(3)
        
        print(f"   - 실행 상태: {'실행중' if websocket_manager.is_running else '중지됨'}")
        print(f"   - 이벤트 루프: {'있음' if websocket_manager._event_loop else '없음'}")
        
        # 4. 구독 테스트
        print("4️⃣ 종목 구독 테스트...")
        test_stocks = ["005930", "000660", "035420"]  # 삼성전자, SK하이닉스, NAVER
        
        success_count = 0
        for stock_code in test_stocks:
            print(f"   📡 {stock_code} 구독 시도...")
            
            try:
                result = websocket_manager.subscribe_stock_sync(stock_code)
                if result:
                    success_count += 1
                    print(f"   ✅ {stock_code} 구독 성공")
                else:
                    print(f"   ❌ {stock_code} 구독 실패")
            except Exception as e:
                print(f"   ❌ {stock_code} 구독 오류: {e}")
        
        # 5. 구독 상태 확인
        time.sleep(2)  # 구독 처리 대기
        
        print("5️⃣ 구독 상태 확인...")
        subscribed_stocks = websocket_manager.get_subscribed_stocks()
        subscription_count = websocket_manager.get_subscription_count()
        
        print(f"   - 구독 성공: {success_count}/{len(test_stocks)}")
        print(f"   - 총 구독수: {subscription_count}")
        print(f"   - 구독 목록: {subscribed_stocks}")
        print(f"   - 사용량: {websocket_manager.get_websocket_usage()}")
        
        # 6. 상태 상세 조회
        print("6️⃣ 상태 상세 조회...")
        status = websocket_manager.get_status()
        print(f"   - 연결: {'✅' if status.get('connection', {}).get('is_connected', False) else '❌'}")
        print(f"   - 실행: {'✅' if status.get('connection', {}).get('is_running', False) else '❌'}")
        print(f"   - 구독수: {status.get('subscriptions', {}).get('subscribed_count', 0)}")
        
        # 7. 정리
        print("7️⃣ 정리 작업...")
        websocket_manager.safe_cleanup()
        print(f"   ✅ 정리 완료")
        
        print("\n" + "=" * 50)
        print(f"🎯 테스트 결과: {success_count}/{len(test_stocks)} 성공")
        print(f"📊 최종 구독수: {subscription_count}개")
        
        return success_count > 0
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}")
        logger.error(f"웹소켓 구독 테스트 오류: {e}")
        return False


def test_subscription_without_event_loop():
    """이벤트 루프 없이 구독 테스트"""
    print("\n🔧 이벤트 루프 없이 구독 테스트")
    print("=" * 50)
    
    try:
        # 직접 websocket connection과 subscription manager 테스트
        from websocket.kis_websocket_connection import KISWebSocketConnection
        from websocket.kis_websocket_subscription_manager import KISWebSocketSubscriptionManager
        
        connection = KISWebSocketConnection()
        subscription_manager = KISWebSocketSubscriptionManager()
        
        print("1️⃣ 컴포넌트 생성 완료")
        
        # 구독 매니저만 테스트 (연결 없이)
        print("2️⃣ 구독 매니저 단독 테스트")
        
        # 직접 구독 추가
        test_code = "005930"
        if subscription_manager.add_subscription(test_code):
            print(f"3️⃣ 구독 매니저에 {test_code} 추가 성공")
            print(f"   - 구독수: {subscription_manager.get_subscription_count()}")
            print(f"   - 구독 목록: {subscription_manager.get_subscribed_stocks()}")
            print(f"   - 구독 가능: {subscription_manager.can_subscribe('000660')}")
            print(f"   - 구독 상태: {subscription_manager.is_subscribed(test_code)}")
        else:
            print(f"3️⃣ 구독 매니저에 {test_code} 추가 실패")
        
        print("4️⃣ 구독 매니저 테스트 완료")
            
    except Exception as e:
        print(f"❌ 이벤트 루프 없는 테스트 실패: {e}")


if __name__ == "__main__":
    print("🚀 웹소켓 구독 디버그 테스트 시작")
    
    # 메인 테스트
    success = test_websocket_subscription()
    
    # 추가 테스트
    test_subscription_without_event_loop()
    
    print(f"\n🏁 전체 테스트 {'성공' if success else '실패'}") 