"""«Связь с телефоном»: Fluxion Mobile asks this PC over the home network.

The PC answers on an OpenAI-compatible API (``GET /v1/models``, ``POST /v1/chat/completions``)
on port 8766 while Fluxion is open, only to a phone that knows the pairing code (``Bearer``).
A question with a photo goes to the vision model of the PC (Qwen2.5-VL in the embedded
llama.cpp, or Ollama); a text question goes to the chat model of the window.

- ``config`` — on/off, the port and the code (%APPDATA%\\Fluxion\\phone.json)
- ``vision`` — the vision model: download, load, answer
- ``server`` — the HTTP server
"""
