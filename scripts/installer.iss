; 메피티 Windows 설치 프로그램 (Inno Setup 6)
;
; 마법사 흐름: 설치 폴더 -> AI 모델 선택(Gemini·Claude·ChatGPT/2B/8B/나중에) -> Ollama 설치(로컬 모델을 고르고 없을 때만) -> 설치
; 클라우드(Gemini 무료)가 기본 추천이다. 설치할 것이 없고, 첫 실행에서 Google API 키를 넣는다.
; 로컬 모델 파일(2.7~4.8GB)은 여기서 받지 않는다. 고른 값을 %USERPROFILE%\.mepiti\setup.json에 남기면
; 앱이 첫 실행에서 진행률과 함께 받고 사용 모델로 정한다(mepiti/models.py). 모델을 받으려면 Ollama가
; 실행 중이어야 하고, 대용량 진행률은 앱 화면이 더 잘 보여 주기 때문이다.
; 모델은 나중에 앱의 설정에서 언제든 바꿀 수 있다.

#define AppVersion "0.3.3"

[Setup]
AppId=Mepiti.Local.Alpha
AppName=메피티
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Mepiti
DefaultGroupName=메피티
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Mepiti-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon\Mepiti.ico
UninstallDisplayIcon={app}\Mepiti.exe

; 한국어 메시지 파일이 설치된 Inno Setup에 있을 때만 쓴다. 없으면 기본(영어) 버튼 문구로 빌드된다.
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif

[Files]
Source: "..\dist\Mepiti\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\메피티"; Filename: "{app}\Mepiti.exe"
Name: "{autodesktop}\메피티"; Filename: "{app}\Mepiti.exe"

[Run]
Filename: "{app}\Mepiti.exe"; Description: "메피티 실행"; Flags: nowait postinstall skipifsilent

[Code]
const
  OllamaSetupUrl = 'https://ollama.com/download/OllamaSetup.exe';

var
  ModelPage: TInputOptionWizardPage;
  OllamaPage: TInputOptionWizardPage;
  DownloadPage: TDownloadWizardPage;
  NeedOllama: Boolean;
  OllamaDownloaded: Boolean;
  Updating: Boolean;       // 이미 쓰던 데이터가 있다 = 업데이트. 모델을 다시 묻지 않고 설정을 건드리지 않는다.

// 데이터는 설치 폴더가 아니라 %USERPROFILE%\.mepiti에 있어 설치·업데이트·제거로 지워지지 않는다.
// 키는 Windows 자격 증명 관리자에 있다. 새 버전을 처음 켤 때 앱이 데이터를 자동 백업한다(mepiti/backup.py).
function HasExistingData: Boolean;
begin
  Result := FileExists(ExpandConstant('{%USERPROFILE}\.mepiti\mepiti.sqlite3'));
end;

// NVIDIA GPU의 VRAM(MiB). nvidia-smi가 없거나 실패하면 0.
function DetectVramMiB: Integer;
var
  ResultCode: Integer;
  Lines: TArrayOfString;
  OutFile: String;
