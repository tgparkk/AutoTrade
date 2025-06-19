"""
포지션 관리를 위한 데이터 모델
"""

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List
from utils.korean_time import now_kst


class PositionStatus(Enum):
    """포지션 상태 열거형"""
    WATCHING = "관찰중"
    BUY_READY = "매수준비"
    BUY_ORDERED = "매수주문"
    BOUGHT = "매수완료"
    SELL_READY = "매도준비"
    SELL_ORDERED = "매도주문"
    SOLD = "매도완료"


@dataclass
class MinuteCandleData:
    """분봉 데이터 구조체"""
    timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    
    def __str__(self) -> str:
        return f"MinuteCandle({self.timestamp.strftime('%H:%M')}: O={self.open_price}, H={self.high_price}, L={self.low_price}, C={self.close_price}, V={self.volume})"


@dataclass
class Position:
    """종목별 포지션 정보를 관리하는 클래스"""
    
    # 기본 정보
    stock_code: str           # 종목코드
    stock_name: str           # 종목명
    
    # 현재 OHLCV 데이터 (일봉 기준)
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    
    # 분봉 데이터 (타임프레임별)
    minute_1_data: List[MinuteCandleData] = field(default_factory=list)   # 1분봉
    minute_5_data: List[MinuteCandleData] = field(default_factory=list)   # 5분봉
    minute_15_data: List[MinuteCandleData] = field(default_factory=list)  # 15분봉
    minute_30_data: List[MinuteCandleData] = field(default_factory=list)  # 30분봉
    minute_60_data: List[MinuteCandleData] = field(default_factory=list)  # 60분봉
    
    # 패턴 분석
    detected_patterns: List[str] = field(default_factory=list)      # 감지된 패턴들
    pattern_scores: Dict[str, float] = field(default_factory=dict)  # 패턴별 점수
    total_pattern_score: float = 0.0                               # 총 패턴 점수
    
    # 기술적 지표
    rsi_value: Optional[float] = None
    macd_value: Optional[float] = None
    macd_signal: Optional[float] = None
    bb_position: Optional[str] = None  # 볼린저밴드 위치 ("upper", "middle", "lower")
    bb_upper: Optional[float] = None   # 볼린저밴드 상단
    bb_middle: Optional[float] = None  # 볼린저밴드 중간
    bb_lower: Optional[float] = None   # 볼린저밴드 하단
    ma_short: Optional[float] = None   # 단기 이동평균
    ma_long: Optional[float] = None    # 장기 이동평균
    volume_ma: Optional[float] = None  # 거래량 이동평균
    volume_spike_ratio: Optional[float] = None  # 거래량 급증 비율
    
    # 거래 정보
    status: PositionStatus = PositionStatus.WATCHING
    buy_price: Optional[float] = None
    buy_quantity: Optional[int] = None
    buy_amount: Optional[float] = None       # 매수금액
    target_price: Optional[float] = None     # 목표가 (익절)
    stop_loss_price: Optional[float] = None  # 손절가
    
    # 주문 정보
    buy_order_id: Optional[str] = None       # 매수 주문번호
    sell_order_id: Optional[str] = None      # 매도 주문번호
    
    # 시간 정보
    detected_time: datetime = field(default_factory=now_kst)
    order_time: Optional[datetime] = None
    execution_time: Optional[datetime] = None
    sell_order_time: Optional[datetime] = None
    sell_execution_time: Optional[datetime] = None
    
    # 손익 정보
    unrealized_pnl: Optional[float] = None   # 미실현 손익
    unrealized_pnl_rate: Optional[float] = None  # 미실현 손익률
    realized_pnl: Optional[float] = None     # 실현 손익
    realized_pnl_rate: Optional[float] = None    # 실현 손익률
    
    # 리스크 관리
    position_size_ratio: float = 0.0         # 포지션 크기 비율
    max_holding_period: int = 1              # 최대 보유기간(일)
    
    # 추가 메타데이터
    created_at: datetime = field(default_factory=now_kst)
    updated_at: datetime = field(default_factory=now_kst)
    
    def __post_init__(self):
        """초기화 후 처리"""
        self.update_timestamp()
    
    def update_timestamp(self):
        """업데이트 시간 갱신"""
        self.updated_at = now_kst()
    
    def add_minute_data(self, timeframe: int, candle: MinuteCandleData):
        """분봉 데이터 추가
        
        Args:
            timeframe: 시간프레임 (1, 5, 15, 30, 60)
            candle: 캔들 데이터
        """
        self.update_timestamp()
        
        if timeframe == 1:
            self.minute_1_data.append(candle)
            # 최대 300개 (5시간) 유지
            if len(self.minute_1_data) > 300:
                self.minute_1_data.pop(0)
        elif timeframe == 5:
            self.minute_5_data.append(candle)
            # 최대 120개 (10시간) 유지
            if len(self.minute_5_data) > 120:
                self.minute_5_data.pop(0)
        elif timeframe == 15:
            self.minute_15_data.append(candle)
            # 최대 80개 (20시간) 유지
            if len(self.minute_15_data) > 80:
                self.minute_15_data.pop(0)
        elif timeframe == 30:
            self.minute_30_data.append(candle)
            # 최대 48개 (24시간) 유지
            if len(self.minute_30_data) > 48:
                self.minute_30_data.pop(0)
        elif timeframe == 60:
            self.minute_60_data.append(candle)
            # 최대 48개 (2일) 유지
            if len(self.minute_60_data) > 48:
                self.minute_60_data.pop(0)
    
    def get_latest_candle(self, timeframe: int) -> Optional[MinuteCandleData]:
        """최신 캔들 데이터 조회
        
        Args:
            timeframe: 시간프레임 (1, 5, 15, 30, 60)
            
        Returns:
            최신 캔들 데이터 또는 None
        """
        data_map = {
            1: self.minute_1_data,
            5: self.minute_5_data,
            15: self.minute_15_data,
            30: self.minute_30_data,
            60: self.minute_60_data
        }
        
        data = data_map.get(timeframe, [])
        return data[-1] if data else None
    
    def update_technical_indicators(self, indicators: Dict):
        """기술적 지표 업데이트
        
        Args:
            indicators: 지표 딕셔너리
        """
        self.update_timestamp()
        
        if 'rsi' in indicators:
            self.rsi_value = indicators['rsi']
        if 'macd' in indicators:
            self.macd_value = indicators['macd']
        if 'macd_signal' in indicators:
            self.macd_signal = indicators['macd_signal']
        if 'bb_upper' in indicators:
            self.bb_upper = indicators['bb_upper']
        if 'bb_middle' in indicators:
            self.bb_middle = indicators['bb_middle']
        if 'bb_lower' in indicators:
            self.bb_lower = indicators['bb_lower']
        if 'ma_short' in indicators:
            self.ma_short = indicators['ma_short']
        if 'ma_long' in indicators:
            self.ma_long = indicators['ma_long']
        if 'volume_ma' in indicators:
            self.volume_ma = indicators['volume_ma']
        if 'volume_spike_ratio' in indicators:
            self.volume_spike_ratio = indicators['volume_spike_ratio']
            
        # 볼린저밴드 포지션 계산
        if all(x is not None for x in [self.bb_upper, self.bb_middle, self.bb_lower]):
            if self.close_price >= self.bb_upper:
                self.bb_position = "upper"
            elif self.close_price <= self.bb_lower:
                self.bb_position = "lower"
            else:
                self.bb_position = "middle"
    
    def update_patterns(self, patterns: List[str], scores: Dict[str, float]):
        """패턴 정보 업데이트
        
        Args:
            patterns: 감지된 패턴 리스트
            scores: 패턴별 점수 딕셔너리
        """
        self.update_timestamp()
        self.detected_patterns = patterns
        self.pattern_scores = scores
        self.total_pattern_score = sum(scores.values())
    
    def calculate_unrealized_pnl(self, current_price: float) -> float:
        """미실현 손익 계산
        
        Args:
            current_price: 현재가
            
        Returns:
            미실현 손익
        """
        if self.buy_price is None or self.buy_quantity is None:
            return 0.0
        
        pnl = (current_price - self.buy_price) * self.buy_quantity
        self.unrealized_pnl = pnl
        self.unrealized_pnl_rate = (current_price - self.buy_price) / self.buy_price * 100
        return pnl
    
    def is_holding_period_exceeded(self) -> bool:
        """보유기간 초과 여부 확인
        
        Returns:
            보유기간 초과 여부
        """
        if self.execution_time is None:
            return False
        
        current_time = now_kst()
        holding_days = (current_time - self.execution_time).days
        return holding_days >= self.max_holding_period
    
    def should_stop_loss(self, current_price: float) -> bool:
        """손절 여부 확인
        
        Args:
            current_price: 현재가
            
        Returns:
            손절 필요 여부
        """
        if self.stop_loss_price is None or self.buy_price is None:
            return False
        
        return current_price <= self.stop_loss_price
    
    def should_take_profit(self, current_price: float) -> bool:
        """익절 여부 확인
        
        Args:
            current_price: 현재가
            
        Returns:
            익절 가능 여부
        """
        if self.target_price is None or self.buy_price is None:
            return False
        
        return current_price >= self.target_price
    
    def __str__(self) -> str:
        """문자열 표현"""
        return f"Position({self.stock_code}[{self.stock_name}]: {self.status.value}, Price={self.close_price}, Patterns={self.detected_patterns})"
    
    def __repr__(self) -> str:
        """디버그용 문자열 표현"""
        return self.__str__() 