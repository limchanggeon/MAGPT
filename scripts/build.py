"""Build on the target OS. No cross-compilation or release signing implied."""
import platform
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
os_name=platform.system()
args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--name','Mepiti','--collect-data','mepiti','--collect-submodules','keyring.backends',
      # 앱 창(pywebview). 운영체제별 창 모듈을 빠뜨리지 않게 통째로 넣는다.
      '--collect-all','webview']
if os_name=='Darwin':args+=['--windowed','--onedir','--osx-bundle-identifier','local.mepiti.app']
elif os_name=='Windows':args+=['--windowed','--onedir']   # 앱 창이 있으니 콘솔 창은 띄우지 않는다
else:raise SystemExit('Windows 또는 macOS에서 빌드하세요.')
subprocess.run(args+['scripts/desktop_entry.py'],cwd=root,check=True)
if os_name=='Darwin':
    subprocess.run(['hdiutil','create','-volname','Mepiti','-srcfolder',str(root/'dist'/'Mepiti.app'),'-ov','-format','UDZO',str(root/'dist'/'Mepiti-macOS.dmg')],check=True)
else:
    import shutil
    compiler=shutil.which('ISCC') or str(Path('C:/Program Files (x86)/Inno Setup 6/ISCC.exe'))
    subprocess.run([compiler,str(root/'scripts'/'installer.iss')],check=True)
