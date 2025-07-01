#!/usr/bin/env python3
"""
체결 통보 처리 전용 모듈

주요 기능:
- KIS 웹소켓 체결 통보 파싱 및 처리
- 매수/매도 체결 처리 및 상태 업데이트
- 부분 체결 및 전량 체결 관리
- 데이터베이스 저장 및 통계 업데이트

성능 최적화:
- 스레드 안전한 체결 처리
- 가중 평균 단가 계산
- 웹소켓 구독 자동 해제
"""

import threading
from typing import Dict, Optional, Callable, TYPE_CHECKING
from datetime import datetime
from models.stock import StockStatus
from utils.korean_time import now_kst
from utils.logger import setup_logger

if TYPE_CHECKING:
    from typing import Any

logger = setup_logger(__name__)


class _ExecutionProcessor:
    """체결 통보 처리 전용 클래스"""
    
    def __init__(self, 
                 # 데이터 저장소들
                 trading_status: Dict[str, StockStatus],
                 trade_info: Dict[str, dict],
                 stock_metadata: Dict[str, dict],
                 
                 # 락들
                 status_lock: threading.RLock,
                 
                 # 콜백 함수들
                 cache_invalidator_func: Callable[[str], None],
                 status_changer_func: Callable[..., bool],
                 database_getter_func: Callable[[], "Any"],
                 market_phase_getter_func: Callable[[], str],
                 
                 # 선택적 참조들
                 realtime_monitor_ref: Optional["Any"] = None,
                 websocket_manager_ref: Optional["Any"] = None):
        """ExecutionProcessor 초기화
        
        Args:
            trading_status: 종목별 거래 상태 딕셔너리
            trade_info: 종목별 거래 정보 딕셔너리
            stock_metadata: 종목 메타데이터 딕셔너리
            status_lock: 상태 변경용 락
            cache_invalidator_func: 캐시 무효화 함수
            status_changer_func: 상태 변경 함수
            database_getter_func: 데이터베이스 인스턴스 획득 함수
            market_phase_getter_func: 현재 시장 단계 획득 함수
            realtime_monitor_ref: RealTimeMonitor 참조 (통계용)
            websocket_manager_ref: WebSocket 매니저 참조 (구독 해제용)
        """
        # 데이터 저장소
        self.trading_status = trading_status
        self.trade_info = trade_info
        self.stock_metadata = stock_metadata
        
        # 락
        self._status_lock = status_lock
        
        # 콜백 함수들
        self._cache_invalidator = cache_invalidator_func
        self._status_changer = status_changer_func
        self._get_database = database_getter_func
        self._get_market_phase = market_phase_getter_func
        
        # 선택적 참조들
        self._realtime_monitor_ref = realtime_monitor_ref
        self._websocket_manager_ref = websocket_manager_ref
        
        logger.info("✅ ExecutionProcessor 초기화 완료")
    
    def set_realtime_monitor_ref(self, realtime_monitor_ref):
        """RealTimeMonitor 참조 설정"""
        self._realtime_monitor_ref = realtime_monitor_ref
    
    def set_websocket_manager_ref(self, websocket_manager_ref):
        """WebSocket 매니저 참조 설정"""
        self._websocket_manager_ref = websocket_manager_ref
    
    def handle_execution_notice(self, data_type: str, data: Dict):
        """체결 통보 처리 - KIS 공식 문서 기준 필드명 사용"""
        try:
            # 체결통보 데이터는 'data' 키 안에 중첩되어 있을 수 있음
            actual_data = data.get('data', data)
            
            # 데이터가 문자열인 경우 파싱이 필요할 수 있음
            if isinstance(actual_data, str):
                logger.debug(f"체결통보 원본 데이터: {actual_data}")
                
                # 🔥 KIS 공식 문서 기준 체결통보 파싱 (wikidocs 참조)
                # menulist = "고객ID|계좌번호|주문번호|원주문번호|매도매수구분|정정구분|주문종류|주문조건|주식단축종목코드|체결수량|체결단가|주식체결시간|거부여부|체결여부|접수여부|지점번호|주문수량|계좌명|체결종목명|신용구분|신용대출일자|체결종목명40|주문가격"
                parts = actual_data.split('^')
                if len(parts) >= 23:  # 최소 필드 수 확인
                    # KIS 공식 순서대로 파싱
                    customer_id = parts[0]           # 고객ID
                    account_no = parts[1]            # 계좌번호
                    order_no = parts[2]              # 주문번호
                    orig_order_no = parts[3]         # 원주문번호
                    sell_buy_dvsn = parts[4]         # 매도매수구분
                    ord_dvsn = parts[5]              # 정정구분
                    ord_kind = parts[6]              # 주문종류
                    ord_cond = parts[7]              # 주문조건
                    stock_code = parts[8]            # 주식단축종목코드
                    exec_qty = int(parts[9]) if parts[9] else 0        # 체결수량
                    exec_price = float(parts[10]) if parts[10] else 0  # 체결단가
                    exec_time = parts[11]            # 주식체결시간
                    reject_yn = parts[12]            # 거부여부
                    exec_yn = parts[13]              # 체결여부 (1:주문·정정·취소·거부, 2:체결)
                    receipt_yn = parts[14]           # 접수여부
                    branch_no = parts[15]            # 지점번호
                    ord_qty = int(parts[16]) if parts[16] else 0       # 주문수량
                    account_name = parts[17]         # 계좌명
                    exec_stock_name = parts[18]      # 체결종목명
                    credit_dvsn = parts[19]          # 신용구분
                    credit_loan_date = parts[20]     # 신용대출일자
                    exec_stock_name_40 = parts[21]   # 체결종목명40
                    ord_price = float(parts[22]) if parts[22] else 0   # 주문가격
                    
                    # CNTG_YN 필드(체결여부) 확인 (actual_data가 dict인 경우)
                    eflag = '2'
                    if isinstance(actual_data, dict):
                        eflag = actual_data.get('exec_yn', '2')
                    if eflag != '2':
                        logger.debug(f"체결 아님(CNTG_YN={eflag}) - 무시: {stock_code}")
                        return

                    # 이후 처리에 필요한 필드 (딕셔너리인 경우에만 덮어씀)
                    if isinstance(actual_data, dict):
                        ord_type = actual_data.get('ord_gno_brno', '')
                        sell_buy_dvsn = actual_data.get('sll_buy_dvsn_cd', '')
                    else:
                        ord_type = ''  # 정보 없음

                    # 파싱된 데이터로 체결통보 정보 구성
                    parsed_notice = {
                        'mksc_shrn_iscd': stock_code,        # 종목코드
                        'exec_prce': exec_price,             # 체결가격
                        'exec_qty': exec_qty,                # 체결수량
                        'sll_buy_dvsn_cd': sell_buy_dvsn,    # 매도매수구분
                        'ord_no': order_no,                  # 주문번호
                        'ord_gno_brno': branch_no,           # 주문채번지점번호
                        'exec_time': exec_time,              # 체결시간
                        'reject_yn': reject_yn,              # 거부여부
                        'exec_yn': exec_yn,                  # 체결여부
                        'receipt_yn': receipt_yn,            # 접수여부
                        'account_no': account_no,            # 계좌번호
                        'customer_id': customer_id,          # 고객ID
                        'ord_qty': ord_qty,                  # 주문수량
                        'ord_price': ord_price,              # 주문가격
                        'exec_stock_name': exec_stock_name,  # 종목명
                        'timestamp': now_kst()               # 처리시간
                    }
                    actual_data = parsed_notice
                else:
                    logger.warning(f"체결통보 필드 부족: {len(parts)}개 (최소 23개 필요)")
                    return
            
            # 기존 로직과 호환되도록 처리
            stock_code = actual_data.get('mksc_shrn_iscd', '').strip()
            if not stock_code or stock_code not in self.trading_status:
                logger.debug(f"체결통보 - 관리 대상이 아닌 종목: {stock_code}")
                return
            
            exec_price = float(actual_data.get('exec_prce', 0))
            exec_qty = int(actual_data.get('exec_qty', 0))
            sell_buy_dvsn = actual_data.get('sll_buy_dvsn_cd', '')
            ord_type = actual_data.get('ord_gno_brno', '')
            
            # CNTG_YN 필드(체결여부) 확인 (actual_data가 dict인 경우)
            eflag = '2'
            if isinstance(actual_data, dict):
                eflag = actual_data.get('exec_yn', '2')
            if eflag != '2':
                logger.debug(f"체결 아님(CNTG_YN={eflag}) - 무시: {stock_code}")
                return

            if exec_price <= 0 or exec_qty <= 0:
                logger.warning(f"체결통보 - 잘못된 데이터: {stock_code} 가격:{exec_price} 수량:{exec_qty}")
                return
            
            current_status = self.trading_status.get(stock_code)
            logger.info(f"📢 체결 통보: {stock_code} {exec_qty}주 @{exec_price:,}원 "
                       f"구분:{sell_buy_dvsn} 현재상태:{current_status.value if current_status else 'None'}")
            
            # 🔥 실제 종목 상태 업데이트
            if sell_buy_dvsn == '02':  # 매수 체결
                self._handle_buy_execution(stock_code, exec_price, exec_qty, ord_type)
            elif sell_buy_dvsn == '01':  # 매도 체결
                self._handle_sell_execution(stock_code, exec_price, exec_qty, ord_type)
            else:
                logger.warning(f"알 수 없는 매도매수구분: {sell_buy_dvsn}")

            # 매도 전량 체결 후 웹소켓 구독 해제
            if exec_qty == 0 and self._websocket_manager_ref:
                try:
                    if hasattr(self._websocket_manager_ref, 'unsubscribe_stock_sync'):
                        unsub_success = self._websocket_manager_ref.unsubscribe_stock_sync(stock_code)
                        if unsub_success:
                            logger.info(f"📡 웹소켓 구독 해제 성공: {stock_code}")
                        else:
                            logger.warning(f"⚠️ 웹소켓 구독 해제 실패: {stock_code}")
                except Exception as ws_e:
                    logger.error(f"웹소켓 구독 해제 오류 {stock_code}: {ws_e}")

        except Exception as e:
            logger.error(f"체결 통보 처리 오류: {e}")
            logger.debug(f"체결통보 데이터 구조: {data}")
            import traceback
            logger.debug(f"스택 트레이스: {traceback.format_exc()}")
    
    def _handle_buy_execution(self, stock_code: str, exec_price: float, exec_qty: int, ord_type: str):
        """매수 체결 처리"""
        try:
            current_status = self.trading_status.get(stock_code)

            if current_status not in [StockStatus.BUY_ORDERED, StockStatus.PARTIAL_BOUGHT]:
                logger.warning(
                    f"매수 체결이지만 주문 상태가 예상과 다름: {stock_code} 상태:{current_status.value if current_status else 'None'}")

            # ------------------------------
            # 누적 체결 정보 업데이트
            # ------------------------------
            with self._status_lock:
                info = self.trade_info.get(stock_code, {})

                # 최초 주문 수량이 기록되지 않았다면 buy_quantity 필드 또는 exec_qty 로 대체
                if info.get('ordered_qty') is None:
                    ordered_qty = info.get('buy_quantity') or exec_qty
                    info['ordered_qty'] = ordered_qty
                else:
                    ordered_qty = info['ordered_qty']

                filled_prev = info.get('filled_qty', 0) or 0
                filled_new = filled_prev + exec_qty
                remaining_qty = max(ordered_qty - filled_new, 0)

                # 가중 평균 단가 계산
                if filled_prev == 0:
                    avg_price = exec_price
                else:
                    prev_avg = info.get('avg_exec_price', exec_price)
                    avg_price = (prev_avg * filled_prev + exec_price * exec_qty) / filled_new

                # trade_info 반영
                info['filled_qty'] = filled_new
                info['remaining_qty'] = remaining_qty
                info['avg_exec_price'] = avg_price
                info['buy_price'] = avg_price  # 최종 평단을 buy_price 로 사용
                info['execution_time'] = now_kst()

                # 최초 체결 시 주문 시간 정보 보정 (order_time 없으면 현재시각)
                if 'order_time' not in info or info['order_time'] is None:
                    info['order_time'] = now_kst()
                    self.trade_info[stock_code] = info
                    # Stock 객체에도 반영 (캐시 무효화 후 재생성 방식)
                    self._cache_invalidator(stock_code)

            # ------------------------------
            # 상태 결정
            # ------------------------------
            new_status = StockStatus.BOUGHT if remaining_qty == 0 else StockStatus.PARTIAL_BOUGHT

            success = self._status_changer(
                stock_code=stock_code,
                new_status=new_status,
                reason="buy_executed_partial" if remaining_qty else "buy_executed_full",
                buy_price=avg_price,
                buy_quantity=filled_new,
                buy_amount=avg_price * filled_new
            )

            if success:
                # DB 저장 (부분 체결도 저장하여 누적 기록)
                try:
                    database = self._get_database()
                    metadata = self.stock_metadata.get(stock_code, {})
                    trade_info = self.trade_info.get(stock_code, {})

                    database.save_buy_execution_to_db(
                        stock_code=stock_code,
                        exec_price=exec_price,
                        exec_qty=exec_qty,
                        stock_metadata=metadata,
                        trade_info=trade_info,
                        get_current_market_phase_func=self._get_market_phase
                    )
                except Exception as db_e:
                    logger.error(f"❌ 매수 체결 DB 저장 오류 {stock_code}: {db_e}")

                if self._realtime_monitor_ref and remaining_qty == 0:
                    self._realtime_monitor_ref.buy_orders_executed += 1

                logger.info(
                    f"✅ 매수 체결 처리: {stock_code} {exec_qty}주 @{exec_price:,}원 (누적 {filled_new}/{ordered_qty}주, 잔량 {remaining_qty})")
            else:
                logger.error(f"❌ 매수 체결 상태 업데이트 실패: {stock_code}")

        except Exception as e:
            logger.error(f"매수 체결 처리 오류 {stock_code}: {e}")
    
    def _handle_sell_execution(self, stock_code: str, exec_price: float, exec_qty: int, ord_type: str):
        """매도 체결 처리"""
        try:
            current_status = self.trading_status.get(stock_code)

            if current_status not in [StockStatus.SELL_ORDERED, StockStatus.PARTIAL_SOLD]:
                logger.warning(
                    f"매도 체결이지만 주문 상태가 예상과 다름: {stock_code} 상태:{current_status.value if current_status else 'None'}")

            with self._status_lock:
                info = self.trade_info.get(stock_code, {})

                # 최초 매도 주문 수량 기록
                if info.get('ordered_qty') is None:
                    ordered_qty = info.get('sell_quantity') or exec_qty
                    info['ordered_qty'] = ordered_qty
                else:
                    ordered_qty = info['ordered_qty']

                filled_prev = info.get('filled_qty', 0) or 0
                filled_new = filled_prev + exec_qty
                remaining_qty = max(ordered_qty - filled_new, 0)

                # 평균 체결가(매도)는 가중 평균 필요 X 하지만 기록 일관성 유지
                if filled_prev == 0:
                    avg_price = exec_price
                else:
                    prev_avg = info.get('avg_exec_price', exec_price)
                    avg_price = (prev_avg * filled_prev + exec_price * exec_qty) / filled_new

                info['filled_qty'] = filled_new
                info['remaining_qty'] = remaining_qty
                info['avg_exec_price'] = avg_price
                info['sell_price'] = avg_price
                info['sell_execution_time'] = now_kst()

            # 🆕 Stock 객체의 보유 수량 동기화 (캐시 무효화 후 재생성 방식)
            self._cache_invalidator(stock_code)

            # 손익 계산 — buy_price 는 평단, buy_quantity 는 총수량로 가정
            buy_price = info.get('buy_price', 0)
            buy_total_qty = info.get('buy_quantity', 0) or info.get('ordered_qty', 0)

            realized_pnl = 0
            realized_pnl_rate = 0
            if buy_price > 0 and buy_total_qty > 0:
                realized_pnl = (avg_price - buy_price) * filled_new
                realized_pnl_rate = (avg_price - buy_price) / buy_price * 100

            new_status = StockStatus.SOLD if remaining_qty == 0 else StockStatus.PARTIAL_SOLD

            now_ts = now_kst()
            success = self._status_changer(
                stock_code=stock_code,
                new_status=new_status,
                reason="sell_executed_partial" if remaining_qty else "sell_executed_full",
                sell_price=avg_price,
                sell_execution_time=now_ts,
                sell_order_time=now_ts,
                realized_pnl=realized_pnl,
                realized_pnl_rate=realized_pnl_rate
            )

            if success:
                try:
                    database = self._get_database()
                    metadata = self.stock_metadata.get(stock_code, {})
                    trade_info = self.trade_info.get(stock_code, {})

                    database.save_sell_execution_to_db(
                        stock_code=stock_code,
                        exec_price=exec_price,
                        exec_qty=exec_qty,
                        realized_pnl=realized_pnl,
                        realized_pnl_rate=realized_pnl_rate,
                        stock_metadata=metadata,
                        trade_info=trade_info,
                        get_current_market_phase_func=self._get_market_phase
                    )
                except Exception as db_e:
                    logger.error(f"❌ 매도 체결 DB 저장 오류 {stock_code}: {db_e}")

                if self._realtime_monitor_ref and remaining_qty == 0:
                    self._realtime_monitor_ref.sell_orders_executed += 1

                logger.info(
                    f"✅ 매도 체결 처리: {stock_code} {exec_qty}주 @{exec_price:,}원 (누적 {filled_new}/{ordered_qty}주, 잔량 {remaining_qty})")

                # 매도 전량 체결 후 웹소켓 구독 해제
                if remaining_qty == 0 and self._websocket_manager_ref:
                    try:
                        if hasattr(self._websocket_manager_ref, 'unsubscribe_stock_sync'):
                            unsub_success = self._websocket_manager_ref.unsubscribe_stock_sync(stock_code)
                            if unsub_success:
                                logger.info(f"📡 웹소켓 구독 해제 성공: {stock_code}")
                            else:
                                logger.warning(f"⚠️ 웹소켓 구독 해제 실패: {stock_code}")
                    except Exception as ws_e:
                        logger.error(f"웹소켓 구독 해제 오류 {stock_code}: {ws_e}")

            else:
                logger.error(f"❌ 매도 체결 상태 업데이트 실패: {stock_code}")
        except Exception as e:
            logger.error(f"매도 체결 처리 오류 {stock_code}: {e}") 