# AutoTrade - 한국투자증권 KIS API 자동매매 시스템

## 📊 새로운 기능: 데이터베이스 저장 및 분석

### 🎯 데이터베이스 기능 개요
파라미터 조정, 머신러닝, 거래기록 분석을 위한 체계적인 데이터베이스 저장 기능이 추가되었습니다.

### 📋 데이터베이스 테이블 구조

#### 1. **장전 스캔 결과** (`pre_market_scans`)
```sql
- 스캔 날짜/시간, 종목코드, 종목명
- 선정 점수, 선정 기준 (JSON)
- 기술적 지표: RSI, MACD, 이동평균
- 패턴 분석: 점수, 감지된 패턴명
- 거래량, 시가총액 등 기본 정보
```

#### 2. **장중 스캔 결과** (`intraday_scans`)
```sql
- 스캔 날짜/시간, 종목코드, 종목명
- 선정 점수, 선정 기준 (급등, 이격도 등)
- 실시간 가격 정보
- 스캔 사유 (volume_surge, disparity 등)
```

#### 3. **매수 주문** (`buy_orders`)
```sql
- 주문 정보: 날짜, 시간, 종목, 가격, 수량
- 주문 상태: ordered, executed, failed
- 선정 기준, 시장 단계
- 장전 스캔 연결 (pre_scan_id)
```

#### 4. **매도 주문** (`sell_orders`)
```sql
- 주문 정보: 날짜, 시간, 종목, 가격, 수량
- 손익 정보: 금액, 수익률, 보유시간
- 매도 사유: 익절, 손절, 보유기간초과 등
- 매수 주문 연결 (buy_order_id)
```

### 🔧 주요 기능

#### 자동 데이터 수집
- **장전 스캔**: 종목 선정 시 자동으로 기술적 지표와 함께 저장
- **매수 주문**: 주문 실행 시 선정 기준, 시장 상황 등 저장
- **매도 주문**: 매도 시 손익, 보유시간, 매도 사유 등 저장

#### 분석 및 조회 기능
- **일일 요약**: 당일 거래 통계, 손익, 승률
- **성과 분석**: 기간별 거래 성과, 최대 수익/손실
- **패턴 분석**: 매도 사유별, 시간대별 성과 분석

### 📱 텔레그램 봇 명령어

새로운 데이터베이스 조회 명령어들이 추가되었습니다:

```
📊 데이터베이스 조회
/db_summary - 일일 거래 요약
/db_today - 오늘 거래 현황  
/db_performance - 성과 분석
/db_scans - 스캔 결과 조회
```

### 🧪 테스트 방법

데이터베이스 기능을 테스트하려면:

```bash
python test_database.py
```

### 💡 활용 방안

#### 1. **파라미터 최적화**
- 선정 기준별 성과 분석
- 손절/익절 비율 최적화
- 시간대별 매매 효율성 분석

#### 2. **머신러닝 준비**
- 기술적 지표와 수익률 상관관계 분석
- 패턴별 성공률 학습 데이터
- 시장 상황별 전략 효과 분석

#### 3. **성과 추적**
- 일/주/월별 성과 리포트
- 전략별 수익률 비교
- 리스크 관리 효과 측정

### 📁 파일 구조

```
database/
├── __init__.py              # 패키지 초기화
├── trade_database.py        # 메인 데이터베이스 클래스
└── migrations/              # 향후 스키마 변경용

data/
├── trading.db              # 실제 거래 데이터베이스
└── test_trading.db         # 테스트용 데이터베이스
```

### 🔄 기존 시스템과의 통합

데이터베이스 기능은 기존 시스템에 자동으로 통합됩니다:

- **TradeExecutor**: 매수/매도 주문 시 자동 저장
- **MarketScanner**: 장전 스캔 결과 자동 저장  
- **TelegramBot**: 새로운 조회 명령어 추가
- **TradeManager**: 데이터베이스 초기화 및 관리

---

## 기존 기능들

### 🚀 주요 기능 