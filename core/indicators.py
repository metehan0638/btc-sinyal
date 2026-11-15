"""Göstergeler (indikatörler): her gösterge yalnızca 5 etiketten birini üretir.

    Güçlü Dip · Dip · Nötr · Şişmiş · Aşırı Şişmiş

Gösterge kendi hesabını ve kendi eşiklerini bilir; genel skor (core/scoring.py) göstergelerin içini bilmez, yalnızca etiketlerini
ortak puana çevirir. Yeni gösterge eklemek için: `Indicator`'dan türeyen bir sınıf yaz, `REGISTRY`'e ekle, config.yaml'daki
`indicators:` listesine yaz. Genel skor algoritmasına dokunmak gerekmez.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from core import external, mvrv, sentiment

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


def bollinger_percent_b(close: pd.Series, period: int = 20, mult: float = 2.0) -> pd.Series:
    """Bollinger %B = (kapanış - alt bant) / (üst bant - alt bant). Orta bant SMA(period), bantlar ± mult x standart sapma.

    Standart sapma POPÜLASYON sapmasıdır (ddof=0: John Bollinger'ın tanımı ve TradingView'daki varsayılan). %B = 0 alt banda, 0.5 orta banda,
    1 üst banda eşittir; < 0 alt bandın altı, > 1 üst bandın üstüdür. İlk `period - 1` mum için NaN; fiyat `period` mum boyunca hiç
    değişmediyse (bant genişliği 0) %B tanımsızdır (NaN)."""
    close = pd.Series(close, dtype=float)
    mid = close.rolling(period).mean()
    spread = mult * close.rolling(period).std(ddof=0)
    width = 2 * spread
    return ((close - (mid - spread)) / width.where(width > 0)).astype(float)


def money_flow_index(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> pd.Series:
    """MFI (TradingView ta.mfi ile aynı): tipik fiyat = (H+L+C)/3, para akışı = tipik fiyat x hacim. Tipik fiyat bir öncekinden yüksekse
    pozitif, düşükse negatif akış sayılır (eşitse hiçbiri). MFI = 100 - 100 / (1 + son `period` mumun pozitif akış toplamı / negatif akış toplamı).
    İlk `period` mum için NaN. Negatif akış yoksa 100, hiç akış yoksa (hacim 0 veya fiyat sabit) NaN."""
    typical = (pd.Series(high, dtype=float) + pd.Series(low, dtype=float) + pd.Series(close, dtype=float)) / 3
    flow = typical * pd.Series(volume, dtype=float)
    change = typical.diff()
    positive = flow.where(change > 0, 0.0).where(change.notna())
    negative = flow.where(change < 0, 0.0).where(change.notna())
    up, down = positive.rolling(period).sum(), negative.rolling(period).sum()
    mfi = 100.0 - 100.0 / (1.0 + up / down.where(down > 0))
    return mfi.where(down > 0, pd.Series(np.where(up > 0, 100.0, np.nan), index=typical.index)).astype(float)


_WARNED: set[str] = set()


def _warn_once(message: str) -> None:
    if message not in _WARNED:
        _WARNED.add(message)
        print(f"[gösterge] {message}", file=sys.stderr)


class Indicator:
    """Gösterge tabanı. Alt sınıf `compute` (ham değer serisi) ve `classify` (tek değer -> etiket) yazar."""
    key = ""
    name = ""
    value_range: tuple[float, float] | None = None  # grafik ekseni (None: otomatik)

    def compute(self, df: pd.DataFrame, tf: str | None = None) -> pd.Series:
        """Varlığın KAPANMIŞ mumlarından ham değer serisi; df.index ile aynı indeksli olmalı (değer yoksa NaN).
        `tf` ("1d", "1w", "1M") yalnızca dış (günlük) veri kullanan göstergeler için gerekir; verilmezse mum aralığından tahmin edilir."""
        raise NotImplementedError

    def classify(self, value: float) -> str | None:
        """Tek ham değeri 5 etiketten birine çevirir; değer yoksa (NaN) None."""
        raise NotImplementedError

    def levels(self) -> list[tuple[float, str]]:
        """Grafikte çizilecek eşik çizgileri: (değer, açıklama). En dip tarafından en şişmiş tarafa doğru sıralı olmalı."""
        return []

    def text(self, value: float) -> str:
        return f"{value:.2f}"

    def labels(self, values: pd.Series) -> pd.Series:
        """Ham değer serisini etiket serisine çevirir (değer yoksa None)."""
        return pd.Series([self.classify(v) for v in values], index=values.index, dtype=object)


class BandIndicator(Indicator):
    """Dört eşikle 5 etikete ayrılan gösterge (RSI, Bollinger %B, MFI):
    değer < t1 Güçlü Dip · t1 ≤ değer ≤ t2 Dip · t2 < değer < t3 Nötr · t3 ≤ değer ≤ t4 Şişmiş · değer > t4 Aşırı Şişmiş."""
    thresholds: tuple[float, float, float, float] = (0, 0, 0, 0)

    def classify(self, value: float) -> str | None:
        if value is None or pd.isna(value):
            return None
        t1, t2, t3, t4 = self.thresholds
        if value < t1:
            return STRONG_BOTTOM
        if value <= t2:
            return BOTTOM
        if value < t3:
            return NEUTRAL
        if value <= t4:
            return OVERHEATED
        return EXTREME

    def levels(self) -> list[tuple[float, str]]:
        return [(t, f"{t:g}") for t in self.thresholds]


class RSIIndicator(BandIndicator):
    """RSI(14). < 30 Güçlü Dip · 30–35 Dip · 35–65 Nötr (sınırlar hariç) · 65–70 Şişmiş · > 70 Aşırı Şişmiş."""
    key = "rsi"
    value_range = (0, 100)
    thresholds = (30, 35, 65, 70)

    def __init__(self, period: int = 14):
        if int(period) < 2:
            raise ValueError("RSI periyodu en az 2 olmalı.")
        self.period = int(period)
        self.name = f"RSI({self.period})"

    def compute(self, df: pd.DataFrame, tf: str | None = None) -> pd.Series:
        return wilder_rsi(df["close"], self.period)

    def text(self, value: float) -> str:
        return f"{value:.1f}"


class BollingerPercentBIndicator(BandIndicator):
    """Bollinger %B (varsayılan: SMA 20, ±2 standart sapma): fiyatın Bollinger bantları içindeki konumu.

    %B < 0 Güçlü Dip (alt bandın altı) · 0 ≤ %B ≤ 0.10 Dip · 0.10 < %B < 0.90 Nötr (sınırlar hariç) · 0.90 ≤ %B ≤ 1 Şişmiş ·
    %B > 1 Aşırı Şişmiş (üst bandın üstü)."""
    key = "bollinger"
    thresholds = (0.0, 0.10, 0.90, 1.0)

    def __init__(self, period: int = 20, mult: float = 2.0):
        if int(period) < 2:
            raise ValueError("Bollinger periyodu en az 2 olmalı.")
        if float(mult) <= 0:
            raise ValueError("Bollinger standart sapma çarpanı 0'dan büyük olmalı.")
        self.period, self.mult = int(period), float(mult)
        self.name = f"Bollinger %B({self.period}, {self.mult:g})"

    def compute(self, df: pd.DataFrame, tf: str | None = None) -> pd.Series:
        return bollinger_percent_b(df["close"], self.period, self.mult)


class MFIIndicator(BandIndicator):
    """MFI (Money Flow Index, varsayılan 14): fiyatla birlikte işlem hacmini de hesaba katan RSI benzeri momentum göstergesi.

    MFI < 20 Güçlü Dip · 20 ≤ MFI ≤ 30 Dip · 30 < MFI < 70 Nötr · 70 ≤ MFI ≤ 80 Şişmiş · MFI > 80 Aşırı Şişmiş."""
    key = "mfi"
    value_range = (0, 100)
    thresholds = (20, 30, 70, 80)

    def __init__(self, period: int = 14):
        if int(period) < 2:
            raise ValueError("MFI periyodu en az 2 olmalı.")
        self.period = int(period)
        self.name = f"MFI({self.period})"

    def compute(self, df: pd.DataFrame, tf: str | None = None) -> pd.Series:
        return money_flow_index(df["high"], df["low"], df["close"], df["volume"], self.period)

    def text(self, value: float) -> str:
        return f"{value:.1f}"


class ExternalDailyIndicator(Indicator):
    """Dış kaynaktan GÜNLÜK gelen veriyi kullanan gösterge tabanı (Fear & Greed, MVRV).

    Günlük mumda o günün değeri; haftalık/aylık mumda o dönemin günlük değerlerinin ortalaması (aggregate="mean") veya son günün değeri
    (aggregate="last"). Dönemin bir günü bile eksikse o mum için değer üretilmez. Kaynak alınamazsa gösterge hata fırlatmaz: değer üretmez
    (NaN), böylece genel skora girmez ve nötr sayılmaz."""
    source_key = ""   # build_indicators'ın hangi veri kaynağını bağlayacağı
    max_stale_days = 0

    def __init__(self, aggregate: str = "mean", max_stale_days: int | None = None, source=None):
        if aggregate not in external.HOW:
            raise ValueError(f"aggregate 'mean' veya 'last' olmalı, verilen: {aggregate}")
        if max_stale_days is not None:
            if int(max_stale_days) < 0:
                raise ValueError("max_stale_days negatif olamaz.")
            self.max_stale_days = int(max_stale_days)
        self.aggregate, self.source = aggregate, source

    def daily(self) -> pd.Series:
        """Günlük seri (indeks = UTC gün başı). Alt sınıf yazar; hata fırlatabilir."""
        raise NotImplementedError

    def compute(self, df: pd.DataFrame, tf: str | None = None) -> pd.Series:
        empty = pd.Series(np.nan, index=df.index, dtype=float)
        if self.source is None:
            _warn_once(f"{self.name}: veri kaynağı bağlı değil, hesaba katılmayacak.")
            return empty
        try:
            daily = self.daily()
        except Exception as e:  # noqa: BLE001
            _warn_once(f"{self.name} verisi alınamadı ({e}); bu çalışmada hesaba katılmayacak.")
            return empty
        return external.align_daily(daily, df.index, tf or external.infer_timeframe(df.index), self.aggregate, self.max_stale_days)


class FearGreedIndicator(ExternalDailyIndicator):
    """Crypto Fear & Greed Index (0–100, alternative.me): yatırımcı psikolojisi. Düşük = korku, yüksek = açgözlülük.

    0–10 Güçlü Dip · 11–34 Dip · 35–65 Nötr · 66–89 Şişmiş · 90–100 Aşırı Şişmiş. Haftalık/aylık ortalama tam sayı olmayabileceği için
    sınırlar sürekli uygulanır: ≤ 10 Güçlü Dip · 10–35 (35 hariç) Dip · 35–65 (65 dahil) Nötr · 65–90 (90 hariç) Şişmiş · ≥ 90 Aşırı Şişmiş;
    tam sayı değerlerde bu tablo ile birebir aynıdır."""
    key, name, source_key = "fear_greed", "Fear & Greed", "fear_greed"
    value_range = (0, 100)

    def daily(self) -> pd.Series:
        return self.source()

    def classify(self, value: float) -> str | None:
        if value is None or pd.isna(value):
            return None
        if value <= 10:
            return STRONG_BOTTOM
        if value < 35:
            return BOTTOM
        if value <= 65:
            return NEUTRAL
        if value < 90:
            return OVERHEATED
        return EXTREME

    def levels(self) -> list[tuple[float, str]]:
        return [(10, "10"), (35, "35"), (65, "65"), (90, "90")]

    def text(self, value: float) -> str:
        return f"{value:.0f}" if abs(value - round(value)) < 1e-9 else f"{value:.1f}"


class MVRVZScoreIndicator(ExternalDailyIndicator):
    """MVRV Z-Score (yalnızca Bitcoin): piyasa değerinin gerçekleşmiş değerine (Realized Cap) göre tarihsel olarak ne kadar yüksek/düşük olduğu.

    Z = (Piyasa değeri − Gerçekleşmiş değer) / piyasa değerinin standart sapması (verinin başından o güne). Veri: Coin Metrics Community API.

    Z < 0 Güçlü Dip · 0 ≤ Z < 1 Dip · 1 ≤ Z < 3 Nötr · 3 ≤ Z < 7 Şişmiş · Z ≥ 7 Aşırı Şişmiş (alt sınır dahil).
    Veri yayınlanma gecikmesi için son bilinen değer en fazla `max_stale_days` (varsayılan 2) gün ileri taşınır."""
    key, name, source_key = "mvrv", "MVRV Z-Score", "mvrv"
    max_stale_days = 2

    def daily(self) -> pd.Series:
        return mvrv.zscore(self.source())

    def classify(self, value: float) -> str | None:
        if value is None or pd.isna(value):
            return None
        if value < 0:
            return STRONG_BOTTOM
        if value < 1:
            return BOTTOM
        if value < 3:
            return NEUTRAL
        if value < 7:
            return OVERHEATED
        return EXTREME

    def levels(self) -> list[tuple[float, str]]:
        return [(0, "0"), (1, "1"), (3, "3"), (7, "7")]


# Yeni gösterge eklerken buraya kaydet (anahtar = config.yaml'daki isim).
REGISTRY: dict[str, type[Indicator]] = {"rsi": RSIIndicator, "bollinger": BollingerPercentBIndicator, "mfi": MFIIndicator,
                                        "fear_greed": FearGreedIndicator, "mvrv": MVRVZScoreIndicator}
DEFAULT_INDICATORS = {"rsi": {"period": 14}}

# Dış veri kaynakları (argümansız çağrılır, süreç içinde hatırlanır). Test/özel kullanım için build_indicators(sources=...) ile değiştirilebilir.
_DEFAULT_SOURCES = {"fear_greed": external.memoize(sentiment.fetch_fear_greed),
                    "mvrv": external.memoize(mvrv.fetch_mvrv_data)}


def default_sources() -> dict:
    return _DEFAULT_SOURCES


def clear_sources() -> None:
    """Dış veri önbelleğini boşaltır (panelde 'Veriyi yenile' için)."""
    for fn in _DEFAULT_SOURCES.values():
        fn.clear()


def build_indicators(config: dict | None = None, sources: dict | None = None) -> list[Indicator]:
    """config.yaml'daki `indicators:` bölümünden gösterge nesneleri üretir; yoksa varsayılan (yalnızca RSI).
    `sources`: {kaynak adı: argümansız fonksiyon} ile dış veri kaynakları değiştirilebilir (varsayılanlar gerçek API'lerdir)."""
    spec = DEFAULT_INDICATORS if config is None else config
    if not spec:
        raise ValueError("config.yaml içinde en az bir gösterge tanımlı olmalı (indicators:).")
    unknown = [key for key in spec if key not in REGISTRY]
    if unknown:
        raise ValueError(f"Bilinmeyen gösterge: {', '.join(unknown)}. Tanımlı olanlar: {', '.join(REGISTRY)}.")
    available = {**_DEFAULT_SOURCES, **(sources or {})}
    built = []
    for key, params in spec.items():
        cls, kwargs = REGISTRY[key], dict(params or {})
        if getattr(cls, "source_key", "") and "source" not in kwargs:
            kwargs["source"] = available.get(cls.source_key)
        built.append(cls(**kwargs))
    return built
