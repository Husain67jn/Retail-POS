; ===========================================================================
;  Inno Setup script for Mughal Electric Store (RetailPOS)
; ---------------------------------------------------------------------------
;  Produces a single Setup.exe that:
;    1. Installs the PyInstaller onedir bundle (dist\RetailPOS\) to Program Files.
;    2. Silently installs the Microsoft Visual C++ 2015-2022 x64 Redistributable
;       (vc_redist.x64.exe) so Qt's runtime dependencies are present system-wide
;       on a fresh Windows machine -- eliminating the "DLL load failed while
;       importing QtWidgets" crash.
;    3. Creates Start Menu (and optional Desktop) shortcuts.
;
;  BUILD ORDER:
;    1. python build_exe.py        (creates dist\RetailPOS\ and downloads
;                                    installer\redist\vc_redist.x64.exe)
;    2. Open this file in Inno Setup 6 and click Compile (or run:
;       "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss)
;
;  The compiled installer is written to installer\Output\.
; ===========================================================================

#define MyAppName "Mughal Electric Store"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Mughal Electric Store"
#define MyAppExeName "RetailPOS.exe"
#define MyDistDir "dist\RetailPOS"
#define MyRedist "installer\redist\vc_redist.x64.exe"

[Setup]
; AppId uniquely identifies this application for upgrades/uninstall. Do NOT
; change it once the app has been shipped.
AppId={{7C3B1F2E-9A44-4E5C-8B77-2D6E1A0F5C31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer\Output
OutputBaseFilename=MughalElectricStore-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The app and the VC++ redistributable are 64-bit and require admin rights
; (Program Files + a system-wide runtime install).
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The entire PyInstaller onedir output (RetailPOS.exe + _internal\...).
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

; Bundle vc_redist.x64.exe only if it was fetched by build_exe.py. Guarding with
; FileExists keeps the script compilable even when the redist is not present.
#if FileExists(MyRedist)
Source: "{#MyRedist}"; DestDir: "{tmp}"; Flags: deleteafterinstall
#endif

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Silently install the VC++ runtime BEFORE offering to launch the app. Exit
; code 3010 (reboot required) is treated as success by Inno's default handling.
#if FileExists(MyRedist)
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Installing Microsoft Visual C++ Runtime..."; Flags: waituntilterminated
#endif
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
