"""Build on the target OS. No cross-compilation or release signing implied."""
import platform
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
os_name=platform.system()
args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--name','Mepiti','--collect-data','mepiti','--collect-submodules','keyring.backends',
      # 앱 창(pywebview). 운영체제별 창 모듈을 빠뜨리지 않게 통째로 넣는다.
      '--collect-all','webview','--collect-submodules','anthropic']
icon=root/'scripts'/'icon'
if os_name=='Darwin':args+=['--windowed','--onedir','--osx-bundle-identifier','local.mepiti.app','--icon',str(icon/'Mepiti.icns')]
elif os_name=='Windows':args+=['--windowed','--onedir','--icon',str(icon/'Mepiti.ico')]   # 앱 창이 있으니 콘솔 창은 띄우지 않는다
else:raise SystemExit('Windows 또는 macOS에서 빌드하세요.')
subprocess.run(args+['scripts/desktop_entry.py'],cwd=root,check=True)
if os_name=='Darwin':
    # 메뉴 막대·Dock에 'Python'이 아니라 '메피티'가 나오게 앱 정보를 채운다.
    import plistlib,re
    version=re.search(r"__version__\s*=\s*'([^']+)'",(root/'mepiti'/'__init__.py').read_text()).group(1)
    app=root/'dist'/'Mepiti.app'
    info=app/'Contents'/'Info.plist'
    data=plistlib.loads(info.read_bytes())
    data.update(CFBundleName='메피티',CFBundleDisplayName='메피티',CFBundleShortVersionString=version,CFBundleVersion=version,
                LSApplicationCategoryType='public.app-category.games',NSHighResolutionCapable=True,
                NSRequiresAquaSystemAppearance=False,
                # 앱 창이 내 컴퓨터 안의 서버(http://127.0.0.1)를 연다.
                NSAppTransportSecurity={'NSAllowsLocalNetworking':True})
    info.write_bytes(plistlib.dumps(data))
    # Info.plist를 고치면 서명이 깨져 '손상된 앱'으로 보인다. 개발용(ad-hoc)으로 다시 서명한다.
    subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
    subprocess.run(['hdiutil','create','-volname','Mepiti','-srcfolder',str(root/'dist'/'Mepiti.app'),'-ov','-format','UDZO',str(root/'dist'/'Mepiti-macOS.dmg')],check=True)
else:
    import shutil
    compiler=shutil.which('ISCC') or str(Path('C:/Program Files (x86)/Inno Setup 6/ISCC.exe'))
    subprocess.run([compiler,str(root/'scripts'/'installer.iss')],check=True)
