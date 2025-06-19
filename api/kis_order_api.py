"""
KIS API 주문 관련 함수 (공식 문서 기반)
"""
import time
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, List
from utils.logger import setup_logger
from . import kis_auth as kis

logger = setup_logger(__name__)


def get_order_cash(ord_dv: str = "", itm_no: str = "", qty: int = 0, unpr: int = 0,
                   tr_cont: str = "") -> Optional[pd.DataFrame]:
    """주식주문(현금) - 매수/매도"""
    url = '/uapi/domestic-stock/v1/trading/order-cash'

    if ord_dv == "buy":
        tr_id = "TTTC0802U"  # 주식 현금 매수 주문 [모의투자] VTTC0802U
    elif ord_dv == "sell":
        tr_id = "TTTC0801U"  # 주식 현금 매도 주문 [모의투자] VTTC0801U
    else:
        logger.error("매수/매도 구분 확인 필요")
        return None

    if not itm_no:
        logger.error("주문종목번호 확인 필요")
        return None

    if qty == 0:
        logger.error("주문수량 확인 필요")
        return None

    if unpr == 0:
        logger.error("주문단가 확인 필요")
        return None

    params = {
        "CANO": kis.getTREnv().my_acct,         # 계좌번호 8자리
        "ACNT_PRDT_CD": kis.getTREnv().my_prod, # 계좌상품코드 2자리
        "PDNO": itm_no,                         # 종목코드(6자리)
        "ORD_DVSN": "00",                       # 주문구분 00:지정가, 01:시장가
        "ORD_QTY": str(int(qty)),               # 주문주식수
        "ORD_UNPR": str(int(unpr))              # 주문단가
    }

    res = kis._url_fetch(url, tr_id, tr_cont, params, postFlag=True)

    if res and res.isOK():
        current_data = pd.DataFrame(res.getBody().output, index=[0])
        return current_data
    else:
        if res:
            logger.error(f"{res.getErrorCode()}, {res.getErrorMessage()}")
        return None


def get_order_rvsecncl(ord_orgno: str = "", orgn_odno: str = "", ord_dvsn: str = "",
                       rvse_cncl_dvsn_cd: str = "", ord_qty: int = 0, ord_unpr: int = 0,
                       qty_all_ord_yn: str = "", tr_cont: str = "") -> Optional[pd.DataFrame]:
    """주식주문(정정취소) - 신 TR ID 사용"""
    url = '/uapi/domestic-stock/v1/trading/order-rvsecncl'
    tr_id = "TTTC0013U"  # 🆕 신 TR ID (구: TTTC0803U)

    if not ord_orgno:
        logger.error("주문조직번호 확인 필요")
        return None

    if not orgn_odno:
        logger.error("원주문번호 확인 필요")
        return None

    if not ord_dvsn:
        logger.error("주문구분 확인 필요")
        return None

    if rvse_cncl_dvsn_cd not in ["01", "02"]:
        logger.error("정정취소구분코드 확인 필요 (정정:01, 취소:02)")
        return None

    if qty_all_ord_yn == "Y" and ord_qty > 0:
        logger.warning("잔량전부 취소/정정주문인 경우 주문수량 0 처리")
        ord_qty = 0

    if qty_all_ord_yn == "N" and ord_qty == 0:
        logger.error("취소/정정 수량 확인 필요")
        return None

    if rvse_cncl_dvsn_cd == "01" and ord_unpr == 0:
        logger.error("주문단가 확인 필요")
        return None

    params = {
        "CANO": kis.getTREnv().my_acct,
        "ACNT_PRDT_CD": kis.getTREnv().my_prod,
        "KRX_FWDG_ORD_ORGNO": ord_orgno,        # 한국거래소전송주문조직번호
        "ORGN_ODNO": orgn_odno,                 # 원주문번호
        "ORD_DVSN": ord_dvsn,                   # 주문구분
        "RVSE_CNCL_DVSN_CD": rvse_cncl_dvsn_cd, # 정정:01, 취소:02
        "ORD_QTY": str(int(ord_qty)),           # 주문주식수
        "ORD_UNPR": str(int(ord_unpr)),         # 주문단가
        "QTY_ALL_ORD_YN": qty_all_ord_yn        # 잔량전부주문여부
    }

    res = kis._url_fetch(url, tr_id, tr_cont, params, postFlag=True)

    if res and res.isOK():
        current_data = pd.DataFrame(res.getBody().output, index=[0])
        return current_data
    else:
        if res:
            logger.error(f"{res.getErrorCode()}, {res.getErrorMessage()}")
        return None


