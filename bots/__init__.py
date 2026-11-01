"""Fluxion in messengers: Telegram, MAX and VK bots that run inside Fluxion on the user's PC.

Nothing runs on a server: every adapter asks its messenger for new messages (long
polling) with the user's own bot token, the answer is made on the PC — the model,
the project documents, KOMPAS-3D — and sent back, files too.

- ``config``  — the tokens, the allowed users and folders (%APPDATA%\\Fluxion\\bots.json)
- ``base``    — the message of any messenger, the adapter every messenger implements
- ``telegram``, ``maxbot``, ``vk`` — the adapters
- ``brain``   — what the bot does with a message: the allow-list, commands, the answer
- ``jobs``    — the long engineering jobs (report, laser package, purchase list, comparison)
- ``runner``  — a thread per messenger: poll, answer, poll again
"""
