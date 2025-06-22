#!/usr/bin/env python3
"""
AutoTrade 시스템 테스트 스크립트
"""

import sys
import os

# 프로젝트 루트를 Python 경로에 추가
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """필수 모듈 import 테스트"""
    print("=== Import 테스트 ===")
    
    try:
        print("1. 기본 모듈 테스트...")
        import pandas as pd
        import requests
        import sqlite3
        import asyncio
        print("   ✅ 기본 모듈 import 성공")
        
        print("2. 프로젝트 모듈 테스트...")
        from utils.korean_time import now_kst
        from utils.logger import setup_logger
        print("   ✅ 유틸리티 모듈 import 성공")
        
        print("3. 핵심 모듈 테스트...")
        from trade.trade_manager import TradeManager
        from trade.stock_manager import StockManager
        print("   ✅ 핵심 모듈 import 성공")
        
        return True
        
    except ImportError as e:
        print(f"   ❌ Import 오류: {e}")
        return False
    except Exception as e:
        print(f"   ❌ 예상치 못한 오류: {e}")
        return False

def test_basic_functionality():
    """기본 기능 테스트"""
    print("\n=== 기본 기능 테스트 ===")
    
    try:
        print("1. 한국 시간 테스트...")
        from utils.korean_time import now_kst
        current_time = now_kst()
        print(f"   현재 한국 시간: {current_time}")
        
        print("2. 로거 테스트...")
        from utils.logger import setup_logger
        logger = setup_logger("test")
        logger.info("테스트 로그 메시지")
        print("   ✅ 로거 작동 확인")
        
        print("3. 설정 로더 테스트...")
        from utils import get_trading_config_loader
        config_loader = get_trading_config_loader()
        strategy_config = config_loader.load_trading_strategy_config()
        print(f"   ✅ 설정 로드 성공: {len(strategy_config)}개 설정")
        
        return True
        
    except Exception as e:
        print(f"   ❌ 기능 테스트 오류: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_trade_manager():
    """TradeManager 테스트"""
    print("\n=== TradeManager 테스트 ===")
    
    try:
        print("1. TradeManager 인스턴스 생성...")
        from trade.trade_manager import TradeManager
        trade_manager = TradeManager()
        print("   ✅ TradeManager 생성 성공")
        
        print("2. 시스템 상태 확인...")
        status = trade_manager.get_system_status()
        print(f"   시스템 상태: {status}")
        
        print("3. StockManager 확인...")
        stock_summary = trade_manager.stock_manager.get_stock_summary()
        print(f"   종목 관리 상태: {stock_summary}")
        
        return True
        
    except Exception as e:
        print(f"   ❌ TradeManager 테스트 오류: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """메인 테스트 함수"""
    print("🚀 AutoTrade 시스템 테스트 시작")
    print("=" * 50)
    
    # 1. Import 테스트
    if not test_imports():
        print("\n❌ Import 테스트 실패")
        return 1
    
    # 2. 기본 기능 테스트
    if not test_basic_functionality():
        print("\n❌ 기본 기능 테스트 실패")
        return 1
    
    # 3. TradeManager 테스트
    if not test_trade_manager():
        print("\n❌ TradeManager 테스트 실패")
        return 1
    
    print("\n" + "=" * 50)
    print("✅ 모든 테스트 통과!")
    print("시스템이 정상적으로 작동합니다.")
    
    return 0

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n👋 테스트를 중단합니다.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 테스트 실행 오류: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1) 