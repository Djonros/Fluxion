"use strict";

const vscode = require("vscode");

/**
 * @type {vscode.WebviewPanel | undefined}
 */
let chatPanel;

/**
 * @param {vscode.ExtensionContext} context
 */
function activate(context) {
  const serverUrl = vscode.workspace
    .getConfiguration("fluxion")
    .get("serverUrl", "http://localhost:8765");

  // ── Fluxion: Open Chat ────────────────────────────────────────────

  context.subscriptions.push(
    vscode.commands.registerCommand("fluxion.chat", () => {
      if (chatPanel) {
        chatPanel.reveal(vscode.ViewColumn.Two);
        return;
      }

      chatPanel = vscode.window.createWebviewPanel(
        "fluxionChat",
        "Fluxion Chat",
        vscode.ViewColumn.Two,
        { enableScripts: true, retainContextWhenHidden: true }
      );

      chatPanel.webview.html = getChatHtml(serverUrl);

      chatPanel.onDidDispose(() => {
        chatPanel = undefined;
      });
    })
  );

  // ── Fluxion: Index Project ────────────────────────────────────────

  context.subscriptions.push(
    vscode.commands.registerCommand("fluxion.indexWorkspace", async () => {
      const folders = vscode.workspace.workspaceFolders;
      if (!folders) {
        vscode.window.showWarningMessage("Fluxion: No workspace folder open.");
        return;
      }

      const path = folders[0].uri.fsPath;
      vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "Fluxion: Indexing project...",
          cancellable: false,
        },
        async () => {
          try {
            const resp = await fetch(
              `${serverUrl}/api/rag/index`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path }),
              }
            );
            const data = await resp.json();
            if (data.error) {
              vscode.window.showErrorMessage(`Fluxion: ${data.error}`);
            } else {
              vscode.window.showInformationMessage(
                `Fluxion: Indexed ${data.chunks_indexed} chunks.`
              );
            }
          } catch (err) {
            vscode.window.showErrorMessage(
              `Fluxion: Server not reachable. Is it running on ${serverUrl}?`
            );
          }
        }
      );
    })
  );

  // ── Fluxion: Server Status ────────────────────────────────────────

  context.subscriptions.push(
    vscode.commands.registerCommand("fluxion.health", async () => {
      try {
        const resp = await fetch(`${serverUrl}/api/health`);
        const data = await resp.json();
        const status =
          `Fluxion Server\n` +
          `  Status:    ${data.status}\n` +
          `  Model:     ${data.model}\n` +
          `  Ollama:    ${data.ollama_available ? "online" : "offline"}\n` +
          `  RAG:       ${data.rag_enabled ? "enabled" : "disabled"} (${data.rag_chunks} chunks)\n` +
          `  Web:       ${data.web_enabled ? "enabled" : "disabled"}`;
        vscode.window.showInformationMessage(status);
      } catch (err) {
        vscode.window.showErrorMessage(
          `Fluxion: Server not reachable on ${serverUrl}. Run: python -m uvicorn server.app:create_app --factory --port 8765`
        );
      }
    })
  );

  // ── Fluxion: Run Agent ────────────────────────────────────────────

  context.subscriptions.push(
    vscode.commands.registerCommand("fluxion.agentRun", async () => {
      const task = await vscode.window.showInputBox({
        prompt: "Describe the task for the Fluxion agent",
        placeHolder: "e.g. Read main.py and explain what it does",
      });
      if (!task) return;

      const allowWrite = vscode.workspace
        .getConfiguration("fluxion")
        .get("agentAllowWrite", false);

      vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `Fluxion Agent: ${task.slice(0, 50)}...`,
          cancellable: false,
        },
        async () => {
          try {
            const resp = await fetch(`${serverUrl}/api/agent/run`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                task,
                allow_write: allowWrite,
              }),
            });
            const data = await resp.json();

            if (data.success) {
              vscode.window.showInformationMessage(
                `Fluxion Agent completed (${data.iterations_used} iterations).`
              );
            } else {
              vscode.window.showWarningMessage(
                `Fluxion Agent did not complete (${data.iterations_used} iterations).`
              );
            }

            // Show result in a new document
            const doc = await vscode.workspace.openTextDocument({
              content: formatAgentResult(data),
              language: "markdown",
            });
            vscode.window.showTextDocument(doc, vscode.ViewColumn.Two);
          } catch (err) {
            vscode.window.showErrorMessage(
              `Fluxion: Server not reachable on ${serverUrl}.`
            );
          }
        }
      );
    })
  );
}

