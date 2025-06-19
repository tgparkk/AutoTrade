#!/usr/bin/env python3
"""
디버그: 장시작전 프로세스만 테스트
"""

import sys
import os

# 프로젝트 루트를 Python 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trade.trade_manager import TradeManager
from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)


def debug_pre_market():
    """장시작전 프로세스 디버깅"""
    print("=" * 60)
    print("🔍 장시작전 프로세스 디버깅")
    print("=" * 60)
    
    try:
        # TradeManager 초기화
        logger.info("TradeManager 초기화 중...")
        trade_manager = TradeManager()
        logger.info("✅ TradeManager 초기화 완료")
        
        # 현재 상태 확인
        print(f"\n📊 현재 시각: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}")
        
        stock_summary = trade_manager.stock_manager.get_stock_summary()
        print(f"📈 현재 선정 종목 수: {stock_summary['total_selected']}")
        
        # 장시작전 프로세스 실행
        print("\n🚀 장시작전 프로세스 실행 중...")
        success = trade_manager.run_pre_market_process()
        
        if success:
            print("✅ 장시작전 프로세스 성공!")
            
            # 결과 확인
            updated_summary = trade_manager.stock_manager.get_stock_summary()
            print(f"📊 선정된 종목 수: {updated_summary['total_selected']}")
            
            # 선정된 종목들 출력
            selected_stocks = trade_manager.stock_manager.get_all_selected_stocks()
            if selected_stocks:
                print("\n📋 선정된 종목 목록:")
                for i, position in enumerate(selected_stocks, 1):
                    print(f"  {i:2d}. {position.stock_code}[{position.stock_name}] "
                          f"(점수: {position.total_pattern_score:.1f})")
            
        else:
            print("❌ 장시작전 프로세스 실패")
            
    except Exception as e:
        logger.error(f"디버깅 중 오류: {e}")
        print(f"❌ 오류 발생: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("🎯 장시작전 프로세스 디버깅 완료")
    print("=" * 60)
    
    return True


if __name__ == "__main__":
    print("🔍 장시작전 프로세스 디버깅을 시작합니다...")
    
    # 브레이크포인트 설정 위치 (예시)
    # breakpoint()  # 여기서 중단점을 설정할 수 있습니다
    
    try:
        success = debug_pre_market()
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