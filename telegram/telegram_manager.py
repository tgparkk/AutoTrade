"""
텔레그램 봇 - StockBot 원격 제어 및 모니터링
별도 스레드에서 실행되어 실시간 명령 처리
"""
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, TYPE_CHECKING
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from utils.logger import setup_logger
from utils.korean_time import now_kst

if TYPE_CHECKING:
    from main import StockBot

logger = setup_logger(__name__)

class TelegramBot:
    """텔레그램 봇 클래스"""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = int(chat_id)
        self.application: Optional[Application] = None
        self.stock_bot: Optional['StockBot'] = None
        self.bot_paused = False
        self.running = False
        self.bot_thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        # 인증된 사용자 목록
        self.authorized_users = {self.chat_id}

    def set_stock_bot(self, stock_bot: 'StockBot'):
        """StockBot 인스턴스 설정"""
        self.stock_bot = stock_bot
        logger.info("StockBot 인스턴스 연결 완료")

    def _check_authorization(self, user_id: int) -> bool:
        """사용자 권한 확인"""
        return user_id in self.authorized_users

    async def initialize(self):
        """비동기 초기화"""
        try:
            # Application 생성
            self.application = Application.builder().token(self.token).build()

            # 명령어 핸들러 등록
            self.application.add_handler(CommandHandler("start", self._cmd_start))
            self.application.add_handler(CommandHandler("help", self._cmd_help))
            self.application.add_handler(CommandHandler("status", self._cmd_status))
            self.application.add_handler(CommandHandler("pause", self._cmd_pause))
            self.application.add_handler(CommandHandler("resume", self._cmd_resume))
            self.application.add_handler(CommandHandler("refresh", self._cmd_refresh))
            self.application.add_handler(CommandHandler("stop", self._cmd_stop))
            self.application.add_handler(CommandHandler("balance", self._cmd_balance))
            self.application.add_handler(CommandHandler("profit", self._cmd_profit))
            self.application.add_handler(CommandHandler("positions", self._cmd_positions))
            self.application.add_handler(CommandHandler("today", self._cmd_today_summary))
            self.application.add_handler(CommandHandler("scheduler", self._cmd_scheduler_status))
            self.application.add_handler(CommandHandler("stocks", self._cmd_active_stocks))
            self.application.add_handler(CommandHandler("trades", self._cmd_history))

            # 일반 메시지 핸들러 (명령어가 아닌 경우)
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

            logger.info("텔레그램 봇 초기화 완료")

        except Exception as e:
            logger.error(f"텔레그램 봇 초기화 실패: {e}")
            raise

    async def start(self):
        """봇 시작"""
        try:
            if not self.application:
                await self.initialize()

            # 웹훅 삭제 후 폴링 시작
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(drop_pending_updates=True)

            logger.info("텔레그램 봇 시작 완료")
            self.running = True

            # 시작 메시지 전송
            if self.application and self.application.bot:
                try:
                    await self.application.bot.send_message(
                        chat_id=self.chat_id,
                        text="🤖 StockBot 텔레그램 봇이 시작되었습니다!\n/help 명령어로 도움말을 확인하세요."
                    )
                except Exception as e:
                    logger.error(f"시작 메시지 전송 실패: {e}")

        except Exception as e:
            logger.error(f"텔레그램 봇 시작 오류: {e}")
            self.running = False
            raise

    async def stop(self):
        """봇 중지"""
        try:
            self.running = False

            if self.application:
                if self.application.updater:
                    await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()

            logger.info("텔레그램 봇 중지 완료")

        except Exception as e:
            logger.error(f"텔레그램 봇 중지 중 오류: {e}")

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시작 명령어"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        message = (
            "실시간 주식 자동매매 봇이 실행 중입니다.\n"
            "/help - 명령어 도움말"
        )
        await update.message.reply_text(message)

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """도움말"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        message = (
            "<b>StockBot 명령어 목록</b>\n\n"
            "<b>상태 조회</b>\n"
            "/status - 시스템 전체 상태\n"
            "/scheduler - 전략 스케줄러 상태\n"
            "/stocks - 현재 활성 종목\n"
            "/today - 오늘 거래 요약\n\n"
            "<b>계좌 정보</b>\n"
            "/balance - 계좌 잔고\n"
            "/profit - 오늘 수익률\n"
            "/positions - 현재 포지션\n"
            "/trades - 최근 거래 내역\n\n"
            "<b>제어 명령</b>\n"
            "/pause - 거래 일시정지\n"
            "/resume - 거래 재개\n"
            "/refresh - REST API 가격 강제 갱신\n"
            "/stop - 시스템 종료\n\n"
            "/help - 이 도움말"
        )
        await update.message.reply_text(message, parse_mode='HTML')

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시스템 상태"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        try:
            if not self.stock_bot:
                await update.message.reply_text("StockBot 인스턴스에 접근할 수 없습니다.")
                return

            status = self.stock_bot.get_status()

            websocket_status = "❌"
            if status.get('websocket_connected', False):
                websocket_status = f"✅ ({status.get('websocket_subscriptions', 0)}종목)"

            trading_status = "일시정지" if self.bot_paused else ("활성" if status.get('bot_running', False) else "중지")

            scheduler_status = status.get('scheduler', {})
            active_strategies = scheduler_status.get('active_strategies', [])

            message = (
                f"<b>StockBot 시스템 상태</b>\n\n"
                f"봇 실행: {'✅' if status.get('bot_running', False) else '❌'}\n"
                f"일시정지: {'✅' if self.bot_paused else '❌'}\n"
                f"거래 상태: {trading_status}\n"
                f"웹소켓: {websocket_status}\n"
                f"REST API: {'✅' if status.get('api_connected', False) else '❌'}\n"
                f"데이터 수집: {'✅' if status.get('data_collector_running', False) else '❌'}\n\n"
                f"활성 전략: {len(active_strategies)}\n"
                f"활성 종목: {scheduler_status.get('total_active_stocks', 0)}개\n\n"
                f"시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            await update.message.reply_text(message, parse_mode='HTML')

        except Exception as e:
            logger.error(f"상태 조회 오류: {e}")
            await update.message.reply_text("상태 조회 중 오류가 발생했습니다.")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """거래 일시정지"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        self.bot_paused = True
        await update.message.reply_text("거래가 일시정지되었습니다.")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """거래 재개"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        self.bot_paused = False
        await update.message.reply_text("거래가 재개되었습니다.")

    async def _cmd_refresh(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """REST API 강제 갱신"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        try:
            # 캔들 트레이딩 시스템에서는 실시간 데이터를 사용하므로 강제 갱신 불필요
            await update.message.reply_text("캔들 트레이딩 시스템은 실시간 데이터를 사용합니다.")
        except Exception as e:
            logger.error(f"REST API 갱신 오류: {e}")
            await update.message.reply_text("REST API 갱신 중 오류가 발생했습니다.")

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시스템 종료"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        await update.message.reply_text("StockBot을 종료합니다...")

        try:
            if self.stock_bot:
                self.stock_bot.shutdown()
            else:
                logger.warning("stock_bot 인스턴스가 없음 - 직접 종료")
                import sys
                sys.exit(0)
        except Exception as e:
            logger.error(f"시스템 종료 오류: {e}")

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """계좌 잔고 조회 - KIS API 연동"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        try:
            if not self.stock_bot:
                await update.message.reply_text("StockBot 인스턴스에 접근할 수 없습니다.")
                return

            loading_msg = await update.message.reply_text("잔고 조회 중...")

            try:
                # KIS API를 통한 실제 잔고 조회
                account_info = await self._get_account_balance()

                if account_info and account_info.get('success'):
                    balance_data = account_info['data']

                    total_assets = balance_data.get('total_assets', 0)
                    available_cash = balance_data.get('available_cash', 0)
                    stock_evaluation = balance_data.get('stock_evaluation', 0)
                    profit_loss = balance_data.get('profit_loss', 0)
                    profit_rate = balance_data.get('profit_rate', 0.0)
                    holdings = balance_data.get('holdings', [])

                    total_assets_str = f"{total_assets:,}" if total_assets else "0"
                    available_cash_str = f"{available_cash:,}" if available_cash else "0"
                    stock_eval_str = f"{stock_evaluation:,}" if stock_evaluation else "0"
                    profit_str = f"{profit_loss:+,}" if profit_loss else "0"

                    profit_emoji = "📈" if profit_loss > 0 else "📉" if profit_loss < 0 else "➖"

                    message = (
                        f"<b>계좌 잔고</b>\n\n"
                        f"총 평가금액: {total_assets_str}원\n"
                        f"가용 현금: {available_cash_str}원\n"
                        f"주식 평가금액: {stock_eval_str}원\n"
                        f"{profit_emoji} 평가손익: {profit_str}원 ({profit_rate:+.2f}%)\n"
                        f"보유 종목: {len(holdings)}개\n\n"
                        f"시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}"
                    )

                    await loading_msg.edit_text(message, parse_mode='HTML')
                    return

                else:
                    await loading_msg.edit_text("API 오류 - 포지션 데이터로 대체합니다...")

            except Exception as api_error:
                logger.warning(f"KIS API 잔고 조회 실패: {api_error}")
                await loading_msg.edit_text("API 오류 - 로컬 데이터로 추정합니다...")

            # 캔들 트레이딩 시스템 데이터로 대체
            try:
                if hasattr(self.stock_bot, 'candle_trade_manager'):
                    # CandleTradeManager에서 포지션 정보 조회
                    active_positions = self.stock_bot.candle_trade_manager.get_active_positions()
                    total_positions = len(active_positions)

                    message = (
                        f"<b>계좌 정보 (캔들 시스템)</b>\n\n"
                        f"보유 종목: {total_positions}개\n\n"
                        f"정확한 잔고는 증권사 앱에서 확인하세요.\n"
                        f"시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}"
                    )

                    await loading_msg.edit_text(message, parse_mode='HTML')
                else:
                    message = (
                        f"API 오류 및 캔들 시스템 데이터 없음\n"
                        f"증권사 앱에서 직접 확인하세요.\n"
                        f"시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}"
                    )

                    await loading_msg.edit_text(message, parse_mode='HTML')

            except Exception as local_error:
                logger.error(f"로컬 데이터 조회 실패: {local_error}")
                message = (
                    f"모든 데이터 소스 접근 실패\n"
                    f"시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                await loading_msg.edit_text(message, parse_mode='HTML')

        except Exception as e:
            logger.error(f"잔고 조회 총 오류: {e}")
            await update.message.reply_text("잔고 조회 중 오류가 발생했습니다.")

    async def _cmd_profit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """오늘 수익률 - trade_db 사용"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        try:
            if not self.stock_bot:
                await update.message.reply_text("StockBot 인스턴스에 접근할 수 없습니다.")
                return

            # trade_db를 통한 오늘 수익률 조회
            if not hasattr(self.stock_bot, 'trade_db'):
                await update.message.reply_text("거래 데이터베이스에 접근할 수 없습니다.")
                return

            trade_db = self.stock_bot.trade_db
            today_performance = trade_db.calculate_daily_performance()

            if not today_performance:
                await update.message.reply_text("오늘 거래 데이터가 없습니다.")
                return

            realized_pnl = today_performance.get('realized_pnl', 0)
            win_rate = today_performance.get('win_rate', 0)
            total_trades = today_performance.get('total_trades', 0)

            message = (
                f"<b>📊 오늘 수익률</b>\n\n"
                f"💰 실현손익: {realized_pnl:+,.0f}원\n"
                f"📈 승률: {win_rate:.1f}%\n"
                f"📊 총 거래: {total_trades}건\n\n"
                f"🕐 시간: {now_kst().strftime('%H:%M:%S')}"
            )

            await update.message.reply_text(message, parse_mode='HTML')

        except Exception as e:
            logger.error(f"수익률 조회 오류: {e}")
            await update.message.reply_text("수익률 조회 중 오류가 발생했습니다.")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """현재 포지션"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        try:
            if not hasattr(self.stock_bot, 'candle_trade_manager'):
                await update.message.reply_text("캔들 트레이딩 매니저에 접근할 수 없습니다.")
                return

            # CandleTradeManager에서 활성 포지션 조회
            active_positions = self.stock_bot.candle_trade_manager.get_active_positions()

            if not active_positions:
                await update.message.reply_text("현재 보유 중인 포지션이 없습니다.")
                return

            message = "<b>현재 포지션 (캔들 시스템)</b>\n\n"

            for candidate in active_positions:
                stock_code = candidate.stock_code
                stock_name = candidate.stock_name or stock_code

                message += (
                    f"<b>{stock_code}</b> ({stock_name})\n"
                    f"  상태: {candidate.status.value}\n"
                    f"  신호: {candidate.trade_signal.value}\n\n"
                )

            message += f"총 {len(active_positions)}개 포지션"

            await update.message.reply_text(message, parse_mode='HTML')

        except Exception as e:
            logger.error(f"포지션 조회 오류: {e}")
            await update.message.reply_text("포지션 조회 중 오류가 발생했습니다.")

    async def _cmd_today_summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """오늘 요약 - StockBot 내부 데이터 사용"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        try:
            if not self.stock_bot:
                await update.message.reply_text("StockBot 인스턴스에 접근할 수 없습니다.")
                return

            # StockBot 상태와 통계 정보 조회
            status = self.stock_bot.get_status()
            stats = status.get('stats', {})

            # 활성 종목 수
            active_stocks_count = 0
            if hasattr(self.stock_bot, 'strategy_scheduler'):
                active_stocks = getattr(self.stock_bot.strategy_scheduler, 'active_stocks', {})
                active_stocks_count = sum(len(stocks) for stocks in active_stocks.values())

            # 현재 포지션 수
            positions_count = status.get('positions_count', 0)

            message = (
                f"<b>📊 오늘 요약</b>\n\n"
                f"🤖 봇 상태: {'✅ 실행중' if status.get('bot_running', False) else '❌ 중지'}\n"
                f"🔗 웹소켓: {'✅ 연결' if status.get('websocket_connected', False) else '❌ 끊김'}\n"
                f"📡 API: {'✅ 정상' if status.get('api_connected', False) else '❌ 오류'}\n\n"
                f"📈 처리된 신호: {stats.get('signals_processed', 0)}개\n"
                f"💰 실행된 주문: {stats.get('orders_executed', 0)}개\n"
                f"📊 활성 종목: {active_stocks_count}개\n"
                f"💼 현재 포지션: {positions_count}개\n\n"
                f"⏱️ 가동시간: {status.get('uptime', 0) // 60:.0f}분\n"
                f"🕐 시간: {now_kst().strftime('%H:%M:%S')}"
            )

            await update.message.reply_text(message, parse_mode='HTML')

        except Exception as e:
            logger.error(f"오늘 요약 조회 오류: {e}")
            await update.message.reply_text("오늘 요약 조회 중 오류가 발생했습니다.")

    async def _cmd_scheduler_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """스케줄러 상태"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        try:
            if not self.stock_bot:
                await update.message.reply_text("StockBot 인스턴스에 접근할 수 없습니다.")
                return

            status = self.stock_bot.get_status()
            scheduler_info = status.get('scheduler', {})

            if not scheduler_info:
                await update.message.reply_text("스케줄러 정보를 가져올 수 없습니다.")
                return

            current_time_slot = scheduler_info.get('current_time_slot', 'None')
            active_strategies = scheduler_info.get('active_strategies', [])
            total_stocks = scheduler_info.get('total_active_stocks', 0)

            time_slot_names = {
                'golden_time': '골든타임 (09:00-09:30)',
                'morning_leaders': '주도주 시간 (09:30-11:30)',
                'lunch_time': '점심시간 (11:30-14:00)',
                'closing_trend': '마감추세 (14:00-15:20)'
            }

            current_slot_name = time_slot_names.get(current_time_slot, current_time_slot)

            message = (
                f"<b>전략 스케줄러 상태</b>\n\n"
                f"현재 시간대: {current_slot_name}\n"
                f"활성 전략: {', '.join(active_strategies) if active_strategies else '없음'}\n"
                f"활성 종목 수: {total_stocks}개\n\n"
                f"시간: {now_kst().strftime('%H:%M:%S')}"
            )

            await update.message.reply_text(message, parse_mode='HTML')

        except Exception as e:
            logger.error(f"스케줄러 상태 조회 오류: {e}")
            await update.message.reply_text("스케줄러 상태 조회 중 오류가 발생했습니다.")

    async def _cmd_active_stocks(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """현재 활성 종목 - strategy_scheduler.active_stocks 사용"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        try:
            if not self.stock_bot:
                await update.message.reply_text("StockBot 인스턴스에 접근할 수 없습니다.")
                return

            # strategy_scheduler의 active_stocks 조회
            if not hasattr(self.stock_bot, 'strategy_scheduler'):
                await update.message.reply_text("전략 스케줄러에 접근할 수 없습니다.")
                return

            strategy_scheduler = self.stock_bot.strategy_scheduler
            active_stocks = getattr(strategy_scheduler, 'active_stocks', {})

            if not active_stocks:
                await update.message.reply_text("현재 활성화된 종목이 없습니다.")
                return

            # 총 종목 수 계산
            total_count = sum(len(stocks) for stocks in active_stocks.values())

            message = f"<b>📊 현재 활성 종목 ({total_count}개)</b>\n\n"

            # 전략별로 종목 표시
            for strategy, stocks in active_stocks.items():
                if stocks:  # 종목이 있을 때만 표시
                    strategy_name = {
                        'gap_trading': '🎯 갭 트레이딩',
                        'volume_breakout': '📈 거래량 돌파',
                        'momentum': '🚀 모멘텀',
                        'disparity_reversal': '🔄 이격도 반등',
                        'existing_holding': '💼 기존 보유'
                    }.get(strategy, f'📌 {strategy}')

                    message += f"{strategy_name} ({len(stocks)}개)\n"

                    # 각 전략당 최대 5개 종목만 표시
                    displayed_stocks = stocks[:5]
                    for stock_code in displayed_stocks:
                        message += f"  • {stock_code}\n"

                    if len(stocks) > 5:
                        message += f"  ... 외 {len(stocks) - 5}개\n"

                    message += "\n"

            # 현재 시간 추가
            message += f"🕐 업데이트: {now_kst().strftime('%H:%M:%S')}"

            await update.message.reply_text(message, parse_mode='HTML')

        except Exception as e:
            logger.error(f"활성 종목 조회 오류: {e}")
            await update.message.reply_text("활성 종목 조회 중 오류가 발생했습니다.")

    async def _cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """거래 내역 조회 - trade_db 사용"""
        if not self._check_authorization(update.effective_user.id):
            await update.message.reply_text("권한이 없습니다.")
            return

        try:
            # 일수 파라미터 파싱
            args = update.message.text.split()[1:] if update.message.text else []
            days = int(args[0]) if args and args[0].isdigit() else 3

            if not self.stock_bot or not hasattr(self.stock_bot, 'trade_db'):
                await update.message.reply_text("거래 데이터베이스에 접근할 수 없습니다.")
                return

            trade_db = self.stock_bot.trade_db
            trades = trade_db.get_recent_trades(days=days)

            if not trades:
                await update.message.reply_text(f"최근 {days}일간 거래 내역이 없습니다.")
                return

            # 최근 5건만 표시
            recent_trades = trades[:5]

            message = f"*최근 {days}일 거래 내역*\n\n"

            for trade in recent_trades:
                trade_type = "매수" if trade.get('order_type') == 'BUY' else "매도"
                stock_code = trade.get('stock_code', 'N/A')
                quantity = trade.get('quantity', 0)
                price = trade.get('price', 0)
                strategy = trade.get('strategy_type', 'N/A')
                created_at = trade.get('created_at', '')

                # 날짜 포맷팅
                if created_at:
                    try:
                        from datetime import datetime
                        if isinstance(created_at, str):
                            dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        else:
                            dt = created_at
                        date_str = dt.strftime("%m/%d %H:%M")
                    except:
                        date_str = str(created_at)[:10]
                else:
                    date_str = "N/A"

                message += f"`{date_str}` {trade_type} {stock_code}\n"
                message += f"  {quantity:,}주 @ {price:,}원 ({strategy})\n\n"

            if len(trades) > 5:
                message += f"(총 {len(trades)}건 중 5건만 표시)"

            await update.message.reply_text(message, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"거래 내역 조회 오류: {e}")
            await update.message.reply_text("거래 내역 조회 중 오류가 발생했습니다.")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """일반 메시지 처리"""
        if not self._check_authorization(update.effective_user.id):
            return

        # 특별한 처리가 필요한 경우 여기에 추가
        await update.message.reply_text("알 수 없는 명령입니다. /help로 명령어를 확인하세요.")

    async def _get_account_balance(self) -> Optional[dict]:
        """계좌 잔고 조회"""
        try:
            if not self.stock_bot or not hasattr(self.stock_bot, 'trading_manager'):
                return None

            trading_manager = self.stock_bot.trading_manager
            if hasattr(trading_manager, 'account_api'):
                balance_data = trading_manager.account_api.get_balance()
                if balance_data:
                    return {'success': True, 'data': balance_data}

            return {'success': False, 'error': 'API 접근 불가'}

        except Exception as e:
            logger.error(f"계좌 잔고 조회 오류: {e}")
            return {'success': False, 'error': str(e)}

    async def send_message(self, message: str, parse_mode: str = None):
        """메시지 전송"""
        try:
            if self.application and self.application.bot:
                await self.application.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode=parse_mode
                )
        except Exception as e:
            logger.error(f"메시지 전송 실패: {e}")

    async def send_trade_notification(self, trade_info: dict):
        """거래 알림 전송"""
        try:
            trade_type = trade_info.get('trade_type', 'UNKNOWN')
            stock_code = trade_info.get('stock_code', 'UNKNOWN')
            quantity = trade_info.get('quantity', 0)
            price = trade_info.get('price', 0)

            emoji = "🟢" if trade_type == 'BUY' else "🔴"
            type_text = "매수" if trade_type == 'BUY' else "매도"

            message = (
                f"{emoji} <b>{type_text} 체결</b>\n"
                f"종목: {stock_code}\n"
                f"수량: {quantity:,}주\n"
                f"가격: {price:,}원\n"
                f"시간: {now_kst().strftime('%H:%M:%S')}"
            )

            await self.send_message(message, parse_mode='HTML')

        except Exception as e:
            logger.error(f"거래 알림 전송 실패: {e}")

    def is_paused(self) -> bool:
        """일시정지 상태 확인"""
        return self.bot_paused

    def start_bot(self):
        """텔레그램 봇 시작 (스레드에서 실행)"""
        try:
            if self.running:
                logger.info("텔레그램 봇이 이미 실행 중입니다")
                return

            logger.info("텔레그램 봇 시작 중...")

            def run_bot():
                """별도 스레드에서 봇 실행"""
                try:
                    # 새 이벤트 루프 생성
                    self.loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self.loop)

                    # 봇 시작
                    self.loop.run_until_complete(self.start())

                    # 이벤트 루프 실행 (무한 대기)
                    if self.running:
                        self.loop.run_forever()

                except Exception as e:
                    logger.error(f"텔레그램 봇 실행 오류: {e}")
                finally:
                    try:
                        # 정리
                        if self.loop and not self.loop.is_closed():
                            self.loop.close()
                    except Exception as e:
                        logger.debug(f"이벤트 루프 정리 중 오류: {e}")

            # 별도 스레드에서 봇 실행
            self.bot_thread = threading.Thread(
                target=run_bot,
                name="TelegramBot",
                daemon=True
            )
            self.bot_thread.start()

            # 시작 확인을 위한 짧은 대기
            import time
            time.sleep(2)

            logger.info("텔레그램 봇 스레드 시작 완료")

        except Exception as e:
            logger.error(f"텔레그램 봇 시작 실패: {e}")
            self.running = False

    def stop_bot(self):
        """텔레그램 봇 중지"""
        try:
            logger.info("텔레그램 봇 중지 중...")

            self.running = False

            # 이벤트 루프에서 stop() 실행
            if self.loop and not self.loop.is_closed():
                try:
                    # 비동기 정리 작업을 스케줄링
                    future = asyncio.run_coroutine_threadsafe(self.stop(), self.loop)
                    future.result(timeout=5)  # 5초 타임아웃
                except Exception as e:
                    logger.error(f"비동기 정리 오류: {e}")

                # 이벤트 루프 중지
                try:
                    self.loop.call_soon_threadsafe(self.loop.stop)
                except Exception as e:
                    logger.debug(f"이벤트 루프 중지 오류: {e}")

            # 스레드 종료 대기
            if self.bot_thread and self.bot_thread.is_alive():
                try:
                    self.bot_thread.join(timeout=3)
                except Exception as e:
                    logger.debug(f"스레드 종료 대기 오류: {e}")

            logger.info("텔레그램 봇 중지 완료")

        except Exception as e:
            logger.error(f"텔레그램 봇 중지 오류: {e}")

    def send_notification_sync(self, message: str):
        """동기 방식 알림 전송 - 스레드 안전"""
        try:
            if not self.application or not self.running:
                logger.debug("텔레그램 봇이 실행되지 않아 알림을 전송할 수 없습니다")
                return

            # 텔레그램 봇의 이벤트 루프가 실행 중인지 확인
            if not self.loop or self.loop.is_closed():
                logger.debug("텔레그램 봇 이벤트 루프가 없어 알림을 전송할 수 없습니다")
                return

            try:
                # 스레드 안전한 방식으로 코루틴 실행
                future = asyncio.run_coroutine_threadsafe(
                    self.send_message(message),
                    self.loop
                )

                # 타임아웃 설정 (3초)
                future.result(timeout=3.0)
                logger.debug("텔레그램 알림 전송 성공")

            except asyncio.TimeoutError:
                logger.warning("텔레그램 알림 전송 타임아웃 (3초)")
            except Exception as e:
                logger.error(f"텔레그램 알림 전송 오류: {e}")

        except Exception as e:
            logger.error(f"텔레그램 동기 알림 전송 실패: {e}")

    def send_startup_notification(self):
        """시작 알림 전송"""
        try:
            # 텔레그램 봇이 완전히 시작될 때까지 대기
            import time
            time.sleep(3)  # 3초 대기

            startup_msg = (
                "🚀 StockBot이 시작되었습니다!\n\n"
                f"시작 시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}\n"
                "/help - 명령어 도움말\n"
                "/status - 시스템 상태 확인"
            )
            self.send_notification_sync(startup_msg)

        except Exception as e:
            logger.error(f"시작 알림 전송 실패: {e}")
