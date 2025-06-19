"""
엑셀 파일에서 종목 정보('단축코드', '한글 종목약명')를 추출하여 JSON으로 저장하는 스크립트
"""

import pandas as pd
import json
import os
from pathlib import Path

def extract_stock_data():
    """엑셀에서 종목 정보 추출 및 JSON 저장"""
    
    # 파일 경로 설정
    excel_file = "data_0737_20250613.xlsx"
    output_file = "data/stock_list.json"
    
    # data 폴더 생성
    os.makedirs("data", exist_ok=True)
    
    try:
        print(f"엑셀 파일 읽는 중: {excel_file}")
        
        # 엑셀 파일 읽기
        df = pd.read_excel(excel_file)
        
        print(f"전체 행 수: {len(df)}")
        print(f"컬럼명: {list(df.columns)}")
        
        # 필요한 컬럼 확인
        required_columns = ['단축코드', '한글 종목약명', '시장구분']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            print(f"누락된 컬럼: {missing_columns}")
            print("사용 가능한 컬럼들:")
            for i, col in enumerate(df.columns, 1):
                print(f"  {i:2d}. {col}")
            return False
        
        # 시장구분 필터링 - KOSPI만 추출
        print(f"전체 종목 수: {len(df)}")
        kospi_df = df[df['시장구분'] == 'KOSPI']
        print(f"KOSPI 종목 수: {len(kospi_df)}")
        
        # 시장구분별 통계 출력
        market_counts = df['시장구분'].value_counts()
        print("시장구분별 종목 수:")
        for market, count in market_counts.items():
            print(f"  - {market}: {count}개")
        
        # 데이터 추출 및 정리 (KOSPI만)
        stock_data = []
        for _, row in kospi_df.iterrows():
            stock_code = str(row['단축코드']).strip()
            stock_name = str(row['한글 종목약명']).strip()
            market = str(row['시장구분']).strip()
            
            # 유효한 데이터만 포함
            if stock_code and stock_name and stock_code != 'nan' and stock_name != 'nan':
                stock_data.append({
                    'code': stock_code,
                    'name': stock_name,
                    'market': market
                })
        
        # 중복 제거 (종목코드 기준)
        unique_stocks = {}
        for stock in stock_data:
            unique_stocks[stock['code']] = stock
        
        final_stock_list = list(unique_stocks.values())
        
        print(f"추출된 고유 KOSPI 종목 수: {len(final_stock_list)}")
        
        # JSON으로 저장
        output_data = {
            'source_file': excel_file,
            'extract_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
            'market_filter': 'KOSPI',
            'total_stocks': len(final_stock_list),
            'stocks': final_stock_list
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        
        print(f"종목 정보가 저장되었습니다: {output_file}")
        
        # 샘플 데이터 출력
        print("\n=== 샘플 데이터 (처음 10개) ===")
        for i, stock in enumerate(final_stock_list[:10], 1):
            print(f"{i:2d}. {stock['code']} - {stock['name']}")
        
        return True
        
    except FileNotFoundError:
        print(f"파일을 찾을 수 없습니다: {excel_file}")
        return False
    except Exception as e:
        print(f"오류 발생: {e}")
        return False

if __name__ == "__main__":
    extract_stock_data() 