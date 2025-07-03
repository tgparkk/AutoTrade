"""
로깅 시스템
날짜별 로그 파일 생성 및 관리
"""
import os
import sys
import functools
from datetime import datetime
from pathlib import Path
from typing import Optional
from loguru import logger
from utils.korean_time import now_kst, now_kst_timestamp, now_kst_str, now_kst_time_str

# 기본 설정값
DEFAULT_LOG_LEVEL = 'DEBUG'
DEFAULT_RETENTION_DAYS = 30

class LoggerSetup:
    """로거 설정 및 관리 클래스"""

    # 로그 레벨
    LOG_LEVELS = {
        'DEBUG': 'DEBUG',
        'INFO': 'INFO',
        'WARNING': 'WARNING',
        'ERROR': 'ERROR',
        'CRITICAL': 'CRITICAL'
    }

    # 로그 포맷
    LOG_FORMAT = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # 특수 로그 카테고리
    CATEGORIES = {
        'trade': '거래',
        'order': '주문',
        'error': '에러',
        'system': '시스템',
        'strategy': '전략',
        'websocket': '웹소켓',
        'telegram': '텔레그램'
    }

    def __init__(self):
        """초기화"""
        self.log_dir = Path("logs")
        self.log_level = DEFAULT_LOG_LEVEL
        self.retention_days = DEFAULT_RETENTION_DAYS

        # 로그 디렉토리 생성
        self._create_log_directories()

        # 기본 로거 설정
        self._setup_default_logger()

    def _create_log_directories(self):
        """로그 디렉토리 생성"""
        # 메인 로그 디렉토리
        self.log_dir.mkdir(exist_ok=True)

        # 날짜별 디렉토리
        today = now_kst().strftime('%Y-%m-%d')
        today_dir = self.log_dir / today
        today_dir.mkdir(exist_ok=True)

        # 카테고리별 디렉토리
        for category in self.CATEGORIES.keys():
            category_dir = today_dir / category
            category_dir.mkdir(exist_ok=True)

    def _setup_default_logger(self):
        """기본 로거 설정"""
        # 기존 핸들러 제거
        logger.remove()

        # 콘솔 출력
        logger.add(
            sys.stdout,
            format=self.LOG_FORMAT,
            level=self.log_level,
            colorize=True
        )

        # 전체 로그 파일 (날짜별)
        today = now_kst().strftime('%Y-%m-%d')
        logger.add(
            f"logs/{today}/all.log",
            format=self.LOG_FORMAT,
            level=self.log_level,
            rotation="00:00",  # 매일 자정에 로테이션
            retention=f"{self.retention_days} days",
            encoding="utf-8"
        )

        # 에러 전용 로그
        logger.add(
            f"logs/{today}/error/errors.log",
            format=self.LOG_FORMAT,
            level="ERROR",
            rotation="00:00",
            retention=f"{self.retention_days} days",
            encoding="utf-8"
        )

    def get_category_logger(self, category: str) -> logger:
        """카테고리별 로거 생성"""
        if category not in self.CATEGORIES:
            category = 'system'

        today = now_kst().strftime('%Y-%m-%d')
        log_file = f"logs/{today}/{category}/{category}.log"

        # 카테고리별 로그 파일 추가
        logger.add(
            log_file,
            format=self.LOG_FORMAT,
            level=self.log_level,
            rotation="00:00",
            retention=f"{self.retention_days} days",
            encoding="utf-8",
            filter=lambda record: record["extra"].get("category") == category
        )

        return logger.bind(category=category)


# 전역 로거 인스턴스
_logger_setup = None


def setup_logger(name: Optional[str] = None, category: Optional[str] = None) -> logger:
    """
    로거 설정 및 반환

    Args:
        name: 로거 이름 (모듈명 등)
        category: 로그 카테고리 (trade, order, error 등)

    Returns:
        설정된 로거 인스턴스
    """
    global _logger_setup

    # 최초 실행시 설정
    if _logger_setup is None:
        _logger_setup = LoggerSetup()

    # 카테고리별 로거 반환
    if category:
        return _logger_setup.get_category_logger(category)

    # 기본 로거 반환
    if name:
        return logger.bind(name=name)

    return logger


# 거래 관련 로그 함수
def log_trade(action: str, stock_code: str, quantity: int,
              price: float, **kwargs):
    """
    거래 로그 기록

    Args:
        action: 거래 액션 (BUY, SELL, CANCEL 등)
        stock_code: 종목코드
        quantity: 수량
        price: 가격
        **kwargs: 추가 정보
    """
    trade_logger = setup_logger(category='trade')

    log_data = {
        'action': action,
        'stock_code': stock_code,
        'quantity': quantity,
        'price': price,
        'timestamp': now_kst().isoformat(),
        **kwargs
    }

    trade_logger.info(f"거래 실행: {action} {stock_code} {quantity}주 @{price:,.0f}원 | {log_data}")


