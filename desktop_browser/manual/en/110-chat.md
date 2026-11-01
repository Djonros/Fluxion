# Chat
<!-- раздел: Чат и документы | инструмент: - -->
## What it is for

Questions, texts, letters, translations, questions about the code of a project — in one window.

## How to start

The **«Chat»** page is the main screen of the program.

## Step by step

1. Type a question and press **Enter** («Send»). **Shift+Enter** — a new line: the field grows up to eight lines and then scrolls. A long text can be pasted whole — it is not cut.
2. Commands at the start of a message: `/компас …` — a part in KOMPAS, `/автокад path` — a drawing standards check, `/архив …` — a question to the design archive, `/расчёт …` — the engineering calculator.
3. **Ctrl+Enter** — send the message to the agent (it reads the files of the project).
4. Menu **⋮**: «New chat», «Export chat to .md…», «Clear all history…», «Forget project».
5. **«Forget project»** (menu ⋮ or «Project»): a new chat in which the model refers to nothing — neither the files of the project nor earlier messages; «My style» is turned off. Nothing is deleted: the index stays on disk. To bring the project back, choose the folder: «Project» → «Choose folder…».

## Chat history

Every chat is saved by itself after each answer. The list of past chats is on the left, under «Chat» and «Project», newest first: «Today», «Yesterday», «Earlier».

- **Go back to a chat** — click it: the messages, the sources under the answers, the «Agent: files» and «Plan» chips, the model — all as it was, and you can ask on. If the chat was held in another project folder, Fluxion asks whether to open it.
- **New chat** — the **+** button above the list or **Ctrl+N**. The previous one stays in the history.
- **Name** — the first question. To rename: double-click the chat or right-click → «Rename…». An empty name goes back to the first question.
- **Search** — the «Search chats» field looks in the names and in the text of the messages.
- **Delete** — right-click → «Delete» or the **Delete** key; Fluxion asks for confirmation. Several chats at once: select them with **Ctrl** or **Shift**. **«Clear all history…»** (right-click on the list or menu ⋮) deletes all chats.
- The history is kept on this computer: `%LOCALAPPDATA%\Fluxion\data\chats.json` (in the portable version — `data\chats.json` next to the program), up to the last 500 chats. It is never sent to the internet.
- **«Forget project»** does not erase the history: the previous chat stays in the list with its folder, the new one goes without a project. Deleting chats does not touch the project and its index.

## What you get

The answer appears as it is typed. Conversations are kept in the list of chats on the left.

## If it does not work

- «Engine not connected» — download a model (Ctrl+M).
- Fluxion answers in the language of the question: ask in English — it answers in English.
- The answer refers to files that have nothing to do with the question — «Forget project».
- In the address bar of the built-in browser a query is searched in DuckDuckGo; through your own SearXNG — only if «Edit» → «Extended web search» is on.
