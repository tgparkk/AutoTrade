#!/usr/bin/env python3
"""
콜백 최적화 테스트
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

# Mock 클래스들
class MockSubscriptionManager:
    def __init__(self):
        self.tr_id_callback_count = 0
        self.global_callback_count = 0
        
        self.tr_id_callbacks = {
            'H0STCNT0': [self.tr_id_callback_mock]
        }
        self.global_callbacks = {
            'stock_price': [self.global_callback_mock]
        }
    
    def tr_id_callback_mock(self, tr_id, stock_code, data):
        self.tr_id_callback_count += 1
        print(f"TR_ID 콜백 실행: {tr_id}, 종목: {stock_code}")
    
    def global_callback_mock(self, data_type, data):
        self.global_callback_count += 1
        print(f"글로벌 콜백 실행: {data_type}")
    
    def get_tr_id_callbacks(self, tr_id):
        return self.tr_id_callbacks.get(tr_id, [])
    
    def get_global_callbacks(self, data_type):
        return self.global_callbacks.get(data_type, [])

class MockDataParser:
    def parse_contract_data(self, data):
        return {'stock_code': '005930', 'price': '74000'}

async def test_callback_optimization():
    """콜백 최적화 테스트"""
    print("=== 콜백 최적화 테스트 시작 ===")
    
    # 메시지 핸들러 import
    from websocket.kis_websocket_message_handler import KISWebSocketMessageHandler
    
    # Mock 객체 생성
    mock_subscription_manager = MockSubscriptionManager()
    mock_data_parser = MockDataParser()
    
    # 메시지 핸들러 생성
    handler = KISWebSocketMessageHandler(mock_data_parser, mock_subscription_manager)
    
    print("1. 초기 콜백 카운트:")
    print(f"   TR_ID 콜백: {mock_subscription_manager.tr_id_callback_count}")
    print(f"   글로벌 콜백: {mock_subscription_manager.global_callback_count}")
    
    # TR_ID 콜백만 실행 (최적화된 버전)
    print("\n2. TR_ID 콜백 실행 테스트...")
    await handler._execute_tr_id_callbacks('H0STCNT0', {'stock_code': '005930', 'price': '74000'})
    
    print("\n3. 최종 콜백 카운트:")
    print(f"   TR_ID 콜백: {mock_subscription_manager.tr_id_callback_count}")
    print(f"   글로벌 콜백: {mock_subscription_manager.global_callback_count}")
    
    # 결과 검증
    if mock_subscription_manager.tr_id_callback_count == 1 and mock_subscription_manager.global_callback_count == 0:
        print("\n✅ 콜백 최적화 성공: TR_ID 콜백만 실행됨")
    else:
        print("\n❌ 콜백 최적화 실패: 예상과 다른 결과")
    
    print("=== 콜백 최적화 테스트 완료 ===")

def main():
    asyncio.run(test_callback_optimization())

if __name__ == "__main__":
    main() 