# Cloud model via API
<!-- раздел: Агент и обучение | инструмент: models | pro -->
## What it is for

The chat and the agent answer through a provider's model in the cloud: fast even on a weak computer, without downloading weights. Besides OpenRouter, Groq, Together and OpenAI there are Russian providers: ProxyAPI, VseGPT, AITunnel, BotHub, GigaChat (Sber), YandexGPT and Cloud.ru — paid in roubles, working without a VPN.

## How to start

Menu **«Models» → «Cloud model via API…»** (the section at the bottom of the «Models» page).

## Step by step

1. Choose the provider — the API address is filled in by itself, and a hint where to get the key appears under the form.
2. Paste the key from your account with the provider.
3. Press **«List models»** and choose a model (or type its name).
4. **«Test connection»**, then tick «Use the cloud model instead of the local one» and **«Save»**.

## What you get

The chat and the agent work through the chosen model; the status bar shows «(cloud)» next to the model name. Questions and fragments of code go to the provider, and the tokens are paid there.

## If it does not work

- GigaChat: paste the **authorization key** (not the Client Secret) — Fluxion gets and renews the 30-minute token by itself. A certificate error — install the Russian «Сертификаты Минцифры» from gosuslugi.ru/crt. For keys of companies set `FLUXION_GIGACHAT_SCOPE=GIGACHAT_API_CORP` (or `GIGACHAT_API_B2B`).
- YandexGPT: in the model name replace `<folder_id>` with the ID of the folder; the key is the API key of a service account with the role `ai.languageModels.user`.
- ProxyAPI and VseGPT: the model is written with its vendor — `openai/gpt-4o-mini`, not `gpt-4o-mini`.
- 401 — the key is not accepted; 402 — top up the balance with the provider; 404 — check the model name.
