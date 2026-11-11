; Compile with /DAppVersion=x.y.z. All paths are relative to this script.
#ifndef AppVersion
  #error AppVersion must be supplied by the build script
#endif
#ifndef AppIdValue
  #define AppIdValue "AbreHamrahiAPI"
#endif
#ifndef AppGroup
  #define AppGroup "Abre Hamrahi API"
#endif
[Setup]
AppId={#AppIdValue}
AppName=Abre Hamrahi API
AppVersion={#AppVersion}
AppPublisher=Abre Hamrahi API contributors
AppPublisherURL=https://github.com/digiodin1/abre-hamrahi-api
DefaultDirName={localappdata}\Programs\AbreHamrahiAPI
DefaultGroupName={#AppGroup}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\windows
OutputBaseFilename=AbreHamrahiAPI-{#AppVersion}-windows-x64-setup
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\AbreHamrahiAPI.exe
AppMutex=Local\AbreHamrahiAPI.Desktop
CloseApplications=no
RestartApplications=no
SetupLogging=yes
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Files]
Source: "..\dist\windows\AbreHamrahiAPI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\windows-install.md"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion
[Icons]
Name: "{userprograms}\{#AppGroup}\Abre Hamrahi API"; Filename: "{app}\AbreHamrahiAPI.exe"; WorkingDir: "{app}"
Name: "{userprograms}\{#AppGroup}\Uninstall"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Abre Hamrahi API"; Filename: "{app}\AbreHamrahiAPI.exe"; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
Filename: "{app}\AbreHamrahiAPI.exe"; Description: "Open Abre Hamrahi API"; Flags: nowait postinstall skipifsilent
[UninstallRun]
Filename: "{app}\AbreHamrahiAPI.exe"; Parameters: "--remove-startup"; Flags: runhidden waituntilterminated
; Never delete user settings, sessions, local files or cloud objects on uninstall.
