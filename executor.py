#!/usr/bin/env python3
"""
executor.py — слой ИСПОЛНЕНИЯ за единым интерфейсом.

Агент (agent.py) принимает РЕШЕНИЕ (войти лимиткой -10%, выйти по TP/SL/таймауту).
КАК это решение исполняется — дело Executor'а. Так live-режим добавляется как
вторая реализация, не трогая логику агента.

Реализации:
  PaperExecutor  — филлы модельные (цена = целевая, издержки по ExecutionConfig).
                   Деньги не двигаются. Это то, что работает сейчас.
  GMGNExecutor   — заглушка на будущее:
                   dry-run: реальные котировки GMGN (read-only ключ), ордер НЕ шлём;
                   live:    настоящий on-chain swap (trading-ключ + кошелёк).

Контракт (все реализации):
  entry_fill(want_price, kind, trade) -> фактическая цена входа   (kind: "limit"|"market")
  exit_fill(want_price, reason, trade) -> фактическая цена выхода (reason: "TP"|"SL"|"TIME")
  cost(liq_usd, stake) -> доля round-trip издержек (комиссия+сеть+слиппедж)
"""
from __future__ import annotations


class Executor:
    name = "base"

    def entry_fill(self, want_price: float, kind: str, trade) -> float:
        raise NotImplementedError

    def exit_fill(self, want_price: float, reason: str, trade) -> float:
        raise NotImplementedError

    def cost(self, liq_usd: float, stake: float) -> float:
        raise NotImplementedError


class PaperExecutor(Executor):
    """Бумажное исполнение: цена = целевая, издержки по ExecutionConfig.

    Идентично прежней зашитой логике агента — числа бэктеста не меняются."""
    name = "paper"

    def __init__(self, exec_cfg):
        self.cfg = exec_cfg

    def entry_fill(self, want_price, kind, trade):
        return want_price                       # лимитка/рынок наливается по целевой цене

    def exit_fill(self, want_price, reason, trade):
        return want_price                       # TP/SL/таймаут по целевой цене

    def cost(self, liq_usd, stake):
        c = self.cfg
        liq = liq_usd if liq_usd and liq_usd > 0 else 30000.0
        fee = c.swap_fee_pct * 2
        priority = (c.priority_fee_usd * 2) / stake
        slip_buy = min(c.impact_k * stake / liq, c.max_slippage_pct)
        slip_sell = min(c.sell_slippage_mult * c.impact_k * stake / liq, c.max_slippage_pct)
        return fee + priority + slip_buy + slip_sell


class GMGNExecutor(Executor):
    """ЗАГЛУШКА. Реальное исполнение через GMGN API.

    Планируемые режимы:
      mode="dry"  — филлы по РЕАЛЬНЫМ котировкам GMGN (read-only ключ), своп НЕ
                    отправляется. Пейпер, но по фактическим ценам/слиппеджу биржи.
                    Нужен только read-only ключ, риска ноль.
      mode="live" — настоящий on-chain swap (trading-ключ + приватник кошелька).
                    ТОЛЬКО burner-кошелёк. Реальные деньги. Включает пользователь.

    Не реализовано: сначала нужно подтвердить реальный интерфейс gmgn-cli/API
    (эндпоинты, аутентификацию, формат лимитки с TP/SL). См. README.
    """
    name = "gmgn"

    def __init__(self, mode: str = "dry", api_key: str | None = None,
                 wallet_key: str | None = None, exec_cfg=None):
        assert mode in ("dry", "live")
        self.mode = mode
        self.api_key = api_key
        self.wallet_key = wallet_key
        self.cfg = exec_cfg
        raise NotImplementedError(
            "GMGNExecutor ещё не подключён. Нужен реальный интерфейс GMGN API "
            "(read-only ключ для dry-run). Пока используй PaperExecutor.")

    # Будущая реализация:
    # def entry_fill(self, want_price, kind, trade):
    #     quote = gmgn_quote(trade.ca, side="buy", limit=want_price)   # read-only
    #     if self.mode == "live":
    #         gmgn_place_limit_buy(trade.ca, sol_amount=..., limit=want_price,
    #                              tp=trade.tp, sl=trade.sl, key=self.api_key)
    #     return quote.expected_fill_price
    # def exit_fill(self, want_price, reason, trade): ...
    # def cost(self, liq_usd, stake):  # из фактического слиппеджа котировки GMGN
    #     ...
