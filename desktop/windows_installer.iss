; Stable AppId and per-user location preserve upgrades and user data.
#ifndef AppVersion
  #error AppVersion must be supplied by build_windows.py
#endif
#ifndef PackageDir
  #error PackageDir must be supplied by build_windows.py
#endif
#ifndef OutputDir
  #error OutputDir must be supplied by build_windows.py
#endif
[Setup]
AppId={{9A3EEFD8-3595-4F60-A089-275727524BEA}
AppName=星栖小镇
AppVersion={#AppVersion}
AppPublisher=Starhaven Town contributors
AppPublisherURL=https://github.com/2017Rocke/starhaven-town
AppSupportURL=https://github.com/2017Rocke/starhaven-town/issues
AppUpdatesURL=https://github.com/2017Rocke/starhaven-town/releases/latest
DefaultDirName={localappdata}\Programs\StarhavenTown
DefaultGroupName=星栖小镇
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=StarhavenTown-{#AppVersion}-windows-x64-Setup
SetupIconFile={#PackageDir}\app-icon.ico
UninstallDisplayIcon={app}\StarhavenTown.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AppMutex=Local\StarhavenTownSingleInstance
LicenseFile={#PackageDir}\LICENSE

[Tasks]
Name: desktopicon; Description: "创建桌面图标"; GroupDescription: "快捷方式："

[Files]
Source: "{#PackageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\星栖小镇"; Filename: "{app}\StarhavenTown.exe"; Tasks: desktopicon
Name: "{userprograms}\星栖小镇\星栖小镇"; Filename: "{app}\StarhavenTown.exe"
Name: "{userprograms}\星栖小镇\卸载星栖小镇"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\StarhavenTown.exe"; Description: "打开星栖小镇"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
var
  ReleaseVersion: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release', ReleaseVersion) and (ReleaseVersion >= 461808);
  if not Result then
    MsgBox('此版本需要 .NET Framework 4.7.2 或更新版本。Windows 10 1809 标准系统已自带；如果被精简，请通过微软官方渠道修复后重试。', mbError, MB_OK);
end;

function HasWebView2(): Boolean;
var
  Version: String;
  ClientKey: String;
begin
  ClientKey := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F1E7E726-8477-4F68-B92D-0D344C4EA749}';
  Result := ((RegQueryStringValue(HKCU, ClientKey, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM32, ClientKey, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')));
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and not HasWebView2() then
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 +
      '本机可能尚未安装 Microsoft Edge WebView2 运行时。首次打开小镇会提供微软官方下载入口，请安装 Evergreen Standalone Installer 的 x64 版本后重新打开。';
end;
// No uninstall-delete rule touches %LOCALAPPDATA%\StarhavenTown.
