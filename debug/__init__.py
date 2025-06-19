"""
AutoTrade 디버깅 패키지

VS Code F5 디버깅을 위한 스크립트들이 포함되어 있습니다.

사용 가능한 디버그 스크립트:
- debug_pre_market.py: 장시작전 프로세스 테스트
- debug_realtime_monitor.py: 실시간 모니터링 테스트  
- debug_trade_execution.py: 거래 실행 테스트
- debug_api_test.py: API 연결 테스트
"""

__version__ = "1.0.0"
__all__ = [
    "debug_pre_market",
    "debug_realtime_monitor", 
    "debug_trade_execution",
    "debug_api_test"
] 