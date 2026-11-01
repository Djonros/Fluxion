# Installation and updates
<!-- раздел: Начало работы -->
## What it is for

To install Fluxion, move to a new version and keep your chats, document indexes and settings.

## How to start

Download **FluxionSetup.exe** from the download page and run it. No administrator rights are needed. Whether a new version is out is shown by **Help → «Check for updates…»**.

## Step by step

1. Run `FluxionSetup.exe` and press «Install». The program goes to `%LOCALAPPDATA%\Programs\Fluxion`; a shortcut appears in the Start menu, and on the desktop if you tick it.
2. At the end leave «Launch Fluxion» ticked.
3. A new version in one click: Fluxion checks for updates once a day and, when a new version is out, shows what is new in it. Press **«Update now»**: the program downloads the installer, checks it, closes, updates and opens again. «Skip this version» — do not remind about it; «Check for updates automatically» turns the check off. To check by hand — **Help → «Check for updates…»**.
4. To remove the program: Windows Settings → Apps → Fluxion → Uninstall. The uninstaller asks whether to delete your data too; answer «No» if you are going to install Fluxion again.

## What you get

The program is updated and your data stays: it is kept not in the program folder but in `%LOCALAPPDATA%\Fluxion` (chats, indexes, logs, models) and `%APPDATA%\Fluxion` (licence, cloud model, materials, plant norms).

## If it does not work

- Windows shows a SmartScreen warning: «More info» → «Run anyway».
- No «Update now» button, only «Download from the site»: this copy is not from the installer (portable or unpacked from `.7z`). Download `FluxionSetup.exe` from the site and install it — from then on updates take one click.
- «Checksum does not match»: the file was downloaded with an error or replaced; nothing is installed. Try again later or download the installer from the site.
- The installer says files are in use: close Fluxion (and `QtWebEngineProcess.exe` in Task Manager) and press «Retry».
- While an update is being installed, do not start Fluxion: it waits for the installer to finish (up to three minutes) and then opens by itself.
- You need a version without installation, e.g. on a USB stick: take the `.7z` archive from the download page, unpack it and put an empty file `portable.txt` next to `FluxionBrowserLite.exe` — all data is then kept in the program folder.
- Chats gone after moving from the `.7z` archive to the installer: earlier versions kept them in the `data` folder next to the exe. Unpack the new `.7z` over the old folder and start Fluxion from there once — the data is copied to `%LOCALAPPDATA%\Fluxion` — or copy the `data` folder there by hand.
