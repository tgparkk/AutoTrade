"""
주식 데이터 관리를 위한 모델
"""

import time
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, List
from utils.korean_time import now_kst


class StockStatus(Enum):
    """주식 상태 열거형"""
    WATCHING = "관찰중"
    BUY_READY = "매수준비"
    BUY_ORDERED = "매수주문"
    BOUGHT = "매수완료"
    SELL_READY = "매도준비"
    SELL_ORDERED = "매도주문"
    SOLD = "매도완료"


@dataclass(frozen=True)
class ReferenceData:
    """기준 데이터 (전날까지, 불변)"""
    # 전일 OHLCV
    yesterday_close: float = 0      # 전일 종가
    yesterday_volume: int = 0       # 전일 거래량
    yesterday_high: float = 0       # 전일 고가
    yesterday_low: float = 0        # 전일 저가
    
    # 기술적 지표 (불변)
    sma_20: float = 0              # 20일 이동평균
    rsi: float = 50                # RSI
    macd: float = 0                # MACD
    macd_signal: float = 0         # MACD 시그널
    bb_upper: float = 0            # 볼린저밴드 상단
    bb_middle: float = 0           # 볼린저밴드 중간
    bb_lower: float = 0            # 볼린저밴드 하단
    
    # 패턴 분석 (불변)
    pattern_score: float = 0        # 패턴 점수
    pattern_names: List[str] = field(default_factory=list)
    
    # 유동성 기준 (불변)
    avg_daily_volume: int = 0       # 평균 일일 거래량
    avg_trading_value: int = 0      # 평균 거래대금


