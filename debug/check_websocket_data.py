#!/usr/bin/env python3
"""
웹소켓 데이터 수신 상태 확인 도구

이 스크립트는 장중이 아닐 때도 웹소켓 데이터 수신 상태를 확인할 수 있습니다.
실행 방법: python debug/check_websocket_data.py
"""

import os
import sys
import asyncio
import time
from datetime import datetime

# 프로젝트 루트를 Python 경로에 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.korean_time import now_kst
from utils.logger import setup_logger
from trade.stock_manager import StockManager
from websocket.kis_websocket_manager import KISWebSocketManager

logger = setup_logger(__name__)


class WebSocketDataChecker:
    """웹소켓 데이터 수신 상태 체크 도구"""
    
    def __init__(self):
        self.stock_manager = None
        self.websocket_manager = None
    
    async def initialize(self):
        """초기화"""
        try:
            print("📡 웹소켓 데이터 수신 체크 도구 초기화 중...")
            
            # StockManager 초기화
            print("  - StockManager 초기화...")
            self.stock_manager = StockManager()
            
            # WebSocket Manager 초기화
            print("  - WebSocket Manager 초기화...")
            self.websocket_manager = KISWebSocketManager()
            
            # StockManager에 웹소켓 콜백 설정
            self.stock_manager.setup_websocket_callbacks(self.websocket_manager)
            
            # 웹소켓 연결
            print("  - 웹소켓 연결 중...")
            if self.websocket_manager.connect():
                print("  ✅ 웹소켓 연결 성공")
                
                # 테스트용 종목 구독 (몇 개만)
                test_stocks = ['005930', '000660', '035420']  # 삼성전자, SK하이닉스, NAVER
                for stock_code in test_stocks:
                    try:
                        if await self.websocket_manager.subscribe_stock(stock_code):
                            print(f"  ✅ {stock_code} 구독 성공")
                        else:
                            print(f"  ❌ {stock_code} 구독 실패")
                    except Exception as e:
                        print(f"  ❌ {stock_code} 구독 오류: {e}")
                
                return True
            else:
                print("  ❌ 웹소켓 연결 실패")
                return False
                
        except Exception as e:
            logger.error(f"초기화 오류: {e}")
            print(f"❌ 초기화 오류: {e}")
            return False
    
    def check_websocket_status(self):
        """웹소켓 상태 체크"""
        try:
            print("\n" + "=" * 50)
            print("📡 웹소켓 상태 체크")
            print("=" * 50)
            
            if not self.websocket_manager:
                print("❌ 웹소켓 매니저가 초기화되지 않았습니다")
                return
            
            # 기본 연결 상태
            print(f"🔌 연결 상태:")
            print(f"  - 연결됨: {self.websocket_manager.is_connected}")
            print(f"  - 실행중: {self.websocket_manager.is_running}")
            print(f"  - 건강상태: {self.websocket_manager.is_websocket_healthy()}")
            
            # 구독 상태
            subscribed_stocks = self.websocket_manager.get_subscribed_stocks()
            print(f"\n📋 구독 상태:")
            print(f"  - 구독 종목 수: {len(subscribed_stocks)}")
            print(f"  - 구독 여유: {self.websocket_manager.has_subscription_capacity()}")
            print(f"  - 구독 종목: {', '.join(subscribed_stocks)}")
            
            # 메시지 수신 통계
            message_stats = self.websocket_manager.message_handler.stats
            print(f"\n📨 메시지 수신 통계:")
            print(f"  - 총 수신 메시지: {message_stats.get('messages_received', 0)}건")
            print(f"  - 핑퐁 응답: {message_stats.get('ping_pong_count', 0)}회")
            print(f"  - 오류 횟수: {message_stats.get('errors', 0)}건")
            
            last_message_time = message_stats.get('last_message_time')
            if last_message_time:
                time_diff = (now_kst() - last_message_time).total_seconds()
                print(f"  - 최근 메시지: {time_diff:.0f}초 전 ({last_message_time.strftime('%H:%M:%S')})")
            else:
                print("  - 최근 메시지: 없음")
            
            last_ping_time = message_stats.get('last_ping_pong_time')
            if last_ping_time:
                ping_diff = (now_kst() - last_ping_time).total_seconds()
                print(f"  - 최근 핑퐁: {ping_diff:.0f}초 전 ({last_ping_time.strftime('%H:%M:%S')})")
            else:
                print("  - 최근 핑퐁: 없음")
            
            print("=" * 50)
            
        except Exception as e:
            logger.error(f"웹소켓 상태 체크 오류: {e}")
            print(f"❌ 웹소켓 상태 체크 오류: {e}")
    
    def monitor_data_reception(self, duration_seconds=30):
        """실시간 데이터 수신 모니터링"""
        try:
            print(f"\n🔄 {duration_seconds}초간 실시간 데이터 수신 모니터링 시작...")
            print("(Ctrl+C로 중단 가능)")
            
            if not self.websocket_manager:
                print("❌ 웹소켓 매니저가 초기화되지 않았습니다")
                return
            
            start_time = time.time()
            last_message_count = self.websocket_manager.message_handler.stats.get('messages_received', 0)
            
            try:
                while (time.time() - start_time) < duration_seconds:
                    current_message_count = self.websocket_manager.message_handler.stats.get('messages_received', 0)
                    new_messages = current_message_count - last_message_count
                    
                    if new_messages > 0:
                        elapsed = time.time() - start_time
                        print(f"  📨 {elapsed:.0f}초: 새 메시지 {new_messages}건 수신 (총 {current_message_count}건)")
                        last_message_count = current_message_count
                    
                    time.sleep(2)  # 2초마다 체크
                    
            except KeyboardInterrupt:
                print("\n⏹️ 모니터링 중단됨")
            
            final_message_count = self.websocket_manager.message_handler.stats.get('messages_received', 0)
            total_new_messages = final_message_count - last_message_count
            
            print(f"\n📊 모니터링 결과:")
            print(f"  - 모니터링 시간: {time.time() - start_time:.0f}초")
            print(f"  - 수신된 새 메시지: {total_new_messages}건")
            print(f"  - 초당 평균 메시지: {total_new_messages / (time.time() - start_time):.1f}건/초")
            
        except Exception as e:
            logger.error(f"데이터 수신 모니터링 오류: {e}")
            print(f"❌ 데이터 수신 모니터링 오류: {e}")
    
    async def cleanup(self):
        """정리"""
        try:
            print("\n🧹 정리 중...")
            if self.websocket_manager:
                await self.websocket_manager.cleanup()
            if self.stock_manager:
                # StockManager에는 cleanup 메서드가 없으므로 생략
                pass
            print("✅ 정리 완료")
        except Exception as e:
            logger.error(f"정리 오류: {e}")
            print(f"❌ 정리 오류: {e}")


