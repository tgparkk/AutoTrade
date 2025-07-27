# MarketScanner 모듈화 가이드

## 📊 개요

`market_scanner.py` 파일이 1338줄로 너무 커져서 유지보수가 어려워진 문제를 해결하기 위해 모듈화를 진행했습니다.

## 🏗️ 모듈화 구조

### 기존 구조
```
trade/
└── market_scanner.py (1338줄) ❌ 너무 큼
```

### 새로운 구조
```
trade/
├── market_scanner.py (1007줄) ✅ 25% 감소
└── scanner/
    ├── market_scanner_advanced.py ✅ 고급 스캐너 분리
    ├── advanced_pre_market_scanner.py
    ├── volume_bollinger.py
    ├── envelope_analyzer.py
    ├── pullback_detector.py
    └── __init__.py (모듈 export)
```

## 🔧 변경 사항

### 1. 고급 스캐너 기능 분리
기존 `MarketScanner`에 있던 고급 스캐너 관련 메서드들을 별도 모듈로 분리:

**분리된 메서드들:**
- `scan_market_pre_open_advanced()` → `MarketScannerAdvanced`
- `_get_advanced_scanner()` → 내부 구현
- `_collect_stocks_data_for_advanced_scan()` → 내부 구현
- `_convert_ohlcv_to_advanced_format()` → 내부 구현
- `_save_advanced_scan_results()` → 내부 구현
- `_select_top_stocks_from_advanced_results()` → 위임 방식
- `_select_stocks_from_combined_results()` → 위임 방식

### 2. 위임 패턴 적용
기존 코드에 영향을 주지 않으면서 모듈화를 달성:

```python
# 기존 방식 (직접 구현)
def scan_market_pre_open_advanced(self):
    # 100줄의 구현 코드...
    pass

# 새로운 방식 (모듈 위임)
def scan_market_pre_open_advanced(self):
    advanced_module = self._get_advanced_scanner_module()
    if not advanced_module:
        return []
    return advanced_module.scan_market_pre_open_advanced()
```

### 3. 지연 로딩 (Lazy Loading)
고급 스캐너 모듈은 필요할 때만 로드:

```python
# 초기화 시 모듈 로드하지 않음
self._advanced_scanner_module = None

# 첫 사용 시에만 로드
def _get_advanced_scanner_module(self):
    if self._advanced_scanner_module is None:
        # 모듈 초기화
    return self._advanced_scanner_module
```

## 🚀 사용 방법

### 기존 코드 호환성
**기존 코드는 그대로 작동합니다:**

```python
# 기존 방식 (변경 없음)
market_scanner = MarketScanner(stock_manager, websocket_manager)
results = market_scanner.scan_market_pre_open_advanced()
success = market_scanner.run_pre_market_scan(use_advanced_scanner=True)
```

### 새로운 모듈 직접 사용
```python
# 고급 스캐너 모듈 직접 사용
from trade.scanner.market_scanner_advanced import MarketScannerAdvanced

advanced_scanner = MarketScannerAdvanced(stock_manager, websocket_manager)
advanced_scanner.set_config(strategy_config, performance_config)
results = advanced_scanner.scan_market_pre_open_advanced()
```

### 개별 모듈 사용
```python
# 개별 분석 모듈 사용
from trade.scanner import (
    calculate_volume_bollinger_bands,
    calculate_envelope,
    detect_pullback_pattern
)

vol_bands = calculate_volume_bollinger_bands(volumes)
envelope = calculate_envelope(prices)
pullback = detect_pullback_pattern(opens, highs, lows, closes, volumes)
```

## 📁 파일별 역할

### `market_scanner.py` (메인)
- 기존 장전 스캔 로직 유지
- 고급 스캐너 모듈 위임
- 웹소켓 및 데이터베이스 연동

### `market_scanner_advanced.py` (확장)
- 고급 스캐너 전용 로직
- 데이터 수집 및 변환
- 결과 처리 및 저장

### 분석 모듈들
- `volume_bollinger.py` - 거래량 볼린저밴드
- `envelope_analyzer.py` - 엔벨로프 및 신고가 분석
- `pullback_detector.py` - 눌림목 패턴 감지
- `advanced_pre_market_scanner.py` - 통합 스캐너

## ✅ 장점

### 1. 유지보수성 향상
- 파일 크기 25% 감소 (1338 → 1007줄)
- 기능별 명확한 분리
- 각 모듈의 책임 분명

### 2. 기존 코드 호환성
- 기존 API 변경 없음
- 기존 설정 그대로 사용
- 점진적 마이그레이션 가능

### 3. 확장성
- 새로운 스캐너 추가 용이
- 개별 모듈 테스트 가능
- 선택적 기능 로딩

### 4. 성능
- 지연 로딩으로 메모리 절약
- 필요한 모듈만 로드
- 빠른 초기화

## 🔧 설정 방법

### `trading_config.ini` 설정
```ini
[TRADING_STRATEGY]
# 기존 설정들...

# 스캐너 선택
use_advanced_scanner = true      # 고급 스캐너 사용
use_combined_scanner = false     # 통합(기존+고급) 사용
```

### 프로그래밍 방식
```python
# TradeManager에서 자동 감지
trade_manager.run_pre_market_process()

# MarketScanner에서 직접 호출
market_scanner.run_pre_market_scan(use_advanced_scanner=True)
market_scanner.run_pre_market_scan_combined()
```

## 🚨 마이그레이션 주의사항

### 1. Import 변경 없음
기존 import는 그대로 유지됩니다:
```python
from trade.market_scanner import MarketScanner  # 변경 없음
```

### 2. 메서드 시그니처 유지
모든 public 메서드는 동일한 인터페이스를 유지합니다.

### 3. 설정 호환성
기존 설정 파일은 그대로 사용 가능합니다.

### 4. 성능 영향 없음
모듈화로 인한 성능 저하는 없습니다.

## 🔍 트러블슈팅

### 고급 스캐너 모듈 로드 실패
```python
# 로그에서 확인
logger.warning("고급 스캐너 모듈을 사용할 수 없습니다")

# 해결방법
1. scanner/ 디렉토리 확인
2. __init__.py 파일 존재 확인
3. 모듈 import 오류 확인
```

### 기존 기능 영향 없음 확인
```python
# 기존 스캐너는 항상 작동
results = market_scanner.scan_market_pre_open()  # ✅ 정상 작동

# 고급 스캐너 실패시 기존 스캐너 사용
if not advanced_results:
    traditional_results = market_scanner.scan_market_pre_open()
```

이제 `market_scanner.py`가 훨씬 관리하기 쉬워졌으며, 기존 코드에 전혀 영향을 주지 않으면서 새로운 고급 기능들을 모듈화했습니다!