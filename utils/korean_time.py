"""
한국시간 전용 유틸리티 모듈
모든 now_kst() 사용을 명시적 한국시간으로 통일
"""
from datetime import datetime, time
import pytz

# 한국시간대 상수
KST = pytz.timezone('Asia/Seoul')

def now_kst() -> datetime:
    """한국시간 현재 시각 반환"""
    return datetime.now(KST)

def now_kst_timestamp() -> float:
    """한국시간 현재 시각의 timestamp 반환"""
    return now_kst().timestamp()

def now_kst_str(format_str: str = '%Y-%m-%d %H:%M:%S') -> str:
    """한국시간 현재 시각을 문자열로 반환"""
    return now_kst().strftime(format_str)

def now_kst_date_str() -> str:
    """한국시간 현재 날짜만 문자열로 반환 (YYYY-MM-DD)"""
    return now_kst().strftime('%Y-%m-%d')

def now_kst_time_str() -> str:
    """한국시간 현재 시간만 문자열로 반환 (HH:MM:SS)"""
    return now_kst().strftime('%H:%M:%S')

def now_kst_time() -> time:
    """한국시간 현재 시간만 반환 (time 객체)"""
    return now_kst().time()

def now_kst_iso() -> str:
    """한국시간 현재 시각을 ISO 형식으로 반환"""
    return now_kst().isoformat()