def get_inquire_psbl_rvsecncl_lst(tr_cont: str = "", FK100: str = "", NK100: str = "",
                                  dataframe: Optional[pd.DataFrame] = None) -> Optional[pd.DataFrame]:
    """주식정정취소가능주문조회 (페이징 지원)"""
    url = '/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl'
    tr_id = "TTTC8036R"

    params = {
        "CANO": kis.getTREnv().my_acct,
        "ACNT_PRDT_CD": kis.getTREnv().my_prod,
        "INQR_DVSN_1": "1",                     # 조회구분1 0:조회순서, 1:주문순, 2:종목순
        "INQR_DVSN_2": "0",                     # 조회구분2 0:전체, 1:매도, 2:매수
        "CTX_AREA_FK100": FK100,
        "CTX_AREA_NK100": NK100
    }

    res = kis._url_fetch(url, tr_id, tr_cont, params)

    if not res or not res.isOK():
        logger.error("정정취소가능주문조회 실패")
        return dataframe

    current_data = pd.DataFrame(res.getBody().output)

    # 기존 데이터와 병합
    if dataframe is not None:
        dataframe = pd.concat([dataframe, current_data], ignore_index=True)
    else:
        dataframe = current_data

    # 페이징 처리
    tr_cont = res.getHeader().tr_cont
    FK100 = res.getBody().ctx_area_fk100
    NK100 = res.getBody().ctx_area_nk100

    if tr_cont in ("D", "E"):  # 마지막 페이지
        logger.debug("정정취소가능주문조회 완료")
        return dataframe
    elif tr_cont in ("F", "M"):  # 다음 페이지 존재
        logger.debug("다음 페이지 조회 중...")
        time.sleep(0.1)  # 시스템 안정성을 위한 지연
        return get_inquire_psbl_rvsecncl_lst("N", FK100, NK100, dataframe)

    return dataframe


def get_inquire_daily_ccld_obj(dv: str = "01", inqr_strt_dt: Optional[str] = None,
                               inqr_end_dt: Optional[str] = None, tr_cont: str = "",
                               FK100: str = "", NK100: str = "") -> Optional[pd.DataFrame]:
    """주식일별주문체결조회 - 요약 정보"""
    url = '/uapi/domestic-stock/v1/trading/inquire-daily-ccld'

    if dv == "01":
        tr_id = "TTTC8001R"  # 3개월 이내
    else:
        tr_id = "CTSC9115R"  # 3개월 이전

    if inqr_strt_dt is None:
        inqr_strt_dt = datetime.today().strftime("%Y%m%d")
    if inqr_end_dt is None:
        inqr_end_dt = datetime.today().strftime("%Y%m%d")

    params = {
        "CANO": kis.getTREnv().my_acct,
        "ACNT_PRDT_CD": kis.getTREnv().my_prod,
        "INQR_STRT_DT": inqr_strt_dt,           # 조회시작일자
        "INQR_END_DT": inqr_end_dt,             # 조회종료일자
        "SLL_BUY_DVSN_CD": "00",                # 매도매수구분 00:전체
        "INQR_DVSN": "01",                      # 조회구분 00:역순, 01:정순
        "PDNO": "",                             # 종목번호
        "CCLD_DVSN": "00",                      # 체결구분 00:전체
        "ORD_GNO_BRNO": "",                     # 사용안함
        "ODNO": "",                             # 주문번호
        "INQR_DVSN_3": "00",                    # 조회구분3 00:전체
        "INQR_DVSN_1": "0",                     # 조회구분1
        "CTX_AREA_FK100": FK100,
        "CTX_AREA_NK100": NK100
    }

    res = kis._url_fetch(url, tr_id, tr_cont, params)

    if res and res.isOK():
        current_data = pd.DataFrame(res.getBody().output2, index=[0])
        return current_data
    else:
        logger.error("주식일별주문체결조회 실패")
        return None


