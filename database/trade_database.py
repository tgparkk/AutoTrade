"""
AutoTrade 거래 데이터베이스 관리 클래스

주요 기능:
- 장전/장중 스캔 결과 저장
- 매수/매도 주문 기록
- 일일 거래 요약
- 성과 분석 데이터 제공
"""

import sqlite3
import json
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any, Tuple, Callable
from pathlib import Path
from utils.korean_time import now_kst
from utils.logger import setup_logger

logger = setup_logger(__name__)


class TradeDatabase:
    """거래 데이터베이스 관리 클래스"""
    
    def __init__(self, db_path: str = "data/trading.db"):
        """TradeDatabase 초기화
        
        Args:
            db_path: 데이터베이스 파일 경로
        """
        self.db_path = db_path
        
        # 데이터베이스 디렉토리 생성
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # 데이터베이스 초기화
        self._init_database()
        
        logger.info(f"TradeDatabase 초기화 완료: {db_path}")
    
    def _init_database(self):
        """데이터베이스 테이블 초기화"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 1. Pre-Market Scan 테이블
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS pre_market_scans (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        scan_date DATE NOT NULL,
                        scan_time DATETIME NOT NULL,
                        stock_code VARCHAR(10) NOT NULL,
                        stock_name VARCHAR(100) NOT NULL,
                        
                        selection_score DECIMAL(5,2),
                        selection_criteria TEXT,
                        
                        pattern_score DECIMAL(5,2),
                        pattern_names TEXT,
                        rsi DECIMAL(5,2),
                        macd DECIMAL(8,4),
                        sma_20 DECIMAL(10,2),
                        
                        yesterday_close DECIMAL(10,2),
                        yesterday_volume BIGINT,
                        market_cap BIGINT,
                        
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # 2. Intraday Scan 테이블
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS intraday_scans (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        scan_date DATE NOT NULL,
                        scan_time DATETIME NOT NULL,
                        stock_code VARCHAR(10) NOT NULL,
                        stock_name VARCHAR(100) NOT NULL,
                        
                        selection_score DECIMAL(5,2),
                        selection_criteria TEXT,
                        scan_reason VARCHAR(50),
                        
                        current_price DECIMAL(10,2),
                        volume_spike_ratio DECIMAL(5,2),
                        price_change_rate DECIMAL(5,2),
                        
                        contract_strength DECIMAL(5,2),
                        buy_ratio DECIMAL(5,2),
                        
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # 3. Buy Orders 테이블
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS buy_orders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_date DATE NOT NULL,
                        order_time DATETIME NOT NULL,
                        execution_time DATETIME,
                        
                        stock_code VARCHAR(10) NOT NULL,
                        stock_name VARCHAR(100) NOT NULL,
                        
                        order_id VARCHAR(50),
                        order_orgno VARCHAR(10),
                        order_status VARCHAR(20),
                        
                        order_price DECIMAL(10,2),
                        execution_price DECIMAL(10,2),
                        quantity INTEGER,
                        total_amount DECIMAL(15,2),
                        
                        target_profit_rate DECIMAL(5,2),
                        stop_loss_rate DECIMAL(5,2),
                        
                        selection_source VARCHAR(20),
                        selection_criteria TEXT,
                        
                        market_phase VARCHAR(20),
                        position_size_ratio DECIMAL(5,2),
                        
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # 4. Sell Orders 테이블
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sell_orders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_date DATE NOT NULL,
                        order_time DATETIME NOT NULL,
                        execution_time DATETIME,
                        
                        stock_code VARCHAR(10) NOT NULL,
                        stock_name VARCHAR(100) NOT NULL,
                        
                        buy_order_id INTEGER,
                        
                        order_id VARCHAR(50),
                        order_orgno VARCHAR(10),
                        order_status VARCHAR(20),
                        
                        order_price DECIMAL(10,2),
                        execution_price DECIMAL(10,2),
                        quantity INTEGER,
                        total_amount DECIMAL(15,2),
                        
                        profit_loss DECIMAL(15,2),
                        profit_loss_rate DECIMAL(5,2),
                        holding_minutes INTEGER,
                        
                        sell_reason VARCHAR(50),
                        sell_criteria TEXT,
                        
                        market_phase VARCHAR(20),
                        
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        
                        FOREIGN KEY (buy_order_id) REFERENCES buy_orders(id)
                    )
                """)
                
                # 5. Daily Summary 테이블
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS daily_summaries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trade_date DATE NOT NULL UNIQUE,
                        
                        pre_market_scanned_count INTEGER DEFAULT 0,
                        intraday_scanned_count INTEGER DEFAULT 0,
                        
                        total_buy_orders INTEGER DEFAULT 0,
                        total_sell_orders INTEGER DEFAULT 0,
                        executed_buy_orders INTEGER DEFAULT 0,
                        executed_sell_orders INTEGER DEFAULT 0,
                        
                        total_profit_loss DECIMAL(15,2) DEFAULT 0,
                        win_count INTEGER DEFAULT 0,
                        loss_count INTEGER DEFAULT 0,
                        win_rate DECIMAL(5,2) DEFAULT 0,
                        
                        total_investment DECIMAL(15,2) DEFAULT 0,
                        max_position_count INTEGER DEFAULT 0,
                        avg_holding_minutes DECIMAL(8,2) DEFAULT 0,
                        
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # 6. 일일 성과 메트릭 테이블 (자동 파라미터 튜닝용)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS metrics_daily (
                        trade_date DATE PRIMARY KEY,
                        trades INTEGER,
                        win_rate REAL,
                        total_pnl REAL,
                        avg_pnl REAL,
                        max_drawdown REAL DEFAULT 0,
                        params_json TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # 인덱스 생성
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_pre_market_date ON pre_market_scans(scan_date)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_pre_market_stock ON pre_market_scans(stock_code)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_intraday_date ON intraday_scans(scan_date)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_intraday_stock ON intraday_scans(stock_code)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_buy_orders_date ON buy_orders(order_date)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_buy_orders_stock ON buy_orders(stock_code)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_sell_orders_date ON sell_orders(order_date)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_sell_orders_stock ON sell_orders(stock_code)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_date ON metrics_daily(trade_date)")
                
                conn.commit()
                logger.info("데이터베이스 테이블 초기화 완료")
                
        except Exception as e:
            logger.error(f"데이터베이스 초기화 실패: {e}")
            raise
    
    # === Pre-Market Scan 관련 메서드들 ===
    
    def save_pre_market_scan(self, stock_data: Dict[str, Any]) -> int:
        """장전 스캔 결과 저장
        
        Args:
            stock_data: 종목 스캔 데이터
            
        Returns:
            저장된 레코드의 ID
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                current_time = now_kst()
                
                cursor.execute("""
                    INSERT INTO pre_market_scans (
                        scan_date, scan_time, stock_code, stock_name,
                        selection_score, selection_criteria,
                        pattern_score, pattern_names, rsi, macd, sma_20,
                        yesterday_close, yesterday_volume, market_cap
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    current_time.date(),
                    current_time,
                    stock_data.get('stock_code'),
                    stock_data.get('stock_name'),
                    stock_data.get('selection_score'),
                    json.dumps(stock_data.get('selection_criteria', {}), ensure_ascii=False),
                    stock_data.get('pattern_score'),
                    json.dumps(stock_data.get('pattern_names', []), ensure_ascii=False),
                    stock_data.get('rsi'),
                    stock_data.get('macd'),
                    stock_data.get('sma_20'),
                    stock_data.get('yesterday_close'),
                    stock_data.get('yesterday_volume'),
                    stock_data.get('market_cap')
                ))
                
                record_id = cursor.lastrowid or 0
                conn.commit()
                
                logger.debug(f"장전 스캔 결과 저장: {stock_data.get('stock_code')} (ID: {record_id})")
                return record_id
                
        except Exception as e:
            logger.error(f"장전 스캔 결과 저장 실패: {e}")
            return 0
    
    def get_pre_market_scans(self, scan_date: Optional[date] = None) -> List[Dict[str, Any]]:
        """장전 스캔 결과 조회
        
        Args:
            scan_date: 조회할 날짜 (None이면 오늘)
            
        Returns:
            스캔 결과 리스트
        """
        try:
            if scan_date is None:
                scan_date = now_kst().date()
            
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM pre_market_scans 
                    WHERE scan_date = ? 
                    ORDER BY selection_score DESC
                """, (scan_date,))
                
                results = []
                for row in cursor.fetchall():
                    result = dict(row)
                    # JSON 필드 파싱
                    if result['selection_criteria']:
                        result['selection_criteria'] = json.loads(result['selection_criteria'])
                    if result['pattern_names']:
                        result['pattern_names'] = json.loads(result['pattern_names'])
                    results.append(result)
                
                return results
                
        except Exception as e:
            logger.error(f"장전 스캔 결과 조회 실패: {e}")
            return []
    
    # === Intraday Scan 관련 메서드들 ===
    
    def save_intraday_scan(self, stock_data: Dict[str, Any]) -> int:
        """장중 스캔 결과 저장"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                current_time = now_kst()
                
                cursor.execute("""
                    INSERT INTO intraday_scans (
                        scan_date, scan_time, stock_code, stock_name,
                        selection_score, selection_criteria, scan_reason,
                        current_price, volume_spike_ratio, price_change_rate,
                        contract_strength, buy_ratio
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    current_time.date(),
                    current_time,
                    stock_data.get('stock_code'),
                    stock_data.get('stock_name'),
                    stock_data.get('selection_score'),
                    json.dumps(stock_data.get('selection_criteria', {}), ensure_ascii=False),
                    stock_data.get('scan_reason'),
                    stock_data.get('current_price'),
                    stock_data.get('volume_spike_ratio'),
                    stock_data.get('price_change_rate'),
                    stock_data.get('contract_strength'),
                    stock_data.get('buy_ratio')
                ))
                
                record_id = cursor.lastrowid or 0
                conn.commit()
                
                logger.debug(f"장중 스캔 결과 저장: {stock_data.get('stock_code')} (ID: {record_id})")
                return record_id
                
        except Exception as e:
            logger.error(f"장중 스캔 결과 저장 실패: {e}")
            return 0
    
    def save_intraday_scan_result(self, stock_code: str, stock_name: Optional[str], 
                                  score: float, reasons: str) -> int:
        """장중 스캔 결과를 API 호출과 함께 저장
        
        Args:
            stock_code: 종목코드
            stock_name: 종목명 (Optional)
            score: 선정 점수
            reasons: 선정 사유
            
        Returns:
            저장된 레코드 ID (실패시 0)
        """
        try:
            # KIS API를 통한 현재가 정보 조회
            try:
                from api.kis_market_api import get_inquire_price
                price_data = get_inquire_price(div_code="J", itm_no=stock_code)
            except ImportError:
                logger.warning("KIS API 모듈을 찾을 수 없음 - 기본값으로 저장")
                price_data = None
            
            # 기본 스캔 데이터 준비
            scan_data = {
                'stock_code': stock_code,
                'stock_name': stock_name if stock_name else f"종목{stock_code}",
                'selection_score': score,
                'selection_criteria': reasons,
                'scan_reason': 'intraday_scan',
                'current_price': 0,
                'volume_spike_ratio': 1.0,
                'price_change_rate': 0.0,
                'contract_strength': 100.0,
                'buy_ratio': 50.0
            }
            
            # KIS API 데이터가 있으면 추가 정보 수집
            if price_data is not None and not price_data.empty:
                row = price_data.iloc[0]
                scan_data.update({
                    'current_price': float(row.get('stck_prpr', 0)),  # 현재가
                    'price_change_rate': float(row.get('prdy_ctrt', 0.0)),  # 전일대비율
                    'volume_spike_ratio': 1.0  # 추후 계산 로직 추가 가능
                })
            
            # 데이터베이스에 저장
            result = self.save_intraday_scan(scan_data)
            return result if result is not None else 0
            
        except Exception as e:
            logger.error(f"❌ 장중 스캔 결과 처리 및 저장 오류 {stock_code}: {e}")
            return 0
    
    # === Buy Orders 관련 메서드들 ===
    
    def save_buy_order(self, order_data: Dict[str, Any]) -> int:
        """매수 주문 저장"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                current_time = now_kst()
                
                cursor.execute("""
                    INSERT INTO buy_orders (
                        order_date, order_time, execution_time,
                        stock_code, stock_name,
                        order_id, order_orgno, order_status,
                        order_price, execution_price, quantity, total_amount,
                        target_profit_rate, stop_loss_rate,
                        selection_source, selection_criteria,
                        market_phase, position_size_ratio
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    current_time.date(),
                    order_data.get('order_time', current_time),
                    order_data.get('execution_time'),
                    order_data.get('stock_code'),
                    order_data.get('stock_name'),
                    order_data.get('order_id'),
                    order_data.get('order_orgno'),
                    order_data.get('order_status', 'ordered'),
                    order_data.get('order_price'),
                    order_data.get('execution_price'),
                    order_data.get('quantity'),
                    order_data.get('total_amount'),
                    order_data.get('target_profit_rate'),
                    order_data.get('stop_loss_rate'),
                    order_data.get('selection_source'),
                    json.dumps(order_data.get('selection_criteria', {}), ensure_ascii=False),
                    order_data.get('market_phase'),
                    order_data.get('position_size_ratio')
                ))
                
                record_id = cursor.lastrowid or 0
                conn.commit()
                
                logger.info(f"매수 주문 저장: {order_data.get('stock_code')} (ID: {record_id})")
                return record_id
                
        except Exception as e:
            logger.error(f"매수 주문 저장 실패: {e}")
            return 0
    
    def update_buy_order_execution(self, order_id: str, execution_data: Dict[str, Any]) -> bool:
        """매수 주문 체결 정보 업데이트"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    UPDATE buy_orders 
                    SET execution_time = ?, execution_price = ?, order_status = 'executed'
                    WHERE order_id = ?
                """, (
                    execution_data.get('execution_time', now_kst()),
                    execution_data.get('execution_price'),
                    order_id
                ))
                
                conn.commit()
                logger.info(f"매수 주문 체결 업데이트: {order_id}")
                return True
                
        except Exception as e:
            logger.error(f"매수 주문 체결 업데이트 실패: {e}")
            return False
    
    # === Sell Orders 관련 메서드들 ===
    
    def save_sell_order(self, order_data: Dict[str, Any]) -> int:
        """매도 주문 저장"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                current_time = now_kst()
                
                cursor.execute("""
                    INSERT INTO sell_orders (
                        order_date, order_time, execution_time,
                        stock_code, stock_name, buy_order_id,
                        order_id, order_orgno, order_status,
                        order_price, execution_price, quantity, total_amount,
                        profit_loss, profit_loss_rate, holding_minutes,
                        sell_reason, sell_criteria, market_phase
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    current_time.date(),
                    order_data.get('order_time', current_time),
                    order_data.get('execution_time'),
                    order_data.get('stock_code'),
                    order_data.get('stock_name'),
                    order_data.get('buy_order_id'),
                    order_data.get('order_id'),
                    order_data.get('order_orgno'),
                    order_data.get('order_status', 'ordered'),
                    order_data.get('order_price'),
                    order_data.get('execution_price'),
                    order_data.get('quantity'),
                    order_data.get('total_amount'),
                    order_data.get('profit_loss'),
                    order_data.get('profit_loss_rate'),
                    order_data.get('holding_minutes'),
                    order_data.get('sell_reason'),
                    json.dumps(order_data.get('sell_criteria', {}), ensure_ascii=False),
                    order_data.get('market_phase')
                ))
                
                record_id = cursor.lastrowid or 0
                conn.commit()
                
                logger.info(f"매도 주문 저장: {order_data.get('stock_code')} (ID: {record_id})")
                return record_id
                
        except Exception as e:
            logger.error(f"매도 주문 저장 실패: {e}")
            return 0
    
    # === 체결 정보 저장 메서드들 (StockManager에서 이관) ===
    
    def save_buy_execution_to_db(self, stock_code: str, exec_price: float, exec_qty: int, 
                                stock_metadata: Dict, trade_info: Dict, 
                                get_current_market_phase_func: Callable[[], str]) -> int:
        """매수 체결 정보를 데이터베이스에 저장 (StockManager에서 이관)
        
        Args:
            stock_code: 종목코드
            exec_price: 체결가격
            exec_qty: 체결수량
            stock_metadata: 종목 메타데이터
            trade_info: 거래 정보
            get_current_market_phase_func: 현재 시장 단계 조회 함수
            
        Returns:
            저장된 레코드 ID
        """
        try:
            from utils.korean_time import now_kst
            
            # 손절/익절 설정 조회
            stop_loss_rate = 0
            take_profit_rate = 0
            if trade_info.get('stop_loss_price') and exec_price > 0:
                stop_loss_rate = (trade_info['stop_loss_price'] - exec_price) / exec_price * 100
            if trade_info.get('target_price') and exec_price > 0:
                take_profit_rate = (trade_info['target_price'] - exec_price) / exec_price * 100
            
            order_data = {
                'stock_code': stock_code,
                'stock_name': stock_metadata.get('stock_name', ''),
                'order_time': trade_info.get('buy_order_time') or now_kst(),
                'execution_time': now_kst(),  # 실제 체결 시점
                'order_id': trade_info.get('buy_order_id', ''),
                'order_orgno': trade_info.get('buy_order_orgno', ''),
                'order_status': 'executed',  # 체결 완료
                'order_price': exec_price,
                'execution_price': exec_price,
                'quantity': exec_qty,
                'total_amount': exec_price * exec_qty,
                'target_profit_rate': take_profit_rate,
                'stop_loss_rate': stop_loss_rate,
                'selection_source': stock_metadata.get('selection_source', 'unknown'),
                'selection_criteria': stock_metadata.get('selection_criteria', {}),
                'market_phase': get_current_market_phase_func(),
                'position_size_ratio': 0.0  # 계산 필요시 추가
            }
            
            db_id = self.save_buy_order(order_data)
            if db_id > 0:
                logger.info(f"📊 매수 체결 DB 저장 완료: {stock_code} (ID: {db_id})")
            else:
                logger.warning(f"⚠️ 매수 체결 DB 저장 실패: {stock_code}")
            
            return db_id
            
        except Exception as e:
            logger.error(f"❌ 매수 체결 DB 저장 오류 {stock_code}: {e}")
            return 0
    
    def save_sell_execution_to_db(self, stock_code: str, exec_price: float, exec_qty: int, 
                                 realized_pnl: float, realized_pnl_rate: float,
                                 stock_metadata: Dict, trade_info: Dict,
                                 get_current_market_phase_func: Callable[[], str]) -> int:
        """매도 체결 정보를 데이터베이스에 저장 (StockManager에서 이관)
        
        Args:
            stock_code: 종목코드
            exec_price: 체결가격
            exec_qty: 체결수량
            realized_pnl: 실현손익
            realized_pnl_rate: 실현손익률
            stock_metadata: 종목 메타데이터
            trade_info: 거래 정보
            get_current_market_phase_func: 현재 시장 단계 조회 함수
            
        Returns:
            저장된 레코드 ID
        """
        try:
            from utils.korean_time import now_kst
            
            # 보유 시간 계산
            holding_minutes = 0
            buy_time = trade_info.get('execution_time') or trade_info.get('buy_order_time')
            if buy_time:
                holding_minutes = (now_kst() - buy_time).total_seconds() / 60
            
            order_data = {
                'stock_code': stock_code,
                'stock_name': stock_metadata.get('stock_name', ''),
                'order_time': trade_info.get('sell_order_time', now_kst()),
                'execution_time': now_kst(),  # 실제 체결 시점
                'order_id': trade_info.get('sell_order_id', ''),
                'order_orgno': trade_info.get('sell_order_orgno', ''),
                'order_status': 'executed',  # 체결 완료
                'order_price': exec_price,
                'execution_price': exec_price,
                'quantity': exec_qty,
                'total_amount': exec_price * exec_qty,
                'profit_loss': realized_pnl,
                'profit_loss_rate': realized_pnl_rate,
                'holding_minutes': holding_minutes,
                'sell_reason': trade_info.get('sell_reason', 'execution_notice'),
                'sell_criteria': {'reason': trade_info.get('sell_reason', 'execution_notice'), 
                                'market_phase': get_current_market_phase_func()},
                'market_phase': get_current_market_phase_func(),
                'buy_order_id': None  # 추후 매칭 로직 추가 가능
            }
            
            db_id = self.save_sell_order(order_data)
            if db_id > 0:
                logger.info(f"📊 매도 체결 DB 저장 완료: {stock_code} (ID: {db_id})")
            else:
                logger.warning(f"⚠️ 매도 체결 DB 저장 실패: {stock_code}")
            
            return db_id
            
        except Exception as e:
            logger.error(f"❌ 매도 체결 DB 저장 오류 {stock_code}: {e}")
            return 0
    
    # === 분석 및 통계 메서드들 ===
    
    def get_daily_summary(self, trade_date: Optional[date] = None) -> Dict[str, Any]:
        """일일 거래 요약 조회"""
        try:
            if trade_date is None:
                trade_date = now_kst().date()
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 매수 주문 통계
                cursor.execute("SELECT COUNT(*) FROM buy_orders WHERE order_date = ?", (trade_date,))
                total_buy_orders = cursor.fetchone()[0]
                
                # 매도 주문 통계  
                cursor.execute("SELECT COUNT(*) FROM sell_orders WHERE order_date = ?", (trade_date,))
                total_sell_orders = cursor.fetchone()[0]
                
                # 손익 통계
                cursor.execute("""
                    SELECT 
                        COALESCE(SUM(profit_loss), 0) as total_pnl,
                        COUNT(CASE WHEN profit_loss > 0 THEN 1 END) as wins,
                        COUNT(CASE WHEN profit_loss < 0 THEN 1 END) as losses
                    FROM sell_orders 
                    WHERE order_date = ? AND order_status = 'executed'
                """, (trade_date,))
                
                pnl_result = cursor.fetchone()
                total_pnl = float(pnl_result[0] or 0)
                wins = int(pnl_result[1] or 0)
                losses = int(pnl_result[2] or 0)
                
                # 승률 계산
                total_trades = wins + losses
                win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
                
                return {
                    'trade_date': trade_date,
                    'total_buy_orders': total_buy_orders,
                    'total_sell_orders': total_sell_orders,
                    'total_profit_loss': total_pnl,
                    'win_count': wins,
                    'loss_count': losses,
                    'win_rate': win_rate,
                    'total_trades': total_trades
                }
                
        except Exception as e:
            logger.error(f"일일 요약 조회 실패: {e}")
            return {}
    
    def get_performance_analytics(self, days: int = 30) -> Dict[str, Any]:
        """성과 분석 데이터 조회
        
        Args:
            days: 분석할 일수
            
        Returns:
            성과 분석 결과
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 기간 설정
                end_date = now_kst().date()
                start_date = end_date - timedelta(days=days)
                
                # 매도 사유별 성과
                cursor.execute("""
                    SELECT 
                        sell_reason,
                        COUNT(*) as count,
                        AVG(profit_loss_rate) as avg_return,
                        COUNT(CASE WHEN profit_loss > 0 THEN 1 END) as wins,
                        COUNT(CASE WHEN profit_loss < 0 THEN 1 END) as losses
                    FROM sell_orders 
                    WHERE order_date BETWEEN ? AND ? AND order_status = 'executed'
                    GROUP BY sell_reason
                    ORDER BY count DESC
                """, (start_date, end_date))
                
                sell_reason_stats = []
                for row in cursor.fetchall():
                    total = row[1]
                    wins = row[3]
                    win_rate = (wins / total * 100) if total > 0 else 0
                    
                    sell_reason_stats.append({
                        'sell_reason': row[0],
                        'count': total,
                        'avg_return': row[2],
                        'win_rate': win_rate,
                        'wins': wins,
                        'losses': row[4]
                    })
                
                # 시간대별 성과
                cursor.execute("""
                    SELECT 
                        CASE 
                            WHEN strftime('%H', order_time) BETWEEN '09' AND '10' THEN 'morning'
                            WHEN strftime('%H', order_time) BETWEEN '11' AND '13' THEN 'midday'
                            WHEN strftime('%H', order_time) BETWEEN '14' AND '15' THEN 'afternoon'
                            ELSE 'other'
                        END as time_period,
                        COUNT(*) as count,
                        AVG(profit_loss_rate) as avg_return
                    FROM sell_orders 
                    WHERE order_date BETWEEN ? AND ? AND order_status = 'executed'
                    GROUP BY time_period
                """, (start_date, end_date))
                
                time_period_stats = {row[0]: {'count': row[1], 'avg_return': row[2]} 
                                   for row in cursor.fetchall()}
                
                return {
                    'period': f"{start_date} ~ {end_date}",
                    'sell_reason_analysis': sell_reason_stats,
                    'time_period_analysis': time_period_stats
                }
                
        except Exception as e:
            logger.error(f"성과 분석 조회 실패: {e}")
            return {}
    
    def get_performance_analysis(self, days: int = 30) -> Optional[Dict]:
        """성과 분석 조회
        
        Args:
            days: 분석할 일수
            
        Returns:
            성과 분석 결과 딕셔너리
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 기간별 거래 통계
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total_trades,
                        AVG(profit_loss_rate) as avg_return_rate,
                        MAX(profit_loss) as max_profit,
                        MIN(profit_loss) as max_loss,
                        SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) as win_count,
                        SUM(profit_loss) as total_profit_loss
                    FROM sell_orders 
                    WHERE DATE(order_time) >= DATE('now', '-{} days')
                    AND order_status = 'executed'
                """.format(days))
                
                result = cursor.fetchone()
                
                if result and result[0] > 0:
                    total_trades, avg_return_rate, max_profit, max_loss, win_count, total_profit_loss = result
                    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
                    
                    return {
                        'analysis_period': days,
                        'total_trades': total_trades,
                        'avg_return_rate': avg_return_rate or 0,
                        'max_profit': max_profit or 0,
                        'max_loss': max_loss or 0,
                        'win_count': win_count,
                        'win_rate': win_rate,
                        'total_profit_loss': total_profit_loss or 0
                    }
                else:
                    return {
                        'analysis_period': days,
                        'total_trades': 0,
                        'avg_return_rate': 0,
                        'max_profit': 0,
                        'max_loss': 0,
                        'win_count': 0,
                        'win_rate': 0,
                        'total_profit_loss': 0
                    }
                    
        except Exception as e:
            logger.error(f"성과 분석 조회 오류: {e}")
            return None

    def close(self):
        """데이터베이스 연결 정리"""
        logger.info("TradeDatabase 정리 완료")

    # -----------------------------
    # 🆕 Daily Metrics 저장/조회
    # -----------------------------
    def save_daily_metrics(self, metrics: Dict[str, Any]) -> bool:
        """일일 성과 메트릭 저장 (trade_date PRIMARY KEY 중복 시 REPLACE)"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO metrics_daily (
                        trade_date, trades, win_rate, total_pnl, avg_pnl, max_drawdown, params_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    metrics.get('trade_date'),
                    int(metrics.get('trades', 0)),
                    float(metrics.get('win_rate', 0.0)),
                    float(metrics.get('total_pnl', 0.0)),
                    float(metrics.get('avg_pnl', 0.0)),
                    float(metrics.get('max_drawdown', 0.0)),
                    json.dumps(metrics.get('params', {}))
                ))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"metrics_daily 저장 실패: {e}")
            return False

    def save_daily_summary(self, trade_date: Optional[date] = None) -> bool:
        """해당 일자의 요약 데이터를 daily_summaries 테이블에 저장(UPSERT)합니다.

        Args:
            trade_date: 요약할 거래 날짜. None 이면 오늘(now_kst().date())

        Returns:
            성공 여부
        """
        try:
            if trade_date is None:
                trade_date = now_kst().date()

            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()

                # 1) 스캔 건수
                c.execute("SELECT COUNT(*) FROM pre_market_scans WHERE scan_date = ?", (trade_date,))
                pre_market_scanned_count = c.fetchone()[0] or 0

                c.execute("SELECT COUNT(*) FROM intraday_scans WHERE scan_date = ?", (trade_date,))
                intraday_scanned_count = c.fetchone()[0] or 0

                # 2) 주문 건수
                c.execute("SELECT COUNT(*) FROM buy_orders WHERE order_date = ?", (trade_date,))
                total_buy_orders = c.fetchone()[0] or 0

                c.execute("SELECT COUNT(*) FROM sell_orders WHERE order_date = ?", (trade_date,))
                total_sell_orders = c.fetchone()[0] or 0

                c.execute("SELECT COUNT(*) FROM buy_orders WHERE order_date = ? AND order_status = 'executed'", (trade_date,))
                executed_buy_orders = c.fetchone()[0] or 0

                c.execute("SELECT COUNT(*) FROM sell_orders WHERE order_date = ? AND order_status = 'executed'", (trade_date,))
                executed_sell_orders = c.fetchone()[0] or 0

                # 3) 손익 및 승률
                c.execute(
                    """
                    SELECT 
                        COALESCE(SUM(profit_loss), 0) as total_pnl,
                        COUNT(CASE WHEN profit_loss > 0 THEN 1 END) as wins,
                        COUNT(CASE WHEN profit_loss < 0 THEN 1 END) as losses,
                        AVG(holding_minutes) as avg_holding_minutes
                    FROM sell_orders 
                    WHERE order_date = ? AND order_status = 'executed'
                    """,
                    (trade_date,)
                )
                total_pnl, wins, losses, avg_holding_minutes = c.fetchone()
                total_pnl = float(total_pnl or 0)
                wins = int(wins or 0)
                losses = int(losses or 0)
                avg_holding_minutes = float(avg_holding_minutes or 0)
                total_trades = wins + losses
                win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

                # 4) 총 투자금 및 최대 동시 포지션 수
                c.execute("SELECT COALESCE(SUM(total_amount), 0) FROM buy_orders WHERE order_date = ?", (trade_date,))
                total_investment = c.fetchone()[0] or 0

                # 최대 동시 포지션 수를 간단히 total_buy_orders 로 대체 (정교화 가능)
                max_position_count = total_buy_orders

                # UPSERT
                c.execute(
                    """
                    INSERT INTO daily_summaries (
                        trade_date, pre_market_scanned_count, intraday_scanned_count,
                        total_buy_orders, total_sell_orders, executed_buy_orders,
                        executed_sell_orders, total_profit_loss, win_count, loss_count,
                        win_rate, total_investment, max_position_count, avg_holding_minutes,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(trade_date) DO UPDATE SET
                        pre_market_scanned_count=excluded.pre_market_scanned_count,
                        intraday_scanned_count=excluded.intraday_scanned_count,
                        total_buy_orders=excluded.total_buy_orders,
                        total_sell_orders=excluded.total_sell_orders,
                        executed_buy_orders=excluded.executed_buy_orders,
                        executed_sell_orders=excluded.executed_sell_orders,
                        total_profit_loss=excluded.total_profit_loss,
                        win_count=excluded.win_count,
                        loss_count=excluded.loss_count,
                        win_rate=excluded.win_rate,
                        total_investment=excluded.total_investment,
                        max_position_count=excluded.max_position_count,
                        avg_holding_minutes=excluded.avg_holding_minutes,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        trade_date,
                        pre_market_scanned_count,
                        intraday_scanned_count,
                        total_buy_orders,
                        total_sell_orders,
                        executed_buy_orders,
                        executed_sell_orders,
                        total_pnl,
                        wins,
                        losses,
                        win_rate,
                        total_investment,
                        max_position_count,
                        avg_holding_minutes,
                    ),
                )
                conn.commit()
            logger.info(f"daily_summaries 저장/갱신 완료: {trade_date}")
            return True
        except Exception as e:
            logger.error(f"daily_summaries 저장 실패: {e}")
            return False 