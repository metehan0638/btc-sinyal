"""Bağımsız gösterge backtest'i: AL geçişinde giriş, sabit kâr hedefinde çıkış."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.charting import indicator_series
from core.zones import METRIC_KEYS, bands_for, grade_series

PERIODS_PER_YEAR = {"1d": 365.25, "1w": 52.18, "1M": 12.0}
BASE_INDICATORS = (*METRIC_KEYS, "ichimoku", "cross")


def grades_for_backtest(raw: pd.DataFrame, tf: str, params: dict | None = None,
                        fear_greed: pd.Series | None = None) -> pd.DataFrame:
    """Paneldeki her göstergenin tarihsel derecesini ayrı bir sütunda döndürür."""
    if tf not in PERIODS_PER_YEAR:
        raise ValueError(f"Desteklenmeyen zaman dilimi: {tf}")
    params = params or {}
    bands = bands_for(params, tf)
    columns = {key: grade_series(raw[key], bands[key]) for key in METRIC_KEYS}
    for key in ("cross", "ichimoku"):
        columns[key] = indicator_series(raw, key, tf, params)[0]
    if tf == "1d" and fear_greed is not None:
        values = fear_greed.reindex(raw.index.normalize()).to_numpy()
        fg = pd.Series(values, index=raw.index, dtype=float)
        columns["fear_greed"] = grade_series(fg, bands["fear_greed"])
    return pd.DataFrame(columns, index=raw.index)


def _stats(equity: pd.Series, trades: list[dict], bars_per_year: float,
           invested_bars: int) -> dict:
    if equity.empty:
        return {"total_return": 0.0, "cagr": np.nan, "max_drawdown": 0.0, "trades": 0,
                "target_hits": 0, "win_rate": np.nan, "profit_factor": np.nan,
                "avg_days": np.nan, "exposure": 0.0}
    periods = len(equity)
    ending = float(equity.iloc[-1])
    years = periods / bars_per_year
    cagr = ending ** (1 / years) - 1 if ending > 0 and years > 0 else np.nan
    max_drawdown = float((equity / equity.cummax() - 1).min())
    pnl = np.array([trade["net_return"] for trade in trades], dtype=float)
    positive = pnl[pnl > 0].sum()
    negative = -pnl[pnl < 0].sum()
    profit_factor = positive / negative if negative else (np.inf if positive else np.nan)
    avg_days = np.mean([trade["days"] for trade in trades]) if trades else np.nan
    return {"total_return": ending - 1, "cagr": cagr, "max_drawdown": max_drawdown,
            "trades": len(trades), "target_hits": sum(trade["target_hit"] for trade in trades),
            "win_rate": float(np.mean(pnl > 0)) if len(pnl) else np.nan,
            "profit_factor": float(profit_factor), "avg_days": float(avg_days) if trades else np.nan,
            "exposure": invested_bars / periods}


def simulate_indicator(df: pd.DataFrame, grades: pd.Series, tf: str, target_pct: float = 30.0,
                       fee_pct: float = 0.1, previous_grade: float | None = None) -> dict:
    """AL'a yeni geçişte sonraki mum açılışından alır; hedefe değince satar.

    Pozisyonlar birbirinden bağımsız, tamamı özkaynakla ve uzun yönlüdür. Zarar kes yoktur.
    Dönem sonunda açık pozisyon son kapanışta kapatılır ve ayrıca bu şekilde işaretlenir.
    """
    if tf not in PERIODS_PER_YEAR:
        raise ValueError(f"Desteklenmeyen zaman dilimi: {tf}")
    if target_pct <= 0 or not 0 <= fee_pct < 100:
        raise ValueError("Kâr hedefi pozitif; komisyon ise 0 ile 100 arasında olmalı.")
    if len(df) != len(grades) or not df.index.equals(grades.index):
        raise ValueError("Mumlar ve derece serisi aynı tarih indeksine sahip olmalı.")
    required = {"open", "high", "close"}
    if not required.issubset(df.columns):
        raise ValueError(f"Mum verisinde gerekli sütunlar eksik: {sorted(required - set(df.columns))}")
    if df.empty:
        return {"equity": pd.Series(dtype=float), "trades_detail": [],
                **_stats(pd.Series(dtype=float), [], PERIODS_PER_YEAR[tf], 0)}

    fee = fee_pct / 100
    target = target_pct / 100
    close = df["close"].to_numpy(float)
    opening = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    grade = grades.fillna(0).to_numpy(float)
    if previous_grade is None or pd.isna(previous_grade):
        prior = np.r_[0.0, grade[:-1]]
    else:
        prior = np.r_[float(previous_grade), grade[:-1]]
    buy_transition = (grade > 0) & (prior <= 0)

    equity_values = np.ones(len(df), dtype=float)
    cash, quantity = 1.0, 0.0
    entry_price = entry_equity = 0.0
    entry_pos = None
    trades = []
    invested_bars = 0

    for i in range(len(df)):
        # Kapanmış mumdaki sinyal ancak bir sonraki mumun açılışında uygulanır.
        if quantity == 0 and i > 0 and buy_transition[i - 1]:
            entry_price = float(opening[i])
            if not np.isfinite(entry_price) or entry_price <= 0:
                raise ValueError("Alış açılış fiyatı pozitif ve sonlu olmalı.")
            entry_equity = cash
            quantity = cash * (1 - fee) / entry_price
            cash = 0.0
            entry_pos = i

        if quantity > 0:
            invested_bars += 1
            goal_price = entry_price * (1 + target)
            if high[i] >= goal_price:
                # Mum hedefin üzerinde açıldıysa, erişilebilir açılış fiyatı kullanılır.
                exit_price = max(goal_price, float(opening[i]))
                cash = quantity * exit_price * (1 - fee)
                trades.append({"entry": df.index[entry_pos], "exit": df.index[i],
                               "entry_price": entry_price, "exit_price": exit_price,
                               "net_return": cash / entry_equity - 1,
                               "days": (df.index[i] - df.index[entry_pos]).total_seconds() / 86400,
                               "exit_reason": f"%{target_pct:g} hedef", "target_hit": True})
                quantity, entry_pos = 0.0, None
                equity_values[i] = cash
                continue
            equity_values[i] = quantity * close[i]
        else:
            equity_values[i] = cash

    if quantity > 0:
        cash = quantity * close[-1] * (1 - fee)
        trades.append({"entry": df.index[entry_pos], "exit": df.index[-1],
                       "entry_price": entry_price, "exit_price": float(close[-1]),
                       "net_return": cash / entry_equity - 1,
                       "days": (df.index[-1] - df.index[entry_pos]).total_seconds() / 86400,
                       "exit_reason": "Dönem sonu", "target_hit": False})
        equity_values[-1] = cash

    equity = pd.Series(equity_values, index=df.index, name="Özkaynak")
    return {"equity": equity, "trades_detail": trades,
            **_stats(equity, trades, PERIODS_PER_YEAR[tf], invested_bars)}


def simulate_buy_hold(df: pd.DataFrame, fee_pct: float = 0.1, name: str = "Varlık al-tut") -> pd.Series:
    """Aynı aralıkta ilk açılıştan alıp son kapanışta satan kıyas serisi."""
    if not 0 <= fee_pct < 100:
        raise ValueError("Komisyon 0 ile 100 arasında olmalı.")
    if df.empty:
        return pd.Series(dtype=float, name=name)
    fee = fee_pct / 100
    qty = (1 - fee) / float(df["open"].iloc[0])
    return (df["close"] * qty * (1 - fee)).rename(name)


def run_backtests(df: pd.DataFrame, raw: pd.DataFrame, tf: str, params: dict | None = None,
                  fear_greed: pd.Series | None = None, keys: tuple[str, ...] | None = None,
                  start: pd.Timestamp | None = None, target_pct: float = 30.0,
                  fee_pct: float = 0.1, ticker: str = "BTC") -> dict:
    """Seçilen aralıkta her göstergenin bağımsız sonucunu ve varlık al-tut kıyasını hesaplar.

    Göstergeler tüm tarih serisinde hesaplanıp sonradan tarihe göre daraltılır; böylece
    seçilen aralığın başlangıcında ısınma dönemi ve yapay AL sinyali oluşmaz.
    """
    if not df.index.equals(raw.index):
        raise ValueError("Mum ve ham gösterge serilerinin indeksleri aynı olmalı.")
    grades_all = grades_for_backtest(raw, tf, params, fear_greed)
    selected = df if start is None else df.loc[df.index >= start]
    if selected.empty:
        raise ValueError("Seçilen tarih aralığında mum verisi yok.")
    selected_grades = grades_all.loc[selected.index]
    first_time = selected.index[0]
    earlier = grades_all.loc[grades_all.index < first_time]
    selected_keys = tuple(keys) if keys is not None else tuple(grades_all.columns)
    results = {}
    for key in selected_keys:
        if key not in grades_all:
            raise KeyError(f"Backtest derecesi bulunamadı: {key}")
        if not selected_grades[key].notna().any():
            results[key] = {"equity": None, "trades_detail": [], "available": False,
                            "total_return": np.nan, "cagr": np.nan, "max_drawdown": np.nan,
                            "trades": 0, "target_hits": 0, "win_rate": np.nan,
                            "profit_factor": np.nan, "avg_days": np.nan, "exposure": np.nan}
            continue
        previous = earlier[key].iloc[-1] if not earlier.empty else None
        results[key] = simulate_indicator(selected, selected_grades[key], tf, target_pct,
                                          fee_pct, previous_grade=previous)
        results[key]["available"] = True
    buy_hold = simulate_buy_hold(selected, fee_pct, name=f"{ticker} al-tut")
    results["buy_hold"] = {
        "equity": buy_hold,
        "total_return": float(buy_hold.iloc[-1] - 1),
        "cagr": float(buy_hold.iloc[-1] ** (PERIODS_PER_YEAR[tf] / len(buy_hold)) - 1)
        if buy_hold.iloc[-1] > 0 else np.nan,
        "max_drawdown": float((buy_hold / buy_hold.cummax() - 1).min()),
        "available": True,
    }
    return {"results": results, "grades": selected_grades, "prices": selected}
