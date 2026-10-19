"""Console-free entry point with a persistent startup log and friendly errors."""
import os, subprocess, sys, tempfile, traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parent
LOG=Path(tempfile.gettempdir())/'CindyRemoteKit-startup.log'

def windows_shortcuts():
    # Recreate machine-specific links after moving the portable executable.
    if os.name!='nt' or not getattr(sys,'frozen',False):return
    import base64
    executable=Path(sys.executable).resolve()
    quote=lambda value:"'"+str(value).replace("'","''")+"'"
    script="$ErrorActionPreference='Stop';$shell=New-Object -ComObject WScript.Shell;"
    script+="$targets=@((Join-Path ([Environment]::GetFolderPath('Desktop')) 'LinX Remote.lnk'),"+quote(executable.parent/'LinX Remote.lnk')+");"
    script+="foreach($target in $targets){$link=$shell.CreateShortcut($target);$link.TargetPath="+quote(executable)+";$link.WorkingDirectory="+quote(executable.parent)+";$link.IconLocation="+quote(str(executable)+',0')+";$link.Description='LinX Remote';$link.WindowStyle=7;$link.Save()}"
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-WindowStyle','Hidden','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)

def report(message):
    if os.name=='nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,message,'LinX Remote · 启动失败',16)
    elif sys.platform=='darwin':
        import json
        script='display dialog '+json.dumps(message,ensure_ascii=False)+' with title "LinX Remote" buttons {"好"} default button "好"'
        subprocess.run(['/usr/bin/osascript','-e',script],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

if __name__=='__main__':
    if '--tunnel-worker' in sys.argv:
        import manager
        manager.tunnel_worker(sys.argv[sys.argv.index('--tunnel-worker')+1])
        sys.exit(0)
    if '--background' in sys.argv:
        python=sys.executable
        if os.name=='nt' and (Path(python).parent/'pythonw.exe').exists():python=str(Path(python).parent/'pythonw.exe')
        with open(LOG,'a',encoding='utf-8') as log:
            command=[sys.executable,'--serve'] if getattr(sys,'frozen',False) else [python,str(ROOT/'launch.pyw'),'--serve']
            environment=dict(os.environ)
            if getattr(sys,'frozen',False):environment['PYINSTALLER_RESET_ENVIRONMENT']='1'
            subprocess.Popen(command,env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                             **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}))
    else:
        with open(LOG,'w',encoding='utf-8',buffering=1) as log:
            sys.stdout=sys.stderr=log
            try:
                os.chdir(ROOT)
                import manager
                try:windows_shortcuts()
                except (OSError,subprocess.SubprocessError):pass
                manager.gui()
            except Exception:
                traceback.print_exc()
                report('管理器未能启动。\n错误已保存在：\n'+str(LOG)+'\n请查看日志后重试，或将错误信息发给助手。')
                sys.exit(1)