function deactivate() {}

/**
 * @param {string} data
 */
function formatAgentResult(data) {
  let text = `# Agent Result\n\n`;
  text += `**Success:** ${data.success}\n`;
  text += `**Iterations:** ${data.iterations_used}\n\n`;
  text += `## Answer\n\n${data.final_answer}\n\n`;

  if (data.steps && data.steps.length > 0) {
    text += `## Steps\n\n`;
    for (const step of data.steps) {
      text += `### Iteration ${step.iteration}\n\n`;
      if (step.thought) text += `**Thought:** ${step.thought}\n\n`;
      if (step.tool_name) text += `**Tool:** \`${step.tool_name} ${step.tool_args}\`\n\n`;
      if (step.observation) text += `\`\`\`\n${step.observation}\n\`\`\`\n\n`;
    }
  }

  return text;
}

/**
 * @param {string} serverUrl
 */
function getChatHtml(serverUrl) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Fluxion Chat</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: var(--vscode-font-family, sans-serif);
      font-size: var(--vscode-font-size, 13px);
      background: var(--vscode-editor-background);
      color: var(--vscode-editor-foreground);
      display: flex;
      flex-direction: column;
      height: 100vh;
    }
    #messages {
      flex: 1;
      overflow-y: auto;
      padding: 8px;
    }
    .msg {
      margin-bottom: 12px;
      padding: 8px 12px;
      border-radius: 6px;
      white-space: pre-wrap;
      word-wrap: break-word;
    }
    .msg-user {
      background: var(--vscode-input-background);
      border: 1px solid var(--vscode-input-border);
    }
    .msg-assistant {
      background: var(--vscode-textBlockQuote-background);
    }
    .msg-meta {
      font-size: 11px;
      opacity: 0.7;
      margin-bottom: 4px;
    }
    #input-area {
      display: flex;
      gap: 4px;
      padding: 8px;
      border-top: 1px solid var(--vscode-panel-border);
    }
    #query-input {
      flex: 1;
      background: var(--vscode-input-background);
      color: var(--vscode-input-foreground);
      border: 1px solid var(--vscode-input-border);
      border-radius: 4px;
      padding: 8px;
      font-family: inherit;
      font-size: inherit;
      outline: none;
    }
    #query-input:focus {
      border-color: var(--vscode-focusBorder);
    }
    button {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      border: none;
      border-radius: 4px;
      padding: 6px 16px;
      cursor: pointer;
      font-size: inherit;
    }
    button:hover {
      background: var(--vscode-button-hoverBackground);
    }
    #agent-toggle {
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
      font-size: 11px;
      padding: 4px 8px;
    }
    #agent-toggle.active {
      background: var(--vscode-statusBarItem-warningBackground);
      color: var(--vscode-statusBarItem-warningForeground);
    }
    #status-bar {
      padding: 4px 8px;
      font-size: 11px;
      opacity: 0.7;
      border-bottom: 1px solid var(--vscode-panel-border);
    }
    #logo {
      font-size: 14px;
      font-weight: 700;
      margin-right: 4px;
      background: linear-gradient(90deg, #a78bfa, #22d3ee);
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
    }
  </style>
