#!/usr/bin/env python3
"""
매수 조건 분석을 담당하는 BuyConditionAnalyzer 클래스

주요 기능:
- 데이트레이딩 특화 매수 조건 분석
- 모멘텀 기반 점수 계산
- 호가잔량 및 체결불균형 분석
- 거래량 품질 및 시장 미시구조 분석
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime, time as dt_time
from models.stock import Stock, StockStatus
from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)


class BuyConditionAnalyzer:
    """매수 조건 분석 전담 클래스 (Static Methods)"""
    
    @staticmethod
    def analyze_buy_conditions(stock: Stock, realtime_data: Dict, 
                              market_phase: str, strategy_config: Dict,
                              performance_config: Dict) -> bool:
        """데이트레이딩 특화 매수 조건 분석 (속도 최적화 + 모멘텀 중심)
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            market_phase: 시장 단계
            strategy_config: 전략 설정
            performance_config: 성능 설정
            
        Returns:
            매수 조건 충족 여부
        """
        try:
            # === 🚨 1단계: 즉시 배제 조건 (속도 최적화) ===
            if not BuyConditionAnalyzer._check_basic_eligibility(stock, realtime_data, strategy_config):
                return False
            
            # === 🚀 2단계: 모멘텀 우선 검증 (데이트레이딩 핵심) ===
            momentum_score = BuyConditionAnalyzer._calculate_momentum_score(
                stock, realtime_data, market_phase, performance_config
            )
            
            min_momentum_score = BuyConditionAnalyzer._get_min_momentum_score(market_phase, performance_config)
            if momentum_score < min_momentum_score:
                logger.info(f"❌ 모멘텀 부족 제외: {stock.stock_code}({stock.stock_name}) "
                           f"모멘텀점수: {momentum_score}/{min_momentum_score}")
                return False
            
            # === 📊 3단계: 세부 조건 점수 계산 ===
            total_score = momentum_score
            condition_details = [f"모멘텀({momentum_score}점)"]
            
            # 설정 기반 시장 단계별 임계값
            thresholds = BuyConditionAnalyzer._get_market_phase_thresholds(market_phase, strategy_config, performance_config)
            
            # 이격도 조건 (0~25점)
            divergence_score, divergence_info = BuyConditionAnalyzer._analyze_divergence_buy_score(stock, market_phase)
            total_score += divergence_score
            condition_details.append(f"이격도({divergence_score}점, {divergence_info})")
            
            # 시간 민감성 점수 (0~15점)
            time_score = BuyConditionAnalyzer._calculate_time_sensitivity_score(market_phase, stock)
            total_score += time_score
            condition_details.append(f"시간민감성({time_score}점)")
            
            # === 🆕 4단계: 고급 시장 미시구조 분석 ===
            
            # 호가잔량 분석 (0~10점)
            orderbook_score = BuyConditionAnalyzer._calculate_orderbook_strength_score(stock)
            total_score += orderbook_score
            condition_details.append(f"호가분석({orderbook_score}점)")
            
            # 체결 불균형 분석 (0~8점)
            contract_balance_score = BuyConditionAnalyzer._calculate_contract_balance_score(stock)
            total_score += contract_balance_score
            condition_details.append(f"체결불균형({contract_balance_score}점)")
            
            # 거래량 품질 분석 (0~7점)
            volume_quality_score = BuyConditionAnalyzer._calculate_volume_quality_score(stock, market_phase)
            total_score += volume_quality_score
            condition_details.append(f"거래량품질({volume_quality_score}점)")
            
            # === 📈 5단계: 기존 조건들 ===
            
            # 매수비율 조건 (0~10점)
            buy_ratio = getattr(stock.realtime_data, 'buy_ratio', 50.0)
            if buy_ratio >= thresholds['buy_ratio_min']:
                ratio_score = min(10, int((buy_ratio - thresholds['buy_ratio_min']) / 10 + 7))
                total_score += ratio_score
                condition_details.append(f"매수비율({ratio_score}점)")
            elif buy_ratio >= thresholds['buy_ratio_min'] * 0.8:
                ratio_score = 5
                total_score += ratio_score
                condition_details.append(f"매수비율({ratio_score}점, 부분달성)")
            
            # 패턴 점수 조건 (0~10점)
            if stock.total_pattern_score >= thresholds['min_pattern_score']:
                pattern_score = min(10, int((stock.total_pattern_score - thresholds['min_pattern_score']) / 10 + 7))
                total_score += pattern_score
                condition_details.append(f"패턴({pattern_score}점)")
            elif stock.total_pattern_score >= thresholds['min_pattern_score'] * 0.8:
                pattern_score = 5
                total_score += pattern_score
                condition_details.append(f"패턴({pattern_score}점, 부분달성)")
            
            # === 🎯 최종 매수 신호 판단 ===
            required_total_score = thresholds['required_total_score']
            buy_signal = total_score >= required_total_score
            
            if buy_signal:
                logger.info(f"🚀 {stock.stock_code}({stock.stock_name}) 매수 신호 ({market_phase}): "
                           f"총점 {total_score}/100점 (기준:{required_total_score}점) "
                           f"- {', '.join(condition_details)}")
            else:
                logger.info(f"❌ {stock.stock_code}({stock.stock_name}) 매수 조건 미달 ({market_phase}): "
                            f"총점 {total_score}/100점 (기준:{required_total_score}점) "
                            f"- {', '.join(condition_details)}")
            
            return buy_signal
            
        except Exception as e:
            logger.error(f"매수 조건 분석 오류 {stock.stock_code}: {e}")
            return False
    
    @staticmethod
    def _check_basic_eligibility(stock: Stock, realtime_data: Dict, strategy_config: Dict) -> bool:
        """기본 적격성 체크 (즉시 배제 조건)"""
        try:
            # 거래정지, VI발동 등 절대 금지 조건
            vi_standard_price = getattr(stock.realtime_data, 'vi_standard_price', 0)
            trading_halt = getattr(stock.realtime_data, 'trading_halt', False)
            
            hour_cls_code = getattr(stock.realtime_data, 'hour_cls_code', '0')
            market_op_code = getattr(stock.realtime_data, 'market_operation_code', '20')
            is_vi = (hour_cls_code in ['51', '52']) or (market_op_code in ['30', '31'])
            
            if trading_halt or is_vi:
                logger.debug(f"거래 제외: {stock.stock_code} (거래정지: {trading_halt}, VI발동: {is_vi})")
                return False
            
            # 가격 정보 확인
            current_price = realtime_data.get('current_price', stock.close_price)
            if current_price <= 0:
                logger.debug(f"가격 정보 없음: {stock.stock_code}")
                return False
            
            # 🔥 실시간 데이터 품질 체크 (시스템 완전성 가정)
            total_ask_qty = getattr(stock.realtime_data, 'total_ask_qty', 0)
            total_bid_qty = getattr(stock.realtime_data, 'total_bid_qty', 0)
            volume_turnover_rate = getattr(stock.realtime_data, 'volume_turnover_rate', 0.0)
            buy_contract_count = getattr(stock.realtime_data, 'buy_contract_count', 0)
            sell_contract_count = getattr(stock.realtime_data, 'sell_contract_count', 0)
            
            # 필수 실시간 데이터 존재 여부 확인
            has_orderbook_data = (total_ask_qty > 0 and total_bid_qty > 0)
            has_volume_data = (volume_turnover_rate > 0)
            has_contract_data = (buy_contract_count > 0 or sell_contract_count > 0)
            
            # 최소 2가지 이상의 실시간 데이터가 있어야 매수 허용
            realtime_data_score = sum([has_orderbook_data, has_volume_data, has_contract_data])
            min_required_data = strategy_config.get('min_realtime_data_types', 2)
            
            if realtime_data_score < min_required_data:
                logger.debug(f"실시간 데이터 부족으로 제외: {stock.stock_code} "
                           f"(호가:{has_orderbook_data}, 거래량:{has_volume_data}, "
                           f"체결:{has_contract_data}) - {realtime_data_score}/{min_required_data}개 타입")
                return False
            
            # price_change_rate 백업 로직
            price_change_rate = realtime_data.get('price_change_rate', 0)
            if price_change_rate == 0 and stock.reference_data.yesterday_close > 0:
                calculated_rate = (current_price - stock.reference_data.yesterday_close) / stock.reference_data.yesterday_close * 100
                price_change_rate = calculated_rate
                logger.debug(f"price_change_rate 계산: {stock.stock_code} = {calculated_rate:.2f}%")
            
            # 급락 징후 체크 (5% 이상 하락)
            if price_change_rate <= -5.0:
                logger.debug(f"급락 종목 제외: {stock.stock_code} ({price_change_rate:.1f}%)")
                return False
            
            # 유동성 부족 체크 (호가 스프레드) - 실시간 데이터가 있을 때만
            if has_orderbook_data:
                bid_price = realtime_data.get('bid_price', 0)
                ask_price = realtime_data.get('ask_price', 0)
                if bid_price > 0 and ask_price > 0:
                    spread_rate = (ask_price - bid_price) / bid_price * 100
                    max_spread = strategy_config.get('max_spread_threshold', 5.0)  # 5%
                    if spread_rate > max_spread:
                        logger.debug(f"유동성 부족 제외: {stock.stock_code} (스프레드: {spread_rate:.1f}%)")
                        return False
            
            return True
            
        except Exception as e:
            logger.debug(f"기본 적격성 체크 실패 {stock.stock_code}: {e}")
            return False
    
    @staticmethod
    def _calculate_momentum_score(stock: Stock, realtime_data: Dict, market_phase: str, 
                                 performance_config: Dict) -> int:
        """🚀 모멘텀 점수 계산 (0~40점)"""
        try:
            momentum_score = 0
            
            # 가격 변화율 계산
            current_price = realtime_data.get('current_price', stock.close_price)
            price_change_rate = realtime_data.get('price_change_rate', 0)
            if price_change_rate == 0 and stock.reference_data.yesterday_close > 0:
                price_change_rate = (current_price - stock.reference_data.yesterday_close) / stock.reference_data.yesterday_close * 100
            
            volume_spike_ratio = realtime_data.get('volume_spike_ratio', 1.0)
            contract_strength = getattr(stock.realtime_data, 'contract_strength', 100.0)
            
            # 1. 가격 상승 모멘텀 (0~15점)
            if price_change_rate >= 3.0:  # 3% 이상
                momentum_score += 15
            elif price_change_rate >= 2.0:  # 2% 이상
                momentum_score += 12
            elif price_change_rate >= 1.0:  # 1% 이상
                momentum_score += 8
            elif price_change_rate >= 0.5:  # 0.5% 이상
                momentum_score += 5
            elif price_change_rate >= 0:  # 상승
                momentum_score += 2
            
            # 2. 거래량 모멘텀 (0~15점)
            if volume_spike_ratio >= 5.0:  # 5배 이상
                momentum_score += 15
            elif volume_spike_ratio >= 3.0:  # 3배 이상
                momentum_score += 12
            elif volume_spike_ratio >= 2.0:  # 2배 이상
                momentum_score += 8
            elif volume_spike_ratio >= 1.5:  # 1.5배 이상
                momentum_score += 5
            elif volume_spike_ratio >= 1.2:  # 1.2배 이상
                momentum_score += 2
            
            # 3. 체결강도 모멘텀 (0~10점)
            if contract_strength >= 150:  # 매우 강함
                momentum_score += 10
            elif contract_strength >= 130:  # 강함
                momentum_score += 8
            elif contract_strength >= 110:  # 양호
                momentum_score += 5
            elif contract_strength >= 100:  # 보통
                momentum_score += 3
            elif contract_strength >= 90:  # 약함
                momentum_score += 1
            
            # 시장 단계별 보정
            if market_phase == 'opening':
                momentum_score = int(momentum_score * 1.1)
            elif market_phase == 'pre_close':
                momentum_score = int(momentum_score * 0.9)
            
            return min(40, momentum_score)
            
        except Exception as e:
            logger.debug(f"모멘텀 점수 계산 실패 {stock.stock_code}: {e}")
            return 0
    
    @staticmethod
    def _calculate_orderbook_strength_score(stock: Stock) -> int:
        """🏛️ 호가잔량 강도 분석 (0~10점) - 신규 추가"""
        try:
            total_ask_qty = getattr(stock.realtime_data, 'total_ask_qty', 0)
            total_bid_qty = getattr(stock.realtime_data, 'total_bid_qty', 0)
            
            if total_ask_qty <= 0 or total_bid_qty <= 0:
                return 0  # 🔥 데이터 없으면 0점 (시스템 완전성 가정)
            
            # 매수/매도 호가 불균형 비율
            bid_ask_ratio = total_bid_qty / total_ask_qty
            
            # 매수 호가가 많을수록 높은 점수 (매수 압력)
            if bid_ask_ratio >= 2.0:  # 매수호가가 2배 이상
                return 10
            elif bid_ask_ratio >= 1.5:  # 1.5배 이상
                return 8
            elif bid_ask_ratio >= 1.2:  # 1.2배 이상
                return 6
            elif bid_ask_ratio >= 1.0:  # 균형
                return 4
            elif bid_ask_ratio >= 0.8:  # 약간 매도 우세
                return 2
            else:  # 매도 호가 압도적
                return 0
                
        except Exception as e:
            logger.debug(f"호가잔량 분석 실패 {stock.stock_code}: {e}")
            return 0  # 🔥 실패시 0점 (시스템 완전성 가정)
    
    @staticmethod
    def _calculate_contract_balance_score(stock: Stock) -> int:
        """⚖️ 체결 불균형 분석 (0~8점) - 신규 추가"""
        try:
            buy_contract_count = getattr(stock.realtime_data, 'buy_contract_count', 0)
            sell_contract_count = getattr(stock.realtime_data, 'sell_contract_count', 0)
            
            if buy_contract_count + sell_contract_count <= 0:
                return 0  # 🔥 데이터 없으면 0점 (시스템 완전성 가정)
            
            # 매수체결 비율 계산
            total_contracts = buy_contract_count + sell_contract_count
            buy_contract_ratio = buy_contract_count / total_contracts * 100
            
            # 매수 체결이 많을수록 높은 점수
            if buy_contract_ratio >= 70:  # 70% 이상 매수체결
                return 8
            elif buy_contract_ratio >= 60:  # 60% 이상
                return 6
            elif buy_contract_ratio >= 55:  # 55% 이상 (약간 매수 우세)
                return 4
            elif buy_contract_ratio >= 45:  # 45~55% (균형)
                return 2
            else:  # 45% 미만 (매도 우세)
                return 0
                
        except Exception as e:
            logger.debug(f"체결 불균형 분석 실패 {stock.stock_code}: {e}")
            return 0  # 🔥 실패시 0점 (시스템 완전성 가정)
    
    @staticmethod
    def _calculate_volume_quality_score(stock: Stock, market_phase: str) -> int:
        """📊 거래량 품질 분석 (0~7점) - 신규 추가"""
        try:
            volume_turnover_rate = getattr(stock.realtime_data, 'volume_turnover_rate', 0.0)
            prev_same_time_volume_rate = getattr(stock.realtime_data, 'prev_same_time_volume_rate', 0.0)
            
            # 🔥 기본 데이터 체크 (시스템 완전성 가정)
            if volume_turnover_rate <= 0:
                return 0  # 거래량 회전율 데이터 없으면 0점
            
            score = 0
            
            # 1. 거래량 회전율 품질 (0~4점)
            if volume_turnover_rate >= 2.0:  # 2% 이상 (매우 활발)
                score += 4
            elif volume_turnover_rate >= 1.0:  # 1% 이상 (활발)
                score += 3
            elif volume_turnover_rate >= 0.5:  # 0.5% 이상 (보통)
                score += 2
            elif volume_turnover_rate >= 0.2:  # 0.2% 이상 (약함)
                score += 1
            # 0.2% 미만은 0점
            
            # 2. 전일 동시간 대비 거래량 (0~3점) - 데이터가 있을 때만
            if prev_same_time_volume_rate > 0:
                if prev_same_time_volume_rate >= 200:  # 2배 이상
                    score += 3
                elif prev_same_time_volume_rate >= 150:  # 1.5배 이상
                    score += 2
                elif prev_same_time_volume_rate >= 120:  # 1.2배 이상
                    score += 1
                # 1.2배 미만은 0점
            
            # 시장 단계별 보정 (데이터가 충분할 때만)
            if market_phase == 'opening' and score >= 5:
                score = min(7, score + 1)  # 장 초반 활발한 거래량에 보너스
            
            return min(7, score)
            
        except Exception as e:
            logger.debug(f"거래량 품질 분석 실패 {stock.stock_code}: {e}")
            return 0  # 🔥 실패시 0점 (시스템 완전성 가정)
    
    @staticmethod
    def _calculate_time_sensitivity_score(market_phase: str, stock: Stock) -> int:
        """⏰ 시간 민감성 점수 계산 (0~15점)"""
        try:
            time_score = 0
            current_time = now_kst()
            
            # 1. 시장 단계별 기본 점수 (0~8점)
            if market_phase == 'opening':
                time_score += 6  # 장 초반 적극적
            elif market_phase == 'active':
                time_score += 8  # 활성 시간 최고
            elif market_phase == 'pre_close':
                time_score += 3  # 마감 전 보수적
            elif market_phase == 'closing':
                time_score += 1  # 마감 시간 매우 보수적
            else:
                time_score += 0  # 비활성 시간
            
            # 2. 분 단위 세밀한 타이밍 (0~4점)
            minute = current_time.minute
            if market_phase == 'opening':
                # 장 초반 10분이 골든타임
                if minute <= 10:
                    time_score += 4
                elif minute <= 20:
                    time_score += 2
                elif minute <= 30:
                    time_score += 1
            elif market_phase == 'active':
                # 정시 근처에서 변동성 증가
                if minute in [0, 15, 30, 45]:
                    time_score += 3
                elif minute in list(range(55, 60)) + list(range(0, 5)):
                    time_score += 2
            
            # 3. 거래 활동성 기반 보정 (0~3점)
            realtime_data = stock.realtime_data
            if realtime_data.today_volume > 0:
                realtime_data.update_avg_volume(realtime_data.today_volume)
            
            if realtime_data.avg_volume > 0:
                volume_activity_ratio = realtime_data.today_volume / realtime_data.avg_volume
                if volume_activity_ratio >= 3.0:  # 3배 이상 활발
                    time_score += 3
                elif volume_activity_ratio >= 2.0:  # 2배 이상 활발
                    time_score += 2
                elif volume_activity_ratio >= 1.5:  # 1.5배 이상 활발
                    time_score += 1
            else:
                time_score += 1  # 데이터 없으면 중간 점수
            
            return min(15, time_score)
            
        except Exception as e:
            logger.debug(f"시간 민감성 계산 실패 {stock.stock_code}: {e}")
            return 5
    
    @staticmethod
    def _analyze_divergence_buy_score(stock: Stock, market_phase: str) -> Tuple[int, str]:
        """이격도 기반 매수 점수 계산 (0~25점)"""
        try:
            current_price = stock.realtime_data.current_price
            if current_price > 0 and stock.reference_data.sma_20 > 0:
                sma_20_div = (current_price - stock.reference_data.sma_20) / stock.reference_data.sma_20 * 100
                
                # 당일 고저점 대비 위치 계산
                daily_pos = 50  # 기본값
                if stock.realtime_data.today_high > 0 and stock.realtime_data.today_low > 0:
                    day_range = stock.realtime_data.today_high - stock.realtime_data.today_low
                    if day_range > 0:
                        daily_pos = (current_price - stock.realtime_data.today_low) / day_range * 100
                
                # 기본 이격도 점수 (0~18점)
                base_score = 0
                if sma_20_div <= -5.0:
                    base_score = 18  # 매우 과매도
                elif sma_20_div <= -3.0:
                    base_score = 15  # 과매도
                elif sma_20_div <= -1.5:
                    base_score = 12  # 약간 과매도
                elif sma_20_div <= 0:
                    base_score = 10  # 20일선 아래
                elif sma_20_div <= 1.5:
                    base_score = 7   # 약간 위
                elif sma_20_div <= 3.0:
                    base_score = 5   # 과매수 초기
                elif sma_20_div <= 5.0:
                    base_score = 2   # 과매수
                else:
                    base_score = 0   # 심한 과매수
                
                # 일봉 위치 보정 (±5점)
                position_bonus = 0
                if daily_pos <= 15:
                    position_bonus = 5   # 저점 근처
                elif daily_pos <= 30:
                    position_bonus = 3   # 저점 영역
                elif daily_pos <= 50:
                    position_bonus = 1   # 중간 영역
                elif daily_pos >= 85:
                    position_bonus = -3  # 고점 근처
                elif daily_pos >= 70:
                    position_bonus = -1  # 고점 영역
                
                # 시장 단계별 조정 (±2점)
                phase_adjustment = 0
                if market_phase == 'opening':
                    if sma_20_div <= -2.0:
                        phase_adjustment = 2
                elif market_phase == 'pre_close':
                    if sma_20_div >= 2.0:
                        phase_adjustment = -2
                
                final_score = max(0, min(25, base_score + position_bonus + phase_adjustment))
                
                # 상세 정보 생성
                if sma_20_div <= -3.0:
                    trend_desc = "과매도우수"
                elif sma_20_div <= 0:
                    trend_desc = "과매도양호"
                elif sma_20_div <= 3.0:
                    trend_desc = "과매수주의"
                else:
                    trend_desc = "과매수위험"
                
                if daily_pos <= 30:
                    pos_desc = "저점권"
                elif daily_pos >= 70:
                    pos_desc = "고점권"
                else:
                    pos_desc = "중간권"
                
                info = f"{trend_desc}({sma_20_div:.1f}%), {pos_desc}({daily_pos:.0f}%)"
                return final_score, info
            else:
                return 12, "데이터부족"
                
        except Exception as e:
            logger.debug(f"이격도 점수 계산 실패 {stock.stock_code}: {e}")
            return 12, "계산실패"
    
    @staticmethod
    def _get_min_momentum_score(market_phase: str, performance_config: Dict) -> int:
        """시장 단계별 최소 모멘텀 점수 반환"""
        if market_phase == 'opening':
            return performance_config.get('min_momentum_opening', 20)
        elif market_phase == 'pre_close':
            return performance_config.get('min_momentum_preclose', 25)
        else:
            return performance_config.get('min_momentum_normal', 15)
    
    @staticmethod
    def _get_market_phase_thresholds(market_phase: str, strategy_config: Dict, performance_config: Dict) -> Dict:
        """시장 단계별 임계값 반환"""
        buy_ratio_threshold = performance_config.get('buy_ratio_threshold', 60.0)
        
        if market_phase == 'opening':
            return {
                'buy_ratio_min': buy_ratio_threshold * strategy_config.get('opening_buy_ratio_multiplier', 1.1),
                'min_pattern_score': strategy_config.get('opening_pattern_score_threshold', 75.0),
                'required_total_score': performance_config.get('buy_score_opening_threshold', 70)
            }
        elif market_phase == 'pre_close':
            return {
                'buy_ratio_min': buy_ratio_threshold * strategy_config.get('preclose_buy_ratio_multiplier', 1.2),
                'min_pattern_score': strategy_config.get('opening_pattern_score_threshold', 75.0),
                'required_total_score': performance_config.get('buy_score_preclose_threshold', 75)
            }
        else:
            return {
                'buy_ratio_min': buy_ratio_threshold,
                'min_pattern_score': strategy_config.get('normal_pattern_score_threshold', 70.0),
                'required_total_score': performance_config.get('buy_score_normal_threshold', 60)
            } 