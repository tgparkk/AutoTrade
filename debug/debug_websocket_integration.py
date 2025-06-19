#!/usr/bin/env python3
"""
디버그: TradeManager에서 웹소켓 매니저 통합 테스트
"""

import sys
import os
import asyncio
import time

# 프로젝트 루트를 Python 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trade.trade_manager import TradeManager
from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)


async def debug_websocket_integration():
    """TradeManager에서 웹소켓 통합 테스트"""
    print("=" * 70)
    print("🌐 TradeManager 웹소켓 통합 테스트")
    print("=" * 70)
    
    try:
        # TradeManager 초기화
        logger.info("TradeManager 초기화 중...")
        trade_manager = TradeManager()
        logger.info("✅ TradeManager 초기화 완료")
        
        print(f"\n📊 초기 상태:")
        print(f"   - TradeManager: {trade_manager}")
        
        if trade_manager.websocket_manager:
            print(f"   - 웹소켓 연결: {'✅ 연결' if trade_manager.websocket_manager.is_connected else '❌ 미연결'}")
            print(f"   - 웹소켓 구독: {len(trade_manager.websocket_manager.get_subscribed_stocks())}개")
        else:
            print(f"   - 웹소켓: ❌ 비활성화 (import 실패)")
            print(f"⚠️ 웹소켓 매니저가 None입니다. 모니터링 시작이 제한됩니다.")
            return False
        
        # 테스트 종목 추가
        print(f"\n📈 테스트 종목 추가")
        test_stocks = [
            ("005930", "삼성전자"),
            ("000660", "SK하이닉스"),
            ("035420", "NAVER")
        ]
        
        for stock_code, stock_name in test_stocks:
            success = trade_manager.stock_manager.add_selected_stock(
                stock_code=stock_code,
                stock_name=stock_name,
                open_price=75000,
                high_price=76000,
                low_price=74000,
                close_price=75500,
                volume=1000000,
                selection_score=85.0
            )
            if success:
                print(f"   ✅ {stock_code}[{stock_name}] 추가 완료")
        
        # 모니터링 시작 (웹소켓 연결 및 구독 포함)
        print(f"\n🚀 실시간 모니터링 시작 (웹소켓 통합)")
        print("-" * 50)
        
        # 브레이크포인트 설정 위치 (모니터링 시작 전)
        # breakpoint()
        
        monitor_success = trade_manager.start_market_monitoring()
        
        if monitor_success:
            print(f"✅ 모니터링 시작 성공")
            
            # 웹소켓 상태 확인
            websocket_manager = trade_manager.get_websocket_manager()
            print(f"\n🌐 웹소켓 상태:")
            print(f"   - 연결 상태: {'✅ 연결' if websocket_manager.is_connected else '❌ 미연결'}")
            print(f"   - 실행 상태: {'✅ 실행' if websocket_manager.is_running else '❌ 중지'}")
            print(f"   - 구독 종목: {len(websocket_manager.get_subscribed_stocks())}개")
            
            if websocket_manager.get_subscribed_stocks():
                print(f"   - 구독 목록: {list(websocket_manager.get_subscribed_stocks())}")
            
            # 웹소켓 상태 모니터링 (30초)
            print(f"\n📡 웹소켓 상태 모니터링 (30초)")
            print("-" * 50)
            
            start_time = time.time()
            while time.time() - start_time < 30:
                await asyncio.sleep(5)
                
                # 5초마다 상태 체크
                current_time = now_kst()
                status = websocket_manager.get_status()
                
                print(f"⏰ {current_time.strftime('%H:%M:%S')} - "
                      f"연결: {'✅' if status.get('is_connected', False) else '❌'}, "
                      f"구독: {status.get('subscription_count', 0)}개, "
                      f"메시지: {status.get('total_messages', 0)}개")
            
            print(f"\n✅ 웹소켓 모니터링 완료")
            
        else:
            print(f"❌ 모니터링 시작 실패")
        
        # 모니터링 중지 (웹소켓 정리 포함)
        print(f"\n🛑 모니터링 중지")
        trade_manager.stop_market_monitoring()
        
        # 최종 상태 확인
        print(f"\n📊 최종 웹소켓 상태:")
        if trade_manager.websocket_manager:
            final_status = trade_manager.websocket_manager.get_status()
            print(f"   - 연결: {'✅' if final_status.get('is_connected', False) else '❌'}")
            print(f"   - 구독: {final_status.get('subscription_count', 0)}개")
            print(f"   - 총 메시지: {final_status.get('total_messages', 0)}개")
            print(f"   - 연결 횟수: {final_status.get('connection_count', 0)}회")
        else:
            print(f"   - 웹소켓: ❌ 비활성화")
        
        return True
        
    except Exception as e:
        logger.error(f"웹소켓 통합 테스트 중 오류: {e}")
        print(f"❌ 테스트 실패: {e}")
        return False


def debug_websocket_manager_only():
    """웹소켓 매니저 단독 테스트"""
    print(f"\n🔧 웹소켓 매니저 단독 테스트")
    print("-" * 40)
    
    try:
        # 직접 웹소켓 매니저 생성
        from websocket.kis_websocket_manager import KISWebSocketManager
        
        websocket_manager = KISWebSocketManager()
        print(f"✅ 웹소켓 매니저 생성 완료")
        print(f"   - 초기 상태: {'연결' if websocket_manager.is_connected else '미연결'}")
        
        # 연결 테스트
        print(f"🔌 웹소켓 연결 테스트...")
        if websocket_manager.connect():
            print(f"✅ 웹소켓 연결 성공")
            
            # 간단한 구독 테스트
            print(f"📡 구독 테스트...")
            if websocket_manager.subscribe_stock_sync("005930"):
                print(f"✅ 삼성전자 구독 성공")
            else:
                print(f"❌ 삼성전자 구독 실패")
            
            time.sleep(3)  # 3초 대기
            
            # 상태 확인
            status = websocket_manager.get_status()
            print(f"📊 웹소켓 상태: {status}")
            
            # 정리
            websocket_manager.safe_cleanup()
            print(f"✅ 웹소켓 정리 완료")
        else:
            print(f"❌ 웹소켓 연결 실패")
            
    except Exception as e:
        print(f"❌ 웹소켓 단독 테스트 실패: {e}")


async def main():
    """메인 함수"""
    print("🌐 TradeManager 웹소켓 통합 테스트를 시작합니다...")
    
    try:
        # 웹소켓 매니저 단독 테스트
        debug_websocket_manager_only()
        
        print("\n" + "=" * 70)
        
        # 통합 테스트
        success = await debug_websocket_integration()
        
        if success:
            print("\n✅ 모든 웹소켓 통합 테스트 완료")
        else:
            print("\n❌ 웹소켓 통합 테스트 실패")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n👋 테스트를 중단합니다.")
    except Exception as e:
        print(f"\n❌ 테스트 오류: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main()) 