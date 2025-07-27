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

# 공통 유틸 함수 (scanner.utils)
from trade.scanner.utils import (
    is_data_empty as _is_data_empty,
    get_data_length as _get_data_length,
    convert_to_dict_list as _convert_to_dict_list,
)

# 실시간 divergence 계산 모듈
from trade.scanner.realtime_divergence import (
    get_stock_divergence_rates as compute_rt_divergence_rates,
    get_stock_divergence_signal as compute_rt_divergence_signal,
)

# 고급 스캐너 모듈 (지연 로딩)
try:
    from trade.scanner.market_scanner_advanced import MarketScannerAdvanced
    ADVANCED_SCANNER_AVAILABLE = True
except ImportError:
    MarketScannerAdvanced = None
    ADVANCED_SCANNER_AVAILABLE = False

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
        self.daytrading_config = self.config_loader.load_daytrading_config()
        
        # 스크리닝 기준 (장전 스캔용) - 데이트레이딩 최적화
        self.volume_increase_threshold = self.strategy_config.get('volume_increase_threshold', 2.0)
        self.volume_min_threshold = self.strategy_config.get('volume_min_threshold', 500000)  # 50만주로 강화
        
        # 🆕 데이트레이딩 활성도 필터
        self.min_daily_volatility = self.strategy_config.get('min_daily_volatility', 1.0)
        self.min_price_change_rate = self.strategy_config.get('min_price_change_rate_for_buy', 0.3)
        self.min_volume_turnover_rate = self.strategy_config.get('min_volume_turnover_rate', 0.5)
        self.min_contract_activity = self.strategy_config.get('min_contract_activity', 50)
        # 상위 종목 선정 개수 – 설정 파일(max_premarket_selected_stocks)과 동기화
        self.top_stocks_count = self.performance_config.get('max_premarket_selected_stocks', 15)
        
        # 🆕 장중 스캔 튜닝 파라미터
        self.rank_head_limit   = self.performance_config.get('intraday_rank_head_limit', 50)
        self.min_total_score   = self.performance_config.get('intraday_min_total_score', 18)
        # 단위: 백만원 → 원
        self.min_trading_value = self.performance_config.get('intraday_min_trading_value', 2000) * 1_000_000
        self.max_spread_pct    = self.performance_config.get('intraday_max_spread_percent', 2.0)
        self.reinclude_sold    = self.performance_config.get('intraday_reinclude_sold', True)
        
        # 🆕 데이터베이스는 싱글톤 패턴으로 필요시 생성
        logger.info("✅ MarketScanner 초기화 완료 (데이터베이스는 필요시 생성)")
        
        # 🆕 고급 장전 스캐너 초기화 (지연 로딩)
        self._advanced_scanner_module = None
        
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

        # 전체 KOSPI 종목 중 우선주·스팩 제외
        base_candidates = [
            stock for stock in all_stocks
            if stock['code'].isdigit() and len(stock['code']) == 6 and '우' not in stock['name']
        ]


        # 2. 각 종목별 종합 점수 계산
        scored_stocks = []
        
        for stock in base_candidates: # scan_candidates
            try:
                stock_code = stock['code']
                
                # 종합 점수 계산
                score = self.calculate_comprehensive_score(stock_code)
                
                # API 실패로 점수를 계산할 수 없는 종목은 제외
                if score is None:
                    logger.debug(f"점수 계산 실패로 종목 제외: {stock_code}")
                    continue
                
                # 최소 점수 기준 – PERFORMANCE.opening_pattern_score_threshold 값을 사용
                min_score = self.performance_config.get('opening_pattern_score_threshold', 55.0)
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
    
    def scan_market_pre_open_advanced(self) -> List[Dict[str, Any]]:
        """고급 장전 스캐너를 사용한 시장 스캔 (모듈 위임)
        
        Returns:
            상위 후보 종목들의 상세 분석 결과 리스트
        """
        advanced_module = self._get_advanced_scanner_module()
        if not advanced_module:
            logger.error("고급 스캐너 모듈을 사용할 수 없습니다")
            return []
        
        return advanced_module.scan_market_pre_open_advanced()
    
    def _get_advanced_scanner_module(self):
        """고급 스캐너 모듈 인스턴스 반환 (싱글톤 패턴)"""
        if self._advanced_scanner_module is None:
            if not ADVANCED_SCANNER_AVAILABLE:
                logger.warning("고급 스캐너 모듈을 사용할 수 없습니다")
                return None
            
            try:
                self._advanced_scanner_module = MarketScannerAdvanced(
                    stock_manager=self.stock_manager,
                    websocket_manager=self.websocket_manager
                )
                
                # 설정 주입
                self._advanced_scanner_module.set_config(
                    strategy_config=self.strategy_config,
                    performance_config=self.performance_config
                )
                
                logger.info("✅ 고급 스캐너 모듈 초기화 완료")
                
            except Exception as e:
                logger.error(f"고급 스캐너 모듈 초기화 실패: {e}")
                return None
        
        return self._advanced_scanner_module
    
    
    def run_combined_pre_market_scan(self) -> Tuple[List[Tuple[str, float]], List[Dict[str, Any]]]:
        """기존 + 고급 스캐너 결합 실행 (모듈 위임)
        
        Returns:
            (기존 스캔 결과, 고급 스캐너 결과) 튜플
        """
        # 1. 기존 스캐너 실행
        logger.info("1️⃣ 기존 장전 스캐너 실행")
        traditional_results = self.scan_market_pre_open()
        
        # 2. 고급 스캐너 모듈로 위임
        advanced_module = self._get_advanced_scanner_module()
        if not advanced_module:
            logger.warning("고급 스캐너 모듈 없음 - 기존 스캐너 결과만 반환")
            return traditional_results, []
        
        return advanced_module.run_combined_pre_market_scan(traditional_results)
    
    def _select_top_stocks_from_advanced_results(self, scan_results: List[Dict[str, Any]]) -> bool:
        """고급 스캐너 결과에서 상위 종목 선정 및 등록 (모듈 위임)"""
        advanced_module = self._get_advanced_scanner_module()
        if not advanced_module:
            logger.error("고급 스캐너 모듈 없음")
            return False
        
        return advanced_module.select_top_stocks_from_advanced_results(scan_results)
    
    def _select_stocks_from_combined_results(self, traditional_results: List[Tuple[str, float]], 
                                           advanced_results: List[Dict[str, Any]]) -> bool:
        """통합 스캔 결과에서 종목 선정 (모듈 위임)"""
        advanced_module = self._get_advanced_scanner_module()
        if not advanced_module:
            logger.error("고급 스캐너 모듈 없음")
            return False
        
        return advanced_module.select_stocks_from_combined_results(traditional_results, advanced_results)
    

    
    def _calculate_real_fundamentals(self, stock_code: str, ohlcv_data: Any) -> Optional[Dict]:
        """실제 OHLCV 데이터에서 기본 분석 지표 계산
        
        Args:
            stock_code: 종목코드
            ohlcv_data: API에서 가져온 OHLCV 데이터
            
        Returns:
            분석 결과 딕셔너리 또는 None (데이터 부족시)
        """
        # 외부 모듈(trade.scanner.fundamental) 로직으로 위임
        from trade.scanner.fundamental import calculate_fundamentals
        return calculate_fundamentals(stock_code, ohlcv_data)
    
    # ===== 이격도 계산 메서드 섹션 =====
    
    def get_stock_divergence_rates(self, stock: 'Stock') -> Dict[str, float]:
        """얇은 래퍼 – 실시간 이격도 계산 (trade.scanner.realtime_divergence 사용)"""
        return compute_rt_divergence_rates(stock)
    
    def get_stock_divergence_signal(self, stock: 'Stock') -> Dict[str, Any]:
        """얇은 래퍼 – 실시간 매매 신호 (trade.scanner.realtime_divergence 사용)"""
        return compute_rt_divergence_signal(stock)
    
    # ===== 실시간 이격도 분석 (Stock 객체용) =====
    
    def _get_divergence_analysis(self, stock_code: str, ohlcv_data: Any) -> Optional[Dict]:
        """종목별 이격도 종합 분석 (스크리닝용)
        
        Args:
            stock_code: 종목코드
            ohlcv_data: OHLCV 데이터
            
        Returns:
            이격도 분석 결과 또는 None
        """
        try:
            from trade.scanner.divergence import analyze_divergence
            return analyze_divergence(stock_code, ohlcv_data)
            
        except Exception as e:
            logger.debug(f"이격도 분석 실패 {stock_code}: {e}")
            return None
    
    def _analyze_real_candle_patterns(self, stock_code: str, ohlcv_data: Any) -> Optional[Dict]:
        """(Deprecated) utils.analyze_candle_patterns 래퍼"""
        data_list = _convert_to_dict_list(ohlcv_data)
        from utils.technical_indicators import analyze_candle_patterns
        return analyze_candle_patterns(data_list)
    
    def calculate_comprehensive_score(self, stock_code: str) -> Optional[float]:
        """얇은 래퍼 – trade.scanner.scoring 모듈 호출"""
        from trade.scanner.scoring import calculate_comprehensive_score as _calc
        return _calc(self, stock_code)
    
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
                'pattern_score': pattern_analysis.get('pattern_score', 0) if pattern_analysis else 0,
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
                
                # 🔧 최소 거래량 조건 강화 (데이트레이딩 최적화)
                if volume < self.volume_min_threshold:
                    logger.debug(f"거래량 부족으로 종목 제외: {stock_code} 거래량:{volume:,} < {self.volume_min_threshold:,}")
                    return None
                
                # 🔥 정확한 price_change_rate 계산 (일봉 데이터 기반)
                accurate_price_change_rate = 0.0
                if yesterday_close > 0 and yesterday_close != current_price:
                    accurate_price_change_rate = (current_price - yesterday_close) / yesterday_close * 100
                
                # 🆕 최소 상승률 조건 (데이트레이딩 활성도 필터)
                if accurate_price_change_rate < self.min_price_change_rate:
                    logger.debug(f"상승률 부족으로 종목 제외: {stock_code} ({accurate_price_change_rate:.1f}% < {self.min_price_change_rate}%)")
                    return None
                
                # 🆕 일중 변동성 조건 (고가/저가 기반)
                high_price = float(row.get('stck_hgpr', current_price))
                low_price = float(row.get('stck_lwpr', current_price))
                if high_price > 0 and low_price > 0 and low_price != high_price:
                    daily_volatility = (high_price - low_price) / low_price * 100
                    if daily_volatility < self.min_daily_volatility:
                        logger.debug(f"일중 변동성 부족으로 종목 제외: {stock_code} ({daily_volatility:.1f}% < {self.min_daily_volatility}%)")
                        return None
                
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
    
    def run_pre_market_scan(self, use_advanced_scanner: bool = False) -> bool:
        """전체 장시작전 스캔 프로세스 실행
        
        Args:
            use_advanced_scanner: 고급 스캐너 사용 여부
        
        Returns:
            스캔 성공 여부
        """
        try:
            logger.info("=== 장시작전 시장 스캔 프로세스 시작 ===")
            
            # 1. 기존 선정 종목 초기화
            self.stock_manager.clear_all_stocks()
            
            # 2. 스캐너 선택 및 실행
            if use_advanced_scanner:
                logger.info("🚀 고급 스캐너 모드 사용")
                scan_results = self.scan_market_pre_open_advanced()
                success = self._select_top_stocks_from_advanced_results(scan_results)
            else:
                logger.info("📊 기존 스캐너 모드 사용")
                scan_results = self.scan_market_pre_open()
                if not scan_results:
                    logger.warning("스캔 결과가 없습니다")
                    return False
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
    
    def run_pre_market_scan_combined(self) -> bool:
        """기존 + 고급 스캐너 통합 실행
        
        Returns:
            스캔 성공 여부
        """
        try:
            logger.info("=== 통합 장전 스캔 프로세스 시작 ===")
            
            # 1. 기존 선정 종목 초기화
            self.stock_manager.clear_all_stocks()
            
            # 2. 통합 스캔 실행
            traditional_results, advanced_results = self.run_combined_pre_market_scan()
            
            # 3. 결과 통합 및 종목 선정
            success = self._select_stocks_from_combined_results(traditional_results, advanced_results)
            
            if success:
                logger.info("=== 통합 장전 스캔 프로세스 완료 ===")
                summary = self.stock_manager.get_stock_summary()
                logger.info(f"선정된 종목 수: {summary['total_selected']}")
            else:
                logger.error("통합 종목 선정 과정에서 오류 발생")
            
            return success
            
        except Exception as e:
            logger.error(f"통합 장전 스캔 프로세스 오류: {e}")
            return False
    
    def __str__(self) -> str:
        """문자열 표현"""
        return f"MarketScanner(거래량기준: {self.volume_increase_threshold}배, 최소거래량: {self.volume_min_threshold:,}주)"

    # ===== 장중 추가 종목 선별 섹션 =====
    
    def intraday_scan_additional_stocks(self, max_stocks: int = 5) -> List[Tuple[str, float, str]]:
        """장중 추가 종목 스캔 (순위분석 API 활용) - 현실적 조건으로 조정
        
        Args:
            max_stocks: 최대 선별 종목 수
            
        Returns:
            (종목코드, 점수, 선별사유) 튜플 리스트
        """
        logger.info(f"🔍 장중 추가 종목 스캔 시작 (현실적 조건, 목표: {max_stocks}개)")
        
        try:
            from utils.stock_data_loader import get_stock_data_loader
            stock_loader = get_stock_data_loader()

            from api.kis_market_api import (
                get_disparity_rank, get_fluctuation_rank, 
                get_volume_rank, get_bulk_trans_num_rank,
                get_inquire_price  # 호가창 분석용 추가
            )
            
            # 기존 선정 종목 제외를 위한 코드 리스트
            excluded_codes = set(self.stock_manager.get_all_stock_codes())
            logger.debug(f"기존 관리 종목 제외: {len(excluded_codes)}개 ({', '.join(list(excluded_codes)[:5])}{'...' if len(excluded_codes) > 5 else ''})")
            
            candidate_stocks = {}  # {종목코드: {'score': 점수, 'reasons': [사유들]}}

            # === 순위 API 병렬 호출 (클래스 메서드 사용) ===
            rank_data = self._fetch_rank_data_parallel()
            disparity_data   = rank_data.get('disparity')
            fluctuation_data = rank_data.get('fluctuation')
            volume_data      = rank_data.get('volume')
            strength_data    = rank_data.get('strength')

            # 🔧 1. 이격도 순위 (과매도 구간) - 조건 완화
            logger.debug("📊 이격도 순위 조회 (과매도)")
            if disparity_data is not None and len(disparity_data) > 0:
                for idx, row in disparity_data.head(self.rank_head_limit).iterrows():
                    code = row.get('mksc_shrn_iscd', '')
                    if code and code not in excluded_codes and code in stock_loader:
                        disparity_rate = float(row.get('dspr', 0))
                        if disparity_rate <= -1.5:  # 🔧 -3.0% → -1.5%로 완화
                            score = min(abs(disparity_rate) * 1.5, 15)  # 🔧 최대 점수 20→15로 조정
                            if code not in candidate_stocks:
                                candidate_stocks[code] = {'score': 0, 'reasons': [], 'raw_data': {}}
                            candidate_stocks[code]['score'] += score
                            candidate_stocks[code]['reasons'].append(f"이격도과매도({disparity_rate:.1f}%)")
                            candidate_stocks[code]['raw_data']['disparity_rate'] = disparity_rate
                            # 거래대금 정보 보존 (있다면)
                            tv = float(row.get('acml_tr_pbmn', 0))
                            current_tv = candidate_stocks[code].get('trading_value', 0)
                            if tv > current_tv:
                                candidate_stocks[code]['trading_value'] = tv
            
            # 🔧 2. 등락률 순위 (상승 모멘텀) - 구간 확대
            logger.debug("📊 등락률 순위 조회 (상승)")
            if fluctuation_data is not None and len(fluctuation_data) > 0:
                for idx, row in fluctuation_data.head(self.rank_head_limit).iterrows():
                    code = row.get('mksc_shrn_iscd', '')
                    if code and code not in excluded_codes and code in stock_loader:
                        change_rate = float(row.get('prdy_ctrt', 0))
                        if 0.2 <= change_rate <= 10.0:  # 🔧 0.3~6.0% → 0.2~10.0%로 확대
                            # 🔧 점수 계산 단순화 (복잡한 구간별 차등 제거)
                            score = min(change_rate * 2, 12)  # 단순 비례, 최대 12점
                            
                            if code not in candidate_stocks:
                                candidate_stocks[code] = {'score': 0, 'reasons': [], 'raw_data': {}}
                            candidate_stocks[code]['score'] += score
                            candidate_stocks[code]['reasons'].append(f"상승모멘텀({change_rate:.1f}%)")
                            candidate_stocks[code]['raw_data']['change_rate'] = change_rate
                            # 거래대금 정보 보존 (있다면)
                            tv = float(row.get('acml_tr_pbmn', 0))
                            current_tv = candidate_stocks[code].get('trading_value', 0)
                            if tv > current_tv:
                                candidate_stocks[code]['trading_value'] = tv
            
            # 🔧 3. 거래량 순위 (관심도) - 조건 대폭 완화
            logger.debug("📊 거래량 순위 조회")
            if volume_data is not None and len(volume_data) > 0:
                for idx, row in volume_data.head(self.rank_head_limit).iterrows():
                    code = row.get('mksc_shrn_iscd', '')
                    if code and code not in excluded_codes and code in stock_loader:
                        volume_ratio = float(row.get('vol_inrt', 0))
                        if volume_ratio >= 150:  # 🔧 200% → 150%로 완화
                            # 🔧 점수 체계 단순화
                            if volume_ratio >= 400:
                                score = 10  # 폭발적 증가
                            elif volume_ratio >= 250:
                                score = 8   # 높은 증가
                            else:
                                score = 6   # 적당한 증가
                                
                            if code not in candidate_stocks:
                                candidate_stocks[code] = {'score': 0, 'reasons': [], 'raw_data': {}}
                            candidate_stocks[code]['score'] += score
                            candidate_stocks[code]['reasons'].append(f"거래량급증({volume_ratio:.0f}%)")
                            candidate_stocks[code]['raw_data']['volume_ratio'] = volume_ratio
                            # 거래대금 정보 보존 (있다면)
                            tv = float(row.get('acml_tr_pbmn', 0))
                            current_tv = candidate_stocks[code].get('trading_value', 0)
                            if tv > current_tv:
                                candidate_stocks[code]['trading_value'] = tv
            
            # 🔧 4. 체결강도 상위 (매수세) - 단순화
            logger.debug("📊 체결강도 순위 조회")
            if strength_data is not None and len(strength_data) > 0:
                for idx, row in strength_data.head(self.rank_head_limit).iterrows():
                    code = row.get('mksc_shrn_iscd', '')
                    if code and code not in excluded_codes and code in stock_loader:
                        # 🔧 복잡한 체결강도 분석 → 단순 점수로 변경
                        score = 6  # 순위권 진입 자체가 의미있으므로 기본 점수 부여
                            
                        if code not in candidate_stocks:
                            candidate_stocks[code] = {'score': 0, 'reasons': [], 'raw_data': {}}
                        candidate_stocks[code]['score'] += score
                        candidate_stocks[code]['reasons'].append("체결강도상위")
                        # 거래대금 정보 보존 (있다면)
                        tv = float(row.get('acml_tr_pbmn', 0))
                        current_tv = candidate_stocks[code].get('trading_value', 0)
                        if tv > current_tv:
                            candidate_stocks[code]['trading_value'] = tv
            
            # 🔧 5. 데이트레이딩 특화 분석 - 선택적 적용으로 변경
            logger.debug("📊 데이트레이딩 특화 분석 시작 (선택적 적용)")
            enhanced_candidates = {}
            
            for code, data in candidate_stocks.items():
                # 🔧 기본 점수 임계값 대폭 완화 (15점 → 8점)
                if data['score'] >= 8:
                    try:
                        # 호가창 분석 (실패해도 기본 데이터 유지)
                        orderbook_score, orderbook_reason = self._analyze_orderbook_for_daytrading_flexible(code)
                        
                        # 타이밍 점수 (항상 적용)
                        timing_score, timing_reason = self._calculate_daytrading_timing_score()
                        
                        # 종합 점수 계산 (유동성 포함)
                        total_score = data['score'] + orderbook_score + timing_score
                        
                        try:
                            liq_score = self.stock_manager.get_liquidity_score(code)
                        except AttributeError:
                            liq_score = 0.0
                        liq_weight = self.performance_config.get('liquidity_weight', 1.0)
                        total_score += liq_score * liq_weight
                        
                        # 개선된 사유 정리
                        enhanced_reasons = data['reasons'][:]
                        if orderbook_reason:
                            enhanced_reasons.append(orderbook_reason)
                        if timing_reason:
                            enhanced_reasons.append(timing_reason)
                        
                        enhanced_candidates[code] = {
                            'score': total_score,
                            'reasons': enhanced_reasons,
                            'trading_value': data.get('trading_value', 0),
                            'raw_data': data.get('raw_data', {})
                        }
                        
                    except Exception as e:
                        logger.debug(f"추가 분석 실패 (기본 데이터 유지) {code}: {e}")
                        # 기본 데이터는 유지
                        enhanced_candidates[code] = data
                else:
                    # 기본 점수가 낮은 종목도 유지 (기회 놓치지 않기 위해)
                    enhanced_candidates[code] = data
            
            # 6. 최종 후보 선별 및 점수 계산
            final_candidates = []
            
            # 🔧 동적 거래대금 기준 계산 (현재 후보들의 분포 기반)
            all_trading_values = []
            for code, data in enhanced_candidates.items():
                trading_value = float(data.get('trading_value', 0)) if isinstance(data, dict) else 0
                if trading_value > 0:  # 0은 제외
                    all_trading_values.append(trading_value)
            
            # 동적 거래대금 기준 설정
            if all_trading_values:
                import numpy as np
                all_trading_values.sort()
                
                # 🔧 시장 상황에 따른 적응형 기준
                num_candidates = len(all_trading_values)
                if num_candidates >= 30:  # 충분한 후보가 있으면
                    percentile_threshold = 20  # 하위 20% 제외
                elif num_candidates >= 15:  # 중간 수준이면
                    percentile_threshold = 15  # 하위 15% 제외
                else:  # 후보가 적으면
                    percentile_threshold = 10  # 하위 10%만 제외 (더 관대하게)
                
                percentile_value = np.percentile(all_trading_values, percentile_threshold)
                min_absolute_value = 50_000_000  # 최소 5000만원 (기존 1억원에서 완화)
                dynamic_min_trading_value = max(percentile_value, min_absolute_value)
                
                # 🔧 거래대금 분포 정보 로깅
                median_value = np.percentile(all_trading_values, 50)
                logger.debug(f"📊 동적 거래대금 기준: {dynamic_min_trading_value/1_000_000:,.1f}M "
                           f"({percentile_threshold}th percentile: {percentile_value/1_000_000:,.1f}M, "
                           f"median: {median_value/1_000_000:,.1f}M, 후보: {num_candidates}개)")
            else:
                # 기존 방식으로 fallback
                dynamic_min_trading_value = self.min_trading_value * 0.2  # 더욱 관대하게 (50% → 20%)
                logger.debug(f"📊 거래대금 정보 부족으로 기존 방식 사용: {dynamic_min_trading_value/1_000_000:,.1f}M")
            
            for code, data in enhanced_candidates.items():
                total_score = data['score']
                reasons = ', '.join(data['reasons'])
                
                # 기존 종목 제외 로직 개선 (안전한 상태 조회)
                if code in excluded_codes:
                    if not self.reinclude_sold:
                        continue
                    # 매도 완료된 종목 재포함 검사 (안전한 접근)
                    stock_obj = self.stock_manager.get_selected_stock(code)
                    if not stock_obj or stock_obj.status != StockStatus.SOLD:
                        continue

                # 🔧 동적 거래대금 필터 적용
                trading_value = float(data.get('trading_value', 0)) if isinstance(data, dict) else 0
                if 0 < trading_value < dynamic_min_trading_value:
                    logger.debug(f"거래대금 부족으로 제외 {code}: {trading_value:,.0f} < {dynamic_min_trading_value:,.0f}")
                    continue

                # 🔧 최소 점수 기준 대폭 완화 (20점 → 12점)
                min_relaxed_score = self.performance_config.get('intraday_daytrading_min_score', 20) * 0.6  # 40% 완화
                if total_score >= min_relaxed_score:
                    final_candidates.append((code, total_score, reasons))
            
            # 점수순 정렬 및 상위 선별
            final_candidates.sort(key=lambda x: x[1], reverse=True)
            selected_stocks = final_candidates[:max_stocks]
            
            # 결과 로깅
            logger.info(f"✅ 장중 추가 종목 스캔 완료 (현실적 조건): {len(selected_stocks)}개 선별")
            for i, (code, score, reasons) in enumerate(selected_stocks, 1):
                stock_name = stock_loader.get_stock_name(code)
                logger.info(f"  {i}. {code}[{stock_name}] - 점수:{score:.1f} ({reasons})")
            
            return selected_stocks
            
        except Exception as e:
            logger.error(f"❌ 장중 추가 종목 스캔 실패: {e}")
            return []
    
    def _analyze_orderbook_for_daytrading_flexible(self, stock_code: str) -> Tuple[float, str]:
        """데이트레이딩용 호가창 분석 – 외부 모듈로 위임"""
        from trade.scanner.orderbook import analyze_orderbook
        return analyze_orderbook(stock_code, max_spread_pct=self.max_spread_pct)
    
    def _calculate_daytrading_timing_score(self) -> Tuple[float, str]:
        """데이트레이딩 타이밍 점수 – 외부 모듈로 위임"""
        from trade.scanner.timing import calculate_timing_score
        return calculate_timing_score()

    # ===== 스크리닝용 정적 이격도 분석 =====

    def _get_divergence_signal(self, divergence_analysis: Dict) -> Dict[str, Any]:
        """이격도 기반 매매 신호 생성 (스크리닝용)"""
        from trade.scanner.divergence import divergence_signal
        return divergence_signal(divergence_analysis)

    # ===== 순위 API 병렬 호출 유틸 (클래스 레벨) =====
    def _fetch_rank_data_parallel(self) -> Dict[str, Any]:
        """4개의 주요 순위 API를 ThreadPoolExecutor 로 병렬 호출하여 결과를 합친다."""
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from api.kis_market_api import (
                get_disparity_rank, get_fluctuation_rank,
                get_volume_rank, get_bulk_trans_num_rank,
            )

            max_workers = self.performance_config.get('intraday_parallel_workers', 4)

            api_specs = {
                'disparity': (
                    get_disparity_rank,
                    dict(fid_input_iscd="0001", fid_rank_sort_cls_code="1", fid_hour_cls_code="20"),
                ),
                'fluctuation': (
                    get_fluctuation_rank,
                    dict(fid_input_iscd="0001", fid_rank_sort_cls_code="0", fid_rsfl_rate1="0.2", fid_rsfl_rate2="12.0"),
                ),
                'volume': (
                    get_volume_rank,
                    dict(fid_input_iscd="0001", fid_blng_cls_code="1"),
                ),
                'strength': (
                    get_bulk_trans_num_rank,
                    dict(fid_input_iscd="0001", fid_rank_sort_cls_code="0"),
                ),
            }

            results: Dict[str, Any] = {k: None for k in api_specs}
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_key = {
                    executor.submit(func, **params): key
                    for key, (func, params) in api_specs.items()
                }
                for fut in as_completed(future_key):
                    key = future_key[fut]
                    try:
                        results[key] = fut.result()
                    except Exception as exc:
                        logger.error(f"{key} rank API 병렬 호출 실패: {exc}")

            return results
        except Exception as e:
            logger.error(f"순위 API 병렬 호출 준비 실패: {e}")
            return {'disparity': None, 'fluctuation': None, 'volume': None, 'strength': None}