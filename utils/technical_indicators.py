from __future__ import annotations

"""Technical indicator helper functions

Light-weight pure-python implementations of commonly used indicators.
All calculations use pandas and do **not** depend on TA-Lib.
Only the latest value(s) are returned for speed – enough for scoring logic.
"""

from typing import Dict, Any

import pandas as pd

__all__ = [
    "compute_indicators",
]


def _ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average using pandas ewm."""
    return series.ewm(span=period, adjust=False).mean()


def compute_indicators(
    df: pd.DataFrame,
    close_col: str = "stck_clpr",
    volume_col: str | None = None,
) -> Dict[str, Any]:
    """Calculate basic indicators and return dict with the latest values.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame (최신 행이 index 0 인 상태여도 무방함)
    close_col : str
        종가 열 이름
    volume_col : str | None
        거래량 열 이름 – spike 계산용 (선택)
    """
    if df.empty or close_col not in df.columns:
        return {}

    # 정렬: 오래된 → 최신 순으로 가정. 만약 반대라면 sort_index()
    closes = df[close_col].astype(float).reset_index(drop=True)

    # RSI(14)
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14, min_periods=14).mean()
    avg_loss = loss.rolling(window=14, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))

    # MACD (12,26,9)
    ema_fast = _ema(closes, 12)
    ema_slow = _ema(closes, 26)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, 9)
    macd_hist = macd_line - signal_line

    # Bollinger Bands(20,2)
    ma20 = closes.rolling(window=20).mean()
    std20 = closes.rolling(window=20).std()
    bb_upper = ma20 + 2 * std20
    bb_lower = ma20 - 2 * std20

    out: Dict[str, Any] = {
        "rsi": float(rsi.iloc[-1]) if not rsi.empty else None,
        "macd": float(macd_line.iloc[-1]) if not macd_line.empty else None,
        "macd_signal": float(signal_line.iloc[-1]) if not signal_line.empty else None,
        "macd_hist": float(macd_hist.iloc[-1]) if not macd_hist.empty else None,
        "bb_upper": float(bb_upper.iloc[-1]) if not bb_upper.empty else None,
        "bb_lower": float(bb_lower.iloc[-1]) if not bb_lower.empty else None,
        "bb_middle": float(ma20.iloc[-1]) if not ma20.empty else None,
    }

    # Volume spike ratio (1일/20일평균)
    if volume_col and volume_col in df.columns:
        vols = df[volume_col].astype(float)
        vol_spike = None
        if len(vols) >= 20:
            vol_1 = vols.iloc[-1]
            vol_avg_20 = vols.iloc[-20:].mean()
            if vol_avg_20 > 0:
                vol_spike = vol_1 / vol_avg_20
        out["volume_spike"] = vol_spike

    return out 