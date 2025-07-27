"""
고급 장전 스캐너 사용 예시

이 파일은 새로운 고급 스캐너의 사용법을 보여주는 예시입니다.
실제 trading_config.ini 파일에 설정을 추가하여 사용할 수 있습니다.
"""

# ===== trading_config.ini 설정 예시 =====
"""
[TRADING_STRATEGY]
# 기존 설정들...

# 🆕 고급 스캐너 설정
use_advanced_scanner = true          # 고급 스캐너 사용 여부
use_combined_scanner = false         # 통합 스캐너(기존+고급) 사용 여부

# 고급 스캐너 파라미터
advanced_volume_surge_threshold = 3.0    # 거래량 급증 임계값 (기본 3배)
advanced_min_trading_value = 5000        # 최소 거래대금 (50억원)
advanced_pullback_threshold = 0.02       # 눌림목 임계값 (2%)
advanced_max_gap_up = 0.07              # 최대 갭상승 (7%)
advanced_max_prev_gain = 0.10           # 전일 최대 상승폭 (10%)
advanced_min_intraday_gain = 0.03       # 최소 당일 상승폭 (3%)
advanced_early_surge_limit = 0.20       # 장초반 급등 제외 기준 (20%)

# 점수 가중치 설정
advanced_volume_weight = 0.25           # 거래량 가중치
advanced_envelope_weight = 0.25         # 엔벨로프 가중치  
advanced_pullback_weight = 0.30         # 눌림목 가중치
advanced_momentum_weight = 0.20         # 모멘텀 가중치
"""

# ===== 프로그래밍 방식 사용 예시 =====

from trade.scanner.advanced_pre_market_scanner import AdvancedPreMarketScanner
from trade.market_scanner import MarketScanner

def example_direct_usage():
    """직접 고급 스캐너 사용 예시"""
    
    # 1. 고급 스캐너 직접 초기화
    scanner_config = {
        'volume_surge_threshold': 3.0,
        'min_trading_value': 5000,
        'pullback_threshold': 0.02,
        'max_gap_up': 0.07,
        'max_prev_gain': 0.10,
        'min_intraday_gain': 0.03,
        'early_surge_limit': 0.20,
        'volume_weight': 0.25,
        'envelope_weight': 0.25,
        'pullback_weight': 0.30,
        'momentum_weight': 0.20
    }
    
    advanced_scanner = AdvancedPreMarketScanner(scanner_config)
    
    # 2. 종목 데이터 준비 (실제로는 API에서 수집)
    sample_stock_data = {
        '005930': {  # 삼성전자 예시
            'closes': [70000, 69500, 69000, 68500, 68000],  # 최신순
            'opens': [69800, 69200, 68800, 68200, 67800],
            'highs': [70200, 69800, 69300, 68800, 68300],
            'lows': [69500, 69000, 68500, 68000, 67500],
            'volumes': [25000000, 18000000, 15000000, 12000000, 10000000]
        }
    }
    
    # 3. 개별 종목 분석
    result = advanced_scanner.analyze_stock('005930', sample_stock_data['005930'])
    if result:
        print(f"분석 결과: 점수 {result['final_score']:.1f}")
        print(f"진입 신호: {result['entry_signal']['signal']}")
        print(f"리스크 레벨: {result['risk_level']}")
    
    # 4. 복수 종목 스캔
    scan_results = advanced_scanner.scan_multiple_stocks(sample_stock_data)
    top_candidates = advanced_scanner.get_top_candidates(scan_results, top_n=5, min_score=70)
    
    print(f"상위 후보: {len(top_candidates)}개")
    for candidate in top_candidates:
        print(f"- {candidate['stock_code']}: {candidate['final_score']:.1f}점")


def example_market_scanner_integration():
    """MarketScanner를 통한 통합 사용 예시"""
    
    # MarketScanner 인스턴스가 있다고 가정
    # market_scanner = MarketScanner(stock_manager, websocket_manager)
    
    # 방법 1: 고급 스캐너만 사용
    # results = market_scanner.scan_market_pre_open_advanced()
    
    # 방법 2: 기존 + 고급 스캐너 통합 사용
    # traditional_results, advanced_results = market_scanner.run_combined_pre_market_scan()
    
    # 방법 3: 설정에 따른 자동 선택
    # success = market_scanner.run_pre_market_scan(use_advanced_scanner=True)
    
    print("MarketScanner 통합 사용 완료")


def example_config_based_usage():
    """설정 파일 기반 사용 예시"""
    
    # trading_config.ini에 다음 설정 추가:
    # use_advanced_scanner = true
    # use_combined_scanner = false
    
    # TradeManager에서 자동으로 감지하여 사용
    # trade_manager.run_pre_market_process()
    
    print("설정 기반 사용 - TradeManager가 자동으로 고급 스캐너 사용")


if __name__ == "__main__":
    print("=== 고급 장전 스캐너 사용 예시 ===")
    
    print("\n1. 직접 사용 예시:")
    example_direct_usage()
    
    print("\n2. MarketScanner 통합 사용:")
    example_market_scanner_integration()
    
    print("\n3. 설정 기반 사용:")
    example_config_based_usage()
    
    print("\n=== 사용 예시 완료 ===")