begin
  Result := 0;
  OutFile := ExpandConstant('{tmp}\mepiti-vram.txt');
  if Exec(ExpandConstant('{cmd}'),
          '/C nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits > "' + OutFile + '" 2>nul',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
    if LoadStringsFromFile(OutFile, Lines) and (GetArrayLength(Lines) > 0) then
      Result := StrToIntDef(Trim(Lines[0]), 0);
end;

function OllamaInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe'));
end;

// 모델 선택지 순서. setup.json에 남기는 값과 앱(mepiti/models.py)의 id가 짝이다.
const
  ChoiceCloud = 0;
  ChoiceClaude = 1;
  ChoiceOpenAI = 2;
  ChoiceLight = 3;
  ChoiceQuality = 4;

function LocalModelChosen: Boolean;
begin
  Result := (ModelPage.SelectedValueIndex = ChoiceLight) or (ModelPage.SelectedValueIndex = ChoiceQuality);
end;

procedure InitializeWizard;
var
  Vram: Integer;
  LightFit, QualityFit: String;
begin
  Vram := DetectVramMiB;
  LightFit := '';
  QualityFit := '';
  if Vram >= 7500 then
    QualityFit := ' (이 PC에 맞음)'
  else if Vram > 0 then
    LightFit := ' (이 PC에 맞음)';
  ModelPage := CreateInputOptionPage(wpSelectDir,
    'AI 모델 선택', '답변에 쓸 AI를 고르세요.',
    '나중에 앱의 설정에서 언제든 바꿀 수 있습니다.',
    True, False);
  ModelPage.Add('클라우드 · 무료 (Gemini) — 추천. 설치할 것이 없고 그래픽카드와 상관없습니다. ' +
                '처음 실행할 때 Google API 키(무료)를 넣으면 됩니다. 질문과 캐릭터 정보가 Google로 전송됩니다(만 18세 이상).');
  ModelPage.Add('클라우드 · Claude (유료) — 선불 크레딧이 있는 Anthropic API 키가 필요합니다. 질문 1번에 대략 8~40원.');
  ModelPage.Add('클라우드 · ChatGPT (유료) — 선불 크레딧이 있는 OpenAI API 키가 필요합니다. 질문 1번에 대략 1~85원.');
  ModelPage.Add('2B · 가벼움' + LightFit + ' — 내 PC에서만 실행. 내려받기 2.7GB, 메모리 약 2.4GB. VRAM 4GB(GTX 1650 등)에서 게임과 함께 쓰기 좋습니다.');
  ModelPage.Add('8B · 품질' + QualityFit + ' — 내 PC에서만 실행. 내려받기 4.8GB, 메모리 약 5.2GB. VRAM 8GB 이상 권장. 2B보다 3~4배 느립니다.');
  ModelPage.Add('나중에 고르기');
  ModelPage.SelectedValueIndex := ChoiceCloud;
  Updating := HasExistingData;

  NeedOllama := not OllamaInstalled;
  OllamaPage := CreateInputOptionPage(ModelPage.ID,
    'Ollama 설치', 'AI 답변에는 Ollama가 필요합니다.',
    '로컬 모델을 고르셨습니다. Ollama는 AI 모델을 이 PC에서 돌리는 무료 프로그램입니다. 공식 설치 파일(약 1.5GB)을 내려받아 함께 설치합니다. ' +
    '끄면 메피티만 설치되고, 나중에 ollama.com에서 직접 설치할 수 있습니다.',
    False, False);
  OllamaPage.Add('Ollama 함께 설치 (권장)');
  OllamaPage.Values[0] := True;

  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), nil);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // 클라우드·나중에를 고르면 Ollama가 필요 없다. 로컬은 나중에 앱 설정에서 골라도 된다.
  Result := ((PageID = OllamaPage.ID) and not (NeedOllama and LocalModelChosen))
            or (Updating and ((PageID = ModelPage.ID) or (PageID = OllamaPage.ID)));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and NeedOllama and LocalModelChosen and OllamaPage.Values[0] then
  begin
    DownloadPage.Clear;
    DownloadPage.Add(OllamaSetupUrl, 'OllamaSetup.exe', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        OllamaDownloaded := True;
      except
        // 받지 못해도 메피티 설치는 계속한다. 앱 첫 실행 화면이 Ollama 설치를 안내한다.
        if not DownloadPage.AbortedByUser then
          SuppressibleMsgBox('Ollama를 내려받지 못했습니다. 메피티는 계속 설치합니다. ' +
            '나중에 ollama.com에서 설치하세요.' + #13#10#13#10 + GetExceptionMessage,
            mbError, MB_OK, IDOK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Choice: String;
  DataDir: String;
begin
  if CurStep <> ssPostInstall then
    exit;
  if Updating then
    exit;                  // 업데이트: 쓰던 모델·설정을 그대로 둔다

  if OllamaDownloaded then
  begin
    WizardForm.StatusLabel.Caption := 'Ollama 설치 중...';
    if not Exec(ExpandConstant('{tmp}\OllamaSetup.exe'), '/SILENT /SUPPRESSMSGBOXES /NORESTART', '',
                SW_SHOW, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      SuppressibleMsgBox('Ollama 설치가 끝나지 않았습니다. 나중에 ollama.com에서 설치하세요.', mbError, MB_OK, IDOK);
  end;

  case ModelPage.SelectedValueIndex of
    ChoiceCloud: Choice := 'cloud';
    ChoiceClaude: Choice := 'claude';
    ChoiceOpenAI: Choice := 'openai';
    ChoiceLight: Choice := 'light';
    ChoiceQuality: Choice := 'quality';
  else
    Choice := 'later';
  end;
  DataDir := ExpandConstant('{%USERPROFILE}\.mepiti');
  ForceDirectories(DataDir);
  SaveStringToFile(DataDir + '\setup.json', '{"model_choice":"' + Choice + '"}', False);
end;