async def main():
    """메인 함수"""
    print("=" * 60)
    print("📡 웹소켓 데이터 수신 상태 체크 도구")
    print("=" * 60)
    print(f"시작 시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')} (KST)")
    print("=" * 60)
    
    checker = WebSocketDataChecker()
    
    try:
        # 초기화
        if not await checker.initialize():
            print("❌ 초기화 실패")
            return 1
        
        print("\n✅ 초기화 완료")
        
        # 웹소켓 연결 안정화 대기
        print("⏳ 웹소켓 연결 안정화 대기 중... (5초)")
        await asyncio.sleep(5)
        
        while True:
            print("\n" + "=" * 30)
            print("명령어 선택:")
            print("1. 상태 체크 (status)")
            print("2. 실시간 모니터링 (monitor)")
            print("3. 짧은 모니터링 (quick)")
            print("4. 종료 (quit)")
            print("=" * 30)
            
            try:
                choice = input("선택> ").strip().lower()
                
                if choice in ['1', 'status', 's']:
                    checker.check_websocket_status()
                
                elif choice in ['2', 'monitor', 'm']:
                    duration = input("모니터링 시간(초, 기본값 30초)> ").strip()
                    try:
                        duration = int(duration) if duration else 30
                    except ValueError:
                        duration = 30
                    checker.monitor_data_reception(duration)
                
                elif choice in ['3', 'quick', 'q']:
                    checker.monitor_data_reception(10)  # 10초 짧은 모니터링
                
                elif choice in ['4', 'quit', 'exit']:
                    print("👋 종료합니다.")
                    break
                
                else:
                    print("❌ 잘못된 선택입니다.")
                    
            except KeyboardInterrupt:
                print("\n👋 종료합니다.")
                break
            except EOFError:
                print("\n👋 종료합니다.")
                break
        
        return 0
        
    except Exception as e:
        logger.error(f"실행 오류: {e}")
        print(f"❌ 실행 오류: {e}")
        return 1
    
    finally:
        await checker.cleanup()


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n👋 시스템을 종료합니다.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 시스템 실행 오류: {e}")
        sys.exit(1) 