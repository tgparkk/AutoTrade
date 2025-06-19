#!/usr/bin/env python3
"""
디버그: 실시간 모니터링만 테스트
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


async def debug_realtime_monitor():
    """실시간 모니터링 디버깅"""
    print("=" * 60)
    print("📊 실시간 모니터링 디버깅")
    print("=" * 60)
    
    try:
        # TradeManager 초기화
        logger.info("TradeManager 초기화 중...")
        trade_manager = TradeManager()
        logger.info("✅ TradeManager 초기화 완료")
        
        # 테스트용 종목 추가 (실제 종목이 없을 경우)
        if not trade_manager.stock_manager.get_all_selected_stocks():
            print("📋 테스트용 종목 추가 중...")
            test_stocks = [
                ("005930", "삼성전자", 75000),
                ("000660", "SK하이닉스", 85000),
                ("035420", "NAVER", 120000)
            ]
            
            for stock_code, stock_name, price in test_stocks:
                success = trade_manager.stock_manager.add_selected_stock(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    open_price=price,
                    high_price=price * 1.02,
                    low_price=price * 0.98,
                    close_price=price,
                    volume=100000,
                    selection_score=80.0
                )
                if success:
                    print(f"  ✅ {stock_code}[{stock_name}] 추가")
        
        # 현재 상태 확인
        selected_stocks = trade_manager.stock_manager.get_all_selected_stocks()
        print(f"\n📊 모니터링 대상 종목 수: {len(selected_stocks)}")
        
        # 실시간 모니터링 시작
        print("\n🚀 실시간 모니터링 시작...")
        monitor_success = trade_manager.start_market_monitoring()
        
        if monitor_success:
            print("✅ 실시간 모니터링 시작 성공!")
            
            # 일정 시간 동안 모니터링 실행
            print(f"\n⏱️ 30초 동안 모니터링 실행 중...")
            
            for i in range(30):
                await asyncio.sleep(1)
                
                # 5초마다 상태 출력
                if i % 5 == 0:
                    status = trade_manager.realtime_monitor.get_monitoring_status()
                    print(f"  📈 {i+1}초: 스캔횟수={status['market_scan_count']}, "
                          f"매수신호={status['buy_signals_detected']}, "
                          f"매도신호={status['sell_signals_detected']}")
                
                # 브레이크포인트 설정 위치 (중간에 중단하고 싶을 때)
                # if i == 10:
                #     breakpoint()  # 10초 후 중단점
            
            # 모니터링 중지
            print("\n🛑 실시간 모니터링 중지...")
            trade_manager.stop_market_monitoring()
            
            # 최종 결과 확인
            final_status = trade_manager.realtime_monitor.get_monitoring_status()
            trade_stats = trade_manager.trade_executor.get_trade_statistics()
            
            print(f"\n📊 최종 결과:")
            print(f"  - 총 스캔 횟수: {final_status['market_scan_count']}")
            print(f"  - 매수 신호: {final_status['buy_signals_detected']}")
            print(f"  - 매도 신호: {final_status['sell_signals_detected']}")
            print(f"  - 실행된 거래: {trade_stats['total_trades']}")
            print(f"  - 승률: {trade_stats['win_rate']:.1f}%")
            
        else:
            print("❌ 실시간 모니터링 시작 실패")
            return False
            
    except Exception as e:
        logger.error(f"디버깅 중 오류: {e}")
        print(f"❌ 오류 발생: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("🎯 실시간 모니터링 디버깅 완료")
    print("=" * 60)
    
    return True


async def main():
    """메인 함수"""
    print("📊 실시간 모니터링 디버깅을 시작합니다...")
    
    try:
        success = await debug_realtime_monitor()
        if success:
            print("\n✅ 디버깅 완료")
        else:
            print("\n❌ 디버깅 실패")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n👋 디버깅을 중단합니다.")
    except Exception as e:
        print(f"\n❌ 디버깅 오류: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # 브레이크포인트 설정 위치 (시작 전)
    # breakpoint()
    
    asyncio.run(main()) 