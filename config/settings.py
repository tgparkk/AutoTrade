"""
주식자동매매 시스템 설정 파일
key.ini 파일에서 API/보안 관련 설정을 읽어와서 관리합니다
"""
import os
import configparser
from pathlib import Path
from typing import Optional

# 프로젝트 루트 경로
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_FILE = PROJECT_ROOT / 'config' / 'key.ini'

class Settings:
    """설정 관리 클래스 - API/보안 관련 설정만 관리"""
    
    def __init__(self):
        self.config = configparser.ConfigParser()
        self._load_config()
        
    def _load_config(self):
        """설정 파일 로드"""
        if not CONFIG_FILE.exists():
            print(f"❌ 설정 파일이 없습니다: {CONFIG_FILE}")
            print("📝 config/key.ini 파일을 생성해주세요!")
            raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {CONFIG_FILE}")
            
        try:
            self.config.read(CONFIG_FILE, encoding='utf-8')
            print("✅ API 설정 파일 로드 완료")
            
        except Exception as e:
            print(f"❌ 설정 파일 로드 실패: {e}")
            raise
    
    def get_kis(self, key: str, default: str = "") -> str:
        """KIS 설정값 가져오기"""
        return self.config.get('KIS', key, fallback=default).strip('"')
    
    def get_telegram(self, key: str, default: str = "") -> str:
        """텔레그램 설정값 가져오기"""
        return self.config.get('TELEGRAM', key, fallback=default).strip('"')
    
    def get_telegram_bool(self, key: str, default: bool = False) -> bool:
        """텔레그램 불린 설정값 가져오기"""
        value = self.get_telegram(key, str(default)).lower()
        return value in ('true', '1', 'yes', 'on')
    
    def validate_required_settings(self) -> bool:
        """필수 설정값 검증"""
        required_keys = ['KIS_APP_KEY', 'KIS_APP_SECRET', 'KIS_ACCOUNT_NO', 'KIS_HTS_ID']
        missing = []
        
        for key in required_keys:
            value = self.get_kis(key)
            if not value or value == f'your_{key.lower()}_here':
                missing.append(key)
        
        if missing:
            print(f"❌ 필수 설정값이 누락되었습니다: {', '.join(missing)}")
            print("📝 config/key.ini 파일에 다음 설정값들을 추가해주세요:")
            for key in missing:
                print(f"{key}=your_value_here")
            return False
        
        print("✅ 모든 필수 API 설정값이 정상적으로 로드되었습니다")
        return True

# 전역 설정 인스턴스
try:
    _settings = Settings()
except Exception as e:
    print(f"❌ 설정 초기화 실패: {e}")
    _settings = None

# KIS 한국투자증권 API 설정
KIS_BASE_URL = _settings.get_kis('KIS_BASE_URL', 'https://openapi.koreainvestment.com:9443') if _settings else ''
APP_KEY = _settings.get_kis('KIS_APP_KEY', '') if _settings else ''
SECRET_KEY = _settings.get_kis('KIS_APP_SECRET', '') if _settings else ''
ACCOUNT_NUMBER = _settings.get_kis('KIS_ACCOUNT_NO', '') if _settings else ''
ACCOUNT_NUMBER_PREFIX = ACCOUNT_NUMBER[:8] if ACCOUNT_NUMBER else ''
HTS_ID = _settings.get_kis('KIS_HTS_ID', '') if _settings else ''

# 텔레그램 봇 설정
TELEGRAM_BOT_TOKEN = _settings.get_telegram('token', '') if _settings else ''
TELEGRAM_CHAT_ID = _settings.get_telegram('chat_id', '') if _settings else ''

def validate_settings():
    """필수 설정값 검증"""
    if not _settings:
        return False
    return _settings.validate_required_settings()

def get_settings() -> Optional[Settings]:
    """설정 인스턴스 반환"""
    return _settings

# 모듈 import 시 자동 검증
if _settings and not _settings.validate_required_settings():
    print("⚠️ 설정 오류로 인해 시스템이 정상 작동하지 않을 수 있습니다.")
    print("🔧 config/key.ini 파일을 수정하고 다시 실행해주세요.")

# 직접 실행 시 전체 검증
if __name__ == "__main__":
    if validate_settings():
        print("✅ 모든 API 설정이 정상입니다!")
    else:
        print("❌ API 설정에 문제가 있습니다.")
