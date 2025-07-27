"""
MarketScanner의 고급 스캐너 기능 모듈

기존 MarketScanner에 영향을 주지 않으면서 고급 스캐너 기능을 분리
"""

from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from trade.stock_manager import StockManager
    from websocket.kis_websocket_manager import KISWebSocketManager

from utils.logger import setup_logger
from utils.korean_time import now_kst
from .advanced_pre_market_scanner import AdvancedPreMarketScanner

logger = setup_logger(__name__)

__all__ = ["MarketScannerAdvanced"]


class MarketScannerAdvanced:
    """MarketScanner의 고급 스캐너 기능 확장 클래스"""
    
    def __init__(self, stock_manager: "StockManager", websocket_manager: "KISWebSocketManager" = None):
        """고급 스캐너 모듈 초기화
        
        Args:
            stock_manager: 종목 관리자 인스턴스
            websocket_manager: 웹소켓 매니저 인스턴스
        """
        self.stock_manager = stock_manager
        self.websocket_manager = websocket_manager
        self._advanced_scanner = None
        
        # 설정 로드 (필요시 외부에서 주입)
        self.strategy_config = {}
        self.performance_config = {}
        self.top_stocks_count = 15
        
        logger.info("MarketScannerAdvanced 초기화 완료")
    
    def set_config(self, strategy_config: Dict, performance_config: Dict):
        """설정 주입
        
        Args:
            strategy_config: 전략 설정
            performance_config: 성능 설정
        """
        self.strategy_config = strategy_config
        self.performance_config = performance_config
        self.top_stocks_count = performance_config.get('max_premarket_selected_stocks', 15)
    
    def scan_market_pre_open_advanced(self) -> List[Dict[str, Any]]:
        """고급 장전 스캐너를 사용한 시장 스캔
        
        Returns:
            상위 후보 종목들의 상세 분석 결과 리스트
        """
        logger.info("🚀 고급 장전 스캐너 시작 - 눌림목 매매 전략")
        
        try:
            # 1. 고급 스캐너 초기화
            advanced_scanner = self._get_advanced_scanner()
            
            # 2. 종목 데이터 수집
            stocks_data = self._collect_stocks_data_for_advanced_scan()
            
            if not stocks_data:
                logger.warning("스캔할 종목 데이터가 없습니다")
                return []
            
            logger.info(f"데이터 수집 완료: {len(stocks_data)}개 종목")
            
            # 3. 고급 스캐너로 분석
            scan_results = advanced_scanner.scan_multiple_stocks(stocks_data)
            
            # 4. 상위 후보 선별
            top_candidates = advanced_scanner.get_top_candidates(
                scan_results, 
                top_n=self.top_stocks_count,
                min_score=70  # 높은 기준 적용
            )
            
            # 5. 결과 로깅
            logger.info(f"🎯 고급 스캔 완료: {len(top_candidates)}개 상위 후보 선별")
            
            for i, result in enumerate(top_candidates, 1):
                stock_code = result['stock_code']
                score = result['final_score']
                entry_signal = result['entry_signal']
                risk_level = result['risk_level']
                
                logger.info(
                    f"{i:2d}. {stock_code} - 점수: {score:.1f} | "
                    f"진입신호: {entry_signal['strength']} | "
                    f"리스크: {risk_level}"
                )
            
            # 6. 데이터베이스 저장
            self._save_advanced_scan_results(top_candidates)
            
            return top_candidates
            
        except Exception as e:
            logger.error(f"고급 장전 스캔 오류: {e}")
            return []
    
    def run_combined_pre_market_scan(self, traditional_results: List[Tuple[str, float]]) -> Tuple[List[Tuple[str, float]], List[Dict[str, Any]]]:
        """기존 + 고급 스캐너 결합 실행
        
        Args:
            traditional_results: 기존 스캐너 결과
        
        Returns:
            (기존 스캔 결과, 고급 스캔 결과) 튜플
        """
        logger.info("🔍 통합 장전 스캔 시작")
        
        # 고급 스캐너 실행  
        logger.info("2️⃣ 고급 장전 스캐너 실행")
        advanced_results = self.scan_market_pre_open_advanced()
        
        # 결과 비교 로깅
        logger.info("📊 스캔 결과 비교:")
        logger.info(f"   기존 스캐너: {len(traditional_results)}개 종목")
        logger.info(f"   고급 스캐너: {len(advanced_results)}개 종목")
        
        # 공통 종목 찾기
        traditional_codes = {code for code, _ in traditional_results}
        advanced_codes = {result['stock_code'] for result in advanced_results}
        common_codes = traditional_codes & advanced_codes
        
        if common_codes:
            logger.info(f"   공통 선정: {len(common_codes)}개 - {list(common_codes)}")
        
        return traditional_results, advanced_results
    
    def select_top_stocks_from_advanced_results(self, scan_results: List[Dict[str, Any]]) -> bool:
        """고급 스캐너 결과에서 상위 종목 선정 및 등록
        
        Args:
            scan_results: 고급 스캐너 분석 결과 리스트
            
        Returns:
            선정 성공 여부
        """
        if not scan_results:
            logger.warning("고급 스캐너 결과가 없습니다")
            return False
        
        try:
            success_count = 0
            websocket_success_count = 0
            
            for result in scan_results:
                stock_code = result['stock_code']
                final_score = result['final_score']
                
                try:
                    # Stock 객체 생성 및 등록
                    success = self.stock_manager.create_and_add_stock_from_code(
                        stock_code=stock_code,
                        total_pattern_score=final_score,
                        selection_reason=f"고급스캐너(점수:{final_score:.1f})",
                        market_phase="pre_open"
                    )
                    
                    if success:
                        success_count += 1
                        logger.info(f"✅ {stock_code} 등록 완료 (점수: {final_score:.1f})")
                        
                        # 웹소켓 구독 시도
                        if self.websocket_manager:
                            try:
                                ws_success = self.websocket_manager.subscribe_stock(stock_code)
                                if ws_success:
                                    websocket_success_count += 1
                                    logger.debug(f"웹소켓 구독 성공: {stock_code}")
                            except Exception as ws_e:
                                logger.debug(f"웹소켓 구독 실패 {stock_code}: {ws_e}")
                    else:
                        logger.warning(f"❌ {stock_code} 등록 실패")
                        
                except Exception as e:
                    logger.error(f"종목 등록 오류 {stock_code}: {e}")
                    continue
            
            logger.info(f"고급 스캐너 종목 등록 완료: {success_count}/{len(scan_results)}개 성공")
            
            if self.websocket_manager and success_count > 0:
                logger.info(f"웹소켓 구독 완료: {websocket_success_count}/{success_count}개 성공")
            
            return success_count > 0
            
        except Exception as e:
            logger.error(f"고급 스캐너 종목 선정 오류: {e}")
            return False
    
    def select_stocks_from_combined_results(self, traditional_results: List[Tuple[str, float]], 
                                          advanced_results: List[Dict[str, Any]]) -> bool:
        """통합 스캔 결과에서 종목 선정
        
        Args:
            traditional_results: 기존 스캐너 결과 [(종목코드, 점수)]
            advanced_results: 고급 스캐너 결과
            
        Returns:
            선정 성공 여부
        """
        try:
            all_candidates = []
            
            # 1. 기존 스캐너 결과 추가
            for stock_code, score in traditional_results:
                all_candidates.append({
                    'stock_code': stock_code,
                    'score': score,
                    'source': 'traditional',
                    'selection_reason': f"기존스캐너(점수:{score:.1f})"
                })
            
            # 2. 고급 스캐너 결과 추가
            for result in advanced_results:
                stock_code = result['stock_code']
                score = result['final_score']
                
                # 중복 종목 처리 (높은 점수 우선)
                existing = next((c for c in all_candidates if c['stock_code'] == stock_code), None)
                if existing:
                    if score > existing['score']:
                        existing['score'] = score
                        existing['source'] = 'advanced'
                        existing['selection_reason'] = f"고급스캐너(점수:{score:.1f})"
                    continue
                
                all_candidates.append({
                    'stock_code': stock_code,
                    'score': score,
                    'source': 'advanced',
                    'selection_reason': f"고급스캐너(점수:{score:.1f})"
                })
            
            # 3. 점수순 정렬 및 상위 종목 선별
            all_candidates.sort(key=lambda x: x['score'], reverse=True)
            top_candidates = all_candidates[:self.top_stocks_count]
            
            # 4. 종목 등록
            success_count = 0
            websocket_success_count = 0
            
            for candidate in top_candidates:
                stock_code = candidate['stock_code']
                score = candidate['score']
                selection_reason = candidate['selection_reason']
                
                try:
                    success = self.stock_manager.create_and_add_stock_from_code(
                        stock_code=stock_code,
                        total_pattern_score=score,
                        selection_reason=selection_reason,
                        market_phase="pre_open"
                    )
                    
                    if success:
                        success_count += 1
                        logger.info(f"✅ {stock_code} 등록 완료 ({selection_reason})")
                        
                        # 웹소켓 구독
                        if self.websocket_manager:
                            try:
                                ws_success = self.websocket_manager.subscribe_stock(stock_code)
                                if ws_success:
                                    websocket_success_count += 1
                            except Exception as ws_e:
                                logger.debug(f"웹소켓 구독 실패 {stock_code}: {ws_e}")
                    else:
                        logger.warning(f"❌ {stock_code} 등록 실패")
                        
                except Exception as e:
                    logger.error(f"통합 종목 등록 오류 {stock_code}: {e}")
                    continue
            
            logger.info(f"통합 스캔 종목 등록 완료: {success_count}/{len(top_candidates)}개 성공")
            logger.info(f"  - 기존 스캐너: {len([c for c in top_candidates if c['source'] == 'traditional'])}개")
            logger.info(f"  - 고급 스캐너: {len([c for c in top_candidates if c['source'] == 'advanced'])}개")
            
            if self.websocket_manager and success_count > 0:
                logger.info(f"웹소켓 구독 완료: {websocket_success_count}/{success_count}개 성공")
            
            return success_count > 0
            
        except Exception as e:
            logger.error(f"통합 결과 종목 선정 오류: {e}")
            return False
    
    def _get_advanced_scanner(self):
        """고급 스캐너 인스턴스 반환 (싱글톤 패턴)"""
        if self._advanced_scanner is None:
            # 설정 구성
            scanner_config = {
                'volume_surge_threshold': self.strategy_config.get('volume_increase_threshold', 3.0),
                'min_trading_value': 5000,  # 50억원
                'pullback_threshold': 0.02,  # 2%
                'max_gap_up': 0.07,  # 7%
                'max_prev_gain': 0.10,  # 10%
                'min_intraday_gain': 0.03,  # 3%
                'early_surge_limit': 0.20,  # 20%
                'volume_weight': 0.25,
                'envelope_weight': 0.25,
                'pullback_weight': 0.30,
                'momentum_weight': 0.20
            }
            
            self._advanced_scanner = AdvancedPreMarketScanner(scanner_config)
            logger.info("✅ 고급 장전 스캐너 초기화 완료")
        
        return self._advanced_scanner
    
    def _collect_stocks_data_for_advanced_scan(self) -> Dict[str, Dict[str, Any]]:
        """고급 스캔용 종목 데이터 수집
        
        Returns:
            {종목코드: 종목데이터} 딕셔너리
        """
        from utils.stock_data_loader import get_stock_data_loader
        from api.kis_market_api import get_inquire_daily_itemchartprice
        
        try:
            stock_loader = get_stock_data_loader()
            all_stocks = stock_loader.stock_list
            
            # 기본 필터링: 우선주, 스팩 제외
            base_candidates = [
                stock for stock in all_stocks
                if stock['code'].isdigit() and len(stock['code']) == 6 and '우' not in stock['name']
            ]
            
            # 상위 200개 종목만 스캔 (성능 최적화)
            scan_limit = min(200, len(base_candidates))
            scan_candidates = base_candidates[:scan_limit]
            
            stocks_data = {}
            success_count = 0
            
            for i, stock in enumerate(scan_candidates):
                stock_code = stock['code']
                
                try:
                    # API 호출로 OHLCV 데이터 수집
                    ohlcv_data = get_inquire_daily_itemchartprice(
                        output_dv="2",
                        itm_no=stock_code,
                        period_code="D",
                        adj_prc="1",
                    )
                    
                    if not ohlcv_data or len(ohlcv_data) < 20:
                        continue
                    
                    # 데이터 변환
                    stock_data = self._convert_ohlcv_to_advanced_format(ohlcv_data)
                    
                    if stock_data:
                        stocks_data[stock_code] = stock_data
                        success_count += 1
                    
                    # 진행 상황 로깅
                    if (i + 1) % 50 == 0:
                        logger.info(f"데이터 수집 진행: {i+1}/{scan_limit} ({success_count}개 성공)")
                
                except Exception as e:
                    logger.debug(f"{stock_code} 데이터 수집 실패: {e}")
                    continue
            
            logger.info(f"데이터 수집 완료: {success_count}/{scan_limit} 성공")
            return stocks_data
            
        except Exception as e:
            logger.error(f"종목 데이터 수집 오류: {e}")
            return {}
    
    def _convert_ohlcv_to_advanced_format(self, ohlcv_data: List[Dict]) -> Optional[Dict[str, List[float]]]:
        """OHLCV 데이터를 고급 스캐너 형식으로 변환
        
        Args:
            ohlcv_data: API에서 받은 OHLCV 데이터
            
        Returns:
            변환된 데이터 딕셔너리
        """
        try:
            if not ohlcv_data or len(ohlcv_data) < 10:
                return None
            
            # 최신순으로 정렬 (0번째가 최신)
            sorted_data = sorted(ohlcv_data, key=lambda x: x.get('stck_bsop_date', ''), reverse=True)
            
            # 리스트 추출 (최신순)
            closes = [float(item.get('stck_clpr', 0)) for item in sorted_data]
            opens = [float(item.get('stck_oprc', 0)) for item in sorted_data]
            highs = [float(item.get('stck_hgpr', 0)) for item in sorted_data]
            lows = [float(item.get('stck_lwpr', 0)) for item in sorted_data]
            volumes = [float(item.get('acml_vol', 0)) for item in sorted_data]
            
            # 데이터 검증
            if not all([closes, opens, highs, lows, volumes]):
                return None
            
            if any(price <= 0 for price in closes[:5]):  # 최근 5일 가격 체크
                return None
            
            return {
                'closes': closes,
                'opens': opens,
                'highs': highs,
                'lows': lows,
                'volumes': volumes
            }
            
        except Exception as e:
            logger.debug(f"OHLCV 데이터 변환 오류: {e}")
            return None
    
    def _save_advanced_scan_results(self, scan_results: List[Dict[str, Any]]):
        """고급 스캔 결과를 데이터베이스에 저장
        
        Args:
            scan_results: 스캔 결과 리스트
        """
        try:
            # 데이터베이스 저장 (옵션)
            from database.trade_database import TradeDatabase
            database = TradeDatabase()
            
            scan_time = now_kst()
            
            for result in scan_results:
                # 장전 스캔 결과 저장
                scan_data = {
                    'scan_date': scan_time.date(),
                    'scan_time': scan_time.time(),
                    'stock_code': result['stock_code'],
                    'selection_score': result['final_score'],
                    'selection_criteria': {
                        'strategy': 'advanced_pullback',
                        'volume_score': result['volume_analysis'].get('score', 0),
                        'envelope_score': result['envelope_analysis'].get('score', 0),
                        'pullback_score': result['pullback_analysis'].get('score', 0),
                        'entry_signal': result['entry_signal']['signal'],
                        'risk_level': result['risk_level']
                    },
                    'technical_indicators': {
                        'current_price': result['current_price'],
                        'volume_ratio': result['volume_analysis'].get('volume_ratio', 0),
                        'pullback_confidence': result['pullback_analysis'].get('confidence', 0)
                    }
                }
                
                database.save_pre_market_scan(scan_data)
            
            logger.info(f"✅ 고급 스캔 결과 저장 완료: {len(scan_results)}개")
            
        except Exception as e:
            logger.debug(f"스캔 결과 저장 오류 (선택사항): {e}")