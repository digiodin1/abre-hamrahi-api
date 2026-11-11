# Windows installation

Download `AbreHamrahiAPI-<version>-windows-x64-setup.exe` from the project's GitHub Releases. The installer includes Python, Tcl/Tk, web assets and the native file watcher. The destination machine does not need Python, pip, Git or Node.js. Target: Windows 10/11 x64; other architectures and Windows versions are not supported by this package.

Run the installer as your normal Windows user. Administrator access is not required. The default program directory is `%LOCALAPPDATA%\Programs\AbreHamrahiAPI`; a custom directory may be selected. Launch **Abre Hamrahi API** from the Start menu. The optional desktop shortcut starts the application if needed, or opens its existing local panel. The default panel address is `http://127.0.0.1:8765/`.

Sign in with your own account, select folders and save. Each installation starts without anyone else's session, folder list or backup files. Settings, encrypted session, backup index and reports are stored separately in `%LOCALAPPDATA%\AbrHamrahiAPI`. Passwords are not included in the installer; the saved session is protected by the current user's Windows DPAPI. It is not portable between Windows accounts.

Enable automatic sync, file-change sync and **Run at Windows sign-in** in Settings if desired. These are opt-in for a fresh installation. The standalone background process shows an icon in the Windows notification area, possibly inside the hidden-icons menu. Click it to open the panel, or right-click for Open and Exit. The icon is restored when Explorer restarts. Closing the browser leaves backup and the icon running; Exit stops both after bounded cleanup. Use Settings > Exit before upgrading, moving the program directory or uninstalling. Setup checks the standalone application's mutex and does not force-kill a running backup. If port 8765 is occupied, close the conflicting instance or explicitly choose another port:

```powershell
& '.\AbreHamrahiAPI.exe' --port 8877
```

An advanced installation can use `--data-dir <absolute-private-directory>` as well. Keep this directory outside the program directory. Startup and shortcuts created inside the application preserve the selected directory and port. Moving an installation requires recreating its shortcuts and re-enabling startup.

For a deliberate migration from a source installation, stop the old process first and retain its private directory. `AbreHamrahiAPI.exe --remember-profile --data-dir <existing-private-directory> --port <port>` atomically remembers that choice for ordinary Start-menu and executable launches in `%LOCALAPPDATA%\AbrHamrahiAPI\launcher.json`. Add `--startup` explicitly to register that profile's installed executable at sign-in. It does not move private files or enable automatic sync. Uninstall removes the remembered profile's startup registration and preserves its data and launcher preference. A fresh installation without a remembered profile still uses the default directory and port.

Source installations can opt into the same notification icon with `abre-hamrahi-api --tray`. Folder selection and preparing a new folder entry remain available during uploads. Saving folder changes waits for the current run to finish; selecting a folder does not silently change an active backup.

Windows limits a Run-key startup command to 260 characters. The application tries existing short path aliases first. If a very long custom install/profile path still exceeds that limit, startup is refused with an instruction to choose a shorter path, and the previous settings are preserved. Manual launch remains available. The default install and state locations avoid unusually long custom paths.

Upgrade by closing the application and running the newer setup. The installer replaces program files and preserves private state. Uninstall from Windows Settings or the Start menu. Uninstall removes the default profile's startup registration and program files; it preserves the private state directory, source folders and all cloud objects. If you configured additional private profiles, disable startup separately for each before uninstalling. Re-enabling startup after reinstall may require toggling its setting off and on.

The executable and setup are currently unsigned. SHA-256 checksums accompany release assets for integrity verification. No antivirus or Windows security settings need changing. Builds are tested on the available Windows host using an isolated directory and profile, with Python removed from PATH; this is not certification on every supported Windows build. Account login and real provider access still require internet and the user's own Abrehamrahi account.

## Rebuild

Use Windows x64 with Python 3.14 and a separate build virtual environment. From the repository root:

```powershell
python -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install -r packaging\requirements-windows.txt
.venv-build\Scripts\python.exe packaging\build_windows.py --iscc 'C:\Path\To\Inno Setup 6\ISCC.exe'
```

Use the official Inno Setup 6.7.4 compiler. Download and verify its Authenticode signature before use. Build dependencies are pinned; CI also tests source behavior on other supported Python versions. Outputs stay in `dist/windows`; intermediate files stay in `build/windows`. Only the Python package, UI assets, license and runtime dependencies are collected. Local state, `.qa`, backups, project files and developer sessions are excluded.

Run `python tests/windows_package_check.py --bundle dist/windows/AbreHamrahiAPI --installer dist/windows/AbreHamrahiAPI-<version>-windows-x64-setup.exe` to exercise the real bundle, silent installation, update and uninstall in isolated paths. The check refuses to run if this product is already installed, does not use a real cloud account, and leaves its reports in ignored `.qa/windows-package-check`.
