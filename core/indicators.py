"""Göstergeler (indikatörler): her gösterge yalnızca 5 etiketten birini üretir.

    Güçlü Dip · Dip · Nötr · Şişmiş · Aşırı Şişmiş

Gösterge kendi hesabını ve kendi eşiklerini bilir; genel skor (core/scoring.py) göstergelerin içini bilmez, yalnızca etiketlerini
ortak puana çevirir. Yeni gösterge eklemek için: `Indicator`'dan türeyen bir sınıf yaz, `REGISTRY`'e ekle, config.yaml'daki
`indicators:` listesine yaz. Genel skor algoritmasına dokunmak gerekmez.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Etiketler, ortak sinyal puanları ve rapor simgeleri. Sıra: en dip -> en şişmiş.
STRONG_BOTTOM, BOTTOM, NEUTRAL, OVERHEATED, EXTREME = "Güçlü Dip", "Dip", "Nötr", "Şişmiş", "Aşırı Şişmiş"
LABELS = (STRONG_BOTTOM, BOTTOM, NEUTRAL, OVERHEATED, EXTREME)
POINTS = {STRONG_BOTTOM: 100, BOTTOM: 50, NEUTRAL: 0, OVERHEATED: -50, EXTREME: -100}
ICONS = {STRONG_BOTTOM: "🟢🟢", BOTTOM: "🟢", NEUTRAL: "⚪", OVERHEATED: "🔴", EXTREME: "🔴🔴"}

TIMEFRAMES = ("1d", "1w", "1M")
# Göstergenin ısınması ve güvenilir değer vermesi için gereken en az kapalı mum sayısı (yedek borsa geçmişi kısaysa uyarı).
MIN_BARS = {"1d": 200, "1w": 100, "1M": 48}
BAR_NAME = {"1d": "gün", "1w": "hafta", "1M": "ay"}


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI (TradingView / StockCharts yöntemi): ilk ortalama ilk `period` değişimin ortalaması, sonra Wilder yumuşatma.
    İlk `period` mum için NaN. Hiç düşüş yoksa 100, hiç hareket yoksa NaN."""
    close = pd.Series(close, dtype=float)
    delta = close.diff().to_numpy()
    gain, loss = np.where(delta > 0, delta, 0.0), np.where(delta < 0, -delta, 0.0)
    out = np.full(len(close), np.nan)
    if len(close) <= period:
        return pd.Series(out, index=close.index)

    def value(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else np.nan
        return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    avg_gain, avg_loss = gain[1:period + 1].mean(), loss[1:period + 1].mean()
    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(close)):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period
        out[i] = value(avg_gain, avg_loss)
    return pd.Series(out, index=close.index)


class Indicator:
    """Gösterge tabanı. Alt sınıf `compute` (ham değer serisi) ve `classify` (tek değer -> etiket) yazar."""
    key = ""
    name = ""
    value_range: tuple[float, float] | None = None  # grafik ekseni

    def compute(self, df: pd.DataFrame) -> pd.Series:
        raise NotImplementedError

    def classify(self, value: float) -> str | None:
        """Tek ham değeri 5 etiketten birine çevirir; değer yoksa (NaN) None."""
        raise NotImplementedError

    def levels(self) -> list[tuple[float, str]]:
        """Grafikte çizilecek eşik çizgileri: (değer, açıklama)."""
        return []

    def text(self, value: float) -> str:
        return f"{value:.2f}"

    def labels(self, values: pd.Series) -> pd.Series:
        """Ham değer serisini etiket serisine çevirir (değer yoksa None)."""
        return pd.Series([self.classify(v) for v in values], index=values.index, dtype=object)


class RSIIndicator(Indicator):
    """RSI(14). < 30 Güçlü Dip · 30–35 Dip · 35–65 Nötr (sınırlar hariç) · 65–70 Şişmiş · > 70 Aşırı Şişmiş."""
    key = "rsi"
    value_range = (0, 100)
    STRONG_BOTTOM_BELOW, BOTTOM_UPTO, OVERHEATED_FROM, EXTREME_ABOVE = 30, 35, 65, 70

    def __init__(self, period: int = 14):
        if int(period) < 2:
            raise ValueError("RSI periyodu en az 2 olmalı.")
        self.period = int(period)
        self.name = f"RSI({self.period})"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        return wilder_rsi(df["close"], self.period)

    def classify(self, value: float) -> str | None:
        if value is None or pd.isna(value):
            return None
        if value < self.STRONG_BOTTOM_BELOW:
            return STRONG_BOTTOM
        if value <= self.BOTTOM_UPTO:
            return BOTTOM
        if value < self.OVERHEATED_FROM:
            return NEUTRAL
        if value <= self.EXTREME_ABOVE:
            return OVERHEATED
        return EXTREME

    def levels(self) -> list[tuple[float, str]]:
        return [(self.STRONG_BOTTOM_BELOW, "30"), (self.BOTTOM_UPTO, "35"), (self.OVERHEATED_FROM, "65"),
                (self.EXTREME_ABOVE, "70")]

    def text(self, value: float) -> str:
        return f"{value:.1f}"


# Yeni gösterge eklerken buraya kaydet (anahtar = config.yaml'daki isim).
REGISTRY: dict[str, type[Indicator]] = {"rsi": RSIIndicator}
DEFAULT_INDICATORS = {"rsi": {"period": 14}}


def build_indicators(config: dict | None = None) -> list[Indicator]:
    """config.yaml'daki `indicators:` bölümünden gösterge nesneleri üretir; yoksa varsayılan (yalnızca RSI)."""
    spec = DEFAULT_INDICATORS if config is None else config
    if not spec:
        raise ValueError("config.yaml içinde en az bir gösterge tanımlı olmalı (indicators:).")
    unknown = [key for key in spec if key not in REGISTRY]
    if unknown:
        raise ValueError(f"Bilinmeyen gösterge: {', '.join(unknown)}. Tanımlı olanlar: {', '.join(REGISTRY)}.")
    return [REGISTRY[key](**(params or {})) for key, params in spec.items()]
