"""monitor_core.py – RealTimeMonitor 의 핵심 루프 분리 모듈
RealTimeMonitor의 monitor_cycle_legacy 로직을 MonitorCore.run_cycle로 이동하여
단일 책임 원칙을 적용한 리팩터링
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Dict
from utils.logger import setup_logger

if TYPE_CHECKING:
    from trade.realtime_monitor import RealTimeMonitor

logger = setup_logger(__name__)

class MonitorCore:
    """RealTimeMonitor 의 핵심 모니터링 사이클 처리 담당"""

    def __init__(self, monitor: "RealTimeMonitor"):
        self.monitor = monitor  # RealTimeMonitor 인스턴스

    def run_cycle(self):
        """메인 모니터링 사이클 (기존 monitor_cycle_legacy 로직)"""
        # 🔥 동시 실행 방지 (스레드 안전성 보장)
        #   전용 락을 non-blocking 으로 획득하여 재진입을 차단한다.
        lock_acquired: bool = self.monitor._cycle_lock.acquire(blocking=False)
        if not lock_acquired:
            logger.debug("⚠️ 이전 monitor_cycle() 아직 실행 중 - 이번 사이클 건너뜀")
            return
        
        try:
            # 통계 증가 (StatsTracker 사용)
            self.monitor.stats_tracker.inc_market_scan()
            scan_count = self.monitor.stats_tracker.market_scan_count
            
            # 시장 상황 확인 및 모니터링 주기 조정
            self.monitor.adjust_monitoring_frequency()
            
            # 테스트 모드 설정 (config에서 로드)
            test_mode = self.monitor.strategy_config.get('test_mode', True)

            #self.monitor._check_and_run_intraday_scan()
            
            if not test_mode:
                # 실제 운영 모드: 시장시간 체크
                if not self.monitor.is_market_open():
                    if scan_count % 60 == 0:  # 10분마다 로그
                        logger.info("시장 마감 - 대기 중...")
                    return
                
                # 거래 시간이 아니면 모니터링만
                if not self.monitor.is_trading_time():
                    market_phase = self.monitor.get_market_phase()
                    if market_phase == 'lunch':
                        if scan_count % 30 == 0:  # 5분마다 로그
                            logger.info("점심시간 - 모니터링만 실행")
                    elif market_phase == 'closing':
                        logger.info("장 마감 시간 - 보유 포지션 정리 중...")
                        self.monitor.process_sell_ready_stocks()  # 마감 시간에는 매도만
                    return
            else:
                # 테스트 모드: 시간 제한 없이 실행
                test_mode_log_interval = self.monitor.strategy_config.get('test_mode_log_interval_cycles', 100)
                if scan_count % test_mode_log_interval == 0:  # 설정 기반 테스트 모드 알림
                    logger.info("🧪 테스트 모드 실행 중 - 시장시간 무관하게 매수/매도 분석 진행")
            
            # 🔥 설정 기반 성능 로깅 주기 (정확한 시간 간격 계산)
            performance_log_seconds = self.monitor.strategy_config.get('performance_log_interval_minutes', 5) * 60
            performance_check_interval = max(1, round(performance_log_seconds / self.monitor.current_monitoring_interval))
            if scan_count % performance_check_interval == 0:
                self.monitor._log_performance_metrics()
            
            # 매수 준비 종목 처리
            buy_result = self.monitor.process_buy_ready_stocks()
            
            # 매도 준비 종목 처리  
            sell_result = self.monitor.process_sell_ready_stocks()
            
            # 🆕 장중 추가 종목 스캔
            self.monitor._check_and_run_intraday_scan()
            
            # 🔥 백그라운드 장중 스캔 결과 처리 (큐 기반 스레드 안전)
            self.monitor._process_background_scan_results()
            
            # 🔥 대기 중인 웹소켓 구독 처리 (메인 스레드에서 안전하게 처리)
            self.monitor.sub_manager.process_pending()
            
            # 🔥 설정 기반 정체된 주문 타임아웃 체크 (정확한 시간 간격 계산)
            stuck_order_check_seconds = self.monitor.strategy_config.get('stuck_order_check_interval_seconds', 30)
            stuck_order_check_interval = max(1, round(stuck_order_check_seconds / self.monitor.current_monitoring_interval))
            if scan_count % stuck_order_check_interval == 0:
                self.monitor._check_stuck_orders()
            
            # 🔥 설정 기반 주기적 상태 리포트 (정확한 시간 간격 계산)
            status_report_seconds = self.monitor.strategy_config.get('status_report_interval_minutes', 1) * 60
            status_report_interval = max(1, round(status_report_seconds / self.monitor.current_monitoring_interval))
            if scan_count % status_report_interval == 0:
                self.monitor._log_status_report(buy_result, sell_result)
            
            # 🔥 주기적 메모리 정리 (1시간마다)
            memory_cleanup_seconds = 3600
            memory_cleanup_interval = max(1, round(memory_cleanup_seconds / self.monitor.current_monitoring_interval))
            if scan_count % memory_cleanup_interval == 0:
                self.monitor._cleanup_expired_data()
                
            # 🔥 16:00 보고서 자동 출력
            self.monitor._check_and_log_daily_report()
                
        except Exception as e:
            logger.error(f"모니터링 사이클 오류: {e}")
        finally:
            # 🔥 반드시 락 해제 (예외 발생시에도)
            if lock_acquired:
                self.monitor._cycle_lock.release()

    def loop(self):
        """메인 모니터링 루프 (미구현)"""
        raise NotImplementedError

    def adjust_monitoring_frequency(self):
        """모니터링 주기 조정 로직 (미구현)"""
        raise NotImplementedError 