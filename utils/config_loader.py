"""
거래 설정 파일 로더
trading_config.ini에서 거래 전략, 리스크 관리, 캔들패턴 등의 설정을 읽어오는 유틸리티
"""
import configparser
import os
from datetime import time
from typing import Dict, List, Optional
from utils.logger import setup_logger

logger = setup_logger(__name__)

class TradingConfigLoader:
    """거래 설정 파일 로더"""
    
    def __init__(self, config_path: str = "config/trading_config.ini"):
        """
        거래 설정 파일 로더 초기화
        
        Args:
            config_path: 거래 설정 파일 경로
        """
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self._load_config()
    
    def _load_config(self):
        """설정 파일 로드"""
        try:
            if os.path.exists(self.config_path):
                self.config.read(self.config_path, encoding='utf-8')
                logger.info(f"거래 설정 파일 로드 완료: {self.config_path}")
            else:
                logger.warning(f"거래 설정 파일을 찾을 수 없습니다: {self.config_path}")
                logger.info("기본 설정값을 사용합니다.")
        except Exception as e:
            logger.error(f"거래 설정 파일 로드 실패: {e}")
    
    def reload_config(self):
        """설정 파일 재로드"""
        self.config.clear()
        self._load_config()
        logger.info("거래 설정 파일 재로드 완료")
    
    def get_value(self, key: str, section: str, default: str = "") -> str:
        """
        설정값 가져오기
        
        Args:
            key: 설정 키
            section: 섹션명
            default: 기본값
            
        Returns:
            설정값 문자열
        """
        try:
            value = self.config.get(section, key, fallback=default)
            # 주석 제거 (# 문자 이후 제거)
            if '#' in value:
                value = value.split('#')[0]
            return value.strip().strip('"')
        except Exception as e:
            logger.warning(f"설정값 로드 실패 ({section}.{key}): {e}, 기본값 사용: {default}")
            return default
    
    def get_bool(self, key: str, section: str, default: bool = False) -> bool:
        """불린 설정값 가져오기"""
        value = self.get_value(key, section, str(default)).lower()
        return value in ('true', '1', 'yes', 'on')
    
    def get_int(self, key: str, section: str, default: int = 0) -> int:
        """정수 설정값 가져오기"""
        try:
            return int(self.get_value(key, section, str(default)))
        except ValueError:
            logger.warning(f"정수 변환 실패 ({section}.{key}), 기본값 사용: {default}")
            return default
    
    def get_float(self, key: str, section: str, default: float = 0.0) -> float:
        """실수 설정값 가져오기"""
        try:
            return float(self.get_value(key, section, str(default)))
        except ValueError:
            logger.warning(f"실수 변환 실패 ({section}.{key}), 기본값 사용: {default}")
            return default
    
    def load_trading_strategy_config(self) -> Dict:
        """
        거래 전략 설정 로드
        
        Returns:
            거래 전략 설정 딕셔너리
        """
        section = 'TRADING_STRATEGY'
        strategy_config = {
            'trading_mode': self.get_value('TRADING_MODE', section, 'day'),
            'day_trading_exit_time': self.get_value('DAY_TRADING_EXIT_TIME', section, '15:00'),
            'volume_increase_threshold': self.get_float('VOLUME_INCREASE_THRESHOLD', section, 2.0),
            'volume_min_threshold': self.get_int('VOLUME_MIN_THRESHOLD', section, 100000),
            'candle_technical_strategy_enabled': self.get_bool('CANDLE_TECHNICAL_STRATEGY_ENABLED', section, True),
            'min_signal_confidence': self.get_float('MIN_SIGNAL_CONFIDENCE', section, 0.7),
            'multiple_indicator_confirm': self.get_bool('MULTIPLE_INDICATOR_CONFIRM', section, True),
            'max_holding_days': self.get_int('MAX_HOLDING_DAYS', section, 1),
            'next_day_force_sell': self.get_bool('NEXT_DAY_FORCE_SELL', section, True),
            'overnight_holding_allowed': self.get_bool('OVERNIGHT_HOLDING_ALLOWED', section, True),
            'test_mode': self.get_bool('test_mode', section, True)  # 테스트 모드 설정 추가
        }
        
        logger.info("거래 전략 설정 로드 완료")
        return strategy_config
    
    def load_risk_management_config(self) -> Dict:
        """
        리스크 관리 설정 로드
        
        Returns:
            리스크 관리 설정 딕셔너리
        """
        section = 'RISK_MANAGEMENT'
        risk_config = {
            'stop_loss_rate': self.get_float('STOP_LOSS_RATE', section, -0.02),
            'take_profit_rate': self.get_float('TAKE_PROFIT_RATE', section, 0.015),
            'min_position_size': self.get_float('MIN_POSITION_SIZE', section, 0.10),
            'max_position_size': self.get_float('MAX_POSITION_SIZE', section, 1000000),
            'max_daily_loss': self.get_float('MAX_DAILY_LOSS', section, -100000),
            'max_positions': self.get_int('MAX_POSITIONS', section, 5),
            'market_crash_protection': self.get_bool('MARKET_CRASH_PROTECTION', section, True),
            'market_crash_threshold': self.get_float('MARKET_CRASH_THRESHOLD', section, -0.03),
            # 매수 금액 설정
            'base_investment_amount': self.get_float('BASE_INVESTMENT_AMOUNT', section, 1000000),
            'position_size_ratio': self.get_float('POSITION_SIZE_RATIO', section, 0.1),
            'use_account_ratio': self.get_bool('USE_ACCOUNT_RATIO', section, False),
            'opening_reduction_ratio': self.get_float('OPENING_REDUCTION_RATIO', section, 0.5),
            'preclose_reduction_ratio': self.get_float('PRECLOSE_REDUCTION_RATIO', section, 0.3),
            'conservative_ratio': self.get_float('CONSERVATIVE_RATIO', section, 0.7)
        }
        
        logger.info("리스크 관리 설정 로드 완료")
        return risk_config
    
    def load_market_schedule_config(self) -> Dict:
        """
        시장 일정 설정 로드
        
        Returns:
            시장 일정 설정 딕셔너리
        """
        section = 'MARKET_SCHEDULE'
        schedule_config = {
            'market_open_time': self.get_value('MARKET_OPEN_TIME', section, '09:00'),
            'market_close_time': self.get_value('MARKET_CLOSE_TIME', section, '15:30'),
            'pre_market_scan_time': self.get_value('PRE_MARKET_SCAN_TIME', section, '08:30'),
            'trading_enabled': self.get_bool('TRADING_ENABLED', section, True),
            'weekend_trading': self.get_bool('WEEKEND_TRADING', section, False),
            'lunch_break_start': self.get_value('LUNCH_BREAK_START', section, '12:00'),
            'lunch_break_end': self.get_value('LUNCH_BREAK_END', section, '13:00'),
            'lunch_break_trading': self.get_bool('LUNCH_BREAK_TRADING', section, False)
        }
        
        logger.info("시장 일정 설정 로드 완료")
        return schedule_config
    
    def load_candle_pattern_config(self) -> Dict:
        """
        캔들패턴 분석 설정 로드
        
        Returns:
            캔들패턴 설정 딕셔너리
        """
        section = 'CANDLE_PATTERN'
        pattern_config = {
            'analysis_enabled': self.get_bool('PATTERN_ANALYSIS_ENABLED', section, True),
            'min_reliability': self.get_float('MIN_PATTERN_RELIABILITY', section, 0.7),
            'bullish_patterns': {
                'bullish_engulfing': self.get_float('BULLISH_ENGULFING_WEIGHT', section, 1.0),
                'hammer': self.get_float('HAMMER_WEIGHT', section, 0.8),
                'morning_star': self.get_float('MORNING_STAR_WEIGHT', section, 1.2),
                'piercing_line': self.get_float('PIERCING_LINE_WEIGHT', section, 0.9)
            },
            'bearish_patterns': {
                'bearish_engulfing': self.get_float('BEARISH_ENGULFING_WEIGHT', section, 1.0),
                'shooting_star': self.get_float('SHOOTING_STAR_WEIGHT', section, 0.8),
                'evening_star': self.get_float('EVENING_STAR_WEIGHT', section, 1.2),
                'dark_cloud_cover': self.get_float('DARK_CLOUD_COVER_WEIGHT', section, 0.9)
            },
            'neutral_patterns': {
                'doji': self.get_float('DOJI_WEIGHT', section, 0.5),
                'spinning_top': self.get_float('SPINNING_TOP_WEIGHT', section, 0.3)
            }
        }
        
        logger.info("캔들패턴 설정 로드 완료")
        return pattern_config
    
    def load_technical_indicators_config(self) -> Dict:
        """
        기술적 지표 설정 로드
        
        Returns:
            기술적 지표 설정 딕셔너리
        """
        section = 'TECHNICAL_INDICATORS'
        indicators_config = {
            'moving_averages': {
                'short_period': self.get_int('MA_SHORT_PERIOD', section, 5),
                'long_period': self.get_int('MA_LONG_PERIOD', section, 20),
                'crossover_enabled': self.get_bool('MA_CROSSOVER_ENABLED', section, True)
            },
            'rsi': {
                'period': self.get_int('RSI_PERIOD', section, 14),
                'oversold': self.get_int('RSI_OVERSOLD', section, 30),
                'overbought': self.get_int('RSI_OVERBOUGHT', section, 70)
            },
            'macd': {
                'fast': self.get_int('MACD_FAST', section, 12),
                'slow': self.get_int('MACD_SLOW', section, 26),
                'signal': self.get_int('MACD_SIGNAL', section, 9)
            },
            'bollinger_bands': {
                'period': self.get_int('BB_PERIOD', section, 20),
                'std_dev': self.get_float('BB_STD_DEV', section, 2.0)
            },
            'volume': {
                'ma_period': self.get_int('VOLUME_MA_PERIOD', section, 20),
                'spike_threshold': self.get_float('VOLUME_SPIKE_THRESHOLD', section, 2.0)
            }
        }
        
        logger.info("기술적 지표 설정 로드 완료")
        return indicators_config
    
    def load_notification_config(self) -> Dict:
        """
        알림 설정 로드
        
        Returns:
            알림 설정 딕셔너리
        """
        section = 'NOTIFICATION'
        notification_config = {
            'trade_notification': self.get_bool('TRADE_NOTIFICATION', section, True),
            'error_notification': self.get_bool('ERROR_NOTIFICATION', section, True),
            'daily_report': self.get_bool('DAILY_REPORT', section, True),
            'max_notifications_per_hour': self.get_int('MAX_NOTIFICATIONS_PER_HOUR', section, 10),
            'duplicate_interval': self.get_int('DUPLICATE_NOTIFICATION_INTERVAL', section, 300)
        }
        
        logger.info("알림 설정 로드 완료")
        return notification_config
    
    def load_performance_config(self) -> Dict:
        """
        성능 설정 로드
        
        Returns:
            성능 설정 딕셔너리
        """
        section = 'PERFORMANCE'
        performance_config = {
            'cache_ttl_seconds': self.get_float('cache_ttl_seconds', section, 2.0),
            'price_cache_size': self.get_int('price_cache_size', section, 100),
            'enable_cache_debug': self.get_bool('enable_cache_debug', section, False),
            'volume_increase_threshold': self.get_float('volume_increase_threshold', section, 2.0),
            'volume_min_threshold': self.get_int('volume_min_threshold', section, 100000),
            'pattern_score_threshold': self.get_float('pattern_score_threshold', section, 70.0),
            'max_holding_days': self.get_int('max_holding_days', section, 1),
            'contract_strength_threshold': self.get_float('contract_strength_threshold', section, 120.0),
            'buy_ratio_threshold': self.get_float('buy_ratio_threshold', section, 60.0),
            'vi_activation_threshold': self.get_bool('vi_activation_threshold', section, True),
            'market_pressure_weight': self.get_float('market_pressure_weight', section, 0.3),
            'spread_threshold': self.get_float('spread_threshold', section, 0.01),
            'weak_contract_strength_threshold': self.get_float('weak_contract_strength_threshold', section, 80.0),
            'low_buy_ratio_threshold': self.get_float('low_buy_ratio_threshold', section, 30.0),
            'high_volatility_threshold': self.get_float('high_volatility_threshold', section, 5.0),
            'price_decline_from_high_threshold': self.get_float('price_decline_from_high_threshold', section, 0.03)
        }
        
        logger.info("성능 설정 로드 완료")
        return performance_config
    
    def load_all_configs(self) -> Dict:
        """
        모든 거래 설정을 통합하여 반환
        
        Returns:
            전체 거래 설정 딕셔너리
        """
        return {
            'trading_strategy': self.load_trading_strategy_config(),
            'risk_management': self.load_risk_management_config(),
            'market_schedule': self.load_market_schedule_config(),
            'candle_pattern': self.load_candle_pattern_config(),
            'technical_indicators': self.load_technical_indicators_config(),
            'notification': self.load_notification_config()
        }

# 이전 버전과의 호환성을 위한 별칭
ConfigLoader = TradingConfigLoader

# 전역 설정 로더 인스턴스
_trading_config_loader = None

def get_trading_config_loader() -> TradingConfigLoader:
    """
    거래 설정 로더 인스턴스 반환 (싱글톤)
    
    Returns:
        TradingConfigLoader 인스턴스
    """
    global _trading_config_loader
    if _trading_config_loader is None:
        _trading_config_loader = TradingConfigLoader()
    return _trading_config_loader

def get_config_loader() -> TradingConfigLoader:
    """이전 버전과의 호환성을 위한 별칭"""
    return get_trading_config_loader()

def reload_trading_config():
    """거래 설정 재로드"""
    global _trading_config_loader
    if _trading_config_loader:
        _trading_config_loader.reload_config()
    else:
        _trading_config_loader = TradingConfigLoader()
    logger.info("전역 거래 설정 재로드 완료")

def reload_config():
    """이전 버전과의 호환성을 위한 별칭"""
    reload_trading_config() 