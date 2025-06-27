#!/usr/bin/env python3
"""
Bayesian Optimization 기반 자동 파라미터 튜닝 스크립트
• database.metrics_daily 최근 데이터로 목표 함수를 구성
• scikit-optimize(gp_minimize) 사용, 결과를 config/auto_params.json 에 저장

사용 방법:
    python scripts/auto_param_optimizer.py --days 10 --calls 30
"""

import json
import argparse
from pathlib import Path

import numpy as np
from skopt import gp_minimize
from skopt.space import Real

from datetime import date, timedelta

import sqlite3

from utils.logger import setup_logger

logger = setup_logger(__name__)

def load_metrics(db_path: str, days: int = 10):
    """metrics_daily 테이블에서 최근 days 일 데이터를 불러온다"""
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT total_pnl, max_drawdown FROM metrics_daily WHERE trade_date >= ?",
                (cutoff,),
            )
            rows = cur.fetchall()
        return rows
    except Exception as e:
        logger.error(f"metrics fetch error {e}")
        return []

def evaluate_recent_performance(metrics_rows):
    """최근 성과 행 → 위험조정 수익률 점수 계산(높을수록 좋음)"""
    if not metrics_rows:
        return 0.0

    pnls = np.array([row[0] for row in metrics_rows], dtype=float)
    mdds = np.array([row[1] for row in metrics_rows], dtype=float)

    avg_pnl = pnls.mean()
    std_pnl = pnls.std() if pnls.size > 1 else 1.0
    avg_mdd = mdds.mean() if mdds.size > 0 else 0.0

    lam = 0.7  # MDD 가중치
    score = avg_pnl / (std_pnl + 1e-6) - lam * avg_mdd
    return score

# 현재는 실시간 백테스트를 할 수 없으므로, 최근 실제 성과에 근거해 후보 파라미터에 패널티를 주지 않음.
def backtest_score(params_dict, metrics_rows):
    """단순히 최근 성과 점수를 그대로 반환 (파라미터 독립)"""
    return -evaluate_recent_performance(metrics_rows)

def map_to_config(x):
    return {
        "min_contract_strength_for_buy": round(x[0]),
        "min_bid_ask_ratio_for_buy": round(x[1], 2),
        "liquidity_weight": round(x[2], 2),
        "take_profit_rate": round(x[3], 4),
        "stop_loss_rate": round(x[4], 4)
    }

def objective(x, metrics_rows):
    cfg = map_to_config(x)
    return backtest_score(cfg, metrics_rows)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=10)
    parser.add_argument('--calls', type=int, default=25)
    args = parser.parse_args()

    space = [
        Real(80, 120, name='strength'),
        Real(1.0, 1.3, name='bidask'),
        Real(0.5, 2.0, name='liq_weight'),
        Real(0.015, 0.03, name='tp'),
        Real(-0.03, -0.01, name='sl')
    ]

    # 최근 성과 미리 로드
    db_path = "data/trading.db"
    recent_metrics = load_metrics(db_path, days=args.days)

    res = gp_minimize(lambda x: objective(x, recent_metrics), space, n_calls=args.calls, random_state=0)
    best_cfg = map_to_config(res.x)

    Path("config").mkdir(exist_ok=True)
    with open("config/auto_params.json", "w", encoding="utf-8") as f:
        json.dump(best_cfg, f, ensure_ascii=False, indent=2)
    logger.info(f"Best params saved: {best_cfg}")

if __name__ == "__main__":
    main() 