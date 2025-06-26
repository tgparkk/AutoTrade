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
    """공통 fetch 래퍼 – 성공 시 DataFrame 반환

    * 여러 output 필드(`output`, `output1`, `output2` …)를 모두 수집해 단일
      `pd.DataFrame` 으로 병합한다.
    """
    try:
        res = kis._url_fetch(url, tr_id, "", params)
        if res and res.isOK():
            body = res.getBody()

            # output* 속성을 모두 모은다.
            records = []
            for attr in dir(body):
                if attr.lower().startswith("output"):
                    val = getattr(body, attr)
                    if isinstance(val, list):
                        records.extend(val)
                    elif isinstance(val, dict):
                        records.append(val)

            if records:
                return pd.DataFrame(records)

            logger.debug(f"{tr_id} 데이터 없음")
            return pd.DataFrame()

        logger.error(f"{tr_id} 호출 실패 – rt_cd={getattr(res.getBody(), 'rt_cd', '?') if res else 'N/A'}")
        return None
    except Exception as e:
        logger.error(f"{tr_id} 호출 오류: {e}")
        return None

# ------------------------------------------------------------
# 1. 분·초 체결(장전 / 시간외 구분 없음, inqr_hour 로 08 전달)
# ------------------------------------------------------------

def get_preopen_time_itemconclusion(itm_no: str, inqr_hour: str = "08") -> Optional[pd.DataFrame]:
    """당일 시간대별 체결 (TR: FHPST01060000)

    Args:
        itm_no: 종목코드 (6자리)
        inqr_hour: 조회 기준 시작 HH(또는 HHMMSS) – 장전 08, 장중 09 등
    """
    url = "/uapi/domestic-stock/v1/quotations/inquire-time-itemconclusion"
    tr_id = "FHPST01060000"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",   # 주식
        "FID_INPUT_ISCD": itm_no,
        "FID_INPUT_HOUR_1": inqr_hour.zfill(2),
    }
    return _fetch(url, tr_id, params)

# ------------------------------------------------------------
# 2. 전일 시간외 단일가 종가
# ------------------------------------------------------------

def get_preopen_daily_overtimeprice(itm_no: str) -> Optional[pd.DataFrame]:
    """최근 30일 시간외 단일가 일자별 종가 (TR: FHPST02320000)"""
    url = "/uapi/domestic-stock/v1/quotations/inquire-daily-overtimeprice"
    tr_id = "FHPST02320000"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",  # 주식
        "FID_INPUT_ISCD": itm_no,
    }
    return _fetch(url, tr_id, params)

# ------------------------------------------------------------
# 3. 시간외 단일가 분봉 체결내역
# ------------------------------------------------------------

def get_preopen_time_overtimeconclusion(itm_no: str) -> Optional[pd.DataFrame]:
    """시간외 단일가 시간별 체결 (TR: FHPST02310000)"""
    url = "/uapi/domestic-stock/v1/quotations/inquire-time-overtimeconclusion"
    tr_id = "FHPST02310000"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": itm_no,
        "FID_HOUR_CLS_CODE": "1",  # 1: 시간외
    }
    return _fetch(url, tr_id, params)

# ------------------------------------------------------------
# 4. 시간외 단일가 현재가 (17시 기준)
# ------------------------------------------------------------

def get_preopen_overtime_price(itm_no: str) -> Optional[pd.DataFrame]:
    """시간외 단일가 현재가 (TR: FHPST02300000 – 실전 전용)"""
    url = "/uapi/domestic-stock/v1/quotations/inquire-overtime-price"
    tr_id = "FHPST02300000"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": itm_no,
    }
    return _fetch(url, tr_id, params)

# ------------------------------------------------------------
# 5. 시간외 단일가 호가
# ------------------------------------------------------------

def get_preopen_overtime_asking_price(itm_no: str) -> Optional[pd.DataFrame]:
    """시간외 단일가 호가/잔량 (TR: FHPST02300400 – 실전 전용)"""
    url = "/uapi/domestic-stock/v1/quotations/inquire-overtime-asking-price"
    tr_id = "FHPST02300400"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": itm_no,
    }
    return _fetch(url, tr_id, params)

__all__ = [
    "get_preopen_time_itemconclusion",
    "get_preopen_daily_overtimeprice",
    "get_preopen_time_overtimeconclusion",
    "get_preopen_overtime_price",
    "get_preopen_overtime_asking_price",
] 