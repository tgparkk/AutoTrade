#!/usr/bin/env python3
"""
독립 실행용 스크립트
====================

MarketScanner.intraday_scan_additional_stocks 결과를 단독으로 실행 / 확인하기 위한 스크립트입니다.

사용 방법
---------
프로젝트 루트( C:\GIT\AutoTrade )에서 아래와 같이 실행하세요.

    python scripts/run_intraday_scan.py --max 10

Options
    --max : 출력할 최대 종목 개수 (기본값 10)

필수 조건
---------
1. KIS 인증 토큰 파일 `token_info.json` 이 프로젝트 루트에 존재해야 합니다.
   (예: C:\GIT\AutoTrade\token_info.json)
2. .env 에 KIS APP_KEY / SECRET_KEY 등 필수 항목이 올바르게 설정되어 있어야 합니다.
3. 의존 패키지는 requirements.txt 를 참고하세요.
"""

import os
import sys
import argparse
from datetime import datetime

# -----------------------------------------------------------------------------
# 1) 프로젝트 루트 경로를 PYTHONPATH 에 포함
# -----------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

# -----------------------------------------------------------------------------
# 2) 로그 설정 (간단한 콘솔 출력)
# -----------------------------------------------------------------------------
from utils.logger import setup_logger
logger = setup_logger("intraday_scan_script")

# -----------------------------------------------------------------------------
# 3) 필요한 내부 모듈 임포트 (프로젝트 루트가 path 에 포함된 이후)
# -----------------------------------------------------------------------------
from trade.stock_manager import StockManager
from trade.market_scanner import MarketScanner
import api.kis_auth as kis_auth
from utils.korean_time import now_kst
from utils.stock_data_loader import get_stock_data_loader


def main() -> None:
    """스크립트 엔트리포인트"""
    parser = argparse.ArgumentParser(description="Run intraday scan and show ranking")
    parser.add_argument("--max", type=int, default=10, help="최대 출력 종목 수 (default: 10)")
    args = parser.parse_args()

    max_stocks = max(1, args.max)

    # -------------------------------------------------------------------------
    # 4) KIS 인증 (token_info.json 기반)
    # -------------------------------------------------------------------------
    if not kis_auth.auth():
        logger.error("KIS 인증 실패 – token_info.json 또는 .env 설정을 확인하세요.")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # 5) 핵심 객체 생성 및 스캔 실행
    # -------------------------------------------------------------------------
    stock_manager = StockManager()
    scanner = MarketScanner(stock_manager)

    logger.info(f"🔍 intraday_scan_additional_stocks 시작 (max_stocks={max_stocks})")
    results = scanner.intraday_scan_additional_stocks(max_stocks=max_stocks)

    # -------------------------------------------------------------------------
    # 6) 결과 출력
    # -------------------------------------------------------------------------
    if not results:
        print("스캔 결과가 없습니다.")
        return

    print("\n" + "=" * 60)
    print(f"[{now_kst().strftime('%Y-%m-%d %H:%M:%S')}] 장중 추가 종목 스캔 결과")
    print("=" * 60)

    stock_loader = get_stock_data_loader()
    for idx, (code, score, reasons) in enumerate(results, 1):
        stock_name = stock_loader.get_stock_name(code)
        print(f"{idx:2d}. {code} [{stock_name}]  점수: {score:.1f}  사유: {reasons}")

    print("=" * 60)
    print("✅ 스캔 완료")


if __name__ == "__main__":
    main() 