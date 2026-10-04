# Windows system DLLs must not be shadowed by unrelated native runtimes on PATH.
from pathlib import Path

project = Path(SPECPATH)
a = Analysis([str(project / 'main.py')], pathex=[str(project)],
             binaries=[], datas=[(str(project / 'time2manage' / 'ui'), 'time2manage/ui'),
                                  (str(project / 'time2manage' / 'assets'), 'time2manage/assets')],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)

system_names = {'icuuc.dll', 'icuin.dll', 'ucrtbase.dll'}
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in system_names
              and not Path(entry[0]).name.lower().startswith('api-ms-win-')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Time2Manage',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(project / 'time2manage' / 'assets' / 'app.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Time2Manage')
