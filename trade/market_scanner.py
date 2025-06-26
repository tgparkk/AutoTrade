"""
장시작전 시장 스캔 및 종목 선정을 담당하는 MarketScanner 클래스
"""

from typing import Dict, List, Tuple, Optional, Any, TYPE_CHECKING
from datetime import datetime, timedelta
from models.stock import Stock
from .stock_manager import StockManager
from models.stock import StockStatus  # 상태 확인용

if TYPE_CHECKING:
    from websocket.kis_websocket_manager import KISWebSocketManager
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

# 🆕 데이터베이스 저장 기능 추가
try:
    from database.trade_database import TradeDatabase
    DATABASE_AVAILABLE = True
except ImportError:
    TradeDatabase = None
    DATABASE_AVAILABLE = False

logger = setup_logger(__name__)


def _is_data_empty(data: Any) -> bool:
    """데이터가 비어있는지 안전하게 체크하는 함수"""
    if data is None:
        return True
    if hasattr(data, 'empty'):  # DataFrame
        return data.empty
    if hasattr(data, '__len__'):  # List, tuple 등
        return len(data) == 0
    return False


def _get_data_length(data: Any) -> int:
    """데이터 길이를 안전하게 가져오는 함수"""
    if data is None:
        return 0
    if hasattr(data, '__len__'):
        return len(data)
    return 0


def _convert_to_dict_list(ohlcv_data: Any) -> List[Dict]:
    """OHLCV 데이터를 딕셔너리 리스트로 변환"""
    if ohlcv_data is None:
        return []
    
    # DataFrame인 경우
    if hasattr(ohlcv_data, 'to_dict'):
        try:
            # DataFrame을 딕셔너리 리스트로 변환
            return ohlcv_data.to_dict('records')
        except Exception as e:
            logger.debug(f"DataFrame 변환 실패: {e}")
            return []
    
    # 이미 리스트인 경우
    if isinstance(ohlcv_data, list):
        return ohlcv_data
    
    # 기타 경우
    logger.debug(f"알 수 없는 데이터 타입: {type(ohlcv_data)}")
    return []


