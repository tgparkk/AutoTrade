"""
텔레그램 봇 - AutoTrade 시스템 원격 제어 및 모니터링
TradeManager 기반 실시간 명령 처리
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from utils.logger import setup_logger
from utils.korean_time import now_kst

if TYPE_CHECKING:
    from trade.trade_manager import TradeManager

logger = setup_logger(__name__)


class TelegramBot:
    """AutoTrade 시스템용 텔레그램 봇 클래스"""

    def __init__(self, token: str, chat_id: str):
        """텔레그램 봇 초기화"""
        self.token = token
        self.chat_id = chat_id
        self.application = None
        self.polling_task = None  # 폴링 태스크 참조
        self.is_running = False
        self.trade_manager = None
        logger.info(f"텔레그램 봇 초기화 완료 (Chat ID: {chat_id})")
    
    def set_trade_manager(self, trade_manager: 'TradeManager'):
        """TradeManager 참조 설정"""
        self.trade_manager = trade_manager
        logger.info("TradeManager 참조 설정 완료")
    
    async def start(self):
        """텔레그램 봇 시작"""
        try:
            logger.info("🤖 텔레그램 봇 시작...")
            
            # Application 생성
            self.application = Application.builder().token(self.token).build()
            
            # 명령어 핸들러 등록
            await self._register_handlers()
            
            # 봇 시작
            await self.application.initialize()
            await self.application.start()
            
            # 폴링을 백그라운드에서 시작 (메인 스레드를 블로킹하지 않음)
            self.polling_task = asyncio.create_task(self.application.updater.start_polling())
            
            self.is_running = True
            logger.info("✅ 텔레그램 봇 시작 완료")
            
            # 🆕 폴링 상태 확인
            logger.info(f"📡 폴링 상태: {'활성' if self.polling_task and not self.polling_task.done() else '비활성'}")
            logger.info(f"🎯 대상 Chat ID: {self.chat_id}")
            
            # 시작 메시지 전송
            await self.send_message("🚀 AutoTrade 텔레그램 봇이 시작되었습니다!")
            
        except Exception as e:
            logger.error(f"❌ 텔레그램 봇 시작 실패: {e}")
            self.is_running = False
            raise
    
    async def stop(self):
        """텔레그램 봇 중지"""
        try:
            if self.application and self.is_running:
                logger.info("🛑 텔레그램 봇 중지 중...")
                
                # 종료 메시지 전송
                await self.send_message("🛑 AutoTrade 시스템이 종료됩니다.")
                
                # 폴링 태스크 취소
                if self.polling_task and not self.polling_task.done():
                    self.polling_task.cancel()
                    try:
                        await self.polling_task
                    except asyncio.CancelledError:
                        logger.info("텔레그램 폴링 태스크 취소됨")
                
                # 봇 중지
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
                
                self.is_running = False
                logger.info("✅ 텔레그램 봇 중지 완료")
                
        except Exception as e:
            logger.error(f"❌ 텔레그램 봇 중지 오류: {e}")
    
    async def _register_handlers(self):
        """명령어 핸들러 등록"""
        try:
            if not self.application:
                raise RuntimeError("Application이 초기화되지 않았습니다")
            
            # 상태 조회 명령어
            self.application.add_handler(CommandHandler("status", self._handle_status))
            self.application.add_handler(CommandHandler("stocks", self._handle_stocks))
            self.application.add_handler(CommandHandler("summary", self._handle_summary))
            
            # 계좌 정보 명령어
            self.application.add_handler(CommandHandler("balance", self._handle_balance))
            self.application.add_handler(CommandHandler("profit", self._handle_profit))
            self.application.add_handler(CommandHandler("positions", self._handle_positions))
            self.application.add_handler(CommandHandler("trades", self._handle_trades))
            
            # 제어 명령어
            self.application.add_handler(CommandHandler("pause", self._handle_pause))
            self.application.add_handler(CommandHandler("resume", self._handle_resume))
            self.application.add_handler(CommandHandler("stop", self._handle_stop))
            
            # 도움말
            self.application.add_handler(CommandHandler("help", self._handle_help))
            self.application.add_handler(CommandHandler("start", self._handle_help))
            
            logger.info("✅ 텔레그램 명령어 핸들러 등록 완료")
            
            # 🆕 핸들러 등록 확인
            handlers_count = len(self.application.handlers[0])  # 기본 그룹의 핸들러 수
            logger.info(f"📋 등록된 핸들러 수: {handlers_count}개")
            
        except Exception as e:
            logger.error(f"❌ 핸들러 등록 실패: {e}")
            raise
    
    async def send_message(self, message: str):
        """메시지 전송"""
        try:
            if self.application:
                await self.application.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"메시지 전송 실패: {e}")
    
    # === 명령어 핸들러들 ===
    
    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시스템 상태 조회"""
        try:
            if not self.trade_manager:
                await update.message.reply_text("❌ TradeManager가 연결되지 않았습니다.")
                return
            
            status = self.trade_manager.get_system_status()
            
            message = f"""
🤖 <b>AutoTrade 시스템 상태</b>

🔄 <b>시스템</b>
• 실행 상태: {'🟢 실행중' if status.get('system_running') else '🔴 중지'}
• 시장 개장: {'🟢 개장' if status.get('market_open') else '🔴 마감'}
• 거래 시간: {'🟢 거래중' if status.get('trading_time') else '🔴 거래불가'}
• 모니터링: {'🟢 활성' if status.get('monitoring_active') else '🔴 비활성'}

📊 <b>종목 현황</b>
• 선정 종목: {status.get('selected_stocks_count', 0)}개

💰 <b>거래 성과</b>
• 총 거래: {status.get('total_trades', 0)}건
• 승률: {status.get('win_rate', 0):.1f}%
• 총 손익: {status.get('total_pnl', 0):+,.0f}원

⏰ <b>현재 시간</b>
{status.get('current_time', 'N/A')}
            """
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"상태 조회 오류: {e}")
            await update.message.reply_text(f"❌ 상태 조회 실패: {str(e)}")
    
    async def _handle_stocks(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """선정 종목 조회"""
        try:
            if not self.trade_manager:
                await update.message.reply_text("❌ TradeManager가 연결되지 않았습니다.")
                return
            
            selected_stocks = self.trade_manager.stock_manager.get_all_selected_stocks()
            
            if not selected_stocks:
                await update.message.reply_text("📊 현재 선정된 종목이 없습니다.")
                return
            
            message = "📊 <b>선정 종목 목록</b>\n\n"
            
            for i, stock in enumerate(selected_stocks[:10], 1):  # 최대 10개만 표시
                current_price = stock.realtime_data.current_price
                price_change_rate = stock.realtime_data.price_change_rate
                status_emoji = self._get_status_emoji(stock.status)
                
                message += f"{i:2d}. {stock.stock_code} {stock.stock_name}\n"
                message += f"    {status_emoji} {current_price:,}원 ({price_change_rate:+.2f}%)\n"
                message += f"    점수: {stock.total_pattern_score:.1f}\n\n"
            
            if len(selected_stocks) > 10:
                message += f"... 외 {len(selected_stocks) - 10}개 종목"
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"종목 조회 오류: {e}")
            await update.message.reply_text(f"❌ 종목 조회 실패: {str(e)}")
    
    async def _handle_summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """요약 정보 조회"""
        try:
            if not self.trade_manager:
                await update.message.reply_text("❌ TradeManager가 연결되지 않았습니다.")
                return
            
            # 종목 요약
            stock_summary = self.trade_manager.stock_manager.get_stock_summary()
            
            # 거래 통계
            trade_stats = self.trade_manager.trade_executor.get_trade_statistics()
            
            # 모니터링 상태
            monitoring_status = self.trade_manager.realtime_monitor.get_monitoring_status()
            
            message = f"""
📈 <b>AutoTrade 요약</b>

📊 <b>종목 현황</b>
• 총 선정: {stock_summary.get('total_selected', 0)}개
• 관찰중: {stock_summary.get('status_breakdown', {}).get('관찰중', 0)}개
• 보유중: {stock_summary.get('status_breakdown', {}).get('매수완료', 0)}개

💰 <b>거래 성과</b>
• 총 거래: {trade_stats.get('total_trades', 0)}건
• 승률: {trade_stats.get('win_rate', 0):.1f}%
• 평균 수익: {trade_stats.get('avg_profit_per_trade', 0):+,.0f}원
• 총 손익: {trade_stats.get('total_realized_pnl', 0):+,.0f}원

🔍 <b>모니터링</b>
• 스캔 횟수: {monitoring_status.get('market_scan_count', 0):,}회
• 매수 신호: {monitoring_status.get('buy_signals_detected', 0)}건
• 매도 신호: {monitoring_status.get('sell_signals_detected', 0)}건
            """
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"요약 조회 오류: {e}")
            await update.message.reply_text(f"❌ 요약 조회 실패: {str(e)}")
    
    async def _handle_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """계좌 잔고 조회"""
        try:
            from api.kis_market_api import get_account_balance
            
            balance = get_account_balance()
            
            if not balance:
                await update.message.reply_text("❌ 계좌 정보 조회 실패")
                return
            
            message = f"""
💰 <b>계좌 잔고</b>

💵 <b>현금</b>
• 매수가능금액: {balance.get('available_amount', 0):,}원
• 주문가능금액: {balance.get('order_possible_amount', 0):,}원

📊 <b>주식</b>
• 보유주식평가액: {balance.get('stock_value', 0):,}원
• 평가손익: {balance.get('evaluation_pnl', 0):+,}원

💎 <b>총자산</b>
• 총평가금액: {balance.get('total_value', 0):,}원
            """
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"잔고 조회 오류: {e}")
            await update.message.reply_text(f"❌ 잔고 조회 실패: {str(e)}")
    
    async def _handle_profit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """수익 현황 조회"""
        try:
            if not self.trade_manager:
                await update.message.reply_text("❌ TradeManager가 연결되지 않았습니다.")
                return
            
            # 실현 손익
            trade_stats = self.trade_manager.trade_executor.get_trade_statistics()
            
            # 미실현 손익 (보유 중인 종목들)
            bought_stocks = self.trade_manager.stock_manager.get_bought_stocks()
            total_unrealized_pnl = sum(stock.unrealized_pnl or 0 for stock in bought_stocks)
            
            message = f"""
📈 <b>수익 현황</b>

💰 <b>실현 손익</b>
• 총 실현손익: {trade_stats.get('total_realized_pnl', 0):+,.0f}원
• 수익 거래: {trade_stats.get('winning_trades', 0)}건
• 손실 거래: {trade_stats.get('losing_trades', 0)}건
• 승률: {trade_stats.get('win_rate', 0):.1f}%

📊 <b>미실현 손익</b>
• 보유 종목: {len(bought_stocks)}개
• 미실현손익: {total_unrealized_pnl:+,.0f}원

💎 <b>종합</b>
• 총 손익: {trade_stats.get('total_realized_pnl', 0) + total_unrealized_pnl:+,.0f}원
            """
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"수익 조회 오류: {e}")
            await update.message.reply_text(f"❌ 수익 조회 실패: {str(e)}")
    
    async def _handle_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """보유 포지션 조회"""
        try:
            if not self.trade_manager:
                await update.message.reply_text("❌ TradeManager가 연결되지 않았습니다.")
                return
            
            bought_stocks = self.trade_manager.stock_manager.get_bought_stocks()
            
            if not bought_stocks:
                await update.message.reply_text("📊 현재 보유 중인 포지션이 없습니다.")
                return
            
            message = "📊 <b>보유 포지션</b>\n\n"
            
            for i, stock in enumerate(bought_stocks, 1):
                current_price = stock.realtime_data.current_price
                buy_price = stock.buy_price or 0
                pnl = stock.unrealized_pnl or 0
                pnl_rate = stock.unrealized_pnl_rate or 0
                
                pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                
                message += f"{i}. {stock.stock_code} {stock.stock_name}\n"
                message += f"   매수: {buy_price:,}원 → 현재: {current_price:,}원\n"
                message += f"   {pnl_emoji} {pnl:+,.0f}원 ({pnl_rate:+.2f}%)\n\n"
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"포지션 조회 오류: {e}")
            await update.message.reply_text(f"❌ 포지션 조회 실패: {str(e)}")
    
    async def _handle_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """최근 거래 내역 조회"""
        try:
            if not self.trade_manager:
                await update.message.reply_text("❌ TradeManager가 연결되지 않았습니다.")
                return
            
            recent_trades = self.trade_manager.trade_executor.get_recent_trades_summary(5)
            
            if not recent_trades.get('trades'):
                await update.message.reply_text("📊 최근 거래 내역이 없습니다.")
                return
            
            message = "📊 <b>최근 거래 내역</b>\n\n"
            
            for i, trade in enumerate(recent_trades['trades'], 1):
                result_emoji = "🟢" if trade['is_winning'] else "🔴"
                
                message += f"{i}. {trade['stock_code']} {trade.get('stock_name', '')}\n"
                message += f"   {trade['buy_price']:,}원 → {trade['sell_price']:,}원\n"
                message += f"   {result_emoji} {trade['realized_pnl']:+,.0f}원 ({trade['pnl_rate']:+.2f}%)\n"
                message += f"   사유: {trade['sell_reason']}\n\n"
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"거래 내역 조회 오류: {e}")
            await update.message.reply_text(f"❌ 거래 내역 조회 실패: {str(e)}")
    
    async def _handle_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시스템 일시정지"""
        try:
            if not self.trade_manager:
                await update.message.reply_text("❌ TradeManager가 연결되지 않았습니다.")
                return
            
            # 모니터링 일시정지
            if self.trade_manager.realtime_monitor.is_monitoring:
                self.trade_manager.realtime_monitor.is_monitoring = False
                await update.message.reply_text("⏸️ 실시간 모니터링이 일시정지되었습니다.")
            else:
                await update.message.reply_text("ℹ️ 이미 모니터링이 중지된 상태입니다.")
            
        except Exception as e:
            logger.error(f"일시정지 오류: {e}")
            await update.message.reply_text(f"❌ 일시정지 실패: {str(e)}")
    
    async def _handle_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시스템 재개"""
        try:
            if not self.trade_manager:
                await update.message.reply_text("❌ TradeManager가 연결되지 않았습니다.")
                return
            
            # 모니터링 재개
            if not self.trade_manager.realtime_monitor.is_monitoring:
                self.trade_manager.realtime_monitor.is_monitoring = True
                await update.message.reply_text("▶️ 실시간 모니터링이 재개되었습니다.")
            else:
                await update.message.reply_text("ℹ️ 이미 모니터링이 실행 중입니다.")
            
        except Exception as e:
            logger.error(f"재개 오류: {e}")
            await update.message.reply_text(f"❌ 재개 실패: {str(e)}")
    
    async def _handle_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시스템 종료"""
        try:
            await update.message.reply_text("🛑 시스템 종료가 요청되었습니다. 안전하게 종료 중...")
            
            if self.trade_manager:
                # 비동기 종료 (별도 태스크로 실행)
                asyncio.create_task(self.trade_manager.stop_async_system())
            
        except Exception as e:
            logger.error(f"시스템 종료 오류: {e}")
            await update.message.reply_text(f"❌ 시스템 종료 실패: {str(e)}")
    
    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """도움말 표시"""
        # 🆕 메시지 수신 정보 로깅
        user_id = update.effective_user.id if update.effective_user else 'Unknown'
        chat_id = update.effective_chat.id if update.effective_chat else 'Unknown'
        message_text = update.message.text if update.message else 'No text'
        
        logger.info(f"📞 /help 명령어 수신:")
        logger.info(f"   - 사용자 ID: {user_id}")
        logger.info(f"   - 채팅 ID: {chat_id}")
        logger.info(f"   - 설정된 Chat ID: {self.chat_id}")
        logger.info(f"   - 메시지: {message_text}")
        logger.info(f"   - Chat ID 일치: {str(chat_id) == str(self.chat_id)}")
        
        help_message = """
