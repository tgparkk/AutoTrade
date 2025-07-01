"""
Stock Management 모듈

StockManager의 내부 구성 요소들을 모듈화한 패키지

주요 구성 요소:
- _StockCacheManager: 캐시 관리 전용 모듈
- _StockObjectBuilder: Stock 객체 빌드 전용 모듈
- _RealtimeProcessor: 실시간 데이터 처리 전용 모듈
- _ExecutionProcessor: 체결 통보 처리 전용 모듈 (4단계)

성능 최적화와 코드 분리를 통한 유지보수성 향상이 목표입니다.
"""

from ._cache_manager import _StockCacheManager
from ._stock_builder import _StockObjectBuilder
from ._realtime_processor import _RealtimeProcessor
from ._execution_processor import _ExecutionProcessor
from ._stock_lifecycle_manager import _StockLifecycleManager

__all__ = [
    '_StockCacheManager',
    '_StockObjectBuilder', 
    '_RealtimeProcessor',
    '_ExecutionProcessor',
    '_StockLifecycleManager'
] 