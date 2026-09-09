# gmgn-cloud — paper-агент в облаке (Railway)

24/7 бумажный агент WINNER-STYLE: сам генерит сигналы, торгует на бумаге по правилу
(лимит −10% → TP/SL/20м) и показывает дашборд. Никаких секретов и ключей — только
публичные keyless-API (DexScreener/GeckoTerminal). Можно выключить компьютер и
смотреть с телефона.

Один контейнер поднимает три процесса (`main.py`): **producer** (сигналы) +
**agent** (сделки) + **web** (дашборд на `0.0.0.0:$PORT`).

## Деплой на Railway (через GitHub)

1. Создай **пустой** репозиторий на github.com (например `gmgn-cloud`), без README.
2. Запушь этот код (из папки `~/gmgn-cloud`):
   ```bash
   git remote add origin https://github.com/<ты>/gmgn-cloud.git
   git push -u origin main
   ```
3. На **railway.app** → New Project → **Deploy from GitHub repo** → выбери `gmgn-cloud`.
   Railway сам определит Python, поставит `requests` и запустит `python main.py`.
4. Settings → **Networking** → **Generate Domain** → получишь публичный URL.
   Открой его с телефона — это твой дашборд.

## Переменные окружения (Railway → Variables, все опциональны)

| Var | По умолч. | Что делает |
|---|---|---|
| `RULE` | `A` | правило выхода: A (TP+25%/SL−50%) или B (TP+40%/SL−50%) |
| `ENTRY` | `market-below` | нет отката за 5м: `market-below` \| `skip` \| `market` |
| `POLL` | `5` | опрос цены, сек |
| `SCAN` | `60` | скан рынка продюсером, сек |
| `DASH_TOKEN` | — | если задан, дашборд требует `?token=…` (приватный доступ) |

**Приватный дашборд:** задай `DASH_TOKEN=любая_строка`, открывай
`https://<домен>/?token=любая_строка`.

## Важно
- Это **пейпер** — реальных сделок нет, денег не двигает.
- Файловая система Railway **эфемерна**: при передеплое/рестарте логи
  (`events.jsonl`, `paper_trades.jsonl`) обнуляются. Для постоянной истории —
  подключи Railway **Volume** на `/app` (можно добавить позже).
- Дашборд по умолчанию **публичный** (read-only, без секретов). Нужен приват — `DASH_TOKEN`.
