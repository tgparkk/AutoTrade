"""
장시작전 시장 스캔 및 종목 선정을 담당하는 MarketScanner 클래스
"""

from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime, timedelta
from models.position import Position
from .stock_manager import StockManager
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

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
        
        # 스크리닝 기준
        self.volume_increase_threshold = self.strategy_config.get('volume_increase_threshold', 2.0)
        self.volume_min_threshold = self.strategy_config.get('volume_min_threshold', 100000)
        self.top_stocks_count = 15  # 상위 15개 종목 선정
        
        logger.info("MarketScanner 초기화 완료")
    
    def set_websocket_manager(self, websocket_manager):
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

        scan_candidates = scan_candidates[:100]
        
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
            
            ohlcv_data = get_inquire_daily_itemchartprice(
                output_dv="2",
                itm_no=stock_code,
                period_code="D",
                adj_prc="1"
            )
        except Exception as e:
            logger.debug(f"종합 분석용 API 호출 실패 {stock_code}: {e}")
        
        # 기본 분석 (같은 데이터 재사용)
        if _is_data_empty(ohlcv_data):
            logger.debug(f"OHLCV 데이터가 없어 종목 제외: {stock_code}")
            return None
        
        fundamentals = self._calculate_real_fundamentals(stock_code, ohlcv_data)
        if not fundamentals:
            logger.debug(f"기본 분석 실패로 종목 제외: {stock_code}")
            return None
        
        # 캔들패턴 분석 (같은 데이터 재사용)
        if _get_data_length(ohlcv_data) < 5:
            logger.debug(f"캔들패턴 분석용 데이터 부족으로 종목 제외: {stock_code}")
            return None
        
        patterns = self._analyze_real_candle_patterns(stock_code, ohlcv_data)
        if not patterns:
            logger.debug(f"캔들패턴 분석 실패로 종목 제외: {stock_code}")
            return None
        
        # 점수 계산 (가중치 적용)
        volume_score = min(fundamentals['volume_increase_rate'] * 10, 30)  # 최대 30점
        technical_score = (fundamentals['rsi'] / 100) * 20  # 최대 20점
        pattern_score = patterns['total_pattern_score'] * 25  # 최대 25점 (패턴당 평균 0.8점 가정)
        ma_score = 15 if fundamentals['ma_alignment'] else 0  # 15점 또는 0점
        momentum_score = min(fundamentals['price_change_rate'] * 100, 10)  # 최대 10점
        
        total_score = volume_score + technical_score + pattern_score + ma_score + momentum_score
        
        logger.debug(f"{stock_code} 점수 계산: 거래량({volume_score:.1f}) + 기술적({technical_score:.1f}) + "
                    f"패턴({pattern_score:.1f}) + MA({ma_score:.1f}) + 모멘텀({momentum_score:.1f}) = {total_score:.1f}")
        
        return min(total_score, 100)  # 최대 100점
    
    def get_stock_basic_info(self, stock_code: str) -> Dict:
        """종목 기본 정보 조회
        
        Args:
            stock_code: 종목코드
            
        Returns:
            종목 기본 정보
        """
        # StockDataLoader를 사용하여 실제 종목명 조회
        from utils.stock_data_loader import get_stock_data_loader
        
        stock_loader = get_stock_data_loader()
        stock_name = stock_loader.get_stock_name(stock_code)
        
        if not stock_name:
            logger.warning(f"종목 정보를 찾을 수 없습니다: {stock_code}")
            stock_name = f"종목{stock_code}"
        
        return {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'yesterday_close': 75000,  # 더미 데이터 - TODO: 실제 API 연동
            'yesterday_volume': 1000000,
            'market_cap': 500000000000,
            'sector': '반도체'
        }
    
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
                
                # StockManager에 등록
                success = self.stock_manager.add_selected_stock(
                    stock_code=stock_code,
                    stock_name=stock_info['stock_name'],
                    open_price=stock_info['yesterday_close'],  # 전일 종가를 시가로 임시 사용
                    high_price=stock_info['yesterday_close'],
                    low_price=stock_info['yesterday_close'], 
                    close_price=stock_info['yesterday_close'],
                    volume=stock_info['yesterday_volume'],
                    selection_score=score
                )
                
                if success:
                    success_count += 1
                    
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