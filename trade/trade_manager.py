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
            # 시장 스캔 및 종목 선정
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
        current_time = now_kst()
        current_hour = current_time.hour
        
        # 평일 08:00 ~ 09:00 사이에만 실행
        if current_time.weekday() >= 5:  # 주말
            return False
        
        return 8 <= current_hour < 9
    
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
        
        telegram_thread = None
        try:
            self.is_running = True
            
            # 1. 텔레그램 봇을 별도 스레드에서 시작 (주식 로직과 완전 분리)
            logger.info(f"🔍 텔레그램 봇 체크: self.telegram_bot = {self.telegram_bot}")
            if self.telegram_bot:
                logger.info("텔레그램 봇을 별도 스레드에서 시작 중...")
                
                # TradeManager 참조 설정
                self.telegram_bot.set_trade_manager(self)
                
                # 🆕 텔레그램 봇을 별도 스레드에서 실행
                def run_telegram_bot():
                    """텔레그램 봇 전용 스레드 함수"""
                    try:
                        logger.info("🔍 텔레그램 봇 스레드 시작...")
                        
                        # 새로운 이벤트 루프 생성 (메인 루프와 독립)
                        import asyncio
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        # 텔레그램 봇 시작
                        if self.telegram_bot:
                            loop.run_until_complete(self.telegram_bot.start())
                        
                        # 텔레그램 봇이 실행되는 동안 유지
                        logger.info("✅ 텔레그램 봇 스레드 실행 중...")
                        try:
                            loop.run_forever()
                        except KeyboardInterrupt:
                            logger.info("텔레그램 봇 스레드 종료 신호 수신")
                        finally:
                            # 정리 작업
                            if self.telegram_bot and hasattr(self.telegram_bot, 'stop'):
                                loop.run_until_complete(self.telegram_bot.stop())
                            loop.close()
                            logger.info("✅ 텔레그램 봇 스레드 정리 완료")
                            
                    except Exception as tg_error:
                        logger.error(f"❌ 텔레그램 봇 스레드 실행 실패: {tg_error}")
                        import traceback
                        logger.error(f"스택 트레이스: {traceback.format_exc()}")
                
                # 데몬 스레드로 시작 (메인 프로세스 종료시 함께 종료)
                telegram_thread = threading.Thread(
                    target=run_telegram_bot,
                    name="TelegramBot-Thread",
                    daemon=True
                )
                telegram_thread.start()
                
                # 텔레그램 봇 스레드가 시작될 때까지 잠시 대기
                await asyncio.sleep(3)
                
                if telegram_thread.is_alive():
                    logger.info("✅ 텔레그램 봇 별도 스레드 시작 완료")
                else:
                    logger.warning("❌ 텔레그램 봇 스레드 시작 실패")
                    
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
            
            # 3. 메인 루프 실행 (주식 매매 로직만 처리)
            logger.info("메인 루프 시작 - 주기적 시장 스캔 및 매매 대기 (텔레그램 봇과 독립 실행)")
            await self._main_loop()
            
        except Exception as e:
            logger.error(f"시스템 시작 오류: {e}")
            raise
        finally:
            # 텔레그램 스레드 정리는 자동으로 처리됨 (daemon=True)
            if telegram_thread and telegram_thread.is_alive():
                logger.info("텔레그램 봇 스레드 종료 대기 중...")
                # 데몬 스레드이므로 자동으로 종료됨
    
    async def _main_loop(self):
        """메인 실행 루프 - 간소화된 버전"""
        logger.info("📅 주기적 시장 스캔 및 매매 루프 시작")
        
        # 1. 테스트용 초기 종목 분석 (한 번만)
        await self._run_initial_test_scan()
        
        # 2. 메인 루프 변수 초기화
        last_scan_date = None
        market_monitoring_active = True
        last_websocket_health_check = None
        websocket_reconnect_attempts = 0
        max_websocket_reconnect_attempts = 3
        # 🔥 추가: 웹소켓 복구 실패 연속 카운터
        consecutive_websocket_failures = 0
        max_consecutive_failures = 5
        
        try:
            while self.is_running and not self.shutdown_event.is_set():
                current_time = now_kst()
                current_date = current_time.date()
                
                # 🔥 웹소켓 헬스체크 및 자동 재시작 (5분마다)
                if (last_websocket_health_check is None or 
                    (current_time - last_websocket_health_check).total_seconds() >= 300):
                    
                    websocket_healthy = await self._check_and_recover_websocket()
                    if not websocket_healthy:
                        consecutive_websocket_failures += 1
                        websocket_reconnect_attempts += 1
                        logger.warning(f"⚠️ 웹소켓 복구 시도 {websocket_reconnect_attempts}/{max_websocket_reconnect_attempts}, "
                                     f"연속실패 {consecutive_websocket_failures}/{max_consecutive_failures}")
                        
                        # 🔥 연속 실패가 많으면 웹소켓 없이 계속 진행
                        if consecutive_websocket_failures >= max_consecutive_failures:
                            logger.error(f"❌ 웹소켓 연속 실패 한계 도달 ({max_consecutive_failures}회) - 웹소켓 없이 계속 진행")
                            # 텔레그램 알림 전송
                            if self.telegram_bot:
                                try:
                                    await self.telegram_bot.send_message(
                                        f"🚨 웹소켓 연속 실패 {consecutive_websocket_failures}회\n"
                                        "실시간 데이터 없이 계속 진행 중입니다."
                                    )
                                except:
                                    pass
                            
                            # 연속 실패 카운터 리셋 (30분 후 다시 시도하기 위해)
                            consecutive_websocket_failures = 0
                            websocket_reconnect_attempts = 0
                        
                        elif websocket_reconnect_attempts >= max_websocket_reconnect_attempts:
                            logger.error("❌ 웹소켓 복구 실패 한계 도달 - 시스템 재시작 필요")
                            # 필요시 텔레그램 알림 전송
                            if self.telegram_bot:
                                try:
                                    await self.telegram_bot.send_message(
                                        "🚨 웹소켓 연결 복구 실패\n시스템 재시작이 필요할 수 있습니다."
                                    )
                                except:
                                    pass
                        else:
                            # 재연결 시도 대기
                            await asyncio.sleep(30)
                    else:
                        # 웹소켓 정상 - 카운터 리셋
                        consecutive_websocket_failures = 0
                        websocket_reconnect_attempts = 0
                    
                    last_websocket_health_check = current_time
                
                # 장시작전 스캔 처리
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
            
            # 3. 텔레그램 봇 중지
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

    async def _check_and_recover_websocket(self) -> bool:
        """웹소켓 헬스체크 및 자동 복구 (PINGPONG 기반)"""
        try:
            if not self.websocket_manager:
                logger.warning("⚠️ 웹소켓 매니저가 초기화되지 않음")
                return False
            
            # 1. 기본 연결 상태 확인
            if not self.websocket_manager.is_connected:
                logger.warning("⚠️ 웹소켓 연결 끊어짐 - 백그라운드 재연결 시도")
                # 🔥 백그라운드에서 재연결 시도 (매매 루프 블로킹 방지)
                asyncio.create_task(self._background_websocket_recovery())
                return False  # 재연결 중이므로 일시적으로 False 반환
            
            # 2. 실행 상태 확인
            if not self.websocket_manager.is_running:
                logger.warning("⚠️ 웹소켓 실행 중지됨 - 백그라운드 재시작 시도")
                asyncio.create_task(self._background_websocket_recovery())
                return False
            
            # 🔥 3. PINGPONG 하트비트 상태 확인 (핵심 개선)
            pingpong_status = self.websocket_manager.get_pingpong_status()
            if not pingpong_status.get('is_pingpong_recent', False):
                pingpong_age = pingpong_status.get('last_pingpong_age', 0)
                logger.warning(f"⚠️ PINGPONG 하트비트 이상: {pingpong_age:.1f}초 전 마지막 수신")
                
                # PINGPONG이 3분 이상 없으면 백그라운드 재연결 시도
                if pingpong_age > 180:
                    logger.warning("❌ PINGPONG 타임아웃 - 백그라운드 재연결 시도")
                    asyncio.create_task(self._background_websocket_recovery())
                    return False  # 매매는 계속 진행
                else:
                    logger.info(f"🔍 PINGPONG 지연 중이지만 허용 범위 ({pingpong_age:.1f}초)")
            
            # 4. 구독 상태 확인 (논블로킹)
            selected_stocks = self.stock_manager.get_all_selected_stocks()
            subscribed_stocks = self.websocket_manager.get_subscribed_stocks()
            
            missing_subscriptions = []
            for stock in selected_stocks:
                if stock.stock_code not in subscribed_stocks:
                    missing_subscriptions.append(stock.stock_code)
            
            if missing_subscriptions:
                logger.warning(f"⚠️ 웹소켓 구독 누락 종목 발견: {len(missing_subscriptions)}개")
                # 🔥 백그라운드에서 재구독 처리
                asyncio.create_task(self._background_resubscribe(missing_subscriptions))
            
            # 5. 전체 웹소켓 건강 상태 확인
            health_status = self.websocket_manager.get_health_status()
            is_healthy = health_status.get('is_healthy', False)
            
            if is_healthy:
                # 상세한 상태 로깅 (5분마다 한 번만)
                ping_pong_count = health_status.get('ping_pong_count', 0)
                total_messages = health_status.get('total_messages', 0)
                subscribed_count = len(subscribed_stocks)
                
                logger.debug(f"✅ 웹소켓 헬스체크 정상: 연결={self.websocket_manager.is_connected}, "
                            f"실행={self.websocket_manager.is_running}, "
                            f"PINGPONG={ping_pong_count}회, "
                            f"메시지={total_messages}개, "
                            f"구독={subscribed_count}개")
                return True
            else:
                logger.warning(f"⚠️ 웹소켓 건강 상태 이상: {health_status}")
                return False
            
        except Exception as e:
            logger.error(f"❌ 웹소켓 헬스체크 오류: {e}")
            return False
    
    async def _background_websocket_recovery(self):
        """백그라운드에서 웹소켓 복구 (매매 루프 블로킹 방지)"""
        try:
            logger.info("🔄 백그라운드 웹소켓 복구 시작...")
            success = await self._restart_websocket_connection()
            if success:
                logger.info("✅ 백그라운드 웹소켓 복구 성공")
            else:
                logger.error("❌ 백그라운드 웹소켓 복구 실패")
        except Exception as e:
            logger.error(f"❌ 백그라운드 웹소켓 복구 오류: {e}")
    
    async def _background_resubscribe(self, missing_stock_codes: List[str]):
        """백그라운드에서 누락 종목 재구독"""
        try:
            logger.info(f"🔄 백그라운드 재구독 시작: {len(missing_stock_codes)}개 종목")
            await self._resubscribe_missing_stocks(missing_stock_codes)
            logger.info("✅ 백그라운드 재구독 완료")
        except Exception as e:
            logger.error(f"❌ 백그라운드 재구독 오류: {e}")
    
    async def _restart_websocket_connection(self) -> bool:
        """웹소켓 연결 재시작"""
        try:
            logger.info("🔄 웹소켓 연결 재시작 시도...")
            
            # 1. 기존 웹소켓 정리
            if self.websocket_manager:
                try:
                    self.websocket_manager.safe_cleanup()
                except Exception as cleanup_error:
                    logger.error(f"기존 웹소켓 정리 오류: {cleanup_error}")
            
            # 2. 새로운 웹소켓 매니저 초기화
            self.websocket_manager = await self._init_websocket_manager_async()
            
            # 3. MarketScanner에 새 웹소켓 매니저 설정
            if self.market_scanner:
                self.market_scanner.set_websocket_manager(self.websocket_manager)
            
            # 4. 선정된 종목 재구독
            selected_stocks = self.stock_manager.get_all_selected_stocks()
            if selected_stocks:
                logger.info(f"📡 선정 종목 재구독 시작: {len(selected_stocks)}개")
                await self._resubscribe_all_stocks(selected_stocks)
            
            logger.info("✅ 웹소켓 연결 재시작 완료")
            return True
            
        except Exception as e:
            logger.error(f"❌ 웹소켓 연결 재시작 실패: {e}")
            return False
    
    async def _resubscribe_missing_stocks(self, missing_stock_codes: List[str]):
        """누락된 종목 재구독"""
        try:
            if not self.websocket_manager:
                logger.warning("웹소켓 매니저가 없어 종목 재구독을 건너뜁니다")
                return
                
            for stock_code in missing_stock_codes:
                try:
                    # 웹소켓 매니저를 통한 직접 구독
                    success = await self.websocket_manager.subscribe_stock(stock_code)
                    if success:
                        logger.info(f"✅ 종목 재구독 성공: {stock_code}")
                    else:
                        logger.warning(f"❌ 종목 재구독 실패: {stock_code}")
                    
                    # 구독 요청 간 간격
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"종목 재구독 오류 ({stock_code}): {e}")
                    
        except Exception as e:
            logger.error(f"누락 종목 재구독 처리 오류: {e}")
    
    async def _resubscribe_all_stocks(self, selected_stocks):
        """모든 선정 종목 재구독"""
        try:
            if not self.websocket_manager:
                logger.warning("웹소켓 매니저가 없어 종목 재구독을 건너뜁니다")
                return
                
            for stock in selected_stocks:
                try:
                    # 웹소켓 매니저를 통한 직접 구독
                    success = await self.websocket_manager.subscribe_stock(stock.stock_code)
                    if success:
                        logger.info(f"✅ 종목 재구독 성공: {stock.stock_code}")
                    else:
                        logger.warning(f"❌ 종목 재구독 실패: {stock.stock_code}")
                    
                    # 구독 요청 간 간격
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"종목 재구독 오류 ({stock.stock_code}): {e}")
                    
        except Exception as e:
            logger.error(f"전체 종목 재구독 처리 오류: {e}") 