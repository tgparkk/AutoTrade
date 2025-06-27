#!/usr/bin/env python3
"""
매매 조건 분석 및 포지션 사이징을 담당하는 TradingConditionAnalyzer 클래스

주요 기능:
- 매수/매도 조건 분석 (위임)
- 포지션 사이징 (매수량 계산)
- 매도 조건 성과 분석
- 시장 단계별 조건 조정
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime
from models.stock import Stock, StockStatus
from utils.korean_time import now_kst
from utils.logger import setup_logger
from utils import get_trading_config_loader

logger = setup_logger(__name__)


class TradingConditionAnalyzer:
    """매매 조건 분석 및 포지션 사이징 전담 클래스"""
    
    def __init__(self, stock_manager, trade_executor):
        """TradingConditionAnalyzer 초기화
        
        Args:
            stock_manager: 종목 관리자 인스턴스
            trade_executor: 매매 실행자 인스턴스
        """
        self.stock_manager = stock_manager
        self.trade_executor = trade_executor
        
        # 설정 로드
        self.config_loader = get_trading_config_loader()
        self.strategy_config = self.config_loader.load_trading_strategy_config()
        self.performance_config = self.config_loader.load_performance_config()  # 🆕 성능 설정 추가
        self.risk_config = self.config_loader.load_risk_management_config()
        
        logger.info("TradingConditionAnalyzer 초기화 완료")
    
    def get_market_phase(self) -> str:
        """현재 시장 단계 확인 (정확한 시장 시간 기준: 09:00~15:30, 테스트 모드 고려)
        
        Returns:
            시장 단계 ('opening', 'active', 'lunch', 'pre_close', 'closing', 'closed')
        """
        from datetime import time as dt_time
        
        # 🧪 테스트 모드에서는 시간과 관계없이 활성 거래 시간으로 처리
        test_mode = self.strategy_config.get('test_mode', True)
        if test_mode:
            current_hour = now_kst().hour
            # 테스트 모드에서도 시간대별로 다른 단계 반환 (더 현실적인 테스트)
            if 9 <= current_hour < 10:
                return 'opening'
            elif 10 <= current_hour < 15:
                return 'active'
            else:
                return 'active'  # 테스트 모드에서는 기본적으로 활성 시간
        
        current_time = now_kst().time()
        current_weekday = now_kst().weekday()
        
        # 주말 체크 (토: 5, 일: 6)
        if current_weekday >= 5:
            return 'closed'
        
        # 🔥 정확한 시장 시간 기준 (09:00~15:30)
        market_open = dt_time(9, 0)    # 09:00
        market_close = dt_time(15, 30) # 15:30
        
        # 시장 마감 후
        if current_time > market_close:
            return 'closed'
        
        # 시장 개장 전
        if current_time < market_open:
            return 'closed'
        
        # 시장 시간 내 단계별 구분
        if current_time <= dt_time(9, 30):
            return 'opening'        # 09:00~09:30 장 초반
        elif current_time <= dt_time(12, 0):
            return 'active'         # 09:30~12:00 활성 거래
        elif current_time <= dt_time(13, 0):
            return 'lunch'          # 12:00~13:00 점심시간
        elif current_time <= dt_time(14, 50):
            return 'active'         # 13:00~14:50 활성 거래
        elif current_time <= dt_time(15, 0):
            return 'pre_close'      # 14:50~15:00 마감 전
        else:
            return 'closing'        # 15:00~15:30 마감 시간
    
    def analyze_buy_conditions(self, stock: Stock, realtime_data: Dict, 
                              market_phase: Optional[str] = None) -> bool:
        """매수 조건 분석 (TradingConditionAnalyzer 위임)
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            market_phase: 시장 단계 (옵션, None이면 자동 계산)
            
        Returns:
            매수 조건 충족 여부
        """
        try:
            # 시장 단계 결정
            if market_phase is None:
                market_phase = self.get_market_phase()
            
            # 0️⃣ 선행 필터: 호가/체결강도/매수비율 기반 빠른 거르기
            if not self._pre_buy_filters(stock, realtime_data):
                return False
            
            # BuyConditionAnalyzer에 위임 (Static 메서드 사용)
            from .buy_condition_analyzer import BuyConditionAnalyzer
            
            return BuyConditionAnalyzer.analyze_buy_conditions(
                stock=stock,
                realtime_data=realtime_data,
                market_phase=market_phase,
                strategy_config=self.strategy_config,
                performance_config=self.performance_config
            )
            
        except Exception as e:
            logger.error(f"매수 조건 분석 오류 {stock.stock_code}: {e}")
            return False
    
    def analyze_sell_conditions(self, stock: Stock, realtime_data: Dict,
                               market_phase: Optional[str] = None) -> Optional[str]:
        """매도 조건 분석 (SellConditionAnalyzer 위임)
        
        Args:
            stock: 주식 객체
            realtime_data: 실시간 데이터
            market_phase: 시장 단계 (옵션, None이면 자동 계산)
            
        Returns:
            매도 사유 또는 None
        """
        try:
            # 시장 단계 결정
            if market_phase is None:
                market_phase = self.get_market_phase()
            
            # SellConditionAnalyzer에 위임 (Static 메서드 사용)
            from .sell_condition_analyzer import SellConditionAnalyzer
            
            return SellConditionAnalyzer.analyze_sell_conditions(
                stock=stock,
                realtime_data=realtime_data,
                market_phase=market_phase,
                strategy_config=self.strategy_config,
                risk_config=self.risk_config,
                performance_config=self.performance_config
            )
            
        except Exception as e:
            logger.error(f"매도 조건 분석 오류 {stock.stock_code}: {e}")
            return None
    
    def calculate_buy_quantity(self, stock: Stock) -> int:
        """매수량 계산 (설정 기반 개선 버전)
        
        Args:
            stock: 주식 객체
            
        Returns:
            매수량
        """
        try:
            # 🔥 설정에서 기본 투자 금액 로드
            base_amount = self.risk_config.get('base_investment_amount', 1000000)
            use_account_ratio = self.risk_config.get('use_account_ratio', False)
            
            # 계좌 잔고 기반 비율 사용 여부
            if use_account_ratio:
                from api.kis_market_api import get_account_balance
                account_balance = get_account_balance()
                
                if account_balance and isinstance(account_balance, dict):
                    # 총 계좌 자산 = 보유주식 평가액 + 매수가능금액
                    stock_value = account_balance.get('total_value', 0)  # 보유주식 평가액
                    available_amount = account_balance.get('available_amount', 0)  # 매수가능금액
                    total_balance = stock_value + available_amount  # 총 계좌 자산
                    
                    if total_balance > 0:
                        position_ratio = self.risk_config.get('position_size_ratio', 0.1)
                        base_amount = total_balance * position_ratio
                        
                        # 매수가능금액 체크 (안전장치)
                        if base_amount > available_amount:
                            logger.warning(f"계산된 투자금액({base_amount:,}원)이 매수가능금액({available_amount:,}원)을 초과 - 매수가능금액으로 제한")
                            base_amount = available_amount
            
            # 시장 단계별 투자 금액 조정 (설정 기반)
            market_phase = self.get_market_phase()
            
            if market_phase == 'opening':
                # 장 초반 비율 적용
                reduction_ratio = self.risk_config.get('opening_reduction_ratio', 0.5)
                investment_amount = base_amount * reduction_ratio
                logger.debug(f"장 초반 투자금액 조정: {base_amount:,}원 × {reduction_ratio} = {investment_amount:,}원")
            elif market_phase == 'pre_close':
                # 마감 전 비율 적용
                reduction_ratio = self.risk_config.get('preclose_reduction_ratio', 0.3)
                investment_amount = base_amount * reduction_ratio
                logger.debug(f"마감 전 투자금액 조정: {base_amount:,}원 × {reduction_ratio} = {investment_amount:,}원")
            else:
                # 일반 시간대는 100% 투자
                investment_amount = base_amount
                logger.debug(f"일반시간 투자금액: {investment_amount:,}원")
            
            # 포지션 크기에 따른 추가 조정 (설정 기반)
            current_positions = len(self.stock_manager.get_stocks_by_status(StockStatus.BOUGHT))
            max_positions = self.risk_config.get('max_positions', 5)
            
            if current_positions >= max_positions * 0.8:  # 80% 이상 차면 보수적
                conservative_ratio = self.risk_config.get('conservative_ratio', 0.7)
                investment_amount *= conservative_ratio
                logger.debug(f"보수적 조정: × {conservative_ratio} = {investment_amount:,}원 (포지션: {current_positions}/{max_positions})")
            
            # 최대 포지션 크기 제한 적용
            max_position_size = self.risk_config.get('max_position_size', 1000000)
            if investment_amount > max_position_size:
                investment_amount = max_position_size
                logger.debug(f"최대 포지션 크기 제한 적용: {max_position_size:,}원")
            
            # 매수량 계산
            current_price = stock.realtime_data.current_price if stock.realtime_data.current_price > 0 else stock.close_price
            quantity = int(investment_amount / current_price)
            
            # 최소 1주 보장
            final_quantity = max(quantity, 1)
            final_amount = final_quantity * current_price
            
            logger.info(f"💰 매수량 계산 완료: {stock.stock_code}({stock.stock_name}) "
                       f"{final_quantity}주 @{current_price:,}원 = {final_amount:,}원 "
                       f"(시장단계: {market_phase}, 기준금액: {base_amount:,}원)")
            
            return final_quantity
            
        except Exception as e:
            logger.error(f"매수량 계산 오류 {stock.stock_code}: {e}")
            return 0
    
    def get_sell_condition_analysis(self) -> Dict:
        """매도 조건 분석 성과 조회
        
        Returns:
            매도 조건별 성과 분석 딕셔너리
        """
        try:
            # TradeExecutor의 최근 거래 기록에서 매도 사유별 성과 분석
            recent_trades = self.trade_executor.get_recent_trades_summary(20)
            
            # 매도 사유별 통계
            sell_reason_stats = {}
            total_trades = 0
            total_pnl = 0
            
            for trade in recent_trades['trades']:
                reason = trade['sell_reason']
                if reason not in sell_reason_stats:
                    sell_reason_stats[reason] = {
                        'count': 0,
                        'win_count': 0,
                        'total_pnl': 0.0,
                        'avg_pnl': 0.0,
                        'win_rate': 0.0,
                        'avg_holding_minutes': 0.0
                    }
                
                stats = sell_reason_stats[reason]
                stats['count'] += 1
                if trade['is_winning']:
                    stats['win_count'] += 1
                stats['total_pnl'] += trade['realized_pnl']
                stats['avg_holding_minutes'] += trade['holding_minutes']
                
                total_trades += 1
                total_pnl += trade['realized_pnl']
            
            # 각 사유별 평균값 계산
            for reason in sell_reason_stats:
                stats = sell_reason_stats[reason]
                if stats['count'] > 0:
                    stats['win_rate'] = (stats['win_count'] / stats['count']) * 100
                    stats['avg_pnl'] = stats['total_pnl'] / stats['count']
                    stats['avg_holding_minutes'] = stats['avg_holding_minutes'] / stats['count']
            
            # 매도 조건 효과성 순위
            effectiveness_ranking = sorted(
                sell_reason_stats.items(),
                key=lambda x: (x[1]['win_rate'], x[1]['avg_pnl']),
                reverse=True
            )
            
            return {
                'sell_reason_stats': sell_reason_stats,
                'effectiveness_ranking': effectiveness_ranking,
                'overall_stats': {
                    'total_trades': total_trades,
                    'total_pnl': total_pnl,
                    'avg_pnl': total_pnl / total_trades if total_trades > 0 else 0.0
                },
                'recommendations': self._generate_sell_condition_recommendations(sell_reason_stats)
            }
            
        except Exception as e:
            logger.error(f"매도 조건 분석 성과 조회 오류: {e}")
            return {}
    
    def _generate_sell_condition_recommendations(self, sell_reason_stats: Dict) -> List[str]:
        """매도 조건 개선 권장사항 생성
        
        Args:
            sell_reason_stats: 매도 사유별 통계
            
        Returns:
            권장사항 리스트
        """
        recommendations = []
        
        try:
            for reason, stats in sell_reason_stats.items():
                if stats['count'] < 3:  # 샘플이 너무 적으면 건너뛰기
                    continue
                
                # 승률 기반 권장사항
                if stats['win_rate'] < 30:
                    recommendations.append(f"❌ '{reason}' 매도 조건의 승률이 낮습니다 ({stats['win_rate']:.1f}%) - 조건 재검토 필요")
                elif stats['win_rate'] > 70:
                    recommendations.append(f"✅ '{reason}' 매도 조건이 효과적입니다 ({stats['win_rate']:.1f}%) - 유지 권장")
                
                # 평균 손익 기반 권장사항
                if stats['avg_pnl'] < -10000:
                    recommendations.append(f"🔻 '{reason}' 매도시 평균 손실이 큽니다 ({stats['avg_pnl']:,.0f}원) - 더 빠른 매도 검토")
                elif stats['avg_pnl'] > 5000:
                    recommendations.append(f"🔺 '{reason}' 매도시 평균 수익이 좋습니다 ({stats['avg_pnl']:,.0f}원) - 조건 확대 검토")
                
                # 보유 시간 기반 권장사항
                if stats['avg_holding_minutes'] > 240:  # 4시간 초과
                    recommendations.append(f"⏰ '{reason}' 매도시 보유 시간이 깁니다 ({stats['avg_holding_minutes']:.0f}분) - 더 빠른 매도 검토")
            
            # 전체적인 권장사항
            if len(sell_reason_stats) > 10:
                recommendations.append("📊 매도 사유가 너무 많습니다 - 주요 조건으로 단순화 검토")
            
            # 특정 조건별 권장사항
            if 'stop_loss' in sell_reason_stats:
                stop_loss_stats = sell_reason_stats['stop_loss']
                if stop_loss_stats['count'] > 5 and stop_loss_stats['win_rate'] < 20:
                    recommendations.append("🚨 손절 조건이 너무 늦습니다 - 더 빠른 손절 검토")
            
            if 'take_profit' in sell_reason_stats:
                take_profit_stats = sell_reason_stats['take_profit']
                if take_profit_stats['count'] > 3 and take_profit_stats['avg_pnl'] < 5000:
                    recommendations.append("💰 익절 수익이 작습니다 - 익절 목표 상향 검토")
                    
        except Exception as e:
            logger.error(f"매도 조건 권장사항 생성 오류: {e}")
        
        return recommendations

    # ------------------------------------------------------------------
    # 🆕  선행 매수 필터 (호가 잔량·매수비율·체결강도)
    # ------------------------------------------------------------------
    def _pre_buy_filters(self, stock: Stock, realtime_data: Dict) -> bool:
        """호가/체결 정보 기반 1차 매수 필터링"""
        try:
            cfg = self.performance_config  # 가독성 단축

            # 호가 잔량 (default 0)
            bid_qty = getattr(stock.realtime_data, 'total_bid_qty', 0)
            ask_qty = getattr(stock.realtime_data, 'total_ask_qty', 0)

            if bid_qty > 0 and ask_qty > 0:
                ratio_ba = bid_qty / ask_qty
                min_ba = cfg.get('min_bid_ask_ratio_for_buy', 1.2)
                max_ab = cfg.get('max_ask_bid_ratio_for_buy', 2.5)

                # 매수호가 열세( <1.2 )
                if ratio_ba < min_ba:
                    logger.debug(f"매수호가 열세({ratio_ba*100:.1f}%)로 매수 제외: {stock.stock_code}")
                    return False

                # 매도호가 과다( ask/bid > max_ab )
                ratio_ab = ask_qty / bid_qty
                if ratio_ab >= max_ab:
                    logger.debug(f"매도호가 과다({ratio_ab*100:.1f}%)로 매수 제외: {stock.stock_code}")
                    return False

            # 매수비율 / 체결강도
            buy_ratio = getattr(stock.realtime_data, 'buy_ratio', 50.0)
            min_buy_ratio = cfg.get('min_buy_ratio_for_buy', 40.0)
            if buy_ratio < min_buy_ratio:
                logger.debug(f"매수비율 낮음({buy_ratio:.1f}%)로 매수 제외: {stock.stock_code}")
                return False

            strength = getattr(stock.realtime_data, 'contract_strength', 100.0)
            min_strength = cfg.get('min_contract_strength_for_buy', 110.0)
            if strength < min_strength:
                logger.debug(f"체결강도 약함({strength:.1f})로 매수 제외: {stock.stock_code}")
                return False

            # 일일 등락률 필터 – limit-up 근접 종목 제외
            price_change_rate = getattr(stock.realtime_data, 'price_change_rate', 0.0)
            max_pct = cfg.get('max_price_change_rate_for_buy', 15.0)
            if price_change_rate >= max_pct:
                logger.debug(f"등락률 높음({price_change_rate:.1f}%)로 매수 제외: {stock.stock_code}")
                return False

            # 🆕 유동성 점수 필터
            try:
                liq_score = self.stock_manager.get_liquidity_score(stock.stock_code)
            except AttributeError:
                liq_score = 0.0

            min_liq = cfg.get('min_liquidity_score_for_buy', 3.0)
            if liq_score < min_liq:
                logger.debug(f"유동성 낮음({liq_score:.1f})로 매수 제외: {stock.stock_code}")
                return False

            return True

        except Exception as e:
            logger.error(f"선행 매수 필터 오류 {stock.stock_code}: {e}")
            return True  # 오류 시 필터 통과시켜 길목 차단 방지 