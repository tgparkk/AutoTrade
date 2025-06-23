#!/usr/bin/env python3
"""
AutoTrade - 자동매매 시스템 메인 실행 파일

간소화된 main.py:
- TradeManager를 초기화하고 실행하는 역할만 담당
- 모든 주식 관련 비즈니스 로직은 TradeManager에서 처리
"""

import os
import sys
import signal
import asyncio
from datetime import datetime

# 프로젝트 루트를 Python 경로에 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from trade.trade_manager import TradeManager
from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)


async def main():
    """메인 함수 - TradeManager만 실행"""
    print("=" * 60)
    print("🤖 AutoTrade - 자동매매 시스템")
    print("=" * 60)
    print(f"시작 시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')} (KST)")
    print("=" * 60)
    
    trade_manager = None
    
    try:
        # 1. TradeManager 인스턴스 생성
        logger.info("TradeManager 초기화 중...")
        trade_manager = TradeManager()
        logger.info("✅ TradeManager 초기화 완료")
        
        # 2. 시그널 핸들러 등록 (TradeManager의 shutdown_event 사용)
        def signal_handler(signum, frame):
            logger.info(f"종료 시그널 수신: {signum}")
            trade_manager.shutdown_event.set()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # 3. 시스템 시작 (모든 비즈니스 로직은 TradeManager에서 처리)
        logger.info("AutoTrade 시스템 시작...")
        await trade_manager.start_async_system()
        
    except KeyboardInterrupt:
        logger.info("사용자에 의한 중단")
    except Exception as e:
        logger.error(f"시스템 실행 오류: {e}")
        return 1
    finally:
        if trade_manager:
            logger.info("시스템 종료 중...")
            await trade_manager.stop_async_system()
    
    return 0


if __name__ == "__main__":
    print("🚀 AutoTrade 시스템을 시작합니다...")
    
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n👋 시스템을 종료합니다.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 시스템 실행 오류: {e}")
        sys.exit(1)

 