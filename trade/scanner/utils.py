# -*- coding: utf-8 -*-
"""trade.scanner.utils
공통 유틸리티 함수 모음.

MarketScanner 내부에 중복 정의되어 있던 헬퍼들을 외부 모듈로 분리했습니다.
앞으로 장전·장중 스캐너, 기타 분석 모듈에서 공통 사용합니다.
"""
from typing import Any, List, Dict
from utils.logger import setup_logger

logger = setup_logger(__name__)

__all__ = [
    "is_data_empty",
    "get_data_length",
    "convert_to_dict_list",
]


def is_data_empty(data: Any) -> bool:
    """데이터가 비어있는지 안전하게 판정합니다.

    Args:
        data: pandas DataFrame, list, tuple, None 등
    Returns:
        True  - 비어있음 / None / 길이 0
        False - 그 외
    """
    if data is None:
        return True
    # pandas DataFrame 은 empty 속성 보유
    if hasattr(data, "empty"):
        return bool(data.empty)
    # list/tuple/Series 등 길이 확인 가능 객체
    if hasattr(data, "__len__"):
        return len(data) == 0
    # 기타 자료형은 비어있지 않은 것으로 간주
    return False


def get_data_length(data: Any) -> int:
    """데이터 길이를 안전하게 반환합니다."""
    if data is None:
        return 0
    if hasattr(data, "__len__"):
        try:
            return len(data)
        except Exception:
            return 0
    return 0


def convert_to_dict_list(ohlcv_data: Any) -> List[Dict]:
    """OHLCV 데이터(DataFrame 또는 list)를 dict 리스트로 변환합니다.

    DataFrame ➔ dict(orient="records")
    list      ➔ 그대로 반환

    예외/알 수 없는 타입 ➔ 빈 리스트 반환
    """
    if ohlcv_data is None:
        return []

    # pandas DataFrame 처리
    if hasattr(ohlcv_data, "to_dict"):
        try:
            return ohlcv_data.to_dict("records")
        except Exception as e:
            logger.debug(f"DataFrame 변환 실패: {e}")
            return []

    # 이미 list 인 경우
    if isinstance(ohlcv_data, list):
        return ohlcv_data

    logger.debug(f"알 수 없는 데이터 타입: {type(ohlcv_data)}")
    return [] 