@dataclass
class RealtimeData:
    """실시간 데이터 (웹소켓으로 업데이트)"""
    # 현재 가격 정보
    current_price: float = 0        # 현재가
    bid_price: float = 0           # 매수 호가
    ask_price: float = 0           # 매도 호가
    
    # 호가창 5단계
    bid_prices: List[float] = field(default_factory=lambda: [0.0]*5)
    ask_prices: List[float] = field(default_factory=lambda: [0.0]*5)
    bid_volumes: List[int] = field(default_factory=lambda: [0]*5)
    ask_volumes: List[int] = field(default_factory=lambda: [0]*5)
    
    # 거래 정보
    today_volume: int = 0          # 금일 거래량
    contract_volume: int = 0       # 직전 체결량
    today_high: float = 0          # 금일 고가
    today_low: float = 0           # 금일 저가
    
    # 계산 지표
    volume_spike_ratio: float = 1.0  # 거래량 급증 비율
    price_change_rate: float = 0.0   # 시가 대비 등락률
    volatility: float = 0.0          # 변동성
    
    # 업데이트 시간
    last_updated: float = field(default_factory=time.time)
    
    def update_timestamp(self):
        """타임스탬프 업데이트"""
        self.last_updated = time.time()


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
class Stock:
    """종목별 주식 정보를 관리하는 클래스"""
    
    # 기본 정보
    stock_code: str           # 종목코드
    stock_name: str           # 종목명
    
    # 데이터 분리
    reference_data: ReferenceData = field(default_factory=ReferenceData)
    realtime_data: RealtimeData = field(default_factory=RealtimeData)
    
    # 분봉 데이터 (타임프레임별)
    minute_1_data: List[MinuteCandleData] = field(default_factory=list)   # 1분봉
    minute_5_data: List[MinuteCandleData] = field(default_factory=list)   # 5분봉
    minute_15_data: List[MinuteCandleData] = field(default_factory=list)  # 15분봉
    minute_30_data: List[MinuteCandleData] = field(default_factory=list)  # 30분봉
    minute_60_data: List[MinuteCandleData] = field(default_factory=list)  # 60분봉
    
    # 거래 정보
    status: StockStatus = StockStatus.WATCHING
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
    
    # 매도 정보
    sell_price: Optional[float] = None      # 매도가
    sell_reason: Optional[str] = None       # 매도 사유
    
    # 손익 정보
    unrealized_pnl: Optional[float] = None   # 미실현 손익
    unrealized_pnl_rate: Optional[float] = None  # 미실현 손익률
    realized_pnl: Optional[float] = None     # 실현 손익
    realized_pnl_rate: Optional[float] = None    # 실현 손익률
    
    # 리스크 관리
    position_size_ratio: float = 0.0         # 포지션 크기 비율
    max_holding_period: int = 1              # 최대 보유기간(일)
    
    # 메타데이터
    created_at: datetime = field(default_factory=now_kst)
    updated_at: datetime = field(default_factory=now_kst)
    
    def __post_init__(self):
        """초기화 후 처리"""
        self.update_timestamp()
    
    def update_timestamp(self):
        """업데이트 시간 갱신"""
        self.updated_at = now_kst()
        self.realtime_data.update_timestamp()
    
    def update_reference_data(self, **kwargs) -> 'Stock':
        """기준 데이터 업데이트 (불변이므로 새 인스턴스 생성)
        
        Args:
            **kwargs: 업데이트할 기준 데이터 필드들
            
        Returns:
            새로운 Stock 인스턴스
        """
        # 기존 reference_data를 딕셔너리로 변환
        current_data = {
            'yesterday_close': self.reference_data.yesterday_close,
            'yesterday_volume': self.reference_data.yesterday_volume,
            'yesterday_high': self.reference_data.yesterday_high,
            'yesterday_low': self.reference_data.yesterday_low,
            'sma_20': self.reference_data.sma_20,
            'rsi': self.reference_data.rsi,
            'macd': self.reference_data.macd,
            'macd_signal': self.reference_data.macd_signal,
            'bb_upper': self.reference_data.bb_upper,
            'bb_middle': self.reference_data.bb_middle,
            'bb_lower': self.reference_data.bb_lower,
            'pattern_score': self.reference_data.pattern_score,
            'pattern_names': self.reference_data.pattern_names.copy(),
            'avg_daily_volume': self.reference_data.avg_daily_volume,
            'avg_trading_value': self.reference_data.avg_trading_value,
        }
        
        # 새로운 값으로 업데이트
        current_data.update(kwargs)
        
        # 새로운 ReferenceData 생성
        new_reference_data = ReferenceData(**current_data)
        
        # 새로운 Stock 인스턴스 생성
        new_stock = Stock(
            stock_code=self.stock_code,
            stock_name=self.stock_name,
            reference_data=new_reference_data,
            realtime_data=self.realtime_data,
            # 다른 필드들도 복사
            minute_1_data=self.minute_1_data.copy(),
            minute_5_data=self.minute_5_data.copy(),
            minute_15_data=self.minute_15_data.copy(),
            minute_30_data=self.minute_30_data.copy(),
            minute_60_data=self.minute_60_data.copy(),
            status=self.status,
            buy_price=self.buy_price,
            buy_quantity=self.buy_quantity,
            buy_amount=self.buy_amount,
            target_price=self.target_price,
            stop_loss_price=self.stop_loss_price,
            buy_order_id=self.buy_order_id,
            sell_order_id=self.sell_order_id,
            detected_time=self.detected_time,
            order_time=self.order_time,
            execution_time=self.execution_time,
            sell_order_time=self.sell_order_time,
            sell_execution_time=self.sell_execution_time,
            unrealized_pnl=self.unrealized_pnl,
            unrealized_pnl_rate=self.unrealized_pnl_rate,
            realized_pnl=self.realized_pnl,
            realized_pnl_rate=self.realized_pnl_rate,
            position_size_ratio=self.position_size_ratio,
            max_holding_period=self.max_holding_period,
            created_at=self.created_at,
            updated_at=now_kst()
        )
        
        return new_stock
    
    def update_realtime_data(self, **kwargs):
        """실시간 데이터 업데이트
        
        Args:
            **kwargs: 업데이트할 실시간 데이터 필드들
        """
        self.update_timestamp()
        
        # RealtimeData 필드 업데이트
        for key, value in kwargs.items():
            if hasattr(self.realtime_data, key):
                setattr(self.realtime_data, key, value)
        
        # 계산 지표 자동 업데이트
        self._calculate_derived_metrics()
    
    def _calculate_derived_metrics(self):
        """파생 지표 계산"""
        # 거래량 급증 비율 계산
        if self.reference_data.avg_daily_volume > 0:
            self.realtime_data.volume_spike_ratio = (
                self.realtime_data.today_volume / self.reference_data.avg_daily_volume
            )
        
        # 등락률 계산
        if self.reference_data.yesterday_close > 0:
            self.realtime_data.price_change_rate = (
                (self.realtime_data.current_price - self.reference_data.yesterday_close) 
                / self.reference_data.yesterday_close * 100
            )
        
        # 변동성 계산 (일중 고저 기준)
        if self.realtime_data.today_high > 0 and self.realtime_data.today_low > 0:
            self.realtime_data.volatility = (
                (self.realtime_data.today_high - self.realtime_data.today_low) 
                / self.realtime_data.today_low * 100
            )
    
    def add_minute_data(self, timeframe: int, candle: MinuteCandleData):
        """분봉 데이터 추가
        
        Args:
            timeframe: 시간프레임 (1, 5, 15, 30, 60)
            candle: 캔들 데이터
        """
        self.update_timestamp()
        
        data_limits = {1: 300, 5: 120, 15: 80, 30: 48, 60: 48}  # 각 타임프레임별 최대 보관 수
        
        if timeframe == 1:
            self.minute_1_data.append(candle)
            if len(self.minute_1_data) > data_limits[1]:
                self.minute_1_data.pop(0)
        elif timeframe == 5:
            self.minute_5_data.append(candle)
            if len(self.minute_5_data) > data_limits[5]:
                self.minute_5_data.pop(0)
        elif timeframe == 15:
            self.minute_15_data.append(candle)
            if len(self.minute_15_data) > data_limits[15]:
                self.minute_15_data.pop(0)
        elif timeframe == 30:
            self.minute_30_data.append(candle)
            if len(self.minute_30_data) > data_limits[30]:
                self.minute_30_data.pop(0)
        elif timeframe == 60:
            self.minute_60_data.append(candle)
            if len(self.minute_60_data) > data_limits[60]:
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
    
    def calculate_unrealized_pnl(self, current_price: Optional[float] = None) -> float:
        """미실현 손익 계산
        
        Args:
            current_price: 현재가 (None이면 realtime_data의 current_price 사용)
            
        Returns:
            미실현 손익
        """
        if self.buy_price is None or self.buy_quantity is None:
            return 0.0
        
        price = current_price if current_price is not None else self.realtime_data.current_price
        if price <= 0:
            return 0.0
        
        pnl = (price - self.buy_price) * self.buy_quantity
        self.unrealized_pnl = pnl
        self.unrealized_pnl_rate = (price - self.buy_price) / self.buy_price * 100
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
    
    def should_stop_loss(self, current_price: Optional[float] = None) -> bool:
        """손절 여부 확인
        
        Args:
            current_price: 현재가 (None이면 realtime_data의 current_price 사용)
            
        Returns:
            손절 필요 여부
        """
        if self.stop_loss_price is None or self.buy_price is None:
            return False
        
        price = current_price if current_price is not None else self.realtime_data.current_price
        return price <= self.stop_loss_price
    
    def should_take_profit(self, current_price: Optional[float] = None) -> bool:
        """익절 여부 확인
        
        Args:
            current_price: 현재가 (None이면 realtime_data의 current_price 사용)
            
        Returns:
            익절 가능 여부
        """
        if self.target_price is None or self.buy_price is None:
            return False
        
        price = current_price if current_price is not None else self.realtime_data.current_price
        return price >= self.target_price
    
    def get_current_price(self) -> float:
        """현재가 조회"""
        return self.realtime_data.current_price
    
    def get_pattern_score(self) -> float:
        """패턴 점수 조회"""
        return self.reference_data.pattern_score
    
    def get_pattern_names(self) -> List[str]:
        """패턴 이름 목록 조회"""
        return self.reference_data.pattern_names.copy()
    
    # 기존 Position 클래스와의 호환성을 위한 속성들
    @property
    def close_price(self) -> float:
        """현재가 (호환성)"""
        return self.realtime_data.current_price
    
    @property
    def detected_patterns(self) -> List[str]:
        """감지된 패턴 (호환성)"""
        return self.reference_data.pattern_names
    
    @property
    def total_pattern_score(self) -> float:
        """총 패턴 점수 (호환성)"""
        return self.reference_data.pattern_score
    
    def __str__(self) -> str:
        """문자열 표현"""
        return f"Stock({self.stock_code}[{self.stock_name}]: {self.status.value}, Price={self.realtime_data.current_price}, Score={self.reference_data.pattern_score:.1f})"
    
    def __repr__(self) -> str:
        """디버그용 문자열 표현"""
        return self.__str__() 