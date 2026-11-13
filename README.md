# Kripto Takip Paneli

BTC/USDT, ETH/USDT, SOL/USDT ve XRP/USDT için fiyatın **dipte mi yoksa aşırı yükselmiş mi** olduğunu ölçen bir Streamlit paneli ve Telegram raporu.
Sistem, göstergelerin etiketlerini birleştirip **-100 ile +100 arası tek bir Dip Skoru** üretir:

- **+100'e yaklaştıkça** göstergeler dip / alım fırsatı yönünde (Güçlü Dip),
- **-100'e yaklaştıkça** fiyat şişmiş (Aşırı Şişmiş),
- **0 civarı** nötr.

Skor göstergelerin o anki durumunu özetleyen **mekanik bir etikettir; gerçek dip veya tepe garantisi değildir, emir ya da yatırım tavsiyesi değildir.**
Günlük, haftalık ve aylık mumlar için ayrı ayrı hesaplanır.

## Nasıl çalışır?

1. **Her gösterge kendi hesabını yapar ve yalnızca 5 etiketten birini üretir:** Güçlü Dip · Dip · Nötr · Şişmiş · Aşırı Şişmiş.
2. **Etiket ortak puana çevrilir:**

   | Etiket | Puan |
   |---|---:|
   | 🟢🟢 Güçlü Dip | +100 |
   | 🟢 Dip | +50 |
   | ⚪ Nötr | 0 |
   | 🔴 Şişmiş | -50 |
   | 🔴🔴 Aşırı Şişmiş | -100 |

   Bu puanlar göstergelerin kendi skoru değildir, genel skor için kullanılan ortak sinyal puanlarıdır.
3. **Genel skor = toplam gösterge puanı / aktif gösterge sayısı.** Her gösterge eşit ağırlıktadır. Gösterge sayısı artsa da azalsa da skor her zaman -100 ile +100 arasındadır.
   Henüz veri üretmeyen gösterge **nötr sayılmaz, hesaba hiç girmez.**
