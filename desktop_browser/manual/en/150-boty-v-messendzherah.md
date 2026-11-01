# Messenger bots
<!-- раздел: Чат и документы | инструмент: bots -->
## What it is for

Ask Fluxion from your phone — from Telegram, MAX or VK: a question, a calculation, a part in KOMPAS, an assembly report, a laser package. The bot works on your computer while Fluxion is open: no server is needed, no ports to open.

## How to start

Menu **Tools → «Messenger bots…»**. When a bot is on, the status bar shows a **«Bots: Telegram ✓»** button — it opens the same window.

## Step by step

1. Get a bot token:
   - **Telegram** — write to @BotFather: `/newbot`, a name, the bot's address; it sends the token.
   - **MAX** — the bot is created by @MasterBot; for now this is open to organisations verified on the MAX for business platform.
   - **VK** — in the community: Manage → API usage → «Create key» with access to messages and documents; there also turn on Long Poll API with the event «Incoming message» and turn on the community's «Messages».
2. Paste the token on the messenger's tab and press **«Check»** — Fluxion tells the bot's name or what is wrong.
3. If Telegram does not open, set an HTTP proxy: `http://host:port`.
4. Tick **«Enable the bot»**, add the **folders open to the bots** — the bot takes assemblies and files only from them — and press **«Save»**.
5. Write to the bot. To a stranger the bot answers only with a 6-digit code. Type the code into **«Confirm»** — from then on the bot answers them. To remove a person — «Remove from the list».
6. For the bot to answer without you, tick **«Start Fluxion minimized at Windows sign-in»**.

Bot commands (the bot answers in Russian, as Fluxion's reports do):

| Command | What it does |
|---|---|
| a plain question | an answer, as in the program's chat; a voice message too |
| `/расчёт шов катет 6 длиной 200, сила 30 кН` | engineering calculator |
| `/компас втулка 30 на 20 длиной 50, бронза` | a part in KOMPAS-3D, the .m3d file in reply |
| `/архив рама 600.000` | search of the design archive (Pro) |
| `/отчёт Рама 600.000`, `/смета …` | bill of materials and metal or the cost in Excel (Pro) |
| `/лазер Рама 600.000` | flat-pattern DXF by thickness as a .zip (Pro) |
| `/сравнить Рама изм1 ; Рама изм2` | what changed between versions (Pro) |
| `/заявка Рама × 2 ; Стол × 5` | what to buy (Pro) |
| `/файл Рама.pdf` | send a file from the bot's folders |
| `/статус`, `/новый`, `/уведомления`, `/отмена`, `/помощь` | status, a new conversation, notify when jobs in the program end (English: `/status`, `/new`, `/notify`, `/cancel`, `/help`) |

The English names work too: `/calc`, `/kompas`, `/archive`, `/report`, `/estimate`, `/laser`, `/compare`, `/purchase`, `/file`. An assembly can be given by its full path or its file name — the bot looks for it in its folders.

## What you get

Answers come to the messenger, files as documents. Conversations with the bot are kept in Fluxion's chat history marked with the messenger and the name: «[Telegram · Иван] …». Files sent to the bot go to `Documents\Fluxion\Из мессенджеров`.

## If it does not work

- **«Токен не принят» (token not accepted)** — the token was copied in part or the bot was deleted: take a new one.
- **«Telegram недоступен» (Telegram unavailable)** — no network or Telegram is blocked: set an HTTP proxy.
- **Voice messages are not recognised** — download a speech recognition model (menu «Models» → «Speech recognition»); voice messages of Telegram, MAX and VK also need ffmpeg on PATH.
- **«Функция Pro»** — report, estimate, laser, comparison, purchase request and archive work with Pro; questions, calculations and parts in KOMPAS are free.
- **The assembly report does not ask about warnings**: in a report from the bot they are marked «not checked» — mark them in the program.
- **«Пропущено» (skipped) in the laser package** — those parts need a hand in KOMPAS: make them from the program.
- Messages and files pass through the messenger's servers. Do not send through the bot what must not leave the plant.
- MAX was checked on a test server only: if «Check» fails, write to us.