def log_order(order_type: str, order_no: str, stock_code: str,
              status: str, **kwargs):
    """
    주문 로그 기록

    Args:
        order_type: 주문 유형 (매수, 매도, 정정, 취소)
        order_no: 주문번호
        stock_code: 종목코드
        status: 주문 상태
        **kwargs: 추가 정보
    """
    order_logger = setup_logger(category='order')

    log_data = {
        'order_type': order_type,
        'order_no': order_no,
        'stock_code': stock_code,
        'status': status,
        'timestamp': now_kst().isoformat(),
        **kwargs
    }

    order_logger.info(f"주문 처리: {order_type} {stock_code} [{order_no}] {status} | {log_data}")


def log_strategy(strategy_name: str, signal: str, stock_code: str,
                score: float, **kwargs):
    """
    전략 로그 기록

    Args:
        strategy_name: 전략명
        signal: 신호 (BUY, SELL, HOLD)
        stock_code: 종목코드
        score: 신호 점수
        **kwargs: 추가 정보
    """
    strategy_logger = setup_logger(category='strategy')

    log_data = {
        'strategy': strategy_name,
        'signal': signal,
        'stock_code': stock_code,
        'score': score,
        'timestamp': now_kst().isoformat(),
        **kwargs
    }

    strategy_logger.info(f"전략 신호: {strategy_name} {stock_code} {signal} (점수: {score:.2f}) | {log_data}")


def log_error(error_type: str, message: str, **kwargs):
    """
    에러 로그 기록

    Args:
        error_type: 에러 유형
        message: 에러 메시지
        **kwargs: 추가 정보
    """
    error_logger = setup_logger(category='error')

    log_data = {
        'error_type': error_type,
        'message': message,
        'timestamp': now_kst().isoformat(),
        **kwargs
    }

    error_logger.error(f"시스템 에러: {error_type} - {message} | {log_data}")


def log_websocket(event: str, stock_code: Optional[str] = None, **kwargs):
    """
    웹소켓 로그 기록

    Args:
        event: 웹소켓 이벤트
        stock_code: 종목코드 (선택사항)
        **kwargs: 추가 정보
    """
    ws_logger = setup_logger(category='websocket')

    log_data = {
        'event': event,
        'stock_code': stock_code,
        'timestamp': now_kst().isoformat(),
        **kwargs
    }

    ws_logger.info(f"웹소켓: {event} {stock_code or ''} | {log_data}")


def log_telegram(action: str, user_id: Optional[str] = None,
                command: Optional[str] = None, **kwargs):
    """
    텔레그램 로그 기록

    Args:
        action: 텔레그램 액션
        user_id: 사용자 ID
        command: 명령어
        **kwargs: 추가 정보
    """
    tg_logger = setup_logger(category='telegram')

    log_data = {
        'action': action,
        'user_id': user_id,
        'command': command,
        'timestamp': now_kst().isoformat(),
        **kwargs
    }

    tg_logger.info(f"텔레그램: {action} {command or ''} | {log_data}")


# 데코레이터
def log_execution_time(func):
    """함수 실행 시간 로깅 데코레이터"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = now_kst_timestamp()
        try:
            result = func(*args, **kwargs)
            end_time = now_kst_timestamp()
            execution_time = end_time - start_time
            
            logger.debug(f"함수 실행 완료: {func.__name__} ({execution_time:.3f}초)")
            return result
        except Exception as e:
            end_time = now_kst_timestamp()
            execution_time = end_time - start_time
            logger.error(f"함수 실행 실패: {func.__name__} ({execution_time:.3f}초) - {e}")
            raise
    return wrapper


def log_exceptions(func):
    """예외 로깅 데코레이터"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            log_error(
                error_type=type(e).__name__,
                message=str(e),
                function=func.__name__,
                args=str(args),
                kwargs=str(kwargs)
            )
            raise
    return wrapper


def generate_daily_report(date: Optional[str] = None):
    """
    일일 리포트 생성

    Args:
        date: 날짜 (YYYY-MM-DD), None이면 오늘
    """
    if date is None:
        date = now_kst().strftime('%Y-%m-%d')

    report_logger = setup_logger(category='system')
    report_logger.info(f"일일 리포트 생성 시작: {date}")

    # 리포트 로직 구현...
    # (실제 구현은 추후 필요시 추가)

    report_logger.info(f"일일 리포트 생성 완료: {date}")


# === 성능 최적화를 위한 로그 레벨 체크 함수 ===
def is_debug_enabled() -> bool:
    """디버그 로그가 활성화되어 있는지 확인"""
    return DEFAULT_LOG_LEVEL == 'DEBUG'


def is_info_enabled() -> bool:
    """인포 로그가 활성화되어 있는지 확인"""
    return DEFAULT_LOG_LEVEL in ['DEBUG', 'INFO']
