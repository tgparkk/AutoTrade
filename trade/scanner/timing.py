# -*- coding: utf-8 -*-
"""trade.scanner.timing

데이트레이딩 관점에서 시간대별 유리도를 점수화합니다.
"""
from typing import Tuple

from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)

__all__ = ["calculate_timing_score"]


def calculate_timing_score() -> Tuple[float, str]:
    """현재 KST 시각 기준 타이밍 점수.
    MarketScanner._calculate_daytrading_timing_score 로직을 그대로 분리.
    """
    try:
        current_time = now_kst()
        hour = current_time.hour
        minute = current_time.minute

        # 시간대별 점수 매핑
        if 9 <= hour < 10:
            return (5 if minute <= 30 else 3), ("시초고변동성" if minute <= 30 else "시초후반")
        elif 10 <= hour < 11:
            return 6, "오전안정기"
        elif 11 <= hour < 12:
            return 4, "오전후반"
        elif 13 <= hour < 14:
            return 5, "오후재개장"
        elif 14 <= hour < 15:
            return 6, "오후안정기"
        elif 15 <= hour < 15 and minute <= 20:
            return 3, "마감직전"
        else:
            return 0, ""
    except Exception as e:
        logger.debug(f"타이밍 점수 계산 실패: {e}")
        return 0, "" 