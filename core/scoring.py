"""Genel skor (Dip Skoru): göstergelerin ETİKETLERİNDEN hesaplanan, -100 ile +100 arası tek sayı.

1. Her gösterge 5 etiketten birini üretir (core/indicators.py).
2. Etiket ortak puana çevrilir: Güçlü Dip +100 · Dip +50 · Nötr 0 · Şişmiş -50 · Aşırı Şişmiş -100.
3. GENEL SKOR = toplam puan / aktif gösterge sayısı. Veri üretmeyen gösterge (etiketi yok) nötr sayılmaz, hesaba hiç girmez.
4. Genel skor 5 nihai etikete çevrilir.

Skor gerçek dip veya tepe garantisi değildir; göstergelerin o anki aşırılık durumunun özetidir.
"""
from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from core.indicators import (BOTTOM, EXTREME, LABELS, NEUTRAL, OVERHEATED, POINTS, STRONG_BOTTOM, Indicator)

# Nihai etiket sınırları (alt sınır dahil): >= 60 Güçlü Dip · >= 20 Dip · (-20, 20) Nötr · (-60, -20] Şişmiş · <= -60 Aşırı Şişmiş.
# Sınırlar çakışmasın diye simetrik uygulanır: +60 Güçlü Dip olduğu gibi -60 da Aşırı Şişmiş'tir; +20 Dip, -20 Şişmiş.
STRONG_FROM, MILD_FROM = 60, 20
# Geçmiş analizinde "AL tarafı" sayılan en düşük skor (nihai etiket Dip veya Güçlü Dip).
BUY_SIDE_FROM = MILD_FROM
HORIZONS = {"1d": (30, 90, 180), "1w": (4, 13, 26), "1M": (1, 3, 6)}  # skor ortalaması için ileri bakış ufukları (mum)


def final_label(score: float) -> str | None:
    """Genel skoru nihai etikete çevirir; skor yoksa (NaN) None."""
    if score is None or pd.isna(score):
        return None
    if score >= STRONG_FROM:
        return STRONG_BOTTOM
    if score >= MILD_FROM:
        return BOTTOM
    if score > -MILD_FROM:
        return NEUTRAL
    if score > -STRONG_FROM:
        return OVERHEATED
    return EXTREME


def overall_score(labels: Mapping[str, str | None]) -> dict:
    """Etiketlerden genel skor. `labels`: {gösterge anahtarı: etiket veya None (veri yok)}.
    Döndürür: score (veri yoksa NaN), label, active (hesaba girenler), total (tanımlı gösterge sayısı), points."""
    unknown = {label for label in labels.values() if label is not None and label not in POINTS}
    if unknown:
        raise ValueError(f"Bilinmeyen etiket: {', '.join(sorted(unknown))}")
    points = {key: POINTS[label] for key, label in labels.items() if label is not None}
    score = sum(points.values()) / len(points) if points else float("nan")
    return dict(score=score, label=final_label(score), active=len(points), total=len(labels), points=points)


def evaluate(df: pd.DataFrame, indicators: list[Indicator]) -> dict:
    """Son kapanmış mum için tüm göstergeleri ve genel skoru hesaplar."""
    values = {ind.key: ind.compute(df).iloc[-1] for ind in indicators}
    labels = {ind.key: ind.classify(values[ind.key]) for ind in indicators}
    return dict(date=df.index[-1], close=float(df["close"].iloc[-1]), values=values, labels=labels,
                names={ind.key: ind.name for ind in indicators}, **overall_score(labels))


def score_history(df: pd.DataFrame, indicators: list[Indicator]) -> pd.DataFrame:
    """Her mum için gösterge puanları, genel skor ve nihai etiket. Bir mumun skoru yalnızca o muma kadarki veriyi kullanır.

    Sütunlar: her gösterge anahtarı (puan; veri yoksa NaN), 'score', 'active' (aktif gösterge sayısı), 'label' (nihai etiket)."""
    points = pd.DataFrame({ind.key: ind.labels(ind.compute(df)).map(POINTS.get).astype(float) for ind in indicators},
                          index=df.index)
    active = points.notna().sum(axis=1)
    # puanlar tam sayı olduğu için toplam ve bölme tam hesaplanır (sınır değerlerde yuvarlama hatası yok)
    score = points.sum(axis=1, min_count=1) / active.where(active > 0)
    out = points.copy()
    out["score"], out["active"] = score, active
    out["label"] = pd.Series([final_label(s) for s in score], index=df.index, dtype=object)
    return out


def forward_returns(close: pd.Series, horizon: int) -> pd.Series:
    """Her mumun kapanışından `horizon` mum sonraki kapanışa getiri (son `horizon` mum için NaN)."""
    return close.shift(-horizon) / close - 1


def _episodes(labels: pd.Series, value: str) -> int:
    """Skorun bu etikete girip çıktığı ayrı dönem sayısı (art arda gelen mumlar tek dönem sayılır)."""
    inside = labels == value
    return int((inside & ~inside.shift(fill_value=False)).sum())


def bucket_table(close: pd.Series, labels: pd.Series, horizon: int) -> pd.DataFrame:
    """Nihai etikete göre `horizon` mum sonraki getiri istatistikleri (+ tüm mumlar için kıyas satırı).

    Sütunlar: n (getirisi ölçülebilen mum), share (zamanın payı), episodes (etikete ayrı giriş sayısı),
    mean/median (ileri getiri), pos_rate (getirisi pozitif olanların oranı). Komisyon yoktur."""
    forward = forward_returns(close, horizon)
    valid = labels.notna() & forward.notna()
    total = int(valid.sum())

    def row(name, mask, episodes):
        r = forward[mask]
        return dict(label=name, n=int(mask.sum()), share=mask.sum() / total if total else np.nan, episodes=episodes,
                    mean=r.mean() if len(r) else np.nan, median=r.median() if len(r) else np.nan,
                    pos_rate=(r > 0).mean() if len(r) else np.nan)

    rows = [row(label, valid & (labels == label), _episodes(labels, label)) for label in LABELS]
    rows.append(row("Tüm mumlar (kıyas)", valid, np.nan))
    return pd.DataFrame(rows).set_index("label")
