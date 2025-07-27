"""
전체 자동매매 시스템을 관리하는 TradeManager 클래스
"""

import threading
import asyncio
from typing import Dict, List, Optional, TYPE_CHECKING
from datetime import datetime, time as dt_time

if TYPE_CHECKING:
    from telegram_bot.telegram_manager import TelegramBot
from .stock_manager import StockManager
from .market_scanner import MarketScanner
from .realtime_monitor import RealTimeMonitor
from .trade_executor import TradeExecutor
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

logger = setup_logger(__name__)

# 텔레그램 봇 선택적 import
try:
    from telegram_bot.telegram_manager import TelegramBot
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
        
        # 웹소켓 매니저는 start_async_system에서 초기화하도록 변경
        self.websocket_manager = None
        
        # MarketScanner는 웹소켓 없이 초기화 (나중에 설정)
        self.market_scanner = MarketScanner(self.stock_manager, None)
        self.realtime_monitor = RealTimeMonitor(self.stock_manager, self.trade_executor)
        
        # 텔레그램 봇 초기화
        self.telegram_bot = None
        self.telegram_task = None  # 🆕 asyncio Task 참조
        logger.info("🔍 텔레그램 봇 초기화 시작...")
        self._initialize_telegram()
        logger.info(f"🔍 텔레그램 봇 초기화 완료: {self.telegram_bot}")
        
        # 시스템 상태
        self.is_running = False
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
    
    async def _init_websocket_manager_async(self):
        """웹소켓 매니저 비동기 초기화 및 연결"""
        try:
            # 웹소켓 매니저 초기화
            websocket_manager = self._init_websocket_manager()
            
            # 웹소켓 연결
            if not websocket_manager.is_connected:
                logger.info("웹소켓 연결 시작...")
                if not websocket_manager.connect():
                    logger.error("❌ 웹소켓 연결 실패")
                    raise RuntimeError("웹소켓 연결 실패")
                else:
                    logger.info("✅ 웹소켓 연결 성공")
            
            # 웹소켓 메시지 루프 시작
            if not websocket_manager.is_running:
                logger.info("웹소켓 메시지 루프 시작...")
                websocket_manager.start()
                
                # 메시지 루프가 시작될 때까지 잠시 대기
                await asyncio.sleep(2)
                
                if websocket_manager.is_running:
                    logger.info("✅ 웹소켓 메시지 루프 시작 성공")
                else:
                    logger.warning("⚠️ 웹소켓 메시지 루프 시작 상태 확인 필요")
            
            # StockManager 웹소켓 콜백 설정 (웹소켓 초기화와 함께 처리)
            logger.info("🔗 StockManager 웹소켓 콜백 설정...")
            self.stock_manager.setup_websocket_callbacks(websocket_manager)
            logger.info("✅ StockManager 웹소켓 콜백 설정 완료")
            
            return websocket_manager
            
        except Exception as e:
            logger.error(f"웹소켓 매니저 비동기 초기화 실패: {e}")
            raise
    
    def _initialize_telegram(self):
        """텔레그램 봇 초기화"""
        try:
            logger.info(f"🔍 텔레그램 초기화 시작 - TELEGRAM_AVAILABLE: {TELEGRAM_AVAILABLE}")
            
            if not TELEGRAM_AVAILABLE:
                logger.info("텔레그램 라이브러리가 없어 텔레그램 봇을 비활성화합니다")
                return
            
            logger.info(f"🔍 TelegramBot 클래스: {TelegramBot}")
            
            # 텔레그램 설정 로드
            telegram_config = self._load_telegram_config()
            logger.info(f"🔍 텔레그램 설정 로드 결과: {telegram_config}")
            
            if telegram_config['enabled'] and TelegramBot is not None:
                logger.info("🔍 텔레그램 봇 생성 시도...")
                self.telegram_bot = TelegramBot(
                    token=telegram_config['token'],
                    chat_id=telegram_config['chat_id']
                )
                
                logger.info("✅ 텔레그램 봇 초기화 준비 완료")
                logger.info(f"🔍 self.telegram_bot: {self.telegram_bot}")
            else:
                logger.info(f"텔레그램 봇이 비활성화되어 있습니다 - enabled: {telegram_config['enabled']}, TelegramBot: {TelegramBot}")
                
        except Exception as e:
            logger.error(f"텔레그램 봇 초기화 실패: {e}")
            import traceback
            logger.error(f"스택 트레이스: {traceback.format_exc()}")
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
            # 시장 스캔 및 종목 선정 (고급 스캐너 옵션 확인)
            use_advanced_scanner = self.strategy_config.get('use_advanced_scanner', False)
            use_combined_scanner = self.strategy_config.get('use_combined_scanner', False)
            
            if use_combined_scanner:
                logger.info("🔀 통합 스캐너 모드 사용")
                success = self.market_scanner.run_pre_market_scan_combined()
            else:
                success = self.market_scanner.run_pre_market_scan(use_advanced_scanner)
            
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
    
    # === 시장 시간 관련 메서드들 (간소화) ===
    
    def _should_run_pre_market(self) -> bool:
        """장시작전 프로세스 실행 여부 판단"""
        current_dt = now_kst()

        # 주말 제외
        #if current_dt.weekday() >= 5:
        #    return False

        # 평일 08:35:00 ~ 08:59:59 사이에만 실행
        return (current_dt.hour == 8) and (current_dt.minute >= 35)
    
    def _is_market_hours(self) -> bool:
        """현재 장시간 여부 확인 (테스트 모드: 장외시간도 장중으로 가정)"""
        current_time = now_kst()
        
        # 테스트 모드에서는 시간 제한 없이 항상 True 반환
        test_mode = self.strategy_config.get('test_mode', True)
        if test_mode:
            logger.debug(f"테스트 모드 활성화 - 시장시간 체크 무시 (현재: {current_time.strftime('%Y-%m-%d %H:%M:%S')})")
            return True
        
        # 실제 운영 모드에서만 시간 체크
        # 주말만 제외하고 평일은 모두 장중으로 처리 (테스트 모드)
        if current_time.weekday() >= 5:  # 주말만 제외
            return False
        
        return True  # 평일은 모두 장중으로 가정

    def _log_system_status(self):
        """시스템 상태 로깅"""
        try:
            stock_summary = self.stock_manager.get_stock_summary()
            trade_stats = self.trade_executor.get_trade_statistics()
            websocket_status = "연결" if self.websocket_manager and self.websocket_manager.is_connected else "미연결"
            websocket_subs = len(self.websocket_manager.get_subscribed_stocks()) if self.websocket_manager else 0
            
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
            
            # 🚀 KIS API 인증 선행 (rank 조회 등 사전 요청 오류 방지)
            try:
                from api.kis_auth import auth as kis_auth
                if kis_auth():
                    logger.info("✅ KIS API 인증 완료 (start_async_system)")
                else:
                    logger.error("❌ KIS API 인증 실패 - 이후 API 호출 오류 가능")
            except Exception as auth_err:
                logger.error(f"❌ KIS API 인증 호출 오류: {auth_err}")
            
            # 1. 텔레그램 봇을 별도 스레드에서 시작 (주식 로직과 완전 분리)
            logger.info(f"🔍 텔레그램 봇 체크: self.telegram_bot = {self.telegram_bot}")
            if self.telegram_bot:
                logger.info("텔레그램 봇을 asyncio Task 로 시작 중...")

                # TradeManager 참조 설정
                self.telegram_bot.set_trade_manager(self)

                # 텔레그램 봇을 메인 이벤트 루프에서 Task 로 실행
                self.telegram_task = asyncio.create_task(self.telegram_bot.start(), name="TelegramBotTask")

                # 초기화 시간을 위해 잠시 대기 (비동기)
                await asyncio.sleep(2)

                # 텔레그램 봇의 실제 상태 확인 (polling_task 기준)
                if (self.telegram_task.done() and 
                    self.telegram_bot.is_running and 
                    self.telegram_bot.polling_task and 
                    not self.telegram_bot.polling_task.done()):
                    logger.info("✅ 텔레그램 봇 초기화 완료 및 폴링 활성화")
                elif self.telegram_task.done() and not self.telegram_bot.is_running:
                    logger.warning("⚠️ 텔레그램 봇 초기화 실패 – 오류 확인 필요")
                else:
                    logger.info("✅ 텔레그램 봇 Task 실행 중...")
            
            else:
                logger.warning("⚠️ 텔레그램 봇이 None입니다 - 초기화되지 않았음")
            
            # 2. 웹소켓 매니저 초기화 및 연결 (비동기 환경에서 수행)
            try:
                logger.info("웹소켓 매니저 초기화 및 연결 시작...")
                self.websocket_manager = await self._init_websocket_manager_async()
                
                # MarketScanner에 웹소켓 매니저 설정
                self.market_scanner.set_websocket_manager(self.websocket_manager)
                logger.info("✅ MarketScanner 웹소켓 연결 설정 완료")
                
            except Exception as ws_error:
                logger.error(f"❌ 웹소켓 초기화 실패: {ws_error}")
                logger.warning("⚠️ 웹소켓 없이 시스템을 계속 실행합니다 (제한된 기능)")
                # 웹소켓 실패해도 시스템은 계속 실행
                self.websocket_manager = None
            
            # 3. 메인 루프 실행 (주식 매매 로직을 메인 스레드에서 처리 - 권장 방식)
            logger.info("메인 루프 시작 - 매매 로직을 메인 스레드에서 비동기 실행")
            await self._main_loop()
            
        except Exception as e:
            logger.error(f"시스템 시작 오류: {e}")
            raise
        finally:
            # 텔레그램 Task 취소 (시스템 종료 또는 예외 시)
            if self.telegram_task and not self.telegram_task.done():
                logger.info("텔레그램 봇 Task 취소 중...")
                self.telegram_task.cancel()
                try:
                    await self.telegram_task
                except asyncio.CancelledError:
                    logger.info("텔레그램 봇 Task 취소 완료")
    
    async def _main_loop(self):
        """메인 실행 루프 - 매매 로직에만 집중하는 단순화된 버전"""
        logger.info("📅 주기적 시장 스캔 및 매매 루프 시작")
        
        # 1. 테스트용 초기 종목 분석 (한 번만)
        #await self._run_initial_test_scan()
        
        # 2. 메인 루프 변수 초기화
        last_scan_date = None
        market_monitoring_active = True
        
        try:
            while self.is_running and not self.shutdown_event.is_set():
                current_time = now_kst()
                current_date = current_time.date()
                
                #장시작전 스캔 처리
                if self._should_run_pre_market() and last_scan_date != current_date:
                    market_monitoring_active = await self._handle_pre_market_scan(
                        current_date, market_monitoring_active
                    )
                    last_scan_date = current_date
                
                # 장시간 모니터링 처리
                if self._is_market_hours() and not market_monitoring_active:
                    market_monitoring_active = await self._handle_market_hours_start()
                
                # 🔥 핵심 매매 로직 - 장시간 중 주기적 매수/매도 처리
                is_market_hours = self._is_market_hours()
                logger.debug(f"🔍 디버그: is_market_hours={is_market_hours}, market_monitoring_active={market_monitoring_active}")
                
                if is_market_hours and market_monitoring_active:
                    logger.debug("✅ 모니터링 사이클 실행 조건 충족 - monitor_cycle() 호출")
                    # 🔥 RealTimeMonitor의 monitor_cycle을 안전하게 실행 (타임아웃 추가)
                    try:
                        # 🔥 타임아웃을 추가하여 매매 루프가 무한 대기하지 않도록 보호
                        await asyncio.wait_for(
                            asyncio.get_event_loop().run_in_executor(
                                None, self.realtime_monitor.monitor_cycle
                            ),
                            timeout=30.0  # 30초 타임아웃
                        )
                        logger.debug("✅ monitor_cycle() 실행 완료")
                    except asyncio.TimeoutError:
                        logger.warning("⚠️ monitor_cycle() 타임아웃 (30초) - 다음 사이클로 건너뜀")
                    except Exception as e:
                        logger.error(f"모니터링 사이클 실행 오류: {e}")
                        # 오류가 발생해도 시스템은 계속 실행
                else:
                    logger.info(f"❌ 모니터링 사이클 건너뜀: is_market_hours={is_market_hours}, monitoring_active={market_monitoring_active}")
                
                # 장마감 정리 처리
                if market_monitoring_active and not self._is_market_hours():
                    market_monitoring_active = await self._handle_market_close()
                
                # 주기적 상태 체크
                await self._periodic_status_check(current_time)
                
                # 적응적 대기 시간
                await self._adaptive_sleep()
                    
        except asyncio.CancelledError:
            logger.info("메인 루프가 취소되었습니다")
        except Exception as e:
            logger.error(f"메인 루프 오류: {e}")
        finally:
            # 정리 작업
            if market_monitoring_active:
                self.realtime_monitor.is_monitoring = False
            logger.info("📅 메인 루프 종료")
    
    async def _run_initial_test_scan(self):
        """테스트용 초기 종목 분석 (한 번만 실행)"""
        if hasattr(self, '_test_scan_completed'):
            return
        
        logger.info("🔍 테스트 모드: stock_list.json 기반 종목 분석 시작")
        
        # API 인증
        try:
            from api.kis_auth import auth
            if not auth():
                logger.error("❌ KIS API 인증 실패 - 종목 분석을 건너뜁니다")
                self._test_scan_completed = True
                return
            logger.info("✅ KIS API 인증 완료")
        except Exception as e:
            logger.error(f"❌ KIS API 인증 오류: {e}")
            self._test_scan_completed = True
            return
        
        # 종목 분석 실행
        scan_success = self.run_pre_market_process()
        if scan_success:
            logger.info("✅ 테스트용 종목 분석 완료")
        else:
            logger.warning("❌ 테스트용 종목 분석 실패")
        
        self._test_scan_completed = True
    
    async def _handle_pre_market_scan(self, current_date, market_monitoring_active: bool) -> bool:
        """장시작전 스캔 처리"""
        logger.info(f"📊 {current_date} 장시작전 시장 스캔 시작")
        
        # 기존 모니터링 중지
        if market_monitoring_active:
            logger.info("기존 모니터링 중지 중...")
            self.realtime_monitor.is_monitoring = False
            market_monitoring_active = False
        
        # 시장 스캔 및 종목 선정
        scan_success = self.run_pre_market_process()
        if scan_success:
            logger.info("✅ 장시작전 스캔 완료")
        else:
            logger.warning("❌ 장시작전 스캔 실패 - 1시간 후 재시도")
            await asyncio.sleep(3600)  # 1시간 대기
        
        return market_monitoring_active
    
    async def _handle_market_hours_start(self) -> bool:
        """장시간 모니터링 시작 처리"""
        selected_stocks = self.stock_manager.get_all_selected_stocks()
        if not selected_stocks:
            logger.warning("선정된 종목이 없어 모니터링을 시작할 수 없습니다")
            return False
        
        logger.info(f"🚀 장시간 실시간 모니터링 시작 ({len(selected_stocks)}개 종목)")
        
        # 기존 모니터링이 실행 중이면 중지
        if self.realtime_monitor.is_monitoring:
            self.realtime_monitor.stop_monitoring()
        
        # 모니터링 상태만 활성화 (별도 스레드 시작하지 않음)
        self.realtime_monitor.is_monitoring = True
        
        # 통계 초기화
        self.realtime_monitor.market_scan_count = 0
        self.realtime_monitor.buy_signals_detected = 0
        self.realtime_monitor.sell_signals_detected = 0
        self.realtime_monitor.orders_executed = 0
        self.realtime_monitor.alert_sent.clear()
        
        logger.info("✅ 장시간 모니터링 활성화 완료 (메인 루프에서 실행)")
        return True
    
    async def _handle_market_close(self) -> bool:
        """장마감 후 정리 처리"""
        logger.info("🏁 장마감 - 실시간 모니터링 중지")
        self.realtime_monitor.is_monitoring = False
        
        # 일일 결과 리포트
        self._generate_daily_report()
        
        return False  # 모니터링 비활성화
    
    async def _adaptive_sleep(self):
        """적응적 대기 시간"""
        if self._is_market_hours():
            # 장시간: 5초마다 체크 (테스트용으로 단축)
            await asyncio.sleep(5)
        elif self._should_run_pre_market():
            # 장시작전: 1분마다 체크
            await asyncio.sleep(60)
        else:
            # 장외시간: 5분마다 체크 (리소스 절약)
            await asyncio.sleep(300)
    
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
            
            # 선정 종목 상태 확인
            stock_summary = self.stock_manager.get_stock_summary()
            logger.info(f"📊 선정종목: {stock_summary['total_selected']}개")
            
            # 거래 성과 확인
            trade_stats = self.trade_executor.get_trade_statistics()
            if trade_stats['total_trades'] > 0:
                logger.info(f"💰 거래 성과: {trade_stats['total_trades']}건, "
                           f"승률 {trade_stats['win_rate']:.1f}%, "
                           f"손익 {trade_stats['total_pnl']:+,.0f}원")
            
            # 메모리 사용량 체크 (선택적)
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
                self.realtime_monitor.is_monitoring = False
            
            # 2. 웹소켓 정리 (필수)
            logger.info("웹소켓 매니저 정리 중...")
            try:
                if self.websocket_manager:
                    self.websocket_manager.safe_cleanup()
                    logger.info("✅ 웹소켓 매니저 정리 완료")
                else:
                    logger.info("웹소켓 매니저가 초기화되지 않았음")
            except Exception as e:
                logger.error(f"❌ 웹소켓 정리 중 오류: {e}")
            
            # 3. 텔레그램 봇 중지 및 Task 취소
            if self.telegram_task and not self.telegram_task.done():
                self.telegram_task.cancel()
                try:
                    await self.telegram_task
                except asyncio.CancelledError:
                    logger.info("텔레그램 봇 Task 취소 완료")

            if self.telegram_bot and hasattr(self.telegram_bot, 'stop'):
                await self.telegram_bot.stop()
            
            # 4. 일일 리포트 생성
            self._generate_daily_report()
            
            logger.info("=== AutoTrade 시스템 종료 완료 ===")
            
        except Exception as e:
            logger.error(f"시스템 종료 오류: {e}")
    
    def get_status(self) -> dict:
        """시스템 상태 반환 (텔레그램 봇에서 호출)"""
        try:
            return {
                'bot_running': self.is_running,
                'api_connected': True,
                'websocket_connected': self.websocket_manager.is_connected if self.websocket_manager else False,
                'websocket_subscriptions': len(self.websocket_manager.get_subscribed_stocks()) if self.websocket_manager else 0,
                'data_collector_running': self.realtime_monitor.is_monitoring,
                'scheduler': {
                    'active_strategies': ['auto_trading'],
                    'total_active_stocks': len(self.stock_manager.get_all_selected_stocks())
                }
            }
        except Exception as e:
            logger.error(f"상태 조회 오류: {e}")
            return {}

    
    def __str__(self) -> str:
        """문자열 표현"""
        status = "실행중" if self.is_running else "중지"
        selected_count = len(self.stock_manager.get_all_selected_stocks())
        websocket_status = "연결" if self.websocket_manager and self.websocket_manager.is_connected else "미연결"
        return f"TradeManager(상태: {status}, 선정종목: {selected_count}개, 웹소켓: {websocket_status})"

 