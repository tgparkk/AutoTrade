# 🔍 AutoTrade 디버깅 환경

VS Code에서 F5를 눌러 디버깅할 수 있는 환경이 구축되어 있습니다.

## 🚀 사용 방법

### 1. VS Code에서 F5로 디버깅 시작

1. VS Code에서 프로젝트 열기
2. 원하는 디버그 스크립트 선택
3. **F5** 키 누르기
4. 실행할 구성 선택

### 2. 사용 가능한 디버그 구성

| 구성 이름 | 설명 | 파일 |
|----------|------|------|
| 🤖 AutoTrade - 메인 실행 | 전체 시스템 실행 (주기적 스캔/매매) | `main.py` |
| 🔄 AutoTrade - 메인 루프 테스트 | 새로운 주기적 메인 루프 디버깅 | `debug/debug_main_loop.py` |
| 🔍 AutoTrade - 장시작전 프로세스만 | 종목 선정 과정만 테스트 | `debug/debug_pre_market.py` |
| 📊 AutoTrade - 실시간 모니터링만 | 실시간 모니터링만 테스트 | `debug/debug_realtime_monitor.py` |
| 💰 AutoTrade - 거래 실행 테스트 | 매수/매도 실행 테스트 | `debug/debug_trade_execution.py` |
| 🌐 AutoTrade - API 테스트 | API 연결 및 기능 테스트 | `debug/debug_api_test.py` |
| 📡 AutoTrade - 웹소켓 통합 테스트 | TradeManager 웹소켓 통합 확인 | `debug/debug_websocket_integration.py` |

## 🔧 브레이크포인트 설정

각 디버그 스크립트에는 주요 지점에 브레이크포인트 설정 위치가 주석으로 표시되어 있습니다:

```python
# 브레이크포인트 설정 위치 (매수 전)
# breakpoint()
```

주석을 해제하면 해당 지점에서 실행이 중단됩니다.

## 📋 디버그 스크립트 상세

### 🔄 debug_main_loop.py
- **목적**: 새로운 주기적 메인 루프 테스트
- **기능**:
  - 매일 자동 시장 스캔 (08:00~09:00)
  - 장시간 실시간 모니터링 (09:00~15:30)  
  - 장마감 후 정리 및 리포트
  - 시간별 헬스 체크 및 상태 모니터링
  - 30초 시뮬레이션으로 전체 사이클 확인

### 🔍 debug_pre_market.py
- **목적**: 장시작전 프로세스 테스트
- **기능**: 
  - 시장 스캔 및 종목 선정
  - 선정된 종목 정보 출력
  - 종목 점수 계산 과정 확인

### 📊 debug_realtime_monitor.py  
- **목적**: 실시간 모니터링 테스트
- **기능**:
  - 테스트 종목 자동 생성
  - 30초 동안 모니터링 실행
  - 5초마다 상태 정보 출력
  - 매수/매도 신호 감지 확인

### 💰 debug_trade_execution.py
- **목적**: 거래 실행 과정 테스트
- **기능**:
  - 매수 주문 → 체결 → 매도 조건 확인 → 매도 실행
  - 전체 거래 과정을 단계별로 확인
  - 손익 계산 및 통계 확인

### 🌐 debug_api_test.py
- **목적**: API 연결 및 설정 확인
- **기능**:
  - API 모듈 import 테스트
  - 설정 파일 확인
  - 네트워크 연결 테스트
  - 함수 존재 여부 확인

### 📡 debug_websocket_integration.py
- **목적**: TradeManager에서 웹소켓 통합 테스트
- **기능**:
  - 웹소켓 매니저 초기화 확인
  - 모니터링 시작 시 자동 웹소켓 연결
  - 선정 종목 자동 구독
  - 웹소켓 상태 실시간 모니터링
  - 모니터링 중지 시 웹소켓 정리

## 🎯 디버깅 팁

### 1. 단계별 디버깅
```python
# 특정 지점에서 중단하고 싶을 때
breakpoint()

# 조건부 브레이크포인트
if some_condition:
    breakpoint()
```

