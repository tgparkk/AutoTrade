"""
전체 자동매매 시스템을 관리하는 TradeManager 클래스
"""

import time
import threading
import asyncio
import signal
from typing import Dict, List, Optional, TYPE_CHECKING
from datetime import datetime, time as dt_time

if TYPE_CHECKING:
    from telegram.telegram_manager import TelegramBot
from .stock_manager import StockManager
from .market_scanner import MarketScanner
from .realtime_monitor import RealTimeMonitor
from .trade_executor import TradeExecutor
# 웹소켓 매니저는 runtime에 import (import 경로 문제 해결)
from typing import Any
WebSocketManagerType = Any  # KISWebSocketManager 타입 (필수 컴포넌트)
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

logger = setup_logger(__name__)

# 텔레그램 봇 선택적 import
try:
    from telegram.telegram_manager import TelegramBot
    TELEGRAM_AVAILABLE = True
except ImportError as e:
    logger.warning(f"텔레그램 라이브러리를 찾을 수 없습니다: {e}")
    TelegramBot = None
    TELEGRAM_AVAILABLE = False


class TradeManager:
    """전체 자동매매 시스템을 관리하는 메인 클래스"""
    
    def __init__(self):
        """TradeManager 초기화"""
        logger.info("=== TradeManager 초기화 시작 ===")
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        self.market_config = self.config_loader.load_market_schedule_config()
        
        # 핵심 컴포넌트들 초기화
        self.stock_manager = StockManager()
        self.trade_executor = TradeExecutor()
        
        # 웹소켓 매니저 초기화 (필수 컴포넌트) - MarketScanner보다 먼저 초기화
        self.websocket_manager = self._init_websocket_manager()
        
        # MarketScanner에 웹소켓 매니저 전달
        self.market_scanner = MarketScanner(self.stock_manager, self.websocket_manager)
        self.realtime_monitor = RealTimeMonitor(self.stock_manager, self.trade_executor)
        
        # 텔레그램 봇 초기화
        self.telegram_bot = None
        self._initialize_telegram()
        
        # TODO: 향후 추가될 컴포넌트들
        # self.websocket_handler = WebSocketHandler()  # 웹소켓 체결통보 처리
        # self.pattern_detector = PatternDetector()     # 패턴 감지 클래스
        # self.technical_analyzer = TechnicalAnalyzer() # 기술적 분석 클래스
        
        # 시스템 상태
        self.is_running = False
        self.system_thread = None
        self.shutdown_event = threading.Event()
        
        logger.info("=== TradeManager 초기화 완료 ===")
    
    def _init_websocket_manager(self):
        """웹소켓 매니저 초기화 (필수 컴포넌트)"""
        try:
            import sys
            import os
            # 프로젝트 루트를 경로에 추가
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.append(project_root)
            
            from websocket.kis_websocket_manager import KISWebSocketManager
            websocket_manager = KISWebSocketManager()
            logger.info("✅ 웹소켓 매니저 초기화 완료")
            return websocket_manager
        except ImportError as e:
            logger.error(f"❌ 웹소켓 매니저 import 실패: {e}")
            logger.error("🚨 웹소켓은 필수 컴포넌트입니다. 시스템을 시작할 수 없습니다.")
            raise RuntimeError(f"필수 웹소켓 매니저 초기화 실패: {e}")
    
    def _initialize_telegram(self):
        """텔레그램 봇 초기화"""
        try:
            if not TELEGRAM_AVAILABLE:
                logger.info("텔레그램 라이브러리가 없어 텔레그램 봇을 비활성화합니다")
                return
            
            # 텔레그램 설정 로드
            telegram_config = self._load_telegram_config()
            
            if telegram_config['enabled'] and TelegramBot is not None:
                self.telegram_bot = TelegramBot(
                    token=telegram_config['token'],
                    chat_id=telegram_config['chat_id']
                )
                
                # TODO: TradeManager와 TelegramBot 연결 로직 추후 구현
                
                logger.info("텔레그램 봇 초기화 준비 완료")
            else:
                logger.info("텔레그램 봇이 비활성화되어 있습니다")
                
        except Exception as e:
            logger.error(f"텔레그램 봇 초기화 실패: {e}")
            self.telegram_bot = None
    
    def _load_telegram_config(self) -> dict:
        """텔레그램 설정 로드"""
        try:
            # config/key.ini에서 텔레그램 설정 로드
            import configparser
            config = configparser.ConfigParser()
            config.read('config/key.ini', encoding='utf-8')
            
            return {
                'enabled': config.getboolean('TELEGRAM', 'enabled', fallback=False),
                'token': config.get('TELEGRAM', 'token', fallback=''),
                'chat_id': config.get('TELEGRAM', 'chat_id', fallback='')
            }
        except Exception as e:
            logger.warning(f"텔레그램 설정 로드 실패: {e}")
            return {'enabled': False, 'token': '', 'chat_id': ''}
    
    def run_pre_market_process(self) -> bool:
        """장시작전 프로세스 실행
        
        Returns:
            실행 성공 여부
        """
        logger.info("=== 장시작전 프로세스 시작 ===")
        
        try:
            # 1. 기존 모니터링 중지 (만약 실행 중이라면)
            if self.realtime_monitor.is_monitoring:
                self.realtime_monitor.stop_monitoring()
            
            # 2. 시장 스캔 및 종목 선정
            success = self.market_scanner.run_pre_market_scan()
            
            if not success:
                logger.error("장시작전 종목 선정 실패")
                return False
            
            # 3. 선정된 종목 정보 로깅
            summary = self.stock_manager.get_stock_summary()
            logger.info(f"선정 완료: {summary['total_selected']}개 종목")
            
            # 선정된 종목들 출력
            selected_stocks = self.stock_manager.get_all_selected_stocks()
            for i, position in enumerate(selected_stocks, 1):
                logger.info(f"{i:2d}. {position.stock_code}[{position.stock_name}] "
                           f"(점수: {position.total_pattern_score:.1f})")
            
            logger.info("=== 장시작전 프로세스 완료 ===")
            return True
            
        except Exception as e:
            logger.error(f"장시작전 프로세스 오류: {e}")
            return False
    
    def start_market_monitoring(self) -> bool:
        """장시간 실시간 모니터링 시작
        
        Returns:
            시작 성공 여부
        """
        logger.info("=== 장시간 모니터링 시작 ===")
        
        try:
            # 선정된 종목이 있는지 확인
            selected_stocks = self.stock_manager.get_all_selected_stocks()
            if len(selected_stocks) == 0:
                logger.warning("선정된 종목이 없습니다. 먼저 장시작전 프로세스를 실행하세요.")
                return False
            
            # 1. 웹소켓 연결 확인/시작 (필수)
            if not self.websocket_manager.is_connected:
                logger.info("웹소켓 연결 시작...")
                if not self.websocket_manager.connect():
                    logger.error("❌ 웹소켓 연결 실패 - 실시간 모니터링을 시작할 수 없습니다")
                    return False
                else:
                    logger.info("✅ 웹소켓 연결 성공")
            
            # 2. 웹소켓 구독 상태 확인 (select_top_stocks에서 이미 구독됨)
            if self.websocket_manager.is_connected:
                subscribed_count = len(self.websocket_manager.get_subscribed_stocks())
                logger.info(f"웹소켓 구독 확인: {subscribed_count}개 종목 구독됨")
                
                # 구독된 종목이 너무 적으면 경고
                if subscribed_count < len(selected_stocks) / 2:
                    logger.warning(f"⚠️ 웹소켓 구독이 부족합니다: {subscribed_count}/{len(selected_stocks)}")
                    logger.warning("장시작전 프로세스에서 웹소켓 구독이 제대로 이루어지지 않았을 수 있습니다")
            else:
                logger.error("❌ 웹소켓이 연결되지 않아 실시간 모니터링을 시작할 수 없습니다")
                return False
            
            # 2. StockManager 웹소켓 콜백 설정 
            logger.info("🔗 StockManager 웹소켓 연결 설정...")
            self.stock_manager.setup_websocket_callbacks(self.websocket_manager)
            
            # 3. 실시간 모니터링 시작
            self.realtime_monitor.start_monitoring()
            
            logger.info("✅ 장시간 모니터링이 시작되었습니다")
            return True
            
        except Exception as e:
            logger.error(f"장시간 모니터링 시작 오류: {e}")
            return False
    
    def stop_market_monitoring(self):
        """장시간 실시간 모니터링 중지"""
        logger.info("=== 장시간 모니터링 중지 ===")
        
        try:
            # 1. 실시간 모니터링 중지
            self.realtime_monitor.stop_monitoring()
            
            # 2. 웹소켓 구독 관리
            if self.websocket_manager.is_connected:
                subscribed_stocks = self.websocket_manager.get_subscribed_stocks()
                if subscribed_stocks:
                    logger.info(f"웹소켓 구독 현황: {len(subscribed_stocks)}개 종목")
                    # 구독 해제는 다음 스캔 시 새로운 종목으로 자동 교체됨
                    # 성능상 여기서는 명시적 해제하지 않음
            
            logger.info("✅ 장시간 모니터링이 중지되었습니다")
            
        except Exception as e:
            logger.error(f"장시간 모니터링 중지 오류: {e}")
    
    def run_full_trading_day(self):
        """완전한 거래일 실행 (장시작전 + 장시간 모니터링)"""
        logger.info("=== 완전한 거래일 실행 시작 ===")
        
        try:
            # 1. 장시작전 프로세스
            if not self.run_pre_market_process():
                logger.error("장시작전 프로세스 실패로 거래일 실행 중단")
                return False
            
            # 2. 시장 개장까지 대기
            self._wait_for_market_open()
            
            # 3. 장시간 모니터링 시작
            if not self.start_market_monitoring():
                logger.error("장시간 모니터링 시작 실패")
                return False
            
            # 4. 시장 마감까지 대기
            self._wait_for_market_close()
            
            # 5. 일일 결과 리포트
            self._generate_daily_report()
            
            logger.info("=== 완전한 거래일 실행 완료 ===")
            return True
            
        except Exception as e:
            logger.error(f"거래일 실행 오류: {e}")
            return False
    
    def _wait_for_market_open(self):
        """시장 개장까지 대기"""
        market_open_time = dt_time(9, 0)
        
        while True:
            current_time = now_kst().time()
            
            if current_time >= market_open_time:
                logger.info("시장 개장 시간 도달")
                break
            
            # 1분마다 체크
            time.sleep(60)
            
            minutes_to_open = (datetime.combine(datetime.today(), market_open_time) - 
                              datetime.combine(datetime.today(), current_time)).total_seconds() / 60
            
            if minutes_to_open <= 30:  # 30분 이내일 때만 로깅
                logger.info(f"시장 개장까지 {minutes_to_open:.0f}분 남음")
    
    def _wait_for_market_close(self):
        """시장 마감까지 대기"""
        market_close_time = dt_time(15, 30)
        
        while True:
            current_time = now_kst().time()
            
            if current_time >= market_close_time:
                logger.info("시장 마감 시간 도달")
                break
            
            # 10분마다 체크
            time.sleep(600)
    
    def _generate_daily_report(self):
        """일일 거래 결과 리포트 생성"""
        logger.info("=== 일일 거래 결과 리포트 ===")
        
        try:
            # 종목 관리 요약
            stock_summary = self.stock_manager.get_stock_summary()
            logger.info(f"관리된 종목 수: {stock_summary['total_selected']}")
            
            # 거래 통계
            trade_stats = self.trade_executor.get_trade_statistics()
            logger.info(f"총 거래 수: {trade_stats['total_trades']}")
            logger.info(f"수익 거래: {trade_stats['winning_trades']}")
            logger.info(f"손실 거래: {trade_stats['losing_trades']}")
            logger.info(f"승률: {trade_stats['win_rate']:.1f}%")
            logger.info(f"총 실현손익: {trade_stats['total_realized_pnl']:+,.0f}원")
            
            # 현재 보유 포지션
            bought_stocks = self.stock_manager.get_bought_stocks()
            if bought_stocks:
                logger.info(f"미처분 포지션: {len(bought_stocks)}개")
                for position in bought_stocks:
                    logger.info(f"  - {position.stock_code}[{position.stock_name}]: "
                               f"{position.unrealized_pnl:+,.0f}원 ({position.unrealized_pnl_rate:+.2f}%)")
            
        except Exception as e:
            logger.error(f"일일 리포트 생성 오류: {e}")
    
    def get_system_status(self) -> Dict:
        """시스템 전체 상태 정보"""
        try:
            stock_summary = self.stock_manager.get_stock_summary()
            trade_stats = self.trade_executor.get_trade_statistics()
            monitoring_status = self.realtime_monitor.get_monitoring_status()
            
            return {
                'system_running': self.is_running,
                'market_open': monitoring_status['is_market_open'],
                'trading_time': monitoring_status['is_trading_time'],
                'monitoring_active': monitoring_status['is_monitoring'],
                'selected_stocks_count': stock_summary['total_selected'],
                'stock_status_breakdown': stock_summary['status_breakdown'],
                'total_trades': trade_stats['total_trades'],
                'win_rate': trade_stats['win_rate'],
                'total_pnl': trade_stats['total_realized_pnl'],
                'current_time': now_kst().strftime('%Y-%m-%d %H:%M:%S')
            }
            
        except Exception as e:
            logger.error(f"시스템 상태 조회 오류: {e}")
            return {'error': str(e)}
    
    def emergency_stop(self):
        """비상 정지"""
        logger.warning("=== 비상 정지 실행 ===")
        
        try:
            # 모니터링 중지
            self.stop_market_monitoring()
            
            # 모든 미체결 주문 취소 (TODO: 실제 API 연동 시 구현)
            # self._cancel_all_pending_orders()
            
            # 모든 보유 포지션 강제 매도 (TODO: 실제 API 연동 시 구현)  
            # self._force_sell_all_positions()
            
            self.is_running = False
            logger.warning("비상 정지 완료")
            
        except Exception as e:
            logger.error(f"비상 정지 오류: {e}")
    
    # === 시장 시간 및 스케줄링 관련 메서드들 ===
    
    def _should_run_pre_market(self) -> bool:
        """장시작전 프로세스 실행 여부 판단"""
        current_time = now_kst()
        current_hour = current_time.hour
        
        # 평일 08:00 ~ 09:00 사이에만 실행
        if current_time.weekday() >= 5:  # 주말
            return False
        
        return 8 <= current_hour < 9
    
    def _is_market_hours(self) -> bool:
        """현재 장시간 여부 확인 (테스트 모드: 장외시간도 장중으로 가정)"""
        # 🔥 테스트 모드: 항상 장중으로 가정 (주말 제외)
        return self._is_market_hours_test_mode()
    
    def _is_market_hours_test_mode(self) -> bool:
        """테스트 모드: 장외시간도 장중으로 가정"""
        current_time = now_kst()
        current_hour = current_time.hour
        current_minute = current_time.minute
        
        logger.debug(f"🧪 테스트 모드: 현재시간 {current_hour:02d}:{current_minute:02d}을 장중으로 가정")
        
        # 주말만 제외하고 평일은 모두 장중으로 처리
        if current_time.weekday() >= 5:  # 주말만 제외
            return False
        
        return True  # 평일은 모두 장중으로 가정
    
    def _is_market_hours_normal(self) -> bool:
        """정상 모드: 실제 장시간만 장중으로 판단"""
        current_time = now_kst()
        current_hour = current_time.hour
        current_minute = current_time.minute
        
        # 평일 09:00 ~ 15:30
        if current_time.weekday() >= 5:  # 주말
            return False
        
        if current_hour < 9:
            return False
        elif current_hour > 15:
            return False
        elif current_hour == 15 and current_minute > 30:
            return False
        
        return True
    
    def set_test_mode(self, enable: bool = True):
        """테스트 모드 활성화/비활성화
        
        Args:
            enable: True=테스트모드(장외시간도 장중), False=정상모드(실제 장시간만)
        """
        if enable:
            self._is_market_hours = self._is_market_hours_test_mode
            logger.info("🧪 테스트 모드 활성화: 장외시간도 장중으로 가정")
        else:
            self._is_market_hours = self._is_market_hours_normal
            logger.info("📈 정상 모드 활성화: 실제 장시간만 장중으로 판단")
    

    
    def _log_system_status(self):
        """시스템 상태 로깅"""
        try:
            stock_summary = self.stock_manager.get_stock_summary()
            trade_stats = self.trade_executor.get_trade_statistics()
            websocket_status = "연결" if self.websocket_manager.is_connected else "미연결"
            websocket_subs = len(self.websocket_manager.get_subscribed_stocks())
            
            logger.info(f"📊 시스템 상태: 실행중={self.is_running}, "
                       f"선정종목={stock_summary['total_selected']}, "
                       f"거래수={trade_stats['total_trades']}, "
                       f"승률={trade_stats['win_rate']:.1f}%, "
                       f"웹소켓={websocket_status}({websocket_subs}개구독)")
                       
        except Exception as e:
            logger.error(f"시스템 상태 로깅 오류: {e}")
    
    # === 메인 시스템 실행 관련 메서드들 ===
    
    async def start_async_system(self):
        """전체 시스템 시작 (비동기 버전)"""
        logger.info("=== AutoTrade 시스템 시작 ===")
        
        try:
            self.is_running = True
            
            # 1. 텔레그램 봇 시작 (백그라운드)
            telegram_task = None
            if self.telegram_bot:
                logger.info("텔레그램 봇 시작 중...")
                telegram_task = asyncio.create_task(self._start_telegram_bot())
            
            # 2. 메인 루프 실행 (모든 로직은 여기서 주기적으로 처리)
            logger.info("메인 루프 시작 - 주기적 시장 스캔 및 매매 대기")
            await self._main_loop()
            
        except Exception as e:
            logger.error(f"시스템 시작 오류: {e}")
            raise
        finally:
            if telegram_task and not telegram_task.done():
                telegram_task.cancel()
    
    async def _start_telegram_bot(self):
        """텔레그램 봇 시작 (비동기)"""
        try:
            if self.telegram_bot and hasattr(self.telegram_bot, 'start'):
                await self.telegram_bot.start()
        except Exception as e:
            logger.error(f"텔레그램 봇 시작 실패: {e}")
    
    async def _main_loop(self):
        """메인 실행 루프 - 주기적 시장 스캔 및 매매"""
        logger.info("📅 주기적 시장 스캔 및 매매 루프 시작")
        
        # 🧪 테스트 모드: 시작 시 stock_list.json 기반 종목 분석 (한 번만 실행)
        if not hasattr(self, '_test_scan_completed'):
            logger.info("🧪 테스트 모드: stock_list.json 기반 종목 분석 시작")
            
            # 1. API 인증 먼저 수행
            logger.info("🔑 KIS API 인증 시작...")
            try:
                from api.kis_auth import auth
                auth_success = auth()
                if not auth_success:
                    logger.error("❌ KIS API 인증 실패 - 종목 분석을 건너뜁니다")
                    self._test_scan_completed = True
                    return
                logger.info("✅ KIS API 인증 완료")
            except Exception as e:
                logger.error(f"❌ KIS API 인증 오류: {e}")
                self._test_scan_completed = True
                return
            
            # 2. 종목 분석 실행
            scan_success = self.run_pre_market_process()  # 기존 메서드 활용
            if scan_success:
                logger.info("✅ 테스트용 종목 분석 완료")
            else:
                logger.warning("❌ 테스트용 종목 분석 실패")
            self._test_scan_completed = True  # 한 번만 실행하도록 플래그
        
        # 상태 추적 변수들
        last_scan_date = None
        market_monitoring_active = False
        
        try:
            while self.is_running and not self.shutdown_event.is_set():
                current_time = now_kst()
                current_date = current_time.date()
                
                # === 1. 매일 장시작전 스캔 (08:00~09:00) ===
                if self._should_run_pre_market():
                    # 오늘 스캔을 아직 안했다면 실행
                    if last_scan_date != current_date:
                        logger.info(f"📊 {current_date} 장시작전 시장 스캔 시작")
                        
                        # 기존 모니터링 중지
                        if market_monitoring_active:
                            self.stop_market_monitoring()
                            market_monitoring_active = False
                        
                        # 시장 스캔 및 종목 선정
                        scan_success = self.run_pre_market_process()
                        if scan_success:
                            logger.info("✅ 장시작전 스캔 완료")
                            last_scan_date = current_date
                        else:
                            logger.warning("❌ 장시작전 스캔 실패 - 1시간 후 재시도")
                            await asyncio.sleep(3600)  # 1시간 대기
                            continue
                
                # === 2. 장시간 실시간 모니터링 (09:00~15:30) ===
                if self._is_market_hours():
                    # 아직 모니터링이 시작되지 않았다면 시작
                    if not market_monitoring_active:
                        selected_stocks = self.stock_manager.get_all_selected_stocks()
                        if selected_stocks:
                            logger.info(f"🚀 장시간 실시간 모니터링 시작 ({len(selected_stocks)}개 종목)")
                            monitor_success = self.start_market_monitoring()
                            if monitor_success:
                                market_monitoring_active = True
                            else:
                                logger.warning("실시간 모니터링 시작 실패")
                        else:
                            logger.warning("선정된 종목이 없어 모니터링을 시작할 수 없습니다")
                
                # === 3. 장마감 후 정리 (15:30 이후) ===
                elif market_monitoring_active and not self._is_market_hours():
                    logger.info("🏁 장마감 - 실시간 모니터링 중지")
                    self.stop_market_monitoring()
                    market_monitoring_active = False
                    
                    # 일일 결과 리포트
                    self._generate_daily_report()
                
                # === 4. 주기적 상태 체크 및 로깅 ===
                await self._periodic_status_check(current_time)
                
                # === 5. 대기 시간 조정 ===
                if self._is_market_hours():
                    # 장시간: 30초마다 체크 (빠른 반응)
                    await asyncio.sleep(30)
                elif self._should_run_pre_market():
                    # 장시작전: 1분마다 체크
                    await asyncio.sleep(60)
                else:
                    # 장외시간: 5분마다 체크 (리소스 절약)
                    await asyncio.sleep(300)
                    
        except asyncio.CancelledError:
            logger.info("메인 루프가 취소되었습니다")
        except Exception as e:
            logger.error(f"메인 루프 오류: {e}")
        finally:
            # 정리 작업
            if market_monitoring_active:
                self.stop_market_monitoring()
            logger.info("📅 메인 루프 종료")
    
    async def _periodic_status_check(self, current_time):
        """주기적 상태 체크 및 로깅"""
        try:
            # 10분마다 시스템 상태 로깅
            if current_time.minute % 10 == 0 and current_time.second < 30:
                self._log_system_status()
            
            # 1시간마다 상세 상태 체크
            if current_time.minute == 0 and current_time.second < 30:
                await self._hourly_health_check()
                
        except Exception as e:
            logger.error(f"주기적 상태 체크 오류: {e}")
    
    async def _hourly_health_check(self):
        """시간별 헬스 체크"""
        try:
            current_time = now_kst()
            logger.info(f"🏥 {current_time.strftime('%H:00')} 시간별 헬스 체크")
            
            # 1. 시스템 상태 확인
            system_status = self.get_system_status()
            
            # 2. 선정 종목 상태 확인
            stock_summary = self.stock_manager.get_stock_summary()
            logger.info(f"📊 선정종목: {stock_summary['total_selected']}개")
            
            # 3. 거래 성과 확인
            trade_stats = self.trade_executor.get_trade_statistics()
            if trade_stats['total_trades'] > 0:
                logger.info(f"💰 거래 성과: {trade_stats['total_trades']}건, "
                           f"승률 {trade_stats['win_rate']:.1f}%, "
                           f"손익 {trade_stats['total_pnl']:+,.0f}원")
            
            # 4. 메모리 사용량 체크 (선택적)
            try:
                import psutil
                memory_percent = psutil.virtual_memory().percent
                if memory_percent > 80:
                    logger.warning(f"⚠️ 메모리 사용률 높음: {memory_percent:.1f}%")
                else:
                    logger.debug(f"💾 메모리 사용률: {memory_percent:.1f}%")
            except ImportError:
                logger.debug("psutil 라이브러리가 없어 메모리 체크를 건너뜁니다")
                
        except Exception as e:
            logger.error(f"헬스 체크 오류: {e}")
    

    
    async def stop_async_system(self):
        """전체 시스템 종료 (비동기 버전)"""
        logger.info("=== AutoTrade 시스템 종료 시작 ===")
        
        try:
            self.is_running = False
            self.shutdown_event.set()
            
            # 1. 실시간 모니터링 중지
            if self.realtime_monitor.is_monitoring:
                self.stop_market_monitoring()
            
            # 2. 웹소켓 정리 (필수)
            logger.info("웹소켓 매니저 정리 중...")
            try:
                self.websocket_manager.safe_cleanup()
                logger.info("✅ 웹소켓 매니저 정리 완료")
            except Exception as e:
                logger.error(f"❌ 웹소켓 정리 중 오류: {e}")
            
            # 3. 텔레그램 봇 중지
            if self.telegram_bot and hasattr(self.telegram_bot, 'stop'):
                await self.telegram_bot.stop()
            
            # 4. 일일 리포트 생성
            self._generate_daily_report()
            
            logger.info("=== AutoTrade 시스템 종료 완료 ===")
            
        except Exception as e:
            logger.error(f"시스템 종료 오류: {e}")
    
    def signal_handler(self, signum, frame):
        """시그널 핸들러 (Ctrl+C 등)"""
        logger.info(f"종료 시그널 수신: {signum}")
        self.shutdown_event.set()
    
    # === 기존 동기 메서드들 (하위호환성) ===
    
    def start_system(self):
        """시스템 시작 (백그라운드)"""
        if self.is_running:
            logger.warning("시스템이 이미 실행 중입니다")
            return
        
        self.is_running = True
        self.system_thread = threading.Thread(target=self.run_full_trading_day, daemon=True)
        self.system_thread.start()
        logger.info("자동매매 시스템이 백그라운드에서 시작되었습니다")
    
    def stop_system(self):
        """시스템 중지"""
        self.is_running = False
        self.stop_market_monitoring()
        
        if self.system_thread:
            self.system_thread.join(timeout=10)
        
        logger.info("자동매매 시스템이 중지되었습니다")
    
    def get_status(self) -> dict:
        """시스템 상태 반환 (텔레그램 봇에서 호출)"""
        try:
            return {
                'bot_running': self.is_running,
                'api_connected': True,  # TODO: 실제 API 연결 상태 확인
                'websocket_connected': self.websocket_manager.is_connected,
                'websocket_subscriptions': len(self.websocket_manager.get_subscribed_stocks()),
                'data_collector_running': self.realtime_monitor.is_monitoring,
                'scheduler': {
                    'active_strategies': ['auto_trading'],
                    'total_active_stocks': len(self.stock_manager.get_all_selected_stocks())
                }
            }
        except Exception as e:
            logger.error(f"상태 조회 오류: {e}")
            return {}
    
    # 컴포넌트 접근 메서드들
    def get_stock_manager(self) -> StockManager:
        """StockManager 인스턴스 반환"""
        return self.stock_manager
    
    def get_trade_executor(self) -> TradeExecutor:
        """TradeExecutor 인스턴스 반환"""
        return self.trade_executor
    
    def get_market_scanner(self) -> MarketScanner:
        """MarketScanner 인스턴스 반환"""
        return self.market_scanner
    
    def get_realtime_monitor(self) -> RealTimeMonitor:
        """RealTimeMonitor 인스턴스 반환"""
        return self.realtime_monitor
    
    def get_websocket_manager(self) -> WebSocketManagerType:
        """KISWebSocketManager 인스턴스 반환 (필수 컴포넌트)"""
        return self.websocket_manager
    
    def __str__(self) -> str:
        """문자열 표현"""
        status = "실행중" if self.is_running else "중지"
        selected_count = len(self.stock_manager.get_all_selected_stocks())
        websocket_status = "연결" if self.websocket_manager.is_connected else "미연결"
        return f"TradeManager(상태: {status}, 선정종목: {selected_count}개, 웹소켓: {websocket_status})" 