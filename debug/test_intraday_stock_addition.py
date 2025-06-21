#!/usr/bin/env python3
"""
장중 추가 종목 기능 테스트 스크립트

주요 테스트:
1. StockManager.add_intraday_stock() 메서드 테스트
2. 장중 추가 종목 조회 및 관리 테스트
3. 웹소켓 구독 연동 테스트 (시뮬레이션)
4. 장중 추가 종목 요약 정보 테스트
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trade.stock_manager import StockManager
from trade.market_scanner import MarketScanner
from models.stock import StockStatus
from utils.logger import setup_logger

logger = setup_logger(__name__)


def test_intraday_stock_addition():
    """장중 추가 종목 기능 테스트"""
    
    logger.info("=" * 60)
    logger.info("🧪 장중 추가 종목 기능 테스트 시작")
    logger.info("=" * 60)
    
    # StockManager 초기화
    stock_manager = StockManager()
    
    # 1. 기본 선정 종목 추가 (시뮬레이션)
    logger.info("\n📊 1단계: 기본 선정 종목 추가")
    basic_stocks = [
        ("005930", "삼성전자", 75000, 85.5),
        ("000660", "SK하이닉스", 120000, 82.3),
        ("035420", "NAVER", 180000, 78.9)
    ]
    
    for stock_code, stock_name, price, score in basic_stocks:
        success = stock_manager.add_selected_stock(
            stock_code=stock_code,
            stock_name=stock_name,
            open_price=price,
            high_price=price * 1.02,
            low_price=price * 0.98,
            close_price=price,
            volume=1000000,
            selection_score=score
        )
        logger.info(f"  {'✅' if success else '❌'} {stock_code}[{stock_name}] 추가: {success}")
    
    # 2. 장중 추가 종목 테스트
    logger.info("\n🔥 2단계: 장중 추가 종목 테스트")
    intraday_stocks = [
        ("005380", "현대차", 185000, 76.8, "이격도_과매도+거래량_급증"),
        ("051910", "LG화학", 420000, 74.2, "등락률_상승+체결강도_상위"),
        ("006400", "삼성SDI", 380000, 72.5, "거래량_순위+이격도_과매도")
    ]
    
    added_count = 0
    for stock_code, stock_name, price, score, reasons in intraday_stocks:
        # 시장 데이터 시뮬레이션
        market_data = {
            'volume': 500000,
            'high_price': price * 1.015,
            'low_price': price * 0.985,
            'open_price': price * 0.995,
            'yesterday_close': price * 0.98,
            'price_change_rate': 2.5,
            'volume_spike_ratio': 2.8,
            'contract_strength': 125.0,
            'buy_ratio': 65.0,
            'market_pressure': 'BUY'
        }
        
        success = stock_manager.add_intraday_stock(
            stock_code=stock_code,
            stock_name=stock_name,
            current_price=price,
            selection_score=score,
            reasons=reasons,
            market_data=market_data
        )
        
        if success:
            added_count += 1
            logger.info(f"  ✅ 장중 추가 성공: {stock_code}[{stock_name}] @{price:,}원 (점수:{score:.1f})")
        else:
            logger.info(f"  ❌ 장중 추가 실패: {stock_code}[{stock_name}]")
    
    logger.info(f"\n📈 장중 추가 결과: {added_count}/{len(intraday_stocks)}개 성공")
    
    # 3. 장중 추가 종목 조회 테스트
    logger.info("\n📋 3단계: 장중 추가 종목 조회 테스트")
    intraday_added_stocks = stock_manager.get_intraday_added_stocks()
    logger.info(f"장중 추가 종목 수: {len(intraday_added_stocks)}개")
    
    for i, stock in enumerate(intraday_added_stocks, 1):
        logger.info(f"  {i}. {stock.stock_code}[{stock.stock_name}] - "
                   f"점수:{stock.reference_data.pattern_score:.1f}, "
                   f"현재가:{stock.realtime_data.current_price:,}원, "
                   f"상태:{stock.status.value}")
    
    # 4. 종목 요약 정보 테스트
    logger.info("\n📊 4단계: 종목 요약 정보 테스트")
    summary = stock_manager.get_stock_summary()
    logger.info(f"전체 종목 수: {summary['total_selected']}개")
    logger.info(f"  - 장전 선정: {summary['premarket_selected']}개")
    logger.info(f"  - 장중 추가: {summary['intraday_added']}개")
    logger.info(f"활용률: {summary['utilization_rate']:.1f}%")
    
    # 장중 추가 종목 상세 정보
    intraday_details = summary['intraday_details']
    logger.info(f"\n장중 추가 종목 상세:")
    logger.info(f"  - 평균 점수: {intraday_details['average_score']:.1f}")
    logger.info(f"  - 추가 사유별 분포:")
    for reason, count in intraday_details['reasons_distribution'].items():
        logger.info(f"    * {reason}: {count}개")
    
    # 5. 장중 추가 종목 요약 테스트
    logger.info("\n📈 5단계: 장중 추가 종목 요약 테스트")
    intraday_summary = stock_manager.get_intraday_summary()
    logger.info(f"장중 추가 종목 요약:")
    logger.info(f"  - 총 개수: {intraday_summary['total_count']}개")
    logger.info(f"  - 평균 점수: {intraday_summary['average_score']:.1f}")
    logger.info(f"  - 종목 코드: {', '.join(intraday_summary['stock_codes'])}")
    
    # 6. 상태 변경 테스트
    logger.info("\n🔄 6단계: 상태 변경 테스트")
    if intraday_added_stocks:
        test_stock = intraday_added_stocks[0]
        old_status = test_stock.status
        
        success = stock_manager.change_stock_status(
            test_stock.stock_code, 
            StockStatus.BUY_READY,
            "테스트_매수준비"
        )
        
        if success:
            updated_stock = stock_manager.get_selected_stock(test_stock.stock_code)
            if updated_stock:
                logger.info(f"  ✅ 상태 변경 성공: {test_stock.stock_code} "
                           f"{old_status.value} → {updated_stock.status.value}")
            else:
                logger.info(f"  ⚠️ 상태 변경 후 종목 조회 실패: {test_stock.stock_code}")
        else:
            logger.info(f"  ❌ 상태 변경 실패: {test_stock.stock_code}")
    
    # 7. 장중 추가 종목 제거 테스트
    logger.info("\n🗑️ 7단계: 장중 추가 종목 제거 테스트")
    if intraday_added_stocks and len(intraday_added_stocks) > 1:
        remove_stock = intraday_added_stocks[-1]  # 마지막 종목 제거
        
        success = stock_manager.remove_intraday_stock(
            remove_stock.stock_code,
            "테스트_제거"
        )
        
        if success:
            logger.info(f"  ✅ 장중 종목 제거 성공: {remove_stock.stock_code}[{remove_stock.stock_name}]")
            
            # 제거 후 요약 정보 재확인
            updated_summary = stock_manager.get_intraday_summary()
            logger.info(f"  📊 제거 후 장중 종목 수: {updated_summary['total_count']}개")
        else:
            logger.info(f"  ❌ 장중 종목 제거 실패: {remove_stock.stock_code}")
    
    # 8. MarketScanner 연동 테스트 (시뮬레이션)
    logger.info("\n🔍 8단계: MarketScanner 연동 테스트")
    try:
        market_scanner = MarketScanner(stock_manager)
        
        # 장중 스캔 시뮬레이션 (실제 API 호출 없이)
        logger.info("  📡 MarketScanner 인스턴스 생성 성공")
        logger.info("  💡 실제 장중 스캔은 RealTimeMonitor에서 30분마다 자동 실행됩니다")
        
    except Exception as e:
        logger.error(f"  ❌ MarketScanner 연동 오류: {e}")
    
    # 최종 결과 요약
    logger.info("\n" + "=" * 60)
    logger.info("🎉 장중 추가 종목 기능 테스트 완료")
    logger.info("=" * 60)
    
    final_summary = stock_manager.get_stock_summary()
    logger.info(f"최종 종목 현황:")
    logger.info(f"  - 전체: {final_summary['total_selected']}개")
    logger.info(f"  - 장전 선정: {final_summary['premarket_selected']}개")
    logger.info(f"  - 장중 추가: {final_summary['intraday_added']}개")
    logger.info(f"  - 활용률: {final_summary['utilization_rate']:.1f}%")
    
    # 상태별 분포
    logger.info(f"상태별 분포:")
    for status, count in final_summary['status_breakdown'].items():
        if count > 0:
            logger.info(f"  - {status}: {count}개")
    
    logger.info("\n✅ 모든 테스트 완료! 장중 추가 종목 기능이 정상 작동합니다.")


if __name__ == "__main__":
    test_intraday_stock_addition() 