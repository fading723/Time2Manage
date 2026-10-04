#ifndef AppVersion
  #define AppVersion "2.1.2"
#endif

[Setup]
AppId={{59CBB423-E0E2-4B36-900E-F76EC5895C12}
AppName=时间有迹
AppVersion={#AppVersion}
AppPublisher=fading723
AppPublisherURL=https://github.com/fading723/Time2Manage
AppSupportURL=https://github.com/fading723/Time2Manage/issues
DefaultDirName={localappdata}\Programs\Time2Manage
DefaultGroupName=时间有迹
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=installer-output
OutputBaseFilename=Time2Manage-Setup-{#AppVersion}-x64
SetupIconFile=time2manage\assets\app.ico
UninstallDisplayIcon={app}\Time2Manage.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
Uninstallable=yes

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "release\Time2Manage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\时间有迹"; Filename: "{app}\Time2Manage.exe"
Name: "{autodesktop}\时间有迹"; Filename: "{app}\Time2Manage.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Time2Manage.exe"; Description: "启动时间有迹"; Flags: nowait postinstall skipifsilent

; The user database lives outside the installation folder and is retained on uninstall.
