@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "KIT_LOG=%TEMP%\CindyRemoteKit-startup.log"
>"%KIT_LOG%" echo CindyRemoteKit startup %DATE% %TIME%
if not exist "manager.py" goto missing
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import tkinter" >>"%KIT_LOG%" 2>&1
if errorlevel 1 goto try_python
py -3 -c "import sys; print(sys.executable)" >>"%KIT_LOG%" 2>&1
py -3 manager.py >>"%KIT_LOG%" 2>&1
if errorlevel 1 goto failed
exit /b 0
:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python -c "import tkinter" >>"%KIT_LOG%" 2>&1
if errorlevel 1 goto no_python
python -c "import sys; print(sys.executable)" >>"%KIT_LOG%" 2>&1
python manager.py >>"%KIT_LOG%" 2>&1
if errorlevel 1 goto failed
exit /b 0
:missing
echo 找不到 manager.py，请先完整解压迁移包，再运行启动器。
goto show_log
:no_python
echo 未找到带 tkinter 的 Python 3。
echo 请安装 Python 3.10 或更新版本，包含 Tcl/Tk 并添加到 PATH。
echo https://www.python.org/downloads/windows/
goto show_log
:failed
echo 管理器启动失败，错误记录如下：
:show_log
type "%KIT_LOG%"
echo.
echo 日志位置：%KIT_LOG%
echo 请保留上面的错误信息。按任意键关闭窗口。
pause >nul
exit /b 1