4. **Genel skor nihai etikete çevrilir:**

   | Genel skor | Nihai etiket |
   |---|---|
   | +60 ile +100 | Güçlü Dip |
   | +20 ile +59,99 | Dip |
   | -19,99 ile +19,99 | Nötr |
   | -60 ile -20 | Şişmiş |
   | -100 ile -60 | Aşırı Şişmiş |

   Sınırlar çakışmaz: alt sınır dahildir ve uygulama simetriktir (+60 Güçlü Dip olduğu gibi -60 da Aşırı Şişmiş'tir; +20 Dip, -20 Şişmiş).

Örnek (5 gösterge): Güçlü Dip (+100), Dip (+50), Nötr (0), Güçlü Dip (+100), Şişmiş (-50) → (100 + 50 + 0 + 100 - 50) / 5 = **+40 → Dip**.

## Şu an tanımlı gösterge: RSI

RSI(14), Wilder yöntemiyle (TradingView ile aynı) hesaplanır ve RSI değeri şu etiketlere çevrilir. RSI yalnızca etiket üretir, ayrı bir skoru yoktur.

| RSI | Etiket |
|---|---|
| < 30 | Güçlü Dip |
| 30 ≤ RSI ≤ 35 | Dip |
| 35 < RSI < 65 | Nötr |
| 65 ≤ RSI ≤ 70 | Şişmiş |
| > 70 | Aşırı Şişmiş |

Yalnızca RSI varken genel skor RSI etiketinin puanına eşittir (+100, +50, 0, -50 veya -100).
Fear & Greed endeksi yalnızca **bilgi olarak** gösterilir (rapor sonu ve panel), skora girmez.

## Yeni gösterge eklemek

Genel skor algoritmasına dokunmak gerekmez:

1. `core/indicators.py` içinde `Indicator`'dan türeyen bir sınıf yaz: `compute(df)` ham değer serisini, `classify(değer)` 5 etiketten birini (değer yoksa `None`) döndürür.
2. Sınıfı aynı dosyadaki `REGISTRY` sözlüğüne ekle.
3. `config.yaml` içindeki `indicators:` bölümüne anahtarını ve parametrelerini yaz.

Panel (tablo, grafik, backtest) ve Telegram raporu yeni göstergeyi otomatik alır. `tests/test_scoring.py` içindeki örnek test, sahte bir gösterge eklenmesini gösterir.

## Telegram raporu

Gösterge listesi yoktur; her coin için yalnızca:

- coin başlığının altında **günlük kapanış fiyatı** (son kapanmış günlük mumun tarihiyle),
- günlük, haftalık ve aylık **Dip Skoru** ve metin çubuğu (● skoru, │ sıfırı gösterir),
- son mesajın sonunda çubuğun kısa açıklaması ve **Fear & Greed** değeri (dün / 7 / 30 gün önceki değerleriyle).

Bazı göstergeler henüz veri üretmiyorsa satırda `(2/3 gösterge)` gibi aktif sayı görünür.

```
🪙 BTC — Bitcoin
💰 Günlük kapanış (09.11.2026): 75,368.41 USDT

📅 GÜNLÜK
🎯 Dip Skoru: 0 · ⚪ Nötr
-100 ░░░░░░░░░░●░░░░░░░░░░ +100
```

## Panel

- **📊 Güncel durum:** Dip Skoru göstergesi, her göstergenin değeri/etiketi/puanı ve hesabın dökümü.
- **📈 Grafik:** fiyat, seçili göstergenin değeri (etiket eşikleriyle) ve Dip Skoru.
- **🔥 Haber / Hype:** RSS haberleri ve hype analizi (yalnızca panelde).
- **📉 Geçmiş performans:** her gösterge ve genel skor ayrı ayrı simüle edilir. Etiket Dip / Güçlü Dip'e (puan > 0), genel skor ise +20 ve üstüne yeni geçtiğinde sonraki mum açılışında alınır; hedef fiyata ulaşınca satılır. Kâr hedefi ve komisyon panelden değiştirilebilir. Zarar kes yoktur; dönem sonunda açık işlem son kapanıştan kapatılır. Spread ve fiyat kayması hesaba katılmaz. Coin'in al-tut performansı kıyas olarak gösterilir.
- **🎯 Skor geçmişi:** Dip Skorunu geçmişteki her mum için hesaplar (yalnızca o muma kadarki veriyle) ve "skor bu etikette iken sonraki 30/90/180 gün (haftalıkta 4/13/26 hafta, aylıkta 1/3/6 ay) ne oldu" tablosunu verir.

Skor geçmişi tablosu tahmin değil, geçmişin tarifidir: bitişik mumların ileri getiri pencereleri üst üste biner, bu yüzden güvenilirliği **"Ayrı dönem"** sütunu (etikete ayrı giriş sayısı) belirler. Getiriler kapanıştan kapanışa ve komisyonsuzdur.

> **Sınırlar:** Aşırı satım, dipte olunduğunu garanti etmez; düşüş trendinde göstergeler uzun süre Dip tarafında kalabilir, güçlü yükselişte uzun süre Şişmiş tarafında kalabilir.
> Skor dip/tepe tahmini değil, göstergelerin aşırılık durumunun özetidir.

## Kurulum

```
pip install -r requirements.txt
cp .env.example .env      # TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID değerlerini doldur (.env'i paylaşma!)
```

Chat ID'ni bulmak için: botuna Telegram'dan bir mesaj at, sonra `python check_signals.py --get-chat-id`.

## Kullanım

```
python check_signals.py --report --dry-run   # raporu ekrana yazar, göndermez
python check_signals.py --report             # raporu hemen Telegram'a gönderir
python check_signals.py                      # günde bir rapor (zamanlanmış çalışma bunu kullanır)
streamlit run app.py                         # panel
```

## Otomatik çalışma (GitHub Actions)

`.github/workflows/check.yml` her gün 08:30 UTC'de (Türkiye saatiyle 11:30) çalışır ve `state.json` dosyasını commit'ler.
Repo ayarlarında **Secrets** olarak `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` tanımlı olmalı.
Aynı anda Windows Görev Zamanlayıcı (`run_check.bat`) kullanma, iki ayrı rapor gelir.
Golden/dead cross uyarıları kaldırıldı; `state.json` içindeki eski `cross_*` anahtarları ilk çalışmada temizlenir.

## Testler

```
python tests/test_core.py        # RSI hesabı ve etiket sınırları
python tests/test_scoring.py     # genel skor, nihai etiket, skor geçmişi
python tests/test_backtest.py
python tests/test_check.py       # rapor ve durum akışı
python tests/test_news.py
python tests/test_sentiment.py
```

Hiçbiri internet veya Telegram gerektirmez.

## Dosya yapısı

- `core/indicators.py`: etiketler, puanlar, gösterge sınıfları (şimdilik RSI) ve kayıt defteri
- `core/scoring.py`: genel skor, nihai etiket, skor geçmişi ve ileri getiri tabloları
- `core/data.py`: borsa verisi (kapanmamış mumlar atılır)
- `core/backtest.py`: gösterge ve genel skor backtestleri, coin al-tut kıyası
- `core/sentiment.py`: Fear & Greed endeksi (yalnızca bilgi)
- `core/news.py`: RSS haberlerini UI'da sınıflandırma ve hype analizi
- `core/notifier.py`: Telegram
- `check_signals.py`: rapor ve Telegram akışı · `app.py`: Streamlit paneli
