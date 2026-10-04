# Fluxion — VS Code Extension

Local AI coding assistant for VS Code. Connects to the Fluxion API server.

## Install

### 1. Start the Fluxion server

```bash
# In the project root:
pip install -r server/requirements.txt
python -m uvicorn server.app:create_app --factory --port 8765
```

### 2. Install the extension

```bash
# Copy to VS Code extensions folder:
# Windows:
xcopy /E /I extension "%USERPROFILE%\.vscode\extensions\fluxion"

# Linux/macOS:
cp -r extension ~/.vscode/extensions/fluxion
```

Or press `F5` in VS Code with this folder open to launch an Extension Development Host.

### 3. Use

- `Ctrl+Shift+P` → **Fluxion: Open Chat** — chat sidebar with streaming
- `Ctrl+Shift+P` → **Fluxion: Index Project** — index current workspace into RAG
- `Ctrl+Shift+P` → **Fluxion: Server Status** — check server health
- `Ctrl+Shift+P` → **Fluxion: Run Agent** — run ReAct agent on a task

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `fluxion.serverUrl` | `http://localhost:8765` | Fluxion API server URL |
| `fluxion.agentAllowWrite` | `false` | Allow agent to write/edit files |

## Features

- **Streaming chat** — SSE-based live token streaming
- **Agent mode** — toggle in chat to switch from Q&A to autonomous ReAct agent
- **RAG indexing** — index workspace for code-aware answers
- **Agent results** — formatted Markdown output with all steps

## Requirements

- [Fluxion API server](../server/) running on `localhost:8765` (`fluxion-server.bat`)
- A model in Fluxion: the server uses the same engine as the app — the built-in
  llama.cpp with a model downloaded on the «Модели» page. Ollama is optional.