</head>
<body>
  <div id="status-bar">
    <span id="logo" title="Fluxion">&#8734;</span><span id="status-text">Connecting to ${serverUrl}...</span>
    <button id="agent-toggle">Agent: OFF</button>
  </div>
  <div id="messages"></div>
  <div id="input-area">
    <input id="query-input" type="text" placeholder="Ask Fluxion..." />
    <button id="send-btn">Send</button>
  </div>

  <script>
    const SERVER = "${serverUrl}";
    let agentMode = false;
    const messages = document.getElementById("messages");
    const input = document.getElementById("query-input");
    const sendBtn = document.getElementById("send-btn");
    const agentToggle = document.getElementById("agent-toggle");
    const statusText = document.getElementById("status-text");

    // ── Health check ──────────────────────────────────────────
    fetch(SERVER + "/api/health")
      .then(r => r.json())
      .then(d => {
        statusText.textContent = d.model + " | ollama: " + (d.ollama_available ? "online" : "offline") + " | rag: " + d.rag_chunks + " chunks";
      })
      .catch(() => {
        statusText.textContent = "Server offline. Start: python -m uvicorn server.app:create_app --factory --port 8765";
      });

    // ── Agent toggle ──────────────────────────────────────────
    agentToggle.addEventListener("click", () => {
      agentMode = !agentMode;
      agentToggle.textContent = "Agent: " + (agentMode ? "ON" : "OFF");
      agentToggle.classList.toggle("active", agentMode);
      input.placeholder = agentMode ? "Describe a task for the agent..." : "Ask Fluxion...";
    });

    // ── Send ──────────────────────────────────────────────────
    function send() {
      const query = input.value.trim();
      if (!query) return;
      input.value = "";

      addMessage("user", query);

      if (agentMode) {
        sendAgent(query);
      } else {
        sendChat(query);
      }
    }

    sendBtn.addEventListener("click", send);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });

    // ── Chat (streaming) ──────────────────────────────────────
    function sendChat(query) {
      const msgEl = addMessage("assistant", "");
      const metaEl = msgEl.querySelector(".msg-meta");

      fetch(SERVER + "/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, stream: true }),
      })
        .then(r => {
          const reader = r.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";

          function read() {
            reader.read().then(({ done, value }) => {
              if (done) return;
              buffer += decoder.decode(value, { stream: true });

              const lines = buffer.split("\\n");
              buffer = lines.pop();

              for (const line of lines) {
                if (line.startsWith("event: meta")) {
                  continue;
                }
                if (line.startsWith("data: ")) {
                  try {
                    const token = JSON.parse(line.slice(6));
                    msgEl.querySelector(".msg-body").textContent += token;
                  } catch {}
                }
                if (line.startsWith("event: done")) {
                  if (metaEl) metaEl.textContent = "chat";
                }
              }
              read();
            });
          }
          read();
        })
        .catch(() => {
          msgEl.querySelector(".msg-body").textContent = "Error: Server not reachable.";
        });
    }

    // ── Agent ─────────────────────────────────────────────────
    function sendAgent(task) {
      const msgEl = addMessage("assistant", "Running agent...");

      fetch(SERVER + "/api/agent/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task, allow_write: false }),
      })
        .then(r => r.json())
        .then(data => {
          let text = data.final_answer || "(no answer)";
          text += "\\n\\n--- " + data.iterations_used + " iterations, success: " + data.success + " ---";
          msgEl.querySelector(".msg-body").textContent = text;
          if (data.steps) {
            for (const step of data.steps) {
              if (step.tool_name && step.observation) {
                addMessage("assistant", "[" + step.tool_name + "] " + step.observation.slice(0, 200));
              }
            }
          }
        })
        .catch(() => {
          msgEl.querySelector(".msg-body").textContent = "Error: Server not reachable.";
        });
    }

    // ── Helpers ───────────────────────────────────────────────
    function addMessage(role, text) {
      const div = document.createElement("div");
      div.className = "msg msg-" + role;
      div.innerHTML = '<div class="msg-meta">' + role + '</div><div class="msg-body">' + escapeHtml(text) + "</div>";
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
      return div;
    }

    function escapeHtml(s) {
      const d = document.createElement("div");
      d.textContent = s;
      return d.innerHTML;
    }
  </script>
</body>
</html>`;
}

module.exports = { activate, deactivate };
