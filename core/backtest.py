"""Basit long-only backtest. Sinyal mum kapanışında üretilir, işlem o kapanış fiyatından yapılır.
Giriş: AL onayı >= entry. Çıkış: AL onayı <= exit_. Çıkıştan sonra nakitte beklenir (short yok)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def position_series(bull, valid, entry: int, exit_: int) -> np.ndarray:
    """Giriş/çıkış kuralı: AL onayı >= entry -> pozisyona gir, <= exit_ -> çık. Backtest ve bildirimler bunu paylaşır."""
    pos, holding = np.zeros(len(bull)), False
    for i in range(len(bull)):
        if valid[i]:
            if not holding and bull[i] >= entry:
                holding = True
            elif holding and bull[i] <= exit_:
                holding = False
        pos[i] = 1.0 if holding else 0.0
    return pos


def run_backtest(df: pd.DataFrame, sig: pd.DataFrame, entry: int = 8, exit_: int = 4,
                 fee: float = 0.001, bars_per_year: int = 365) -> dict:
    if not sig["valid"].any():
        raise ValueError("Backtest için yeterli veri yok.")
    start = sig["valid"].idxmax()
    d, s = df.loc[start:], sig.loc[start:]
    c = d["close"]

    pos_arr = position_series(s["bull"].values, s["valid"].values, entry, exit_)
    pos = pd.Series(pos_arr, index=d.index)

    r = c.pct_change().fillna(0)
    cost = pos.diff().abs().fillna(pos.iloc[0]) * fee
    ret = pos.shift(1).fillna(0) * r - cost
    equity = (1 + ret).cumprod()
    bh = c / c.iloc[0]

    # işlem listesi
    trades, e_i = [], None
    for i in range(len(d)):
        prev = pos_arr[i - 1] if i > 0 else 0.0
        if pos_arr[i] == 1 and prev == 0:
            e_i = i
        if pos_arr[i] == 0 and prev == 1 and e_i is not None:
            trades.append((d.index[e_i], d.index[i], c.iloc[e_i], c.iloc[i], "Kapandı"))
            e_i = None
    if e_i is not None:
        trades.append((d.index[e_i], d.index[-1], c.iloc[e_i], c.iloc[-1], "Açık"))
    tr = pd.DataFrame(trades, columns=["Giriş", "Çıkış/Son", "Giriş fiyatı", "Çıkış/Son fiyat", "Durum"])
    if len(tr):
        tr["Getiri %"] = (tr["Çıkış/Son fiyat"] / tr["Giriş fiyatı"] - 1 - 2 * fee) * 100
        tr["Süre (bar)"] = [(d.index.get_loc(b) - d.index.get_loc(a)) for a, b in zip(tr["Giriş"], tr["Çıkış/Son"])]

    return dict(equity=equity, buy_hold=bh, position=pos, trades=tr,
                metrics=_metrics(ret, equity, pos, tr, bars_per_year),
                bh_metrics=_metrics(r, bh, pd.Series(1.0, index=d.index), pd.DataFrame(), bars_per_year))


def _metrics(ret, equity, pos, trades, bpy):
    years = len(ret) / bpy
    final = equity.iloc[-1]
    std = ret.std()
    return {
        "Toplam getiri %": (final - 1) * 100,
        "Yıllık bileşik getiri %": ((final ** (1 / years) - 1) * 100) if years > 0 and final > 0 else np.nan,
        "Maks. düşüş %": ((equity / equity.cummax() - 1).min()) * 100,
        "Sharpe": (ret.mean() / std * np.sqrt(bpy)) if std > 0 else np.nan,
        "İşlem sayısı": len(trades),
        "Kazanma oranı %": (trades["Getiri %"] > 0).mean() * 100 if len(trades) else np.nan,
        "Piyasada kalma %": pos.mean() * 100,
    }