### 2. 변수 상태 확인
디버깅 중 REPL에서 변수 값을 확인할 수 있습니다:
```python
# 현재 position 상태 확인
print(position.status)
print(position.__dict__)

# 거래 통계 확인  
print(trade_manager.trade_executor.get_trade_statistics())
```

### 3. 로그 확인
- **콘솔**: 실시간 로그 출력
- **파일**: `logs/` 폴더에 저장

### 4. 설정 확인
디버깅 전에 다음 설정을 확인하세요:
- `config/key.ini`: API 키 설정
- `config/trading_config.ini`: 거래 설정
- `data/stock_list.json`: 종목 데이터

## ⚙️ VS Code 설정

### launch.json
```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "🤖 AutoTrade - 메인 실행 (간소화)",
            "type": "python",
            "request": "launch",
            "program": "${workspaceFolder}/main.py",
            "console": "integratedTerminal",
            "justMyCode": false
        }
    ]
}
```

### settings.json
- Python 인터프리터: `.venv/Scripts/python.exe`
- 자동 import 정리 
- 코드 포맷팅 (Black)
- 린팅 (flake8)

## 🔄 최근 업데이트

### ✨ 웹소켓 매니저 통합 (v2.2)  
- **웹소켓 중앙 관리**: TradeManager에서 웹소켓 생명주기 관리
  - 모니터링 시작 시 자동 웹소켓 연결
  - 선정 종목 자동 구독/해제
  - 실시간 체결 데이터 수신
  - 시스템 종료 시 웹소켓 정리
  - 웹소켓 상태 통합 모니터링

### ✨ 주기적 메인 루프 구조 (v2.1)
- **새로운 메인 루프**: 며칠간 계속 실행 가능한 구조
  - 매일 자동 시장 스캔 (08:00~09:00)
  - 장시간 실시간 모니터링 (09:00~15:30)
  - 장마감 후 정리 및 일일 리포트
  - 시간별/일별 자동 스케줄링
  - 메모리 및 성능 모니터링

### ✨ main.py 간소화 (v2.0)
- **main.py**: TradeManager 실행만 담당하도록 간소화
- **TradeManager**: 모든 주식 관련 비즈니스 로직을 담당
  - 시장 시간 판단
  - 자동 스케줄링 (장시작전/장시작/장마감)
  - 텔레그램 봇 관리
  - 시스템 상태 모니터링

### 📁 코드 구조
```
main.py (간소화)
 └── TradeManager (모든 비즈니스 로직)
     ├── StockManager (종목 관리)
     ├── MarketScanner (시장 스캔)
     ├── RealTimeMonitor (실시간 모니터링)
     ├── TradeExecutor (거래 실행)
     ├── KISWebSocketManager (웹소켓 관리) ⭐ 새로 추가
     └── TelegramBot (텔레그램 봇)
```

## 🚫 주의사항

1. **실제 거래 주의**: 
   - 테스트 환경에서는 실제 주문이 실행되지 않습니다
   - 실제 운영 시에는 충분한 테스트 후 사용하세요

2. **API 키 보안**:
   - API 키는 절대 코드에 하드코딩하지 마세요
   - `config/key.ini` 파일은 .gitignore에 포함되어야 합니다

3. **성능**:
   - 디버깅 모드에서는 성능이 느려집니다
   - 실제 운영 시에는 브레이크포인트를 모두 제거하세요

4. **새로운 구조**:
   - main.py는 이제 단순한 진입점 역할만 합니다
   - 모든 주식 로직은 TradeManager에서 처리됩니다

## 🆘 문제 해결

### 자주 발생하는 오류

1. **ModuleNotFoundError**: 
   ```bash
   pip install -r requirements.txt
   ```

2. **API 설정 오류**:
   - `config/key.ini` 파일 확인
   - API 키 유효성 확인

3. **경로 오류**:
   - VS Code에서 프로젝트 루트 폴더 열기
   - `PYTHONPATH` 설정 확인

### 도움이 필요할 때

1. 로그 파일 확인: `logs/` 폴더
2. 디버그 콘솔에서 변수 상태 확인
3. API 테스트 스크립트로 연결 상태 확인

---

**Happy Debugging! 🐛🔍** 