🤖 <b>AutoTrade 텔레그램 봇 명령어</b>

📊 <b>상태 조회</b>
/status - 시스템 전체 상태
/stocks - 선정 종목 목록
/summary - 요약 정보

💰 <b>계좌 정보</b>
/balance - 계좌 잔고
/profit - 수익 현황
/positions - 보유 포지션
/trades - 최근 거래 내역

⚙️ <b>제어</b>
/pause - 모니터링 일시정지
/resume - 모니터링 재개
/stop - 시스템 종료

❓ <b>기타</b>
/help - 이 도움말
        """
        
        try:
            await update.message.reply_text(help_message, parse_mode='HTML')
            logger.info("✅ /help 응답 전송 완료")
        except Exception as e:
            logger.error(f"❌ /help 응답 전송 실패: {e}")
            await update.message.reply_text("❌ 도움말 표시 중 오류가 발생했습니다.")
    
    def _get_status_emoji(self, status) -> str:
        """상태에 따른 이모지 반환"""
        status_map = {
            "관찰중": "👀",
            "매수준비": "🟡",
            "매수주문": "🟠", 
            "매수완료": "🟢",
            "매도준비": "🟡",
            "매도주문": "🟠",
            "매도완료": "⚫"
        }
        return status_map.get(status.value if hasattr(status, 'value') else str(status), "❓")