def get_inquire_daily_ccld_lst(dv: str = "01", inqr_strt_dt: str = "", inqr_end_dt: str = "",
                               ccld_dvsn: str = "00", tr_cont: str = "", FK100: str = "", NK100: str = "",
                               dataframe: Optional[pd.DataFrame] = None) -> Optional[pd.DataFrame]:
    """주식일별주문체결조회 - 상세 목록 (페이징 지원)

    Args:
        ccld_dvsn: 체결구분 ('00':전체, '01':체결, '02':미체결)
    """
    url = '/uapi/domestic-stock/v1/trading/inquire-daily-ccld'

    if dv == "01":
        tr_id = "TTTC8001R"  # 3개월 이내
    else:
        tr_id = "CTSC9115R"  # 3개월 이전

    if inqr_strt_dt == "":
        inqr_strt_dt = datetime.today().strftime("%Y%m%d")
    if inqr_end_dt == "":
        inqr_end_dt = datetime.today().strftime("%Y%m%d")

    params = {
        "CANO": kis.getTREnv().my_acct,
        "ACNT_PRDT_CD": kis.getTREnv().my_prod,
        "INQR_STRT_DT": inqr_strt_dt,
        "INQR_END_DT": inqr_end_dt,
        "SLL_BUY_DVSN_CD": "00",                # 매도매수구분 00:전체
        "INQR_DVSN": "01",                      # 조회구분 01:정순
        "PDNO": "",                             # 종목번호
        "CCLD_DVSN": ccld_dvsn,                 # 체결구분 00:전체, 01:체결, 02:미체결
        "ORD_GNO_BRNO": "",
        "ODNO": "",
        "INQR_DVSN_3": "00",
        "INQR_DVSN_1": "",
        "CTX_AREA_FK100": FK100,
        "CTX_AREA_NK100": NK100
    }

    res = kis._url_fetch(url, tr_id, tr_cont, params)

    if not res or not res.isOK():
        logger.error("주식일별주문체결조회 실패")
        return dataframe

    current_data = pd.DataFrame(res.getBody().output1)

    # 기존 데이터와 병합
    if dataframe is not None:
        dataframe = pd.concat([dataframe, current_data], ignore_index=True)
    else:
        dataframe = current_data

    # 페이징 처리
    tr_cont = res.getHeader().tr_cont
    FK100 = res.getBody().ctx_area_fk100
    NK100 = res.getBody().ctx_area_nk100

    if tr_cont in ("D", "E"):  # 마지막 페이지
        logger.debug("주식일별주문체결조회 완료")
        return dataframe
    elif tr_cont in ("F", "M"):  # 다음 페이지 존재
        logger.debug("다음 페이지 조회 중...")
        time.sleep(0.1)
        return get_inquire_daily_ccld_lst(dv, inqr_strt_dt, inqr_end_dt, ccld_dvsn, "N", FK100, NK100, dataframe)

    return dataframe

# ========== 🆕 미체결 주문 관리 함수들 ==========

async def check_and_cancel_external_orders(kis_api_manager) -> None:
    """🆕 KIS API로 전체 미체결 주문 조회 및 취소 (외부 매수 포함)"""
    try:
        from datetime import datetime, timedelta

        # 당일 주문 조회
        today_orders = kis_api_manager.get_today_orders()

        if not today_orders:
            logger.debug("📋 조회된 당일 주문이 없습니다")
            return

        logger.info(f"📋 당일 주문 조회 결과: {len(today_orders)}건")

        current_time = datetime.now()
        stale_order_timeout = 300  # 5분

        for order_info in today_orders:
            try:
                # 주문 기본 정보 추출
                stock_code = order_info.get('pdno', '')  # 상품번호 (종목코드)
                order_no = order_info.get('odno', '')    # 주문번호
                order_date = order_info.get('ord_dt', '')  # 주문일자
                order_time = order_info.get('ord_tmd', '')  # 주문시각 (HHMMSS)

                # 체결 상태 정보
                total_qty = int(order_info.get('ord_qty', 0))       # 주문수량
                filled_qty = int(order_info.get('tot_ccld_qty', 0)) # 총체결수량
                remaining_qty = int(order_info.get('rmn_qty', 0))   # 잔여수량
                cancel_yn = order_info.get('cncl_yn', 'N')          # 취소여부

                # 매수/매도 구분
                buy_sell_code = order_info.get('sll_buy_dvsn_cd', '')  # 01:매도, 02:매수
                buy_sell_name = order_info.get('sll_buy_dvsn_cd_name', '')

                order_price = int(order_info.get('ord_unpr', 0))    # 주문단가
                product_name = order_info.get('prdt_name', '')      # 상품명

                # 🚨 미체결 주문 필터링 (잔여수량 > 0, 취소되지 않음)
                if remaining_qty <= 0 or cancel_yn == 'Y':
                    continue

                # 🚨 주문 시간 계산 (당일 주문만 대상)
                if order_date and order_time and len(order_time) >= 6:
                    order_datetime_str = f"{order_date} {order_time[:2]}:{order_time[2:4]}:{order_time[4:6]}"
                    order_datetime = datetime.strptime(order_datetime_str, "%Y%m%d %H:%M:%S")

                    # 주문 경과 시간 계산
                    elapsed_seconds = (current_time - order_datetime).total_seconds()

                    if elapsed_seconds < stale_order_timeout:
                        continue  # 5분 미만이면 아직 취소 안함

                    minutes_elapsed = elapsed_seconds / 60

                    logger.warning(f"⏰ {stock_code}({product_name}) 미체결 주문 발견: "
                                 f"{buy_sell_name} {remaining_qty}주 {order_price:,}원 "
                                 f"(경과: {minutes_elapsed:.1f}분)")

                    # 🎯 미체결 주문 취소 실행
                    cancel_result = await cancel_external_order(
                        kis_api_manager, stock_code, order_no, buy_sell_code,
                        remaining_qty, product_name
                    )

                    if cancel_result:
                        logger.info(f"✅ {stock_code} 외부 미체결 주문 취소 성공")
                    else:
                        logger.warning(f"❌ {stock_code} 외부 미체결 주문 취소 실패")

            except Exception as e:
                logger.error(f"❌ 주문 정보 처리 오류: {e}")
                continue

    except Exception as e:
        logger.error(f"❌ 외부 미체결 주문 체크 오류: {e}")

async def cancel_external_order(kis_api_manager, stock_code: str, order_no: str,
                              buy_sell_code: str, remaining_qty: int, product_name: str) -> bool:
    """🆕 외부 미체결 주문 취소 실행 (개선된 버전)"""
    try:
        # 🎯 1단계: 저장된 주문 정보에서 KRX_FWDG_ORD_ORGNO 찾기
        logger.debug(f"🔍 {stock_code} 저장된 주문 정보에서 조직번호 조회 중...")
        
        ord_orgno = ""
        ord_dvsn = "00"  # 기본값
        
        # TradingManager의 pending_orders에서 찾기 (kis_api_manager를 통해 접근)
        if hasattr(kis_api_manager, 'trading_manager') and hasattr(kis_api_manager.trading_manager, 'pending_orders'):
            pending_orders = kis_api_manager.trading_manager.pending_orders
            if order_no in pending_orders:
                order_info = pending_orders[order_no]
                ord_orgno = order_info.get('krx_fwdg_ord_orgno', '')
                # 주문 데이터에서 주문구분 추출
                order_data = order_info.get('order_data', {})
                ord_dvsn = order_data.get('ord_dvsn', '00')
                logger.info(f"📋 {stock_code} 저장된 주문정보에서 조직번호 획득: {ord_orgno}")
        
        # 저장된 정보에서 찾지 못한 경우, 당일 주문 조회에서 찾기
        if not ord_orgno:
            logger.debug(f"🔍 {stock_code} 당일 주문 조회에서 조직번호 찾는 중...")
            today_orders = kis_api_manager.get_today_orders(include_filled=True)
            
            for order_info in today_orders:
                if str(order_info.get('odno', '')) == str(order_no):
                    # 당일 주문 조회에서는 KRX_FWDG_ORD_ORGNO가 없을 수 있으므로
                    # 다른 필드명들도 확인
                    ord_orgno = str(order_info.get('krx_fwdg_ord_orgno', '') or 
                                   order_info.get('ord_orgno', '') or 
                                   order_info.get('KRX_FWDG_ORD_ORGNO', ''))
                    ord_dvsn = str(order_info.get('ord_dvsn', '00'))
                    logger.info(f"📋 {stock_code} 당일주문에서 조직번호 획득: {ord_orgno}")
                    break

        # 여전히 찾지 못한 경우 정정취소가능주문조회 시도 (마지막 수단)
        if not ord_orgno:
            logger.debug(f"🔍 {stock_code} 정정취소가능주문조회로 조직번호 찾는 중...")
            cancelable_orders = get_inquire_psbl_rvsecncl_lst()
            
            if cancelable_orders is not None and len(cancelable_orders) > 0:
                for _, order in cancelable_orders.iterrows():
                    if str(order.get('odno', '')) == str(order_no):
                        ord_orgno = str(order.get('krx_fwdg_ord_orgno', '') or 
                                       order.get('ord_orgno', '') or 
                                       order.get('KRX_FWDG_ORD_ORGNO', ''))
                        ord_dvsn = str(order.get('ord_dvsn', '00'))
                        psbl_qty = int(order.get('psbl_qty', 0) or order.get('rmn_qty', 0))
                        logger.info(f"📋 {stock_code} 정정취소가능주문에서 조직번호 획득: {ord_orgno}")
                        
                        if psbl_qty <= 0:
                            logger.warning(f"⚠️ {stock_code} 정정취소가능수량이 0 - 이미 처리된 주문")
                            return False
                        break

        # 🎯 2단계: 주문조직번호 검증
        if not ord_orgno:
            logger.error(f"❌ {stock_code} 주문조직번호를 찾을 수 없음 (주문번호: {order_no})")
            return False

        logger.debug(f"📋 {stock_code} 주문정보: 조직번호={ord_orgno}, 구분={ord_dvsn}")

        # 🎯 3단계: 주문 취소 실행
        cancel_result = kis_api_manager.cancel_order(
            order_no=order_no,
            ord_orgno=ord_orgno,    # 🆕 저장된 주문조직번호 사용
            ord_dvsn=ord_dvsn,      # 🆕 정확한 주문구분 사용
            qty_all_ord_yn="Y"      # 전량 취소
        )

        if cancel_result and isinstance(cancel_result, dict) and cancel_result.get('status') == 'success':
            logger.info(f"✅ {stock_code}({product_name}) 외부 주문 취소 성공 (주문번호: {order_no})")
            return True
        else:
            error_msg = cancel_result.get('message', 'Unknown error') if isinstance(cancel_result, dict) else 'API call failed'
            logger.error(f"❌ {stock_code} 외부 주문 취소 실패: {error_msg}")
            return False

    except Exception as e:
        logger.error(f"❌ {stock_code} 외부 주문 취소 처리 오류: {e}")
        return False
