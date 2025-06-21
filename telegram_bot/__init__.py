"""
텔레그램 봇 패키지
"""

# 선택적 import - 텔레그램 라이브러리가 없어도 시스템이 동작하도록
try:
    from .telegram_manager import TelegramBot
    __all__ = ['TelegramBot']
except ImportError:
    # 텔레그램 라이브러리가 없는 경우 빈 패키지로 처리
    __all__ = [] 