class MarketScanner:
    """장시작전 시장 전체 스캔 및 종목 선정을 담당하는 클래스"""
    
    def __init__(self, stock_manager: StockManager, websocket_manager=None):
        """MarketScanner 초기화
        
        Args:
            stock_manager: 종목 관리자 인스턴스
            websocket_manager: 웹소켓 매니저 인스턴스 (실시간 데이터용)
        """
        self.stock_manager = stock_manager
        self.websocket_manager = websocket_manager
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        self.performance_config = self.config_loader.load_performance_config()
        
        # 스크리닝 기준 (장전 스캔용)
        self.volume_increase_threshold = self.strategy_config.get('volume_increase_threshold', 2.0)
        self.volume_min_threshold = self.strategy_config.get('volume_min_threshold', 100000)
        self.top_stocks_count = 15  # 상위 15개 종목 선정
        
        # 🆕 장중 스캔 튜닝 파라미터
        self.rank_head_limit   = self.performance_config.get('intraday_rank_head_limit', 50)
        self.min_total_score   = self.performance_config.get('intraday_min_total_score', 18)
        # 단위: 백만원 → 원
        self.min_trading_value = self.performance_config.get('intraday_min_trading_value', 2000) * 1_000_000
        self.max_spread_pct    = self.performance_config.get('intraday_max_spread_percent', 2.0)
        self.reinclude_sold    = self.performance_config.get('intraday_reinclude_sold', True)
        
        # 🆕 데이터베이스는 싱글톤 패턴으로 필요시 생성
        logger.info("✅ MarketScanner 초기화 완료 (데이터베이스는 필요시 생성)")
        
        logger.info("MarketScanner 초기화 완료")
    
    def _get_database(self):
        """데이터베이스 인스턴스 반환 (싱글톤 패턴)"""
        if not hasattr(self, '_database_instance'):
            if not DATABASE_AVAILABLE:
                logger.warning("데이터베이스 라이브러리 없음")
                return None
            
            import sys
            import os
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(current_dir)
            if project_root not in sys.path:
                sys.path.append(project_root)
            
            try:
                from database.trade_database import TradeDatabase
                self._database_instance = TradeDatabase()
                logger.debug("MarketScanner 데이터베이스 인스턴스 생성")
            except Exception as e:
                logger.error(f"MarketScanner 데이터베이스 생성 실패: {e}")
                self._database_instance = None
        
        return self._database_instance
    
    def set_websocket_manager(self, websocket_manager: "KISWebSocketManager"):
        """웹소켓 매니저 설정
        
        Args:
            websocket_manager: 웹소켓 매니저 인스턴스
        """
        self.websocket_manager = websocket_manager
        logger.info("MarketScanner 웹소켓 매니저 설정 완료")
    
    def scan_market_pre_open(self) -> List[Tuple[str, float]]:
        """장시작전 시장 전체 스캔
        
        Returns:
            (종목코드, 종합점수) 튜플의 리스트 (상위 15개)
        """
        logger.info("장시작전 시장 스캔 시작")
        
        # 1. KOSPI 전 종목 리스트 조회
        from utils.stock_data_loader import get_stock_data_loader
        
        stock_loader = get_stock_data_loader()
        all_stocks = stock_loader.stock_list
        
        logger.info(f"KOSPI 전체 종목 수: {len(all_stocks)}")
        
        # 2. 각 종목별 종합 점수 계산
        scored_stocks = []
        
        # 전체 KOSPI 종목을 대상으로 스캔
        # 성능을 위해 우선주나 특수주는 제외
        scan_candidates = [
            stock for stock in all_stocks 
            if stock['code'].isdigit() and len(stock['code']) == 6 and '우' not in stock['name']
        ]

        #scan_candidates = scan_candidates[:100]
        
        logger.info(f"스캔 대상 종목 수: {len(scan_candidates)} (우선주 제외)")
        
        for stock in scan_candidates:
            try:
                stock_code = stock['code']
                
                # 종합 점수 계산
                score = self.calculate_comprehensive_score(stock_code)
                
                # API 실패로 점수를 계산할 수 없는 종목은 제외
                if score is None:
                    logger.debug(f"점수 계산 실패로 종목 제외: {stock_code}")
                    continue
                
                # 최소 점수 기준 적용 (70점 이상)
                min_score = self.strategy_config.get('min_signal_confidence', 0.7) * 100
                if score >= min_score:
                    scored_stocks.append((stock_code, score))
                    
            except Exception as e:
                logger.debug(f"종목 분석 실패 {stock['code']}: {e}")
                continue
        
        # 3. 점수 기준으로 정렬 및 상위 종목 선정
        scored_stocks.sort(key=lambda x: x[1], reverse=True)
        top_stocks = scored_stocks[:self.top_stocks_count]
        
        logger.info(f"시장 스캔 완료: {len(scored_stocks)}개 후보 중 상위 {len(top_stocks)}개 종목 선정")
        
        # 선정된 종목들 로깅
        for i, (code, score) in enumerate(top_stocks, 1):
            stock_name = stock_loader.get_stock_name(code)
            logger.info(f"{i:2d}. {code}[{stock_name}] - 점수: {score:.1f}")
        
        return top_stocks
    

    
    def _calculate_real_fundamentals(self, stock_code: str, ohlcv_data: Any) -> Optional[Dict]:
        """실제 OHLCV 데이터에서 기본 분석 지표 계산
        
        Args:
            stock_code: 종목코드
            ohlcv_data: API에서 가져온 OHLCV 데이터
            
        Returns:
            분석 결과 딕셔너리 또는 None (데이터 부족시)
        """
        if _get_data_length(ohlcv_data) < 20:  # 최소 20일 데이터 필요
            logger.warning(f"데이터가 부족합니다 {stock_code}: {_get_data_length(ohlcv_data)}일")
            return None
        
        try:
            # DataFrame을 딕셔너리 리스트로 변환
            data_list = _convert_to_dict_list(ohlcv_data)
            if not data_list:
                logger.warning(f"OHLCV 데이터 변환 실패: {stock_code}")
                return None
            
            # 최근 데이터부터 정렬 (API는 보통 최신부터 내림차순)
            recent_data = data_list[:20]  # 최근 20일
            
            # 거래량 증가율 계산 (최근 5일 평균 vs 그 전 5일 평균)
            recent_volumes = [float(day.get('acml_vol', 0)) for day in recent_data[:5]]
            previous_volumes = [float(day.get('acml_vol', 0)) for day in recent_data[5:10]]
            
            recent_avg_vol = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 1
            previous_avg_vol = sum(previous_volumes) / len(previous_volumes) if previous_volumes else 1
            volume_increase_rate = recent_avg_vol / previous_avg_vol if previous_avg_vol > 0 else 1
            
            # 가격 변동률 (전일 대비)
            today_close = float(recent_data[0].get('stck_clpr', 0))
            yesterday_close = float(recent_data[1].get('stck_clpr', 0)) if len(recent_data) > 1 else today_close
            price_change_rate = (today_close - yesterday_close) / yesterday_close if yesterday_close > 0 else 0
            
            # RSI 계산 (단순화된 버전)
            closes = [float(day.get('stck_clpr', 0)) for day in recent_data[:14]]
            rsi = self._calculate_rsi(closes)
            
            # 이동평균선 정배열 여부
            ma_alignment = self._check_ma_alignment(recent_data)
            
            # MACD 신호 (단순화)
            macd_signal = self._calculate_macd_signal(recent_data)
            
            return {
                'volume_increase_rate': volume_increase_rate,
                'yesterday_volume': int(recent_volumes[1]) if len(recent_volumes) > 1 else 0,
                'price_change_rate': price_change_rate,
                'rsi': rsi,
                'macd_signal': macd_signal,
                'ma_alignment': ma_alignment,
                'support_level': min([float(day.get('stck_lwpr', 0)) for day in recent_data[:10]]),
                'resistance_level': max([float(day.get('stck_hgpr', 0)) for day in recent_data[:10]])
            }
            
        except Exception as e:
            logger.error(f"실제 데이터 분석 실패 {stock_code}: {e}")
            return None
    
    def _calculate_rsi(self, closes: List[float]) -> float:
        """RSI 계산
        
        Args:
            closes: 종가 리스트
            
        Returns:
            RSI 값 (0-100)
        """
        if len(closes) < 14:
            return 50.0  # 기본값
        
        gains = []
        losses = []
        
        for i in range(1, len(closes)):
            change = closes[i] - closes[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        if len(gains) == 0:
            return 50.0
        
        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _check_ma_alignment(self, ohlcv_data: List) -> bool:
        """이동평균선 정배열 여부 확인
        
        Args:
            ohlcv_data: OHLCV 데이터
            
        Returns:
            정배열 여부
        """
        if _get_data_length(ohlcv_data) < 20:
            return False
        
        # DataFrame을 딕셔너리 리스트로 변환
        data_list = _convert_to_dict_list(ohlcv_data)
        if not data_list:
            return False
        
        closes = [float(day.get('stck_clpr', 0)) for day in data_list[:20]]
        
        # 5일, 10일, 20일 이동평균 계산
        ma5 = sum(closes[:5]) / 5
        ma10 = sum(closes[:10]) / 10
        ma20 = sum(closes) / 20
        
        # 정배열: 현재가 > MA5 > MA10 > MA20
        current_price = closes[0]
        return current_price > ma5 > ma10 > ma20
    
    def _calculate_macd_signal(self, ohlcv_data: List) -> str:
        """MACD 신호 계산 (단순화)
        
        Args:
            ohlcv_data: OHLCV 데이터
            
        Returns:
            MACD 신호 ('positive', 'negative', 'neutral')
        """
        if _get_data_length(ohlcv_data) < 26:
            return 'neutral'
        
        # DataFrame을 딕셔너리 리스트로 변환
        data_list = _convert_to_dict_list(ohlcv_data)
        if not data_list:
            return 'neutral'
        
        closes = [float(day.get('stck_clpr', 0)) for day in data_list[:26]]
        
        # 단순 EMA 근사
        ema12 = sum(closes[:12]) / 12
        ema26 = sum(closes) / 26
        
        macd_line = ema12 - ema26
        
        if macd_line > 0:
            return 'positive'
        elif macd_line < 0:
            return 'negative'
        else:
            return 'neutral'
    
    # ===== 이격도 계산 메서드 섹션 =====
    
    def _calculate_divergence_rate(self, current_price: float, ma_price: float) -> float:
        """이격도 계산 (이동평균 대비)
        
        Args:
            current_price: 현재가
            ma_price: 이동평균가
            
        Returns:
            이격도 (%) - 양수: 이평선 위, 음수: 이평선 아래
        """
        if current_price <= 0 or ma_price <= 0:
            return 0.0
        
        return (current_price - ma_price) / ma_price * 100
    
    def _calculate_sma(self, prices: List[float], period: int) -> float:
        """단순이동평균 계산
        
        Args:
            prices: 가격 리스트
            period: 기간
            
        Returns:
            단순이동평균
        """
        if len(prices) < period or period <= 0:
            return 0.0
        
        valid_prices = [p for p in prices[:period] if p > 0]
        if not valid_prices:
            return 0.0
        
        return sum(valid_prices) / len(valid_prices)
    
    def _get_divergence_analysis(self, stock_code: str, ohlcv_data: Any) -> Optional[Dict]:
        """종목별 이격도 종합 분석 (스크리닝용)
        
        Args:
            stock_code: 종목코드
            ohlcv_data: OHLCV 데이터
            
        Returns:
            이격도 분석 결과 또는 None
        """
        try:
            # 데이터 변환
            data_list = _convert_to_dict_list(ohlcv_data)
            if len(data_list) < 20:
                return None
            
            # 현재가 및 과거 가격 데이터
            current_price = float(data_list[0].get('stck_clpr', 0))
            if current_price <= 0:
                return None
            
            prices = [float(day.get('stck_clpr', 0)) for day in data_list[:20]]
            
            # 각종 이격도 계산
            divergences = {}
            
            # 5일선 이격도
            sma_5 = self._calculate_sma(prices, 5)
            if sma_5 > 0:
                divergences['sma_5'] = self._calculate_divergence_rate(current_price, sma_5)
            
            # 10일선 이격도
            sma_10 = self._calculate_sma(prices, 10)
            if sma_10 > 0:
                divergences['sma_10'] = self._calculate_divergence_rate(current_price, sma_10)
            
            # 20일선 이격도
            sma_20 = self._calculate_sma(prices, 20)
            if sma_20 > 0:
                divergences['sma_20'] = self._calculate_divergence_rate(current_price, sma_20)
            
            # 전일 대비 변화율
            if len(data_list) > 1:
                yesterday_price = float(data_list[1].get('stck_clpr', 0))
                if yesterday_price > 0:
                    divergences['yesterday_change'] = self._calculate_divergence_rate(current_price, yesterday_price)
            
            return {
                'current_price': current_price,
                'divergences': divergences,
                'sma_values': {'sma_5': sma_5, 'sma_10': sma_10, 'sma_20': sma_20}
            }
            
        except Exception as e:
            logger.debug(f"이격도 분석 실패 {stock_code}: {e}")
            return None
    
    def _get_divergence_signal(self, divergence_analysis: Dict) -> Dict[str, Any]:
        """이격도 기반 매매 신호 생성 (스크리닝용)
        
        Args:
            divergence_analysis: 이격도 분석 결과
            
        Returns:
            매매 신호 딕셔너리
        """
        if not divergence_analysis:
            return {'signal': 'HOLD', 'reason': '분석 데이터 없음', 'score': 0}
        
        divergences = divergence_analysis.get('divergences', {})
        
        sma_5_div = divergences.get('sma_5', 0)
        sma_10_div = divergences.get('sma_10', 0) 
        sma_20_div = divergences.get('sma_20', 0)
        
        signal = 'HOLD'
        reason = []
        score = 0
        
        # 매수 신호 (과매도) - 스크리닝에서는 보수적 기준 적용
        if sma_20_div <= -5 or (sma_10_div <= -3 and sma_5_div <= -2):
            signal = 'BUY'
            score = 15 + abs(min(sma_20_div, sma_10_div, sma_5_div)) * 0.5  # 이격도 기반 점수
            reason.append(f"과매도 구간 (5일:{sma_5_div:.1f}%, 10일:{sma_10_div:.1f}%, 20일:{sma_20_div:.1f}%)")
        
        # 상승 모멘텀 (적당한 상승 이격도)
        elif 1 <= sma_5_div <= 3 and 0 <= sma_10_div <= 2 and -1 <= sma_20_div <= 1:
            signal = 'MOMENTUM'
            score = 10  # 모멘텀 점수
            reason.append(f"상승 모멘텀 (5일:{sma_5_div:.1f}%, 10일:{sma_10_div:.1f}%, 20일:{sma_20_div:.1f}%)")
        
        # 과매수 주의 (스크리닝에서는 제외 대상)
        elif sma_20_div >= 10 or sma_10_div >= 7 or sma_5_div >= 5:
            signal = 'OVERHEATED'
            score = -5  # 감점
            reason.append(f"과열 구간 (5일:{sma_5_div:.1f}%, 10일:{sma_10_div:.1f}%, 20일:{sma_20_div:.1f}%)")
        
        return {
            'signal': signal,
            'reason': '; '.join(reason) if reason else '중립',
            'score': score,
            'divergences': divergences
        }
    
    # ===== 실시간 이격도 분석 (Stock 객체용) =====
    
    def get_stock_divergence_rates(self, stock: 'Stock') -> Dict[str, float]:
        """Stock 객체의 실시간 이격도 계산 (데이트레이딩용)
        
        Args:
            stock: Stock 객체
            
        Returns:
            각종 이격도 정보
        """
        current_price = stock.realtime_data.current_price
        if current_price <= 0:
            return {}
        
        divergences = {}
        
        # 20일선 이격도 (기준 데이터에서)
        if stock.reference_data.sma_20 > 0:
            divergences['sma_20'] = self._calculate_divergence_rate(current_price, stock.reference_data.sma_20)
        
        # 전일 종가 이격도
        if stock.reference_data.yesterday_close > 0:
            divergences['yesterday_close'] = self._calculate_divergence_rate(current_price, stock.reference_data.yesterday_close)
        
        # 당일 시가 이격도 (분봉 데이터가 있을 경우)
        if stock.minute_1_data:
            first_candle = stock.minute_1_data[0]
            if first_candle.open_price > 0:
                divergences['today_open'] = self._calculate_divergence_rate(current_price, first_candle.open_price)
        
        # 5분봉 단순 이동평균 이격도 (최근 5개 캔들)
        if len(stock.minute_5_data) >= 5:
            recent_prices = [candle.close_price for candle in stock.minute_5_data[-5:]]
            sma_5min = self._calculate_sma(recent_prices, 5)
            if sma_5min > 0:
                divergences['sma_5min'] = self._calculate_divergence_rate(current_price, sma_5min)
        
        # 당일 고저점 대비 위치 (%)
        if stock.realtime_data.today_high > 0 and stock.realtime_data.today_low > 0:
            day_range = stock.realtime_data.today_high - stock.realtime_data.today_low
            if day_range > 0:
                divergences['daily_position'] = (
                    (current_price - stock.realtime_data.today_low) / day_range * 100
                )
        
        return divergences
    
    def get_stock_divergence_signal(self, stock: 'Stock') -> Dict[str, Any]:
        """Stock 객체의 이격도 기반 실시간 매매 신호 (데이트레이딩용)
        
        Args:
            stock: Stock 객체
            
        Returns:
            매매 신호 딕셔너리
        """
        divergences = self.get_stock_divergence_rates(stock)
        if not divergences:
            return {'signal': 'HOLD', 'reason': '이격도 계산 불가', 'strength': 0}
        
        sma_20_div = divergences.get('sma_20', 0)
        sma_5min_div = divergences.get('sma_5min', 0)
        daily_pos = divergences.get('daily_position', 50)
        
        signal = 'HOLD'
        reason = []
        strength = 0  # 신호 강도 (0~10)
        
        # 강한 매수 신호
        if sma_20_div <= -3 and daily_pos <= 20:
            signal = 'STRONG_BUY'
            strength = 8 + min(abs(sma_20_div), 7)
            reason.append(f"강한 매수 (20일선:{sma_20_div:.1f}%, 일봉위치:{daily_pos:.0f}%)")
        
        # 일반 매수 신호
        elif sma_20_div <= -2 or (sma_5min_div <= -1.5 and daily_pos <= 30):
            signal = 'BUY'
            strength = 5 + min(abs(sma_20_div), 3)
            reason.append(f"매수 신호 (20일선:{sma_20_div:.1f}%, 5분선:{sma_5min_div:.1f}%)")
        
        # 강한 매도 신호
        elif sma_20_div >= 5 and daily_pos >= 80:
            signal = 'STRONG_SELL'
            strength = -(8 + min(sma_20_div, 7))
            reason.append(f"강한 매도 (20일선:{sma_20_div:.1f}%, 일봉위치:{daily_pos:.0f}%)")
        
        # 일반 매도 신호
        elif sma_20_div >= 3 or (sma_5min_div >= 2 and daily_pos >= 70):
            signal = 'SELL'
            strength = -(5 + min(sma_20_div, 3))
            reason.append(f"매도 신호 (20일선:{sma_20_div:.1f}%, 5분선:{sma_5min_div:.1f}%)")
        
        # 중립
        elif abs(sma_20_div) <= 1 and 30 <= daily_pos <= 70:
            signal = 'NEUTRAL'
            strength = 1
            reason.append("이격도 중립")
        
        return {
            'signal': signal,
            'reason': '; '.join(reason) if reason else '보류',
            'strength': strength,
            'divergences': divergences
        }
    
    def _analyze_real_candle_patterns(self, stock_code: str, ohlcv_data: Any) -> Optional[Dict]:
        """실제 OHLCV 데이터에서 캔들패턴 분석
        
        Args:
            stock_code: 종목코드
            ohlcv_data: OHLCV 데이터
            
        Returns:
            패턴 분석 결과 또는 None (분석 실패시)
        """
        detected_patterns = []
        pattern_scores = {}
        
        try:
            # DataFrame을 딕셔너리 리스트로 변환
            data_list = _convert_to_dict_list(ohlcv_data)
            if not data_list:
                logger.warning(f"캔들패턴 데이터 변환 실패: {stock_code}")
                return None
            
            # 최근 5일 데이터로 패턴 분석
            recent_candles = data_list[:5]
            
            for i, candle in enumerate(recent_candles):
                open_price = float(candle.get('stck_oprc', 0))
                high_price = float(candle.get('stck_hgpr', 0))
                low_price = float(candle.get('stck_lwpr', 0))
                close_price = float(candle.get('stck_clpr', 0))
                
                # 기본 캔들 분석
                body_size = abs(close_price - open_price)
                total_range = high_price - low_price
                upper_shadow = high_price - max(open_price, close_price)
                lower_shadow = min(open_price, close_price) - low_price
                
                if total_range == 0:
                    continue
                
                # 패턴 감지 로직
                patterns = self._detect_candle_patterns(
                    open_price, high_price, low_price, close_price,
                    body_size, total_range, upper_shadow, lower_shadow
                )
                
                for pattern_name, score in patterns.items():
                    if pattern_name not in pattern_scores:
                        detected_patterns.append(pattern_name)
                        pattern_scores[pattern_name] = score
                    else:
                        # 같은 패턴이 여러 날에 나타나면 평균 점수
                        pattern_scores[pattern_name] = (pattern_scores[pattern_name] + score) / 2
            
            total_score = sum(pattern_scores.values())
            reliability = min(total_score / len(detected_patterns), 1.0) if detected_patterns else 0.0
            
            return {
                'detected_patterns': detected_patterns,
                'pattern_scores': pattern_scores,
                'total_pattern_score': total_score,
                'reliability': reliability
            }
            
        except Exception as e:
            logger.error(f"실제 캔들패턴 분석 실패 {stock_code}: {e}")
            return None
    
    def _detect_candle_patterns(self, open_p: float, high_p: float, low_p: float, close_p: float,
                               body_size: float, total_range: float, upper_shadow: float, lower_shadow: float) -> Dict:
        """개별 캔들에서 패턴 감지
        
        Returns:
            감지된 패턴과 점수 딕셔너리
        """
        patterns = {}
        
        if total_range == 0:
            return patterns
        
        body_ratio = body_size / total_range
        upper_ratio = upper_shadow / total_range
        lower_ratio = lower_shadow / total_range
        
        # 해머 패턴 (긴 아래 그림자, 짧은 위 그림자, 작은 몸통)
        if (lower_ratio > 0.5 and upper_ratio < 0.1 and body_ratio < 0.3):
            patterns['hammer'] = 0.8
        
        # 상승장악형 (불리시 인걸핑)
        if close_p > open_p and body_ratio > 0.6:
            patterns['bullish_engulfing'] = 0.9
        
        # 십자형 (도지)
        if body_ratio < 0.1:
            if lower_ratio > 0.3:
                patterns['dragonfly_doji'] = 0.7
            else:
                patterns['doji'] = 0.5
        
        # 역망치형
        if (upper_ratio > 0.5 and lower_ratio < 0.1 and body_ratio < 0.3):
            patterns['inverted_hammer'] = 0.65
        
        return patterns
    
    def calculate_comprehensive_score(self, stock_code: str) -> Optional[float]:
        """종합 점수 계산
        
        Args:
            stock_code: 종목코드
            
        Returns:
            종합 점수 (0~100) 또는 None (분석 실패시)
        """
        # 실제 API에서 데이터 조회 (한 번만 호출하여 효율성 향상)
        ohlcv_data = None
        try:
            from api.kis_market_api import get_inquire_daily_itemchartprice
            
            logger.debug(f"📊 {stock_code} API 호출 시작")
            ohlcv_data = get_inquire_daily_itemchartprice(
                output_dv="2",
                itm_no=stock_code,
                period_code="D",
                adj_prc="1"
            )
            
            # 🔧 디버깅 로그 추가
            if ohlcv_data is not None:
                logger.debug(f"📊 {stock_code} API 성공: 타입={type(ohlcv_data)}, 길이={len(ohlcv_data)}")
            else:
                logger.debug(f"📊 {stock_code} API 실패: None 반환")
                
        except Exception as e:
            logger.debug(f"📊 {stock_code} API 호출 실패: {e}")
        
        # 기본 분석 (같은 데이터 재사용)
        if _is_data_empty(ohlcv_data):
            logger.debug(f"📊 {stock_code} 데이터 없음으로 종목 제외")
            return None
        
        logger.debug(f"📊 {stock_code} 기본 분석 시작")
        fundamentals = self._calculate_real_fundamentals(stock_code, ohlcv_data)
        if not fundamentals:
            logger.debug(f"📊 {stock_code} 기본 분석 실패로 종목 제외")
            return None
        
        # 캔들패턴 분석 (같은 데이터 재사용)
        if _get_data_length(ohlcv_data) < 5:
            logger.debug(f"📊 {stock_code} 캔들패턴 분석용 데이터 부족으로 종목 제외 (길이: {_get_data_length(ohlcv_data)})")
            return None
        
        logger.debug(f"📊 {stock_code} 캔들패턴 분석 시작")
        patterns = self._analyze_real_candle_patterns(stock_code, ohlcv_data)
        if not patterns:
            logger.debug(f"📊 {stock_code} 캔들패턴 분석 실패로 종목 제외")
            return None
        
        # 🆕 이격도 분석 추가 (같은 데이터 재사용)
        logger.debug(f"📊 {stock_code} 이격도 분석 시작")
        divergence_analysis = self._get_divergence_analysis(stock_code, ohlcv_data)
        divergence_signal = self._get_divergence_signal(divergence_analysis) if divergence_analysis else None
        
        # 점수 계산 (가중치 최적화) - 실전 트레이딩 기준 조정
        volume_score = min(fundamentals['volume_increase_rate'] * 10, 22)  # 최대 22점 (22%)
        technical_score = (fundamentals['rsi'] / 100) * 18  # 최대 18점 (18%)
        pattern_score = patterns['total_pattern_score'] * 18  # 최대 18점 (18%)
        ma_score = 15 if fundamentals['ma_alignment'] else 0  # 15점 (15%) - 정배열 중요
        momentum_score = min(fundamentals['price_change_rate'] * 100, 8)  # 최대 8점 (8%)
        
        # 🆕 이격도 점수 추가 (최대 15점) - 매수 타이밍에서 가장 중요한 지표
        divergence_score = 0
        if divergence_signal:
            signal_type = divergence_signal.get('signal', 'HOLD')
            base_score = divergence_signal.get('score', 0)
            
            if signal_type == 'BUY':
                divergence_score = min(base_score * 0.6, 15)  # 과매도 상황에서 최고 점수
            elif signal_type == 'MOMENTUM':
                divergence_score = min(base_score * 0.9, 12)  # 상승 모멘텀에서 좋은 점수
            elif signal_type == 'OVERHEATED':
                divergence_score = max(base_score, -8)        # 과열 구간에서 강한 감점
            else:
                divergence_score = 2  # HOLD도 중립적 가산점 (이격도 정상 = 안정적)
        
        total_score = volume_score + technical_score + pattern_score + ma_score + momentum_score + divergence_score
        
        # 🆕 디버깅 로그에 이격도 점수 추가
        divergence_info = ""
        if divergence_signal and divergence_analysis:
            divergences = divergence_analysis.get('divergences', {})
            sma_20_div = divergences.get('sma_20', 0)
            signal_type = divergence_signal.get('signal', 'HOLD')
            divergence_info = f"이격도({divergence_score:.1f}, 20일선:{sma_20_div:.1f}%, {signal_type}) + "
        
        logger.debug(f"📊 {stock_code} 점수 계산 완료: 거래량({volume_score:.1f}) + 기술적({technical_score:.1f}) + "
                    f"패턴({pattern_score:.1f}) + MA({ma_score:.1f}) + 모멘텀({momentum_score:.1f}) + "
                    f"{divergence_info}= {total_score:.1f}")
        
        return min(total_score, 100)  # 최대 100점
    
    def get_stock_detailed_analysis(self, stock_code: str) -> Optional[Dict]:
        """종목 상세 분석 정보 조회 (기술적 지표 포함)
        
        Args:
            stock_code: 종목코드
            
        Returns:
            상세 분석 결과 딕셔너리 또는 None
        """
        try:
            # OHLCV 데이터 조회
            from api.kis_market_api import get_inquire_daily_itemchartprice
            from datetime import timedelta
            
            ohlcv_data = get_inquire_daily_itemchartprice(
                output_dv="2", div_code="J", itm_no=stock_code,
                inqr_strt_dt=(now_kst() - timedelta(days=30)).strftime("%Y%m%d"),
                inqr_end_dt=now_kst().strftime("%Y%m%d"),
                period_code="D", adj_prc="0"  # 수정주가
            )
            
            if ohlcv_data is None or len(ohlcv_data) < 20:
                logger.debug(f"OHLCV 데이터 부족: {stock_code}")
                return None
            
            # 기본 분석 수행
            fundamentals = self._calculate_real_fundamentals(stock_code, ohlcv_data)
            if not fundamentals:
                return None
            
            # 캔들 패턴 분석
            pattern_analysis = self._analyze_real_candle_patterns(stock_code, ohlcv_data)
            
            # 이격도 분석
            divergence_analysis = self._get_divergence_analysis(stock_code, ohlcv_data)
            
            return {
                'pattern_score': pattern_analysis.get('total_score', 0) if pattern_analysis else 0,
                'pattern_names': pattern_analysis.get('detected_patterns', []) if pattern_analysis else [],
                'rsi': fundamentals.get('rsi', 50),
                'macd': fundamentals.get('macd_signal', 0),
                'sma_20': divergence_analysis.get('sma_20', 0) if divergence_analysis else 0,
                'volume_increase_rate': fundamentals.get('volume_increase_rate', 1.0),
                'price_change_rate': fundamentals.get('price_change_rate', 0)
            }
            
        except Exception as e:
            logger.debug(f"종목 상세 분석 실패 {stock_code}: {e}")
            return None

    def get_stock_basic_info(self, stock_code: str) -> Optional[Dict]:
        """종목 기본 정보 조회 (실제 API 사용)
        
        Args:
            stock_code: 종목코드
            
        Returns:
            종목 기본 정보 또는 None (API 실패 시)
        """
        try:
            # 1. StockDataLoader를 사용하여 종목명 조회
            from utils.stock_data_loader import get_stock_data_loader
            
            stock_loader = get_stock_data_loader()
            stock_name = stock_loader.get_stock_name(stock_code)
            
            if not stock_name:
                logger.warning(f"종목 정보를 찾을 수 없습니다: {stock_code}")
                return None
            
            # 2. 일봉 데이터로 정확한 기준 정보 조회 (price_change_rate 정확성 확보)
            from api.kis_market_api import get_inquire_daily_itemchartprice, get_inquire_price
            
            # 일봉 데이터 조회 (최근 5일)
            from datetime import timedelta
            daily_data = get_inquire_daily_itemchartprice(
                output_dv="2", div_code="J", itm_no=stock_code,
                inqr_strt_dt=(now_kst() - timedelta(days=5)).strftime("%Y%m%d"),  # 5일 전부터
                inqr_end_dt=now_kst().strftime("%Y%m%d"),
                period_code="D"
            )
            
            # 현재가 조회 (실시간 정보용)
            price_data = get_inquire_price(div_code="J", itm_no=stock_code)
            
            # 3. API 데이터 검증
            if daily_data is None or daily_data.empty or price_data is None or price_data.empty:
                logger.warning(f"가격 정보 조회 실패 - 종목 제외: {stock_code}")
                return None
            
            try:
                # 현재가 정보 (price_data에서)
                row = price_data.iloc[0]
                current_price = float(row.get('stck_prpr', 0))
                volume = int(row.get('acml_vol', 0))
                
                # 🔥 일봉 데이터에서 정확한 전일종가 추출
                yesterday_close = current_price  # 기본값
                yesterday_volume = volume  # 기본값
                
                if daily_data is not None and len(daily_data) >= 2:
                    # 최근 2일 데이터에서 전일 정보 추출 (첫 번째가 최신, 두 번째가 전일)
                    if len(daily_data) >= 2:
                        # 🔥 두 번째 행이 전일 데이터 (daily_data.iloc[1])
                        yesterday_day = daily_data.iloc[1]
                        yesterday_close = float(yesterday_day.get('stck_clpr', current_price))  # 전일종가
                        yesterday_volume = int(yesterday_day.get('acml_vol', volume))  # 전일거래량
                        
                        logger.debug(f"일봉 데이터에서 전일 정보 추출: {stock_code} "
                                   f"전일종가:{yesterday_close:,}원, 전일거래량:{yesterday_volume:,}주")
                    elif len(daily_data) >= 1:
                        # 🔥 데이터가 1개만 있으면 해당 데이터를 전일로 간주 (장외시간 등)
                        latest_day = daily_data.iloc[0]
                        yesterday_close = float(latest_day.get('stck_clpr', current_price))  # 전일종가
                        yesterday_volume = int(latest_day.get('acml_vol', volume))  # 전일거래량
                        
                        logger.debug(f"일봉 데이터 1개 사용(전일로 간주): {stock_code} "
                                   f"기준종가:{yesterday_close:,}원, 기준거래량:{yesterday_volume:,}주")
                
                # 여전히 전일종가가 0이면 현재가로 대체
                if yesterday_close <= 0 and current_price > 0:
                    yesterday_close = current_price
                    logger.debug(f"전일종가 최종 보정: {stock_code} {current_price:,}원")
                
                # 필수 데이터가 없으면 종목 제외 (완화된 조건)
                if current_price <= 0 or yesterday_close <= 0:
                    logger.warning(f"필수 데이터 부족으로 종목 제외: {stock_code} "
                                 f"현재가:{current_price}, 전일종가:{yesterday_close}, 거래량:{volume}")
                    return None
                
                # 🔧 최소 거래량 조건 완화 (0주도 허용, 장외시간 대비)
                if volume < 0:  # 음수만 제외
                    logger.warning(f"비정상 거래량으로 종목 제외: {stock_code} 거래량:{volume}")
                    return None
                
                # 🔥 정확한 price_change_rate 계산 (일봉 데이터 기반)
                accurate_price_change_rate = 0.0
                if yesterday_close > 0 and yesterday_close != current_price:
                    accurate_price_change_rate = (current_price - yesterday_close) / yesterday_close * 100
                
                # 종목 기본 정보 구성 (일봉 데이터 활용)
                basic_info = {
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'current_price': current_price,
                    'yesterday_close': yesterday_close,  # 일봉 데이터에서 추출
                    'open_price': float(row.get('stck_oprc', current_price)),
                    'high_price': float(row.get('stck_hgpr', current_price)),
                    'low_price': float(row.get('stck_lwpr', current_price)),
                    'volume': volume,
                    'yesterday_volume': yesterday_volume,  # 일봉 데이터에서 추출
                    'price_change': current_price - yesterday_close,  # 정확한 가격 변화량
                    'price_change_rate': accurate_price_change_rate,  # 정확한 변화율
                    'market_cap': int(row.get('hts_avls', 0)) if 'hts_avls' in row else 0
                }
                
                logger.debug(f"✅ 종목 기본정보 조회 성공: {stock_code}[{stock_name}] "
                           f"현재가: {current_price:,}원, 전일종가: {yesterday_close:,}원, 거래량: {volume:,}주")
                
                return basic_info
                
            except Exception as parse_e:
                logger.warning(f"API 데이터 파싱 오류로 종목 제외: {stock_code}: {parse_e}")
                return None
            
        except Exception as e:
            logger.error(f"종목 기본정보 조회 오류로 종목 제외: {stock_code}: {e}")
            return None
    
    def select_top_stocks(self, scan_results: List[Tuple[str, float]]) -> bool:
        """상위 종목들을 StockManager에 등록하고 웹소켓에 구독
        
        Args:
            scan_results: 스캔 결과 (종목코드, 점수) 리스트
            
        Returns:
            등록 성공 여부
        """
        logger.info(f"상위 {len(scan_results)}개 종목을 StockManager에 등록 및 웹소켓 구독 시작")
        
        success_count = 0
        websocket_success_count = 0
        
        for stock_code, score in scan_results:
            try:
                # 종목 기본 정보 조회
                stock_info = self.get_stock_basic_info(stock_code)
                
                # API 실패 시 해당 종목 건너뛰기 (실전 안전성)
                if stock_info is None:
                    logger.warning(f"종목 기본정보 조회 실패로 건너뛰기: {stock_code}")
                    continue
                
                # StockManager에 등록 (실제 API 데이터 사용)
                success = self.stock_manager.add_selected_stock(
                    stock_code=stock_code,
                    stock_name=stock_info['stock_name'],
                    open_price=stock_info['open_price'],
                    high_price=stock_info['high_price'],
                    low_price=stock_info['low_price'], 
                    close_price=stock_info['current_price'],  # 현재가를 종가로 사용
                    volume=stock_info['volume'],
                    selection_score=score,
                    reference_data={
                        'yesterday_close': stock_info['yesterday_close'],
                        'yesterday_volume': stock_info['yesterday_volume'],
                        'market_cap': stock_info['market_cap'],
                        'price_change': stock_info['price_change'],
                        'price_change_rate': stock_info['price_change_rate']
                    }
                )
                
                # 🆕 명시적으로 WATCHING 상태로 설정 (매수 대기 상태)
                if success:
                    from models.stock import StockStatus
                    self.stock_manager.change_stock_status(
                        stock_code=stock_code, 
                        new_status=StockStatus.WATCHING,
                        reason="market_scan_selected"
                    )
                
                if success:
                    success_count += 1
                    
                    # 🆕 데이터베이스에 장전 스캔 결과 저장
                    database = self._get_database()
                    if database:
                        try:
                            # 종목 상세 정보 조회 (기술적 지표 포함)
                            detailed_info = self.get_stock_detailed_analysis(stock_code)
                            
                            scan_data = {
                                'stock_code': stock_code,
                                'stock_name': stock_info['stock_name'],
                                'selection_score': score,
                                'selection_criteria': {
                                    'scan_type': 'pre_market',
                                    'volume_threshold': self.volume_increase_threshold,
                                    'min_volume': self.volume_min_threshold,
                                    'comprehensive_score': score
                                },
                                'pattern_score': detailed_info.get('pattern_score', 0) if detailed_info else 0,
                                'pattern_names': detailed_info.get('pattern_names', []) if detailed_info else [],
                                'rsi': detailed_info.get('rsi', 50) if detailed_info else 50,
                                'macd': detailed_info.get('macd', 0) if detailed_info else 0,
                                'sma_20': detailed_info.get('sma_20', stock_info['current_price']) if detailed_info else stock_info['current_price'],
                                'yesterday_close': stock_info['yesterday_close'],
                                'yesterday_volume': stock_info['yesterday_volume'],
                                'market_cap': stock_info['market_cap']
                            }
                            
                            db_id = database.save_pre_market_scan(scan_data)
                            if db_id > 0:
                                logger.debug(f"📊 장전 스캔 DB 저장 완료: {stock_code} (ID: {db_id})")
                            else:
                                logger.warning(f"⚠️ 장전 스캔 DB 저장 실패: {stock_code}")
                                
                        except Exception as db_error:
                            logger.error(f"❌ 장전 스캔 DB 저장 오류 {stock_code}: {db_error}")
                    
                    # 🆕 웹소켓에 종목 구독 (실시간 데이터 수신용)
                    if self.websocket_manager:
                        try:
                            websocket_success = self.websocket_manager.subscribe_stock_sync(stock_code)
                            if websocket_success:
                                websocket_success_count += 1
                                logger.debug(f"✅ 웹소켓 구독 성공: {stock_code}")
                            else:
                                logger.warning(f"⚠️ 웹소켓 구독 실패: {stock_code}")
                        except Exception as ws_e:
                            logger.error(f"웹소켓 구독 오류 {stock_code}: {ws_e}")
                    else:
                        logger.warning("웹소켓 매니저가 설정되지 않음 - 실시간 데이터 수신 불가")
                    
            except Exception as e:
                logger.error(f"종목 등록 실패 {stock_code}: {e}")
        
        logger.info(f"종목 등록 완료: {success_count}/{len(scan_results)}개 성공")
        if self.websocket_manager:
            logger.info(f"웹소켓 구독 완료: {websocket_success_count}/{success_count}개 성공")
        
        return success_count > 0
    
    def run_pre_market_scan(self) -> bool:
        """전체 장시작전 스캔 프로세스 실행
        
        Returns:
            스캔 성공 여부
        """
        try:
            logger.info("=== 장시작전 시장 스캔 프로세스 시작 ===")
            
            # 1. 기존 선정 종목 초기화
            self.stock_manager.clear_all_stocks()
            
            # 2. 시장 전체 스캔
            scan_results = self.scan_market_pre_open()
            
            if not scan_results:
                logger.warning("스캔 결과가 없습니다")
                return False
            
            # 3. 상위 종목들 선정 및 등록
            success = self.select_top_stocks(scan_results)
            
            if success:
                logger.info("=== 장시작전 시장 스캔 프로세스 완료 ===")
                summary = self.stock_manager.get_stock_summary()
                logger.info(f"선정된 종목 수: {summary['total_selected']}")
            else:
                logger.error("종목 선정 과정에서 오류 발생")
            
            return success
            
        except Exception as e:
            logger.error(f"장시작전 스캔 프로세스 오류: {e}")
            return False
    
    def __str__(self) -> str:
        """문자열 표현"""
        return f"MarketScanner(거래량기준: {self.volume_increase_threshold}배, 최소거래량: {self.volume_min_threshold:,}주)"

    # ===== 장중 추가 종목 선별 섹션 =====
    
    def intraday_scan_additional_stocks(self, max_stocks: int = 5) -> List[Tuple[str, float, str]]:
        """장중 추가 종목 스캔 (순위분석 API 활용)
        
        Args:
            max_stocks: 최대 선별 종목 수
            
        Returns:
            (종목코드, 점수, 선별사유) 튜플 리스트
        """
        logger.info(f"🔍 장중 추가 종목 스캔 시작 (목표: {max_stocks}개)")
        
        try:
            from utils.stock_data_loader import get_stock_data_loader
            stock_loader = get_stock_data_loader()

            from api.kis_market_api import (
                get_disparity_rank, get_fluctuation_rank, 
                get_volume_rank, get_bulk_trans_num_rank
            )
            
            # 기존 선정 종목 제외를 위한 코드 리스트
            excluded_codes = set(self.stock_manager.get_all_stock_codes())
            logger.debug(f"기존 관리 종목 제외: {len(excluded_codes)}개 ({', '.join(list(excluded_codes)[:5])}{'...' if len(excluded_codes) > 5 else ''})")
            
            candidate_stocks = {}  # {종목코드: {'score': 점수, 'reasons': [사유들]}}
            
            # 1. 이격도 순위 (과매도 구간) - 가장 중요
            logger.debug("📊 이격도 순위 조회 (과매도)")
            disparity_data = get_disparity_rank(
                fid_input_iscd="0000",  # 전체
                fid_rank_sort_cls_code="1",  # 이격도 하위순 (과매도)
                fid_hour_cls_code="20"  # 20일 이격도
            )
            
            if disparity_data is not None and len(disparity_data) > 0:
                for idx, row in disparity_data.head(self.rank_head_limit).iterrows():
                    code = row.get('mksc_shrn_iscd', '')
                    if code and code not in excluded_codes and code in stock_loader:
                        disparity_rate = float(row.get('dspr', 0))
                        if disparity_rate <= -3.0:  # 과매도 기준 강화
                            score = min(abs(disparity_rate) * 2, 20)  # 최대 20점
                            if code not in candidate_stocks:
                                candidate_stocks[code] = {'score': 0, 'reasons': []}
                            candidate_stocks[code]['score'] += score
                            candidate_stocks[code]['reasons'].append(f"이격도과매도({disparity_rate:.1f}%)")
                            # 거래대금 정보 보존 (있다면)
                            tv = float(row.get('acml_tr_pbmn', 0))
                            current_tv = candidate_stocks[code].get('trading_value', 0)
                            if tv > current_tv:
                                candidate_stocks[code]['trading_value'] = tv
            
            # 2. 등락률 순위 (상승 모멘텀) - 두 번째 중요
            logger.debug("📊 등락률 순위 조회 (상승)")
            fluctuation_data = get_fluctuation_rank(
                fid_input_iscd="0000",  # 전체
                fid_rank_sort_cls_code="0",  # 상승률순
                fid_rsfl_rate1="1.0",  # 1% 이상
                fid_rsfl_rate2="15.0"  # 15% 이하 (너무 과열 제외)
            )
            
            if fluctuation_data is not None and len(fluctuation_data) > 0:
                for idx, row in fluctuation_data.head(self.rank_head_limit).iterrows():
                    code = row.get('mksc_shrn_iscd', '')
                    if code and code not in excluded_codes and code in stock_loader:
                        change_rate = float(row.get('prdy_ctrt', 0))
                        if 0.5 <= change_rate <= 10.0:  # 완화된 구간
                            score = min(change_rate * 1.5, 15)  # 최대 15점
                            if code not in candidate_stocks:
                                candidate_stocks[code] = {'score': 0, 'reasons': []}
                            candidate_stocks[code]['score'] += score
                            candidate_stocks[code]['reasons'].append(f"상승모멘텀({change_rate:.1f}%)")
                            # 거래대금 정보 보존 (있다면)
                            tv = float(row.get('acml_tr_pbmn', 0))
                            current_tv = candidate_stocks[code].get('trading_value', 0)
                            if tv > current_tv:
                                candidate_stocks[code]['trading_value'] = tv
            
            # 3. 거래량 순위 (관심도) - 세 번째
            logger.debug("📊 거래량 순위 조회")
            volume_data = get_volume_rank(
                fid_input_iscd="0000",  # 전체
                fid_blng_cls_code="1"   # 거래증가율
            )
            
            if volume_data is not None and len(volume_data) > 0:
                for idx, row in volume_data.head(self.rank_head_limit).iterrows():
                    code = row.get('mksc_shrn_iscd', '')
                    if code and code not in excluded_codes and code in stock_loader:
                        volume_ratio = float(row.get('vol_inrt', 0))
                        if volume_ratio >= 300:  # 300% 이상 거래량 증가
                            score = min(volume_ratio / 15, 15)  # 최대 15점
                            if code not in candidate_stocks:
                                candidate_stocks[code] = {'score': 0, 'reasons': []}
                            candidate_stocks[code]['score'] += score
                            candidate_stocks[code]['reasons'].append(f"거래량급증({volume_ratio:.0f}%)")
                            # 거래대금 정보 보존 (있다면)
                            tv = float(row.get('acml_tr_pbmn', 0))
                            current_tv = candidate_stocks[code].get('trading_value', 0)
                            if tv > current_tv:
                                candidate_stocks[code]['trading_value'] = tv
            
            # 4. 체결강도 상위 (매수세) - 네 번째
            logger.debug("📊 체결강도 순위 조회")
            strength_data = get_bulk_trans_num_rank(
                fid_input_iscd="0000",  # 전체
                fid_rank_sort_cls_code="0"  # 매수상위
            )
            
            if strength_data is not None and len(strength_data) > 0:
                for idx, row in strength_data.head(self.rank_head_limit).iterrows():
                    code = row.get('mksc_shrn_iscd', '')
                    if code and code not in excluded_codes and code in stock_loader:
                        # 체결강도나 매수비율 관련 필드 확인 필요
                        # 임시로 기본 점수 부여
                        score = 8
                        if code not in candidate_stocks:
                            candidate_stocks[code] = {'score': 0, 'reasons': []}
                        candidate_stocks[code]['score'] += score
                        candidate_stocks[code]['reasons'].append("체결강도상위")
                        # 거래대금 정보 보존 (있다면)
                        tv = float(row.get('acml_tr_pbmn', 0))
                        current_tv = candidate_stocks[code].get('trading_value', 0)
                        if tv > current_tv:
                            candidate_stocks[code]['trading_value'] = tv
            
            # 5. 최종 후보 선별 및 점수 계산
            final_candidates = []
            
            for code, data in candidate_stocks.items():
                total_score = data['score']
                reasons = ', '.join(data['reasons'])
                
                # 기존 종목 제외 로직 개선
                if code in excluded_codes:
                    if not (self.reinclude_sold and self.stock_manager.trading_status.get(code) == StockStatus.SOLD):
                        continue

                # 거래대금 필터 (가능하면 row 캐싱 필요)
                # 값이 없는 경우 0 으로 처리
                trading_value = float(data.get('trading_value', 0)) if isinstance(data, dict) else 0
                if 0 < trading_value < self.min_trading_value:
                    logger.debug(f"거래대금 부족으로 제외 {code}: {trading_value:,.0f}")
                    continue

                # 최소 점수 기준 (20점 이상)
                if total_score >= self.min_total_score:
                    final_candidates.append((code, total_score, reasons))
            
            # 점수순 정렬 및 상위 선별
            final_candidates.sort(key=lambda x: x[1], reverse=True)
            selected_stocks = final_candidates[:max_stocks]
            
            # 결과 로깅
            logger.info(f"✅ 장중 추가 종목 스캔 완료: {len(selected_stocks)}개 선별")
            for i, (code, score, reasons) in enumerate(selected_stocks, 1):
                stock_name = stock_loader.get_stock_name(code)
                logger.info(f"  {i}. {code}[{stock_name}] - 점수:{score:.1f} ({reasons})")
            
            return selected_stocks
            
        except Exception as e:
            logger.error(f"❌ 장중 추가 종목 스캔 실패: {e}")
            return [] 