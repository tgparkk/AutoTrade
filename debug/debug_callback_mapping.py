#!/usr/bin/env python3
"""
TR_ID별 콜백 매핑 확인 디버그 스크립트
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def debug_callback_mapping():
    """TR_ID별 콜백 매핑 정보 출력"""
    print("🔍 TR_ID별 콜백 매핑 분석")
    print("=" * 60)
    
    try:
        # StockManager 초기화 (실행 시점 import)
        from trade.stock_manager import StockManager
        from websocket.kis_websocket_manager import KISWebSocketManager
        
        stock_manager = StockManager()
        websocket_manager = KISWebSocketManager()
        
        print("✅ 매니저 초기화 완료")
        
        # 콜백 등록 전 상태
        print("\n📋 콜백 등록 전:")
        subscription_manager = websocket_manager.subscription_manager
        status_before = subscription_manager.get_status()
        tr_id_counts_before = status_before.get('tr_id_callback_counts', {})
        print(f"   TR_ID 콜백 수: {tr_id_counts_before}")
        
        # 콜백 등록
        print("\n🔗 콜백 등록 실행...")
        stock_manager.setup_websocket_callbacks(websocket_manager)
        
        # 콜백 등록 후 상태
        print("\n📋 콜백 등록 후:")
        status_after = subscription_manager.get_status()
        tr_id_counts_after = status_after.get('tr_id_callback_counts', {})
        print(f"   TR_ID 콜백 수: {tr_id_counts_after}")
        
        # 각 TR_ID별 등록된 콜백 함수 확인
        print("\n🎯 TR_ID별 등록된 콜백 함수:")
        print("-" * 40)
        
        tr_id_info = {
            'H0STCNT0': '실시간 체결가',
            'H0STASP0': '실시간 호가',
            'H0STCNI0': '체결통보'
        }
        
        for tr_id, description in tr_id_info.items():
            callbacks = subscription_manager.get_tr_id_callbacks(tr_id)
            print(f"\n📌 {tr_id} ({description}):")
            print(f"   등록된 콜백 수: {len(callbacks)}")
            
            for i, callback in enumerate(callbacks, 1):
                # 콜백 함수 정보 출력
                func_name = getattr(callback, '__name__', 'Unknown')
                func_module = getattr(callback, '__module__', 'Unknown')
                func_qualname = getattr(callback, '__qualname__', 'Unknown')
                
                print(f"   {i}. 함수명: {func_name}")
                print(f"      모듈: {func_module}")
                print(f"      전체명: {func_qualname}")
                
                # 함수 시그니처 확인
                import inspect
                try:
                    signature = inspect.signature(callback)
                    print(f"      시그니처: {func_name}{signature}")
                except Exception as e:
                    print(f"      시그니처: 확인 불가 ({e})")
        
        # 실제 콜백 실행 시뮬레이션
        print("\n🚀 콜백 실행 시뮬레이션:")
        print("-" * 40)
        
        # 테스트 종목 추가
        stock_manager.add_selected_stock(
            stock_code="005930",
            stock_name="삼성전자",
            open_price=73000,
            high_price=74000,
            low_price=72000,
            close_price=73500,
            volume=1000000,
            selection_score=85.5
        )
        
        # 각 TR_ID별 콜백 실행 테스트
        test_cases = [
            {
                'tr_id': 'H0STCNT0',
                'data': {'stock_code': '005930', 'stck_prpr': '74000', 'acml_vol': '1500000'},
                'description': '실시간 체결가'
            },
            {
                'tr_id': 'H0STASP0', 
                'data': {'stock_code': '005930', 'bidp1': '73900', 'askp1': '74100'},
                'description': '실시간 호가'
            },
            {
                'tr_id': 'H0STCNI0',
                'data': {'data': {'mksc_shrn_iscd': '005930', 'exec_prce': '74000', 'exec_qty': '100'}},
                'description': '체결통보'
            }
        ]
        
        for test_case in test_cases:
            tr_id = test_case['tr_id']
            data = test_case['data']
            description = test_case['description']
            
            print(f"\n🔥 {tr_id} ({description}) 콜백 실행:")
            
            callbacks = subscription_manager.get_tr_id_callbacks(tr_id)
            for callback in callbacks:
                try:
                    func_name = getattr(callback, '__name__', 'Unknown')
                    print(f"   → {func_name} 실행...")
                    
                    # 실제 콜백 실행
                    if tr_id == 'H0STCNI0':
                        # 체결통보는 stock_code 없이
                        callback(tr_id, data)
                    else:
                        # 체결가, 호가는 stock_code 포함
                        stock_code = data.get('stock_code', '')
                        callback(tr_id, stock_code, data)
                    
                    print(f"   ✅ {func_name} 실행 완료")
                    
                except Exception as e:
                    print(f"   ❌ {func_name} 실행 오류: {e}")
        
        print("\n✅ 콜백 매핑 분석 완료")
        
    except Exception as e:
        print(f"❌ 분석 중 오류 발생: {e}")
        import traceback
        traceback.print_exc()

def main():
    debug_callback_mapping()

if __name__ == "__main__":
    main() 