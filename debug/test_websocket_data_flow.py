#!/usr/bin/env python3
"""
웹소켓 데이터 흐름 실제 테스트 스크립트

내일 실제 운영에서 웹소켓 데이터 수신과 매매 로직이 제대로 작동할지 검증하는 스크립트입니다.
"""

import sys
import os
import time
import threading
from datetime import datetime
from typing import Dict, List

# 상위 디렉토리 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import setup_logger
from utils.korean_time import now_kst
from trade.stock_manager import StockManager
from trade.trade_executor import TradeExecutor
from trade.realtime_monitor import RealTimeMonitor

logger = setup_logger(__name__)


class WebSocketDataFlowTest:
    """웹소켓 데이터 흐름 실제 테스트"""
    
    def __init__(self):
        """테스트 초기화"""
        logger.info("=" * 60)
        logger.info("🔍 웹소켓 데이터 흐름 실제 테스트 시작")
        logger.info("=" * 60)
        
        # 컴포넌트 초기화
        self.stock_manager = StockManager()
        self.trade_executor = TradeExecutor()
        self.realtime_monitor = RealTimeMonitor(self.stock_manager, self.trade_executor)
        
        # 테스트 대상 종목 (삼성전자)
        self.test_stock_code = "005930"
        self.test_stock_name = "삼성전자"
        
        # 데이터 수신 통계
        self.price_data_received = 0
        self.orderbook_data_received = 0
        self.callback_call_count = 0
        self.last_received_time = None
        
        # 수신된 데이터 저장
        self.received_price_data = []
        self.received_orderbook_data = []
        
        # 테스트 시작 시간
        self.test_start_time = now_kst()
        
    def setup_test_environment(self):
        """테스트 환경 설정"""
        logger.info("🔧 테스트 환경 설정 중...")
        
        try:
            # 1. 테스트 종목 추가
            logger.info(f"📋 테스트 종목 추가: {self.test_stock_code}[{self.test_stock_name}]")
            success = self.stock_manager.add_selected_stock(
                stock_code=self.test_stock_code,
                stock_name=self.test_stock_name,
                open_price=74000.0,
                high_price=74500.0,
                low_price=73500.0,
                close_price=74000.0,
                volume=1000000,
                selection_score=0.0
            )
            
            if not success:
                logger.error("❌ 테스트 종목 추가 실패")
                return False
                
            # 2. 웹소켓 매니저 연결
            websocket_manager = getattr(self.stock_manager, 'websocket_manager', None)
            if not websocket_manager:
                logger.error("❌ 웹소켓 매니저가 없습니다")
                return False
                
            # 3. 웹소켓 연결
            logger.info("🔌 웹소켓 연결 시도...")
            connected = websocket_manager.connect()
            if not connected:
                logger.error("❌ 웹소켓 연결 실패")
                return False
                
            logger.info("✅ 웹소켓 연결 성공")
            
            # 4. 콜백 오버라이드 (데이터 수신 모니터링용)
            self._setup_monitoring_callbacks()
            
            # 5. 테스트 종목 구독
            logger.info(f"📡 테스트 종목 구독: {self.test_stock_code}")
            subscribed = websocket_manager.subscribe_stock_sync(self.test_stock_code)
            if not subscribed:
                logger.warning("⚠️ 종목 구독 실패, 하지만 테스트 계속 진행")
                
            logger.info("✅ 테스트 환경 설정 완료")
            return True
            
        except Exception as e:
            logger.error(f"❌ 테스트 환경 설정 실패: {e}")
            return False
    
    def _setup_monitoring_callbacks(self):
        """데이터 수신 모니터링을 위한 콜백 오버라이드"""
        logger.info("🔍 데이터 수신 모니터링 콜백 설정...")
        
        # 원본 메서드 백업
        self.original_handle_realtime_price = self.stock_manager.handle_realtime_price
        self.original_handle_realtime_orderbook = self.stock_manager.handle_realtime_orderbook
        
        # 모니터링 콜백으로 오버라이드
        self.stock_manager.handle_realtime_price = self._monitored_handle_realtime_price
        self.stock_manager.handle_realtime_orderbook = self._monitored_handle_realtime_orderbook
        
        logger.info("✅ 콜백 모니터링 설정 완료")
    
    def _monitored_handle_realtime_price(self, data_type: str, stock_code: str, data: Dict):
        """실시간 가격 데이터 처리 모니터링"""
        self.callback_call_count += 1
        self.last_received_time = now_kst()
        
        # 통계 업데이트
        if stock_code == self.test_stock_code:
            self.price_data_received += 1
            
            # 중요 데이터 저장
            important_data = {
                'timestamp': self.last_received_time.strftime('%H:%M:%S.%f')[:-3],
                'current_price': data.get('current_price', 0),
                'volume': data.get('acc_volume', 0),
                'contract_strength': data.get('contract_strength', 100.0),
                'buy_ratio': data.get('buy_ratio', 50.0),
                'trading_halt': data.get('trading_halt', False)
            }
            self.received_price_data.append(important_data)
            
            # 실시간 로그 (첫 5개만)
            if self.price_data_received <= 5:
                logger.info(f"📈 가격데이터 수신 #{self.price_data_received}: {stock_code} "
                           f"가격={important_data['current_price']:,}원 "
                           f"체결강도={important_data['contract_strength']:.1f} "
                           f"매수비율={important_data['buy_ratio']:.1f}%")
        
        # 원본 메서드 호출
        try:
            result = self.original_handle_realtime_price(data_type, stock_code, data)
            return result
        except Exception as e:
            logger.error(f"❌ 원본 handle_realtime_price 호출 오류: {e}")
    
    def _monitored_handle_realtime_orderbook(self, data_type: str, stock_code: str, data: Dict):
        """실시간 호가 데이터 처리 모니터링"""
        self.callback_call_count += 1
        self.last_received_time = now_kst()
        
        # 통계 업데이트
        if stock_code == self.test_stock_code:
            self.orderbook_data_received += 1
            
            # 중요 데이터 저장
            important_data = {
                'timestamp': self.last_received_time.strftime('%H:%M:%S.%f')[:-3],
                'bid_price1': data.get('bid_price1', 0),
                'ask_price1': data.get('ask_price1', 0),
                'bid_qty1': data.get('bid_qty1', 0),
                'ask_qty1': data.get('ask_qty1', 0),
                'total_bid_qty': data.get('total_bid_qty', 0),
                'total_ask_qty': data.get('total_ask_qty', 0)
            }
            self.received_orderbook_data.append(important_data)
            
            # 실시간 로그 (첫 3개만)
            if self.orderbook_data_received <= 3:
                logger.info(f"📊 호가데이터 수신 #{self.orderbook_data_received}: {stock_code} "
                           f"매수1={important_data['bid_price1']:,}원({important_data['bid_qty1']:,}주) "
                           f"매도1={important_data['ask_price1']:,}원({important_data['ask_qty1']:,}주)")
        
        # 원본 메서드 호출
        try:
            result = self.original_handle_realtime_orderbook(data_type, stock_code, data)
            return result
        except Exception as e:
            logger.error(f"❌ 원본 handle_realtime_orderbook 호출 오류: {e}")
    
    def run_data_reception_test(self, duration_seconds: int = 60):
        """데이터 수신 테스트 실행"""
        logger.info(f"🕐 데이터 수신 테스트 시작 (지속시간: {duration_seconds}초)")
        
        start_time = now_kst()
        
        # 상태 모니터링 스레드 시작
        monitoring_thread = threading.Thread(
            target=self._periodic_status_check,
            args=(duration_seconds,),
            daemon=True
        )
        monitoring_thread.start()
        
        # 지정된 시간만큼 대기
        time.sleep(duration_seconds)
        
        end_time = now_kst()
        test_duration = (end_time - start_time).total_seconds()
        
        logger.info(f"⏰ 데이터 수신 테스트 완료 (실제 지속시간: {test_duration:.1f}초)")
        
        # 결과 분석
        self._analyze_results(test_duration)
    
    def _periodic_status_check(self, total_duration: int):
        """주기적 상태 체크 (10초마다)"""
        check_interval = 10
        checks_done = 0
        total_checks = total_duration // check_interval
        
        while checks_done < total_checks:
            time.sleep(check_interval)
            checks_done += 1
            
            # 웹소켓 상태 체크
            websocket_manager = getattr(self.stock_manager, 'websocket_manager', None)
            if websocket_manager:
                status = websocket_manager.get_status_summary()
                
                logger.info(f"📊 중간점검 {checks_done}/{total_checks}: "
                           f"연결={status.get('connected', False)} "
                           f"건강={status.get('healthy', False)} "
                           f"가격데이터={self.price_data_received}건 "
                           f"호가데이터={self.orderbook_data_received}건")
    
    def _analyze_results(self, test_duration: float):
        """테스트 결과 분석"""
        logger.info("=" * 60)
        logger.info("📊 웹소켓 데이터 흐름 테스트 결과 분석")
        logger.info("=" * 60)
        
        # 기본 통계
        logger.info(f"🕐 테스트 지속시간: {test_duration:.1f}초")
        logger.info(f"📈 가격 데이터 수신: {self.price_data_received}건")
        logger.info(f"📊 호가 데이터 수신: {self.orderbook_data_received}건")
        logger.info(f"🔄 총 콜백 호출: {self.callback_call_count}회")
        
        if self.last_received_time:
            time_since_last = (now_kst() - self.last_received_time).total_seconds()
            logger.info(f"⏰ 마지막 데이터 수신: {time_since_last:.1f}초 전")
        else:
            logger.warning("⚠️ 데이터를 한 번도 수신하지 못했습니다")
        
        # 수신율 계산
        expected_price_per_second = 1  # 초당 1회 정도 예상
        expected_orderbook_per_second = 2  # 초당 2회 정도 예상
        
        expected_price_total = test_duration * expected_price_per_second
        expected_orderbook_total = test_duration * expected_orderbook_per_second
        
        price_reception_rate = (self.price_data_received / expected_price_total) * 100 if expected_price_total > 0 else 0
        orderbook_reception_rate = (self.orderbook_data_received / expected_orderbook_total) * 100 if expected_orderbook_total > 0 else 0
        
        logger.info(f"📈 가격 데이터 수신율: {price_reception_rate:.1f}% ({self.price_data_received}/{expected_price_total:.0f})")
        logger.info(f"📊 호가 데이터 수신율: {orderbook_reception_rate:.1f}% ({self.orderbook_data_received}/{expected_orderbook_total:.0f})")
        
        # 웹소켓 상태 최종 체크
        websocket_manager = getattr(self.stock_manager, 'websocket_manager', None)
        if websocket_manager:
            final_status = websocket_manager.get_status_summary()
            logger.info(f"🔌 최종 웹소켓 상태: 연결={final_status.get('connected', False)} "
                       f"건강={final_status.get('healthy', False)} "
                       f"구독={final_status.get('subscribed_stocks', 0)}개")
        
        # 실시간 데이터 활용 테스트
        self._test_realtime_data_usage()
        
        # 결론 및 권장사항
        self._provide_recommendations()
    
    def _test_realtime_data_usage(self):
        """실시간 데이터가 매매 로직에 제대로 활용되는지 테스트"""
        logger.info("🔍 실시간 데이터 활용 테스트...")
        
        try:
            # 테스트 종목의 현재 상태 확인
            test_stock = self.stock_manager.get_selected_stock(self.test_stock_code)
            if not test_stock:
                logger.error("❌ 테스트 종목을 찾을 수 없습니다")
                return
            
            # RealtimeData 상태 확인
            realtime = test_stock.realtime_data
            logger.info(f"💾 RealtimeData 상태: 현재가={realtime.current_price:,}원 "
                       f"체결강도={realtime.contract_strength:.1f} "
                       f"매수비율={realtime.buy_ratio:.1f}% "
                       f"최종업데이트={realtime.last_updated.strftime('%H:%M:%S') if realtime.last_updated else 'None'}")
            
            # RealTimeMonitor의 데이터 조회 테스트
            realtime_data = self.realtime_monitor.get_realtime_data(self.test_stock_code)
            if realtime_data:
                logger.info(f"✅ RealTimeMonitor 데이터 조회 성공: "
                           f"현재가={realtime_data.get('current_price', 0):,}원 "
                           f"변화율={realtime_data.get('price_change_rate', 0):.2f}% "
                           f"소스={realtime_data.get('source', 'unknown')}")
            else:
                logger.warning("⚠️ RealTimeMonitor에서 데이터를 조회할 수 없습니다")
            
            # 매수 조건 분석 테스트 (시뮬레이션)
            if realtime_data:
                logger.info("🎯 매수 조건 분석 테스트...")
                buy_signal = self.realtime_monitor.analyze_buy_conditions(test_stock, realtime_data)
                logger.info(f"📊 매수 신호: {'✅ 발생' if buy_signal else '❌ 없음'}")
                
                sell_reason = self.realtime_monitor.analyze_sell_conditions(test_stock, realtime_data)
                logger.info(f"📊 매도 신호: {'✅ ' + sell_reason if sell_reason else '❌ 없음'}")
            
        except Exception as e:
            logger.error(f"❌ 실시간 데이터 활용 테스트 오류: {e}")
    
    def _provide_recommendations(self):
        """테스트 결과 기반 권장사항 제공"""
        logger.info("=" * 50)
        logger.info("💡 내일 실제 운영 권장사항")
        logger.info("=" * 50)
        
        # 데이터 수신 상태 기반 권장사항
        if self.price_data_received == 0:
            logger.warning("🚨 긴급: 가격 데이터를 전혀 수신하지 못했습니다!")
            logger.warning("   - 웹소켓 연결 상태를 확인하세요")
            logger.warning("   - KIS API 구독 권한을 확인하세요")
            logger.warning("   - 방화벽 설정을 확인하세요")
        elif self.price_data_received < 10:
            logger.warning("⚠️ 주의: 가격 데이터 수신이 매우 적습니다")
            logger.warning("   - 시장 시간대에 다시 테스트해보세요")
            logger.warning("   - 네트워크 상태를 확인하세요")
        else:
            logger.info("✅ 가격 데이터 수신이 양호합니다")
        
        if self.orderbook_data_received == 0:
            logger.warning("🚨 긴급: 호가 데이터를 전혀 수신하지 못했습니다!")
            logger.warning("   - 호가 구독 설정을 확인하세요")
        elif self.orderbook_data_received < 20:
            logger.warning("⚠️ 주의: 호가 데이터 수신이 적습니다")
        else:
            logger.info("✅ 호가 데이터 수신이 양호합니다")
        
        # 실시간 데이터 품질 기반 권장사항
        if len(self.received_price_data) > 0:
            latest_price = self.received_price_data[-1]
            if latest_price.get('current_price', 0) > 0:
                logger.info("✅ 실시간 가격 데이터 품질이 양호합니다")
            else:
                logger.warning("⚠️ 실시간 가격 데이터에 0원이 포함되어 있습니다")
        
        # 전반적인 준비 상태 평가
        total_data_received = self.price_data_received + self.orderbook_data_received
        if total_data_received >= 30:
            logger.info("🎉 내일 실제 운영 준비 완료! 웹소켓 데이터 흐름이 정상입니다")
        elif total_data_received >= 10:
            logger.warning("⚠️ 부분적으로 준비됨. 일부 데이터 수신 문제가 있을 수 있습니다")
        else:
            logger.error("❌ 실제 운영 전 문제 해결 필요! 웹소켓 데이터 수신에 심각한 문제가 있습니다")
        
        logger.info("=" * 50)
    
    def cleanup(self):
        """테스트 정리"""
        logger.info("🧹 테스트 환경 정리...")
        
        try:
            # 콜백 복원
            if hasattr(self, 'original_handle_realtime_price'):
                self.stock_manager.handle_realtime_price = self.original_handle_realtime_price
            if hasattr(self, 'original_handle_realtime_orderbook'):
                self.stock_manager.handle_realtime_orderbook = self.original_handle_realtime_orderbook
            
            # 웹소켓 정리
            websocket_manager = getattr(self.stock_manager, 'websocket_manager', None)
            if websocket_manager:
                websocket_manager.safe_cleanup()
            
            logger.info("✅ 테스트 환경 정리 완료")
            
        except Exception as e:
            logger.error(f"❌ 정리 중 오류: {e}")


def main():
    """메인 실행 함수"""
    test = WebSocketDataFlowTest()
    
    try:
        # 테스트 환경 설정
        if not test.setup_test_environment():
            logger.error("❌ 테스트 환경 설정 실패 - 종료")
            return
        
        # 데이터 수신 테스트 실행 (60초)
        test.run_data_reception_test(duration_seconds=60)
        
    except KeyboardInterrupt:
        logger.info("⏹️ 사용자가 테스트를 중단했습니다")
    except Exception as e:
        logger.error(f"❌ 테스트 실행 오류: {e}")
    finally:
        test.cleanup()


if __name__ == "__main__":
    main() 