# Privacy Policy

This policy covers the Fluxion desktop application and the website
[djonros.github.io/Fluxion](https://djonros.github.io/Fluxion/).

**We do not collect personal data.** Neither the application nor the website
uses telemetry, analytics or tracking, and nothing is sent to the developer
automatically.

[Русская версия](privacy.md)

## What stays on your computer

- **Conversations and prompts** are processed by a local model (llama.cpp
  inside the application). Chat history is stored in
  `%LOCALAPPDATA%\Fluxion\data`.
- **Your code and documents**: search indexes are stored in the same folder.
  The agent reads and writes files only in the project folder you choose.
- **Models** (GGUF files) are stored in `%LOCALAPPDATA%\Fluxion\models`.
- **Settings** are stored in `%LOCALAPPDATA%\Fluxion\config`; the licence, the
  cloud model settings and the materials catalog in `%APPDATA%\Fluxion`.
- **The Pro licence** is verified locally (Ed25519 signature), without an
  activation server.

In portable mode (a `portable.txt` file next to `FluxionBrowserLite.exe`) all
of this is stored in the application folder instead.

## When the application uses the internet

| Action | What is sent |
|--------|--------------|
| Update check: automatic, at most once a day (can be turned off with «Проверять обновления автоматически» / "Check for updates automatically"), or Help → Check for updates | A request to GitHub (`api.github.com`) for the latest version. GitHub sees your IP address; no personal data or content is sent |
| "Update now" | Download of the installer and its checksum from GitHub Releases |
| Downloading a model from the catalog | An HTTPS request to Hugging Face (`huggingface.co`): the file name and your IP address |
| Web search (built-in, default) | The search query text, to DuckDuckGo; your project code is not sent |
| Advanced web search (if enabled) | The query, to your own SearXNG instance, which queries search engines |
| Cloud API backend (optional, Pro) | Prompts go to the cloud provider you configure yourself |
| Installing the training environment | Package downloads from PyPI / winget (the Full build installs offline) |
| Crash report | Nothing: "Send by email" opens a message in your own email program, and you decide whether to send it |

Your conversations leave your computer only if you configure a cloud API
backend yourself (Pro).

## The website

The website is a static page hosted on GitHub Pages. It has no user accounts,
no forms, no cookies, no analytics or tracking scripts, and no advertising.
The website itself does not collect or store any personal information.

When the page is opened, technical data (your IP address and browser
information) is received by:

- **GitHub**: the hosting (GitHub Pages) and `api.github.com`, which the page
  asks for the latest release so that the download buttons point to the
  current version. Downloads come from GitHub Releases. See the
  [GitHub Privacy Statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement).
- **Google Fonts** (`fonts.googleapis.com`, `fonts.gstatic.com`), which serves
  the page fonts. See the [Google Privacy Policy](https://policies.google.com/privacy).

## Buying Fluxion Pro

The "Buy Pro" button opens an email in your own email program. If you send it,
the developer receives what you write (usually your name and email address).
The email address is used only to send you the licence key and to answer your
message. It is not shared with anyone and is not used for marketing.

## What Fluxion does not do

- No telemetry or analytics, in the application or on the website.
- No accounts are needed to use the application.
- Nothing is sent automatically: not your code, conversations or indexes.

## Contact

Privacy questions: [djonros@gmail.com](mailto:djonros@gmail.com).

Effective date: 27 October 2026.
