@echo off
chcp 65001 >nul
echo ========================================
echo        AutoTrade System Test
echo ========================================
echo.

:: 현재 디렉토리를 스크립트 위치로 변경
cd /d "%~dp0"
echo 현재 작업 디렉토리: %CD%
echo.

:: Python 설치 확인
python --version >nul 2>&1
if errorlevel 1 (
    echo 오류: Python이 설치되지 않았습니다.
    pause
    exit /b 1
)

:: 테스트 스크립트 실행
echo AutoTrade 시스템 테스트를 시작합니다...
echo.

python test_system.py
set EXIT_CODE=%errorlevel%

echo.
echo 테스트 완료. 종료 코드: %EXIT_CODE%

if %EXIT_CODE% equ 0 (
    echo ✅ 모든 테스트가 통과했습니다!
    echo 이제 run_autotrade.bat로 시스템을 실행할 수 있습니다.
) else (
    echo ❌ 테스트에 실패했습니다.
    echo 오류 내용을 확인하고 문제를 해결해주세요.
)

echo.
pause 