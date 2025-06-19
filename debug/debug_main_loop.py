#!/usr/bin/env python3
"""
디버그: 새로운 메인 루프 테스트 (주기적 시장 스캔 및 매매)
"""

import sys
import os
import asyncio

# 프로젝트 루트를 Python 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trade.trade_manager import TradeManager
from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)


async def debug_main_loop():
    """새로운 메인 루프 디버깅"""
    print("=" * 60)
    print("📅 메인 루프 디버깅 (주기적 스캔 및 매매)")
    print("=" * 60)
    
    try:
        # TradeManager 초기화
        logger.info("TradeManager 초기화 중...")
        trade_manager = TradeManager()
        logger.info("✅ TradeManager 초기화 완료")
        
        # 현재 시간 정보
        current_time = now_kst()
        print(f"\n📊 현재 시각: {current_time.strftime('%Y-%m-%d %H:%M:%S')} ({current_time.strftime('%A')})")
        
        # 시장 시간 상태 확인
        is_pre_market = trade_manager._should_run_pre_market()
        is_market_hours = trade_manager._is_market_hours()
        
        print(f"🕐 장시작전 시간: {'✅ Yes' if is_pre_market else '❌ No'}")
        print(f"🕒 장시간: {'✅ Yes' if is_market_hours else '❌ No'}")
        
        # 메인 루프 시뮬레이션 (30초 동안)
        print(f"\n🔄 메인 루프 시뮬레이션 시작 (30초)")
        print("-" * 40)
        
        # 브레이크포인트 설정 위치 (루프 시작 전)
        # breakpoint()
        
        # 루프 변수 초기화
        loop_count = 0
        last_scan_date = None
        market_monitoring_active = False
        
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < 30:  # 30초 동안 실행
            loop_count += 1
            current_time = now_kst()
            current_date = current_time.date()
            
            print(f"\n🔄 루프 #{loop_count} - {current_time.strftime('%H:%M:%S')}")
            
            # === 1. 장시작전 스캔 시뮬레이션 ===
            if trade_manager._should_run_pre_market():
                if last_scan_date != current_date:
                    print(f"📊 장시작전 스캔 필요 감지")
                    
                    # 실제로는 여기서 스캔을 실행하지만, 디버그에서는 시뮬레이션
                    print(f"   💡 시뮬레이션: 시장 스캔 및 종목 선정")
                    last_scan_date = current_date
                    
                    # 테스트용 종목 추가
                    test_success = trade_manager.stock_manager.add_selected_stock(
                        stock_code="005930",
                        stock_name="삼성전자",
                        open_price=75000,
                        high_price=76000,
                        low_price=74000,
                        close_price=75500,
                        volume=1000000,
                        selection_score=85.0
                    )
                    if test_success:
                        print(f"   ✅ 테스트 종목 추가 완료")
                else:
                    print(f"📊 오늘 이미 스캔 완료")
            
            # === 2. 장시간 모니터링 시뮬레이션 ===
            if trade_manager._is_market_hours():
                if not market_monitoring_active:
                    selected_stocks = trade_manager.stock_manager.get_all_selected_stocks()
                    if selected_stocks:
                        print(f"🚀 모니터링 시작 시뮬레이션 ({len(selected_stocks)}개 종목)")
                        market_monitoring_active = True
                    else:
                        print(f"⚠️ 선정된 종목이 없음")
                else:
                    print(f"📈 실시간 모니터링 진행 중...")
            
            # === 3. 장마감 후 정리 시뮬레이션 ===
            elif market_monitoring_active and not trade_manager._is_market_hours():
                print(f"🏁 장마감 - 모니터링 중지 시뮬레이션")
                market_monitoring_active = False
            
            # === 4. 상태 체크 ===
            if current_time.second % 10 == 0:  # 10초마다
                stock_summary = trade_manager.stock_manager.get_stock_summary()
                websocket_status = "연결" if trade_manager.websocket_manager.is_connected else "미연결"
                websocket_subs = len(trade_manager.websocket_manager.get_subscribed_stocks())
                print(f"   📊 상태: 선정종목={stock_summary['total_selected']}개, "
                      f"웹소켓={websocket_status}({websocket_subs}개구독)")
            
            # === 5. 대기 시간 (실제보다 짧게) ===
            if trade_manager._is_market_hours():
                await asyncio.sleep(2)  # 실제는 30초
            elif trade_manager._should_run_pre_market():
                await asyncio.sleep(3)  # 실제는 60초
            else:
                await asyncio.sleep(5)  # 실제는 300초
        
        print(f"\n✅ 메인 루프 시뮬레이션 완료 (총 {loop_count}회 실행)")
        
        # 최종 상태 확인
        final_summary = trade_manager.stock_manager.get_stock_summary()
        websocket_status = "연결" if trade_manager.websocket_manager.is_connected else "미연결"
        websocket_subs = len(trade_manager.websocket_manager.get_subscribed_stocks())
        
        print(f"📊 최종 상태:")
        print(f"   - 선정 종목: {final_summary['total_selected']}개")
        print(f"   - 마지막 스캔: {last_scan_date}")
        print(f"   - 모니터링 활성: {market_monitoring_active}")
        print(f"   - 웹소켓: {websocket_status} ({websocket_subs}개 구독)")
        
    except Exception as e:
        logger.error(f"디버깅 중 오류: {e}")
        print(f"❌ 오류 발생: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("🎯 메인 루프 디버깅 완료")
    print("=" * 60)
    
    return True


async def debug_time_calculations():
    """시간 계산 로직 디버깅"""
    print(f"\n🕐 시간 계산 로직 테스트")
    print("-" * 40)
    
    # TradeManager 인스턴스 생성
    trade_manager = TradeManager()
    current_time = now_kst()
    
    # 다양한 시간대 테스트
    test_times = [
        (7, 30),   # 장시작 전
        (8, 30),   # 장시작전 시간
        (9, 30),   # 장시간
        (12, 0),   # 점심시간 (장시간)
        (15, 0),   # 장마감 임박
        (16, 0),   # 장마감 후
        (20, 0),   # 저녁
    ]
    
    for hour, minute in test_times:
        # 시간 시뮬레이션을 위해 임시로 현재 시간을 변경
        test_time_str = f"{hour:02d}:{minute:02d}"
        
        # 실제 시간 판단 로직 테스트 (현재 시간 기준)
        is_pre_market = trade_manager._should_run_pre_market()
        is_market = trade_manager._is_market_hours()
        
        status = "장외시간"
        if is_pre_market:
            status = "장시작전"
        elif is_market:
            status = "장시간"
        
        print(f"   {test_time_str}: {status}")
    
    print(f"   현재 시간: {current_time.strftime('%H:%M')} - 실제 상태")


async def main():
    """메인 함수"""
    print("📅 새로운 메인 루프 디버깅을 시작합니다...")
    
    try:
        # 시간 계산 로직 테스트
        await debug_time_calculations()
        
        # 브레이크포인트 설정 위치 (메인 루프 전)
        # breakpoint()
        
        # 메인 루프 테스트
        success = await debug_main_loop()
        
        if success:
            print("\n✅ 모든 디버깅 완료")
        else:
            print("\n❌ 디버깅 실패")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n👋 디버깅을 중단합니다.")
    except Exception as e:
        print(f"\n❌ 디버깅 오류: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main()) 