; Inno Setup script of the Fluxion installer (FluxionSetup-<version>.exe).
;
; Built by scripts\build_installer.ps1 from dist\FluxionBrowserLite after
; scripts\build_lite.ps1:  ISCC /DAppVersion=0.21.0 installer\fluxion.iss
;
; * per user, no administrator rights: {localappdata}\Programs\Fluxion
; * an update is the same setup run over the installed version: the program
;   folder is replaced, user data stays (it lives in %LOCALAPPDATA%\Fluxion
;   and %APPDATA%\Fluxion, see core\app_paths.py)
; * silent mode for the in-app updater:
;     FluxionSetup-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
;   (a running Fluxion is closed; the program is not started afterwards)
;   /RESTARTAPP starts Fluxion again after the silent install: the in-app
;   one-click update passes it (core/update.py)
; * code signing: scripts\build_installer.ps1 passes /DSignToolName=... and
;   /S<name>=<command> when FLUXION_SIGN_THUMBPRINT or FLUXION_SIGNTOOL_CMD is
;   set; then the setup and the uninstaller are signed
; * uninstall removes the program only; it asks whether to remove the data
;   (never in silent mode)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\FluxionBrowserLite"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\artifacts"
#endif
#define AppName "Fluxion"
#define AppExeName "FluxionBrowserLite.exe"

[Setup]
; the same AppId in every version: that is what makes a setup an update
AppId={{6F3C2A51-8E4B-4D7A-9C1E-F1A0B2C3D4E5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Fluxion
AppPublisherURL=https://djonros.github.io/Fluxion/
AppSupportURL=https://djonros.github.io/Fluxion/
AppUpdatesURL=https://djonros.github.io/Fluxion/#download
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Fluxion
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
OutputDir={#OutputDir}
OutputBaseFilename=FluxionSetup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
#ifdef SignToolName
SignTool={#SignToolName}
SignedUninstaller=yes
#endif
ShowLanguageDialog=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
ru.DeleteDataPrompt=Удалить также ваши данные Fluxion?%n%nЧаты, индексы документов, настройки и скачанные модели в папке%n%1%n%nЛицензия сохранится. Если вы собираетесь установить Fluxion снова, ответьте «Нет».

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; binaries of the previous version: an update must not keep stale libraries
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; portable.txt would switch an installed copy to the exe folder for data
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "portable.txt,data\*,config\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Запустить Fluxion"; Flags: nowait postinstall skipifsilent
; the one-click update: start the new version after the silent install
Filename: "{app}\{#AppExeName}"; Flags: nowait skipifnotsilent; Check: RestartAppRequested

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Code]
function RestartAppRequested: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/RESTARTAPP') = 0 then
      Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if (CurUninstallStep <> usPostUninstall) or UninstallSilent then
    exit;
  DataDir := ExpandConstant('{localappdata}\Fluxion');
  if not DirExists(DataDir) then
    exit;
  if MsgBox(FmtMessage(CustomMessage('DeleteDataPrompt'), [DataDir]),
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(DataDir, True, True, True);
end;
