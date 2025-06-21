#!/usr/bin/env python3
"""
StockManager와 WebSocket 시스템 연동 테스트
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import time
from datetime import datetime
from utils.logger import setup_logger

logger = setup_logger(__name__)

class WebSocketIntegrationTest:
    """StockManager와 WebSocket 연동 테스트"""
    
    def __init__(self):
        # 실행 시점에서 import하여 순환 import 문제 회피
        try:
            from trade.stock_manager import StockManager
            self.stock_manager = StockManager()
            logger.info("✅ StockManager 초기화 완료")
        except Exception as e:
            logger.error(f"❌ StockManager 초기화 실패: {e}")
            self.stock_manager = None
        
        try:
            from websocket.kis_websocket_manager import KISWebSocketManager
            self.websocket_manager = KISWebSocketManager()
            logger.info("✅ KISWebSocketManager 초기화 완료")
        except Exception as e:
            logger.error(f"❌ KISWebSocketManager 초기화 실패: {e}")
            self.websocket_manager = None
        
    def test_callback_registration(self):
        """콜백 등록 테스트"""
        logger.info("=== 콜백 등록 테스트 시작 ===")
        
        if not self.stock_manager or not self.websocket_manager:
            logger.error("❌ 매니저 초기화 실패로 테스트 건너뜀")
            return
        
        # StockManager 콜백 등록
        self.stock_manager.setup_websocket_callbacks(self.websocket_manager)
        
        # 등록된 콜백 확인
        status = self.websocket_manager.get_status()
        subscription_status = status.get('subscriptions', {})
        tr_id_callbacks = subscription_status.get('tr_id_callback_counts', {})
        
        logger.info(f"등록된 TR_ID 콜백 수: {tr_id_callbacks}")
        
        # 예상되는 콜백들이 등록되었는지 확인
        expected_tr_ids = ['H0STCNT0', 'H0STASP0', 'H0STCNI0']
        for tr_id in expected_tr_ids:
            count = tr_id_callbacks.get(tr_id, 0)
            if count > 0:
                logger.info(f"✅ {tr_id} 콜백 등록됨: {count}개")
            else:
                logger.warning(f"❌ {tr_id} 콜백 등록 안됨")
        
        logger.info("=== 콜백 등록 테스트 완료 ===\n")
        
    def test_stock_data_flow(self):
        """종목 데이터 플로우 테스트"""
        logger.info("=== 종목 데이터 플로우 테스트 시작 ===")
        
        if not self.stock_manager:
            logger.error("❌ StockManager가 없어 테스트 건너뜀")
            return
        
        # 테스트 종목 추가
        test_stock = "005930"  # 삼성전자
        success = self.stock_manager.add_selected_stock(
            stock_code=test_stock,
            stock_name="삼성전자",
            open_price=73000,
            high_price=74000,
            low_price=72000,
            close_price=73500,
            volume=1000000,
            selection_score=85.5
        )
        
        if success:
            logger.info(f"✅ 테스트 종목 추가 성공: {test_stock}")
            
            # 종목 상태 확인
            stock = self.stock_manager.get_selected_stock(test_stock)
            if stock:
                logger.info(f"종목 정보: {stock.stock_name} - {stock.realtime_data.current_price:,}원")
                logger.info(f"종목 상태: {stock.status.value}")
            else:
                logger.warning("❌ 종목 조회 실패")
        else:
            logger.warning("❌ 테스트 종목 추가 실패")
        
        logger.info("=== 종목 데이터 플로우 테스트 완료 ===\n")
        
    def test_callback_simulation(self):
        """콜백 시뮬레이션 테스트"""
        logger.info("=== 콜백 시뮬레이션 테스트 시작 ===")
        
        if not self.stock_manager:
            logger.error("❌ StockManager가 없어 테스트 건너뜀")
            return
        
        # 테스트 종목이 있는지 확인
        test_stock = "005930"
        if test_stock not in self.stock_manager.stock_metadata:
            logger.warning("테스트 종목이 없어 시뮬레이션을 건너뜁니다")
            return
        
        # 가격 데이터 시뮬레이션
        price_data = {
            'stock_code': test_stock,
            'stck_prpr': '74000',  # 현재가
            'acml_vol': '1500000',  # 누적거래량
            'prdy_vrss': '500',     # 전일대비
        }
        
        logger.info("실시간 가격 콜백 시뮬레이션...")
        self.stock_manager.handle_realtime_price('H0STCNT0', test_stock, price_data)
        
        # 업데이트된 데이터 확인
        updated_stock = self.stock_manager.get_selected_stock(test_stock)
        if updated_stock:
            logger.info(f"업데이트된 가격: {updated_stock.realtime_data.current_price:,}원")
        
        # 호가 데이터 시뮬레이션
        orderbook_data = {
            'stock_code': test_stock,
            'bidp1': '73900',  # 매수1호가
            'askp1': '74100',  # 매도1호가
            'bidp_rsqn1': '100',  # 매수1호가잔량
            'askp_rsqn1': '200',  # 매도1호가잔량
        }
        
        logger.info("실시간 호가 콜백 시뮬레이션...")
        self.stock_manager.handle_realtime_orderbook('H0STASP0', test_stock, orderbook_data)
        
        # 체결통보 시뮬레이션
        execution_data = {
            'data': {
                'mksc_shrn_iscd': test_stock,
                'exec_prce': '74000',
                'exec_qty': '100',
                'ord_gno_brno': 'BUY'
            }
        }
        
        logger.info("체결통보 콜백 시뮬레이션...")
        self.stock_manager.handle_execution_notice('H0STCNI0', execution_data)
        
        logger.info("=== 콜백 시뮬레이션 테스트 완료 ===\n")
        
    def test_subscription_manager_status(self):
        """구독 관리자 상태 테스트"""
        logger.info("=== 구독 관리자 상태 테스트 시작 ===")
        
        if not self.websocket_manager:
            logger.error("❌ WebSocketManager가 없어 테스트 건너뜀")
            return
        
        # 구독 관리자 상태 조회
        subscription_manager = self.websocket_manager.subscription_manager
        status = subscription_manager.get_status()
        
        logger.info(f"최대 구독 가능 종목 수: {status['max_stocks']}")
        logger.info(f"현재 구독 종목 수: {status['subscribed_count']}")
        logger.info(f"웹소켓 사용량: {status['websocket_usage']}")
        
        # 콜백 등록 현황
        tr_id_counts = status.get('tr_id_callback_counts', {})
        logger.info("TR_ID별 콜백 등록 현황:")
        for tr_id, count in tr_id_counts.items():
            logger.info(f"  {tr_id}: {count}개")
        
        global_counts = status.get('global_callback_counts', {})
        logger.info("데이터 타입별 글로벌 콜백 등록 현황:")
        for data_type, count in global_counts.items():
            logger.info(f"  {data_type}: {count}개")
        
        logger.info("=== 구독 관리자 상태 테스트 완료 ===\n")
        
    def run_all_tests(self):
        """모든 테스트 실행"""
        logger.info("🚀 StockManager-WebSocket 연동 테스트 시작")
        logger.info("=" * 60)
        
        try:
            # 1. 콜백 등록 테스트
            self.test_callback_registration()
            
            # 2. 종목 데이터 플로우 테스트
            self.test_stock_data_flow()
            
            # 3. 콜백 시뮬레이션 테스트
            self.test_callback_simulation()
            
            # 4. 구독 관리자 상태 테스트
            self.test_subscription_manager_status()
            
            logger.info("✅ 모든 테스트 완료!")
            
        except Exception as e:
            logger.error(f"❌ 테스트 중 오류 발생: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            # 정리
            if self.stock_manager:
                self.stock_manager.clear_all_stocks()
                logger.info("🧹 테스트 환경 정리 완료")

def main():
    """메인 함수"""
    test = WebSocketIntegrationTest()
    test.run_all_tests()

if __name__ == "__main__":
    main() 