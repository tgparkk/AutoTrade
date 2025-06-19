#!/usr/bin/env python3
"""
디버그: 거래 실행 테스트
"""

import sys
import os

# 프로젝트 루트를 Python 경로에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trade.trade_manager import TradeManager
from models.position import PositionStatus
from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)


def debug_trade_execution():
    """거래 실행 디버깅"""
    print("=" * 60)
    print("💰 거래 실행 디버깅")
    print("=" * 60)
    
    try:
        # TradeManager 초기화
        logger.info("TradeManager 초기화 중...")
        trade_manager = TradeManager()
        logger.info("✅ TradeManager 초기화 완료")
        
        # 테스트용 종목 추가
        print("📋 테스트용 종목 추가 중...")
        test_stock = {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "price": 75000
        }
        
        success = trade_manager.stock_manager.add_selected_stock(
            stock_code=test_stock["stock_code"],
            stock_name=test_stock["stock_name"],
            open_price=test_stock["price"],
            high_price=test_stock["price"] * 1.02,
            low_price=test_stock["price"] * 0.98,
            close_price=test_stock["price"],
            volume=100000,
            selection_score=85.0
        )
        
        if not success:
            print("❌ 테스트 종목 추가 실패")
            return False
        
        print(f"✅ {test_stock['stock_code']}[{test_stock['stock_name']}] 추가 완료")
        
        # 포지션 가져오기
        position = trade_manager.stock_manager.get_selected_stock(test_stock["stock_code"])
        if not position:
            print("❌ 포지션을 찾을 수 없습니다")
            return False
        
        print(f"📊 초기 상태: {position.status.value}")
        
        # 1. 매수 주문 테스트
        print(f"\n💰 1단계: 매수 주문 실행")
        print("-" * 40)
        
        # 브레이크포인트 설정 위치 (매수 전)
        # breakpoint()
        
        buy_price = test_stock["price"]
        buy_quantity = 10
        
        buy_success = trade_manager.trade_executor.execute_buy_order(
            position=position,
            price=buy_price,
            quantity=buy_quantity,
            current_positions_count=0
        )
        
        if buy_success:
            print(f"✅ 매수 주문 성공: {buy_quantity}주 @{buy_price:,}원")
            print(f"   손절가: {position.stop_loss_price:,.0f}원")
            print(f"   익절가: {position.target_price:,.0f}원")
            print(f"   상태: {position.status.value}")
        else:
            print("❌ 매수 주문 실패")
            return False
        
        # 2. 매수 체결 확인 테스트
        print(f"\n💰 2단계: 매수 체결 확인")
        print("-" * 40)
        
        executed_price = buy_price + 100  # 100원 높게 체결됐다고 가정
        
        confirm_success = trade_manager.trade_executor.confirm_buy_execution(
            position=position,
            executed_price=executed_price
        )
        
        if confirm_success:
            print(f"✅ 매수 체결 확인: @{executed_price:,}원")
            print(f"   실제 손절가: {position.stop_loss_price:,.0f}원")
            print(f"   실제 익절가: {position.target_price:,.0f}원")
            print(f"   상태: {position.status.value}")
        else:
            print("❌ 매수 체결 확인 실패")
            return False
        
        # 3. 현재가 업데이트 (가상)
        print(f"\n💰 3단계: 현재가 업데이트")
        print("-" * 40)
        
        current_price = executed_price + 500  # 500원 상승
        trade_manager.stock_manager.update_stock_price(
            stock_code=test_stock["stock_code"],
            current_price=current_price
        )
        
        # 미실현 손익 계산
        if position.buy_price and position.buy_quantity:
            unrealized_pnl = (current_price - position.buy_price) * position.buy_quantity
        else:
            unrealized_pnl = 0
        
        print(f"📈 현재가: {current_price:,}원 (매수가 대비 +{current_price - position.buy_price:,}원)")
        print(f"💰 미실현 손익: {unrealized_pnl:+,.0f}원")
        
        # 4. 매도 조건 확인
        print(f"\n💰 4단계: 매도 조건 확인")
        print("-" * 40)
        
        # 브레이크포인트 설정 위치 (매도 조건 확인 전)
        # breakpoint()
        
        sell_positions = trade_manager.trade_executor.get_positions_to_sell(
            positions=[position],
            current_prices={test_stock["stock_code"]: current_price}
        )
        
        if sell_positions:
            sell_position, sell_reason = sell_positions[0]
            print(f"📊 매도 대상 발견: {sell_reason}")
            
            # 5. 매도 주문 실행
            print(f"\n💰 5단계: 매도 주문 실행")
            print("-" * 40)
            
            sell_success = trade_manager.trade_executor.execute_sell_order(
                position=sell_position,
                price=current_price,
                reason=sell_reason
            )
            
            if sell_success:
                print(f"✅ 매도 주문 성공: @{current_price:,}원 (사유: {sell_reason})")
                print(f"   상태: {position.status.value}")
            else:
                print("❌ 매도 주문 실패")
                return False
            
            # 6. 매도 체결 확인
            print(f"\n💰 6단계: 매도 체결 확인")
            print("-" * 40)
            
            sell_executed_price = current_price - 50  # 50원 낮게 체결
            
            sell_confirm_success = trade_manager.trade_executor.confirm_sell_execution(
                position=position,
                executed_price=sell_executed_price
            )
            
            if sell_confirm_success:
                print(f"✅ 매도 체결 확인: @{sell_executed_price:,}원")
                print(f"💰 실현 손익: {position.realized_pnl:+,.0f}원 ({position.realized_pnl_rate:+.2f}%)")
                print(f"   상태: {position.status.value}")
            else:
                print("❌ 매도 체결 확인 실패")
                return False
                
        else:
            print("📊 매도 조건에 해당하지 않음")
        
        # 7. 최종 거래 통계
        print(f"\n💰 7단계: 거래 통계 확인")
        print("-" * 40)
        
        trade_stats = trade_manager.trade_executor.get_trade_statistics()
        performance_summary = trade_manager.trade_executor.get_performance_summary()
        
        print(f"📊 거래 통계:")
        print(f"   - 총 거래: {trade_stats['total_trades']}건")
        print(f"   - 수익 거래: {trade_stats['winning_trades']}건")
        print(f"   - 손실 거래: {trade_stats['losing_trades']}건")
        print(f"   - 승률: {trade_stats['win_rate']:.1f}%")
        print(f"   - 총 손익: {trade_stats['total_pnl']:+,.0f}원")
        print(f"   - 평균 실행시간: {trade_stats['avg_execution_time']:.3f}초")
        
        print(f"\n📈 성능 요약: {performance_summary}")
        
    except Exception as e:
        logger.error(f"디버깅 중 오류: {e}")
        print(f"❌ 오류 발생: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("🎯 거래 실행 디버깅 완료")
    print("=" * 60)
    
    return True


if __name__ == "__main__":
    print("💰 거래 실행 디버깅을 시작합니다...")
    
    # 브레이크포인트 설정 위치 (시작 전)
    # breakpoint()
    
    try:
        success = debug_trade_execution()
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