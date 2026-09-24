# VS Code extension

Чат-сайдбар, agent mode и индексация проекта прямо в VS Code. Требуется запущенный API-сервер (`fluxion-server.bat`).

## Установка

=== "Батник"

    ```bat
    fluxion-vscode.bat
    ```

=== "Вручную"

    См. `extension/README.md` в корне репозитория.

## Команды

- **Fluxion: Open Chat** — чат-сайдбар со streaming
- **Fluxion: Run Agent** — запуск агента из VS Code
- **Fluxion: Index Project** — индексация workspace в ChromaDB
- **Fluxion: Server Status** — проверка сервера

## Связка с сервером

Extension общается с FastAPI-сервером (по умолчанию `http://localhost:8765`):

- Чат — `POST /api/chat` (SSE streaming)
- Агент — `POST /api/agent/run`
- Индексация — `POST /api/rag/index`

Полный список endpoints — в [API reference](../api.md).
