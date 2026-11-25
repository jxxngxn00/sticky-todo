; Sticky Todo 설치 프로그램 (Inno Setup 6)
;
; - 관리자 권한 없이 현재 사용자 폴더에 설치 (%LOCALAPPDATA%\Programs\StickyTodo)
; - 시작 메뉴 등록, 바탕화면 바로가기·시작 시 자동 실행은 선택
; - 설정 → 앱에서 삭제, 삭제할 때 할 일 데이터(%APPDATA%\StickyTodo)를 지울지 물어봄
;
; 빌드: tools/build.py 가 ISCC로 컴파일한다 (AppVersion, SourceDir, OutputDir을 /D로 넘김)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir ".."
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

#define AppName "Sticky Todo"
#define AppExe "StickyTodo.exe"

[Setup]
; AppId는 바꾸면 안 된다 (업데이트·삭제가 이 값으로 같은 앱을 찾는다)
AppId={{6E0C3F7A-2B1D-4C8E-9A57-3D2F8B4E1C90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=ji.yoon
AppPublisherURL=https://github.com/jxxngxn00/sticky-todo
AppSupportURL=https://github.com/jxxngxn00/sticky-todo/issues
DefaultDirName={userpf}\StickyTodo
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
OutputDir={#OutputDir}
OutputBaseFilename=StickyTodo-Setup
SetupIconFile={#SourceDir}\build\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
; MIT는 동의가 필요 없는 라이선스라 동의 화면은 생략 (LICENSE.txt는 설치 폴더에 포함)
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 작업:"; Flags: unchecked
Name: "autostart"; Description: "윈도우를 켤 때 자동으로 실행"; GroupDescription: "추가 작업:"

[Files]
Source: "{#SourceDir}\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "{#SourceDir}\THIRD-PARTY-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\licenses\Pretendard-OFL.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; 앱 안의 "시작 시 자동 실행"과 같은 값 이름·형식 (경로는 따옴표로 감싼다)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "StickyTodo"; ValueData: """{app}\{#AppExe}"""; Tasks: autostart; Flags: uninsdeletevalue
; 자동 실행을 안 고른 경우에도 삭제할 때는 앱에서 켰던 값을 지운다
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "StickyTodo"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "Sticky Todo 실행"; Flags: nowait postinstall skipifsilent

[Code]
// 실행 중이면 파일을 덮어쓰거나 지울 수 없어서 먼저 닫는다.
// (할 일은 바꾼 뒤 0.3초 안에 자동 저장되므로 강제 종료해도 잃는 내용은 사실상 없다)
procedure CloseRunningApp();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#AppExe}', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  Sleep(500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseRunningApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  CloseRunningApp();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\StickyTodo');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('할 일과 설정 데이터도 지울까요?' + #13#10#13#10 +
                '"아니요"를 누르면 데이터는 남아서, 다시 설치하면 그대로 이어서 쓸 수 있어요.' + #13#10 +
                '(' + DataDir + ')',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
