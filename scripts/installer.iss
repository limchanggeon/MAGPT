[Setup]
AppId=Mepiti.Local.Alpha
AppName=메피티
AppVersion=0.1.0
DefaultDirName={localappdata}\Mepiti
DefaultGroupName=메피티
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Mepiti-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\Mepiti\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\메피티"; Filename: "{app}\Mepiti.exe"
Name: "{autodesktop}\메피티"; Filename: "{app}\Mepiti.exe"

[Run]
Filename: "{app}\Mepiti.exe"; Description: "메피티 실행"; Flags: nowait postinstall skipifsilent
