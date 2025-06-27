#!/usr/bin/env python3
"""
LiquidityTracker – 10초 슬라이딩 윈도우로 종목별 호가·체결 빈도 및 체결량을 집계하여
0~10 스케일의 liquidity_score 를 계산해 제공한다.

• record(stock_code, msg_type, contract_vol=0):
    StockManager / WebSocket 핸들러에서 호출.
    msg_type: 'bidask' | 'contract'

• get_metrics(stock_code) -> dict
• get_score(stock_code)   -> float (0~10, 기본 0)

Score 식 (10초 윈도우):
 quote_score   = min(10, quote_cnt / 10)           # 초당 1건 = 1점
 trade_score   = min(10, math.log10(trade_vol+1)*2)  # 체결량 log 스케일, 10점 캡
 liquidity_score = quote_score*0.4 + trade_score*0.6
"""

from collections import deque, defaultdict
from threading import RLock
from time import time
import math

_WINDOW = 10.0  # seconds

class _LiquidityTracker:
    def __init__(self):
        self._events = defaultdict(deque)  # code -> deque[(ts, is_quote, trade_vol)]
        self._lock = RLock()
    
    def record(self, stock_code: str, msg_type: str, contract_vol: int = 0):
        """msg_type: 'bidask' or 'contract'"""
        if not stock_code:
            return
        now_ts = time()
        is_quote = msg_type == 'bidask'
        with self._lock:
            dq = self._events[stock_code]
            dq.append((now_ts, is_quote, contract_vol if not is_quote else 0))
            # prune old
            cutoff = now_ts - _WINDOW
            while dq and dq[0][0] < cutoff:
                dq.popleft()
    
    def _compute(self, dq):
        quote_cnt = 0
        trade_cnt = 0
        trade_vol = 0
        for (_, is_quote, vol) in dq:
            if is_quote:
                quote_cnt += 1
            else:
                trade_cnt += 1
                trade_vol += vol
        return quote_cnt, trade_cnt, trade_vol
    
    def get_metrics(self, stock_code: str):
        with self._lock:
            dq = self._events.get(stock_code)
            if not dq:
                return {'quote_cnt': 0, 'trade_cnt': 0, 'trade_vol': 0}
            quote_cnt, trade_cnt, trade_vol = self._compute(dq)
            return {'quote_cnt': quote_cnt, 'trade_cnt': trade_cnt, 'trade_vol': trade_vol}
    
    def get_score(self, stock_code: str) -> float:
        m = self.get_metrics(stock_code)
        quote_cnt = m['quote_cnt']
        trade_vol = m['trade_vol']
        quote_score = min(10.0, quote_cnt / _WINDOW)  # 10초 동안 100건 → 10점
        trade_score = min(10.0, math.log10(trade_vol + 1) * 2.0)
        liquidity_score = quote_score * 0.4 + trade_score * 0.6
        return round(liquidity_score, 2)

# 싱글톤 인스턴스
liquidity_tracker = _LiquidityTracker() 