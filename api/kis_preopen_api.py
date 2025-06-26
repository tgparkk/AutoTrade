"""KIS 장전/시간외 시세 API 래퍼

한국투자증권 OpenAPI 의 시간외·장전용 REST 엔드포인트를 간편하게 호출하기 위한 헬퍼 함수 모음.
실전/모의 도메인은 kis._url_fetch 가 자동 처리하므로 URL 과 TR_ID 만 지정하면 된다.

> 구현된 함수
- get_preopen_time_itemconclusion
- get_preopen_daily_overtimeprice
- get_preopen_time_overtimeconclusion
- get_preopen_overtime_price
- get_preopen_overtime_asking_price

각 함수는 pandas.DataFrame (또는 실패 시 None/빈 DF) 를 반환한다.
"""
from typing import Optional
import pandas as pd
from utils.logger import setup_logger
from utils.korean_time import now_kst

# 기존 인증/요청 헬퍼 재사용
from api import kis_auth as kis

logger = setup_logger(__name__)


def _fetch(url: str, tr_id: str, params: dict) -> Optional[pd.DataFrame]:
    """공통 fetch 래퍼 - 성공 시 DataFrame 반환"""
    try:
        res = kis._url_fetch(url, tr_id, "", params)
        if res and res.isOK():
            body = res.getBody()
            output = getattr(body, "output", getattr(body, "Output", []))
            if output:
                return pd.DataFrame(output)
            logger.debug(f"{tr_id} 데이터 없음")
            return pd.DataFrame()
        logger.error(f"{tr_id} 호출 실패")
        return None
    except Exception as e:
        logger.error(f"{tr_id} 호출 오류: {e}")
        return None

# ------------------------------------------------------------
# 1. 분·초 체결(장전 / 시간외 구분 없음, inqr_hour 로 08 전달)
# ------------------------------------------------------------

def get_preopen_time_itemconclusion(itm_no: str, inqr_hour: str = "08") -> Optional[pd.DataFrame]:
    """08시대 분·초 단위 체결내역 (TR: FHKST01010900)"""
    url = "/uapi/domestic-stock/v1/quotations/inquire-time-itemconclusion"
    tr_id = "FHKST01010900"
    params = {
        "FID_INPUT_ISCD": itm_no,
        "FID_INPUT_HOUR_1": inqr_hour,  # 08 ~09 사이 시간 지정
        "FID_PW_DATA_INDV_YN": "N",
        "FID_PERIOD_DIV_CODE": "1"  # 1:시간, 2:일자
    }
    return _fetch(url, tr_id, params)

# ------------------------------------------------------------
# 2. 전일 시간외 단일가 종가
# ------------------------------------------------------------

def get_preopen_daily_overtimeprice(itm_no: str) -> Optional[pd.DataFrame]:
    """전일 시간외 단일가 종가 (TR: FHKST01040200)"""
    url = "/uapi/domestic-stock/v1/quotations/inquire-daily-overtimeprice"
    tr_id = "FHKST01040200"
    params = {"FID_INPUT_ISCD": itm_no}
    return _fetch(url, tr_id, params)

# ------------------------------------------------------------
# 3. 시간외 단일가 분봉 체결내역
# ------------------------------------------------------------

def get_preopen_time_overtimeconclusion(itm_no: str, inqr_hour: str = "16") -> Optional[pd.DataFrame]:
    """16~18시 시간외 단일가 분·초 체결 (TR: FHKST01040500)"""
    url = "/uapi/domestic-stock/v1/quotations/inquire-time-overtimeconclusion"
    tr_id = "FHKST01040500"
    params = {
        "FID_INPUT_ISCD": itm_no,
        "FID_INPUT_HOUR_1": inqr_hour,
        "FID_PW_DATA_INDV_YN": "N",
        "FID_PERIOD_DIV_CODE": "1"
    }
    return _fetch(url, tr_id, params)

# ------------------------------------------------------------
# 4. 시간외 단일가 현재가 (17시 기준)
# ------------------------------------------------------------

def get_preopen_overtime_price(itm_no: str) -> Optional[pd.DataFrame]:
    """시간외 단일가 현재가 (TR: FHKST01040100)"""
    url = "/uapi/domestic-stock/v1/quotations/inquire-overtime-price"
    tr_id = "FHKST01040100"
    params = {"FID_INPUT_ISCD": itm_no}
    return _fetch(url, tr_id, params)

# ------------------------------------------------------------
# 5. 시간외 단일가 호가
# ------------------------------------------------------------

def get_preopen_overtime_asking_price(itm_no: str) -> Optional[pd.DataFrame]:
    """시간외 단일가 호가/잔량 (TR: FHKST01040300)"""
    url = "/uapi/domestic-stock/v1/quotations/inquire-overtime-asking-price"
    tr_id = "FHKST01040300"
    params = {"FID_INPUT_ISCD": itm_no, "FID_FCNG_DT": now_kst().strftime("%Y%m%d")}
    return _fetch(url, tr_id, params)

__all__ = [
    "get_preopen_time_itemconclusion",
    "get_preopen_daily_overtimeprice",
    "get_preopen_time_overtimeconclusion",
    "get_preopen_overtime_price",
    "get_preopen_overtime_asking_price",
] 