@echo off
chcp 65001 >nul
echo ========================================
echo           AutoTrade System
echo ========================================
echo(

:: 현재 디렉토리를 스크립트 위치로 변경
cd /d "%~dp0"
echo 현재 작업 디렉토리: %CD%
echo(

:: Python 가상환경 확인 및 활성화 (있는 경우)
if exist "venv\Scripts\activate.bat" (
    echo 가상환경을 활성화합니다...
    call venv\Scripts\activate.bat
    echo(
)

:: Python 설치 확인
echo Python 설치를 확인합니다...
python --version >nul 2>&1
if errorlevel 1 (
    echo 오류: Python이 설치되지 않았습니다.
    echo Python을 설치한 후 다시 시도해주세요.
    pause
    exit /b 1
)

:: Python 버전 표시
for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo %PYTHON_VERSION%이 설치되어 있습니다.
echo(

:: main.py 파일 존재 확인
echo main.py 파일을 확인합니다...
if not exist "main.py" (
    echo 오류: main.py 파일이 없습니다.
    echo 현재 디렉토리: %CD%
    echo 파일 목록:
    dir *.py
    echo(
    echo 프로젝트 루트 디렉토리에서 실행해주세요.
    pause
    exit /b 1
)
echo main.py 파일이 확인되었습니다.
echo(

:: 로그 디렉토리 생성
if not exist "logs" (
    echo 로그 디렉토리를 생성합니다...
    mkdir logs
)

echo 시작 시간: %date% %time%
echo(

echo Python 스크립트 문법을 검사합니다...
python -m py_compile main.py
if errorlevel 1 (
    echo 오류: main.py에 문법 오류가 있습니다.
    echo 자세한 오류 내용을 확인해주세요.
    pause
    exit /b 1
)
echo 문법 검사 통과.
echo(

echo 필수 모듈 import를 테스트합니다...
python -c "from trade.trade_manager import TradeManager; print('TradeManager import 성공')"
if errorlevel 1 (
    echo 오류: TradeManager import 실패
    echo 모듈 경로를 확인해주세요.
    pause
    exit /b 1
)
echo import 테스트 통과.
echo(

echo AutoTrade 시스템을 시작합니다...
echo 종료하려면 Ctrl+C를 누르세요.
echo(

python main.py

echo(
echo [main.py 실행이 끝났습니다. 위의 에러 메시지를 확인하세요.]
pause 