# Personal assistant
<!-- раздел: Чат и документы | инструмент: assistant | pro -->
## What it is for

Fluxion notices what happens on your computer and answers questions about your work: "what did I do yesterday", "how many hours went into 792720 this week", "which models changed in the project folder". It can tell you at once that a new file appeared in a folder — on the screen or in Telegram. Everything is kept on this computer only.

## How to start

Menu **Связь (Connect) → "Personal assistant…"**. A Pro feature.

## Step by step

1. Tick **"Turn on the personal assistant"**.
2. On the **"Sources"** tab turn on what you need. Each source says what it reads, what it records and what it never does.
   - **Folders** — add the folders to watch (a project, "Incoming", the archive folder). "Which files" may stay empty or hold a mask: `*.a3d;*.cdw;*.pdf`. The content of files is never read — only the name, size and time.
   - **Activity** — which window is in front and how long you worked in it. Keystrokes, screenshots and the text of windows are not recorded, nor private browser windows. Programs in "Do not record programs" are not recorded at all; window titles can be turned off — then only the program name is kept.
3. **"The cloud model may see these records"** is off by default: when a cloud model is chosen in the chat, the journal is not given to it.
4. On the **"Notifications"** tab add a rule: source, event, mask, action. For example: Folders — new file — `*.a3d` — notification and a message to the bots. For Telegram messages turn on `/уведомления` in the bot.
5. Tick **"Start Fluxion minimized when Windows starts"** so the assistant works from the morning, and press **"Save"**.
6. Ask in the chat in plain words: "what did I do yesterday?", "which files changed this week?". In a bot: `/сводка`, `/сводка вчера`, `/сводка неделя`.

While the assistant is on, the window's close button hides Fluxion to the icon by the clock. Right-click the icon for **"Pause for 1 hour"** (nothing is recorded) and **"Exit"**.

## What you get

An answer in the chat from the journal: time in each program and on each document, the first and last action of the day, the new and changed files. Notifications about files by your rules. The **"What Fluxion knows"** tab shows the whole journal with search; records can be deleted for today or all at once. Old records are deleted by themselves after the set number of days (30 by default).

## If it does not work

- **The menu item asks for Pro** — the personal assistant is part of Fluxion Pro: menu "Help" → "Licence…".
- **The chat does not answer from the journal** — check that the assistant is on and the source works ("Working" under it on the "Sources" tab). Ask with words like "what did I do…", "which files…".
- **The answer says the journal is not given to the cloud model** — choose a local model or tick "The cloud model may see these records" for the source.
- **"Folders" says "no folder"** — the folder was renamed or the network drive is not connected: remove it and add it again.
- **A network folder shows changes late** — this happens with network drives; the changes still reach the journal.
