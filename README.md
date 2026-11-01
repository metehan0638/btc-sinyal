# BTC Gösterge Dereceleri

Bitcoin için birkaç göstergeyi değerlerine göre **5 dereceden birine** yerleştiren ve her gün Telegram'a rapor gönderen bir araç:
`🟢🟢 güçlü al · 🟢 al · ⚪ nötr · 🔴 sat · 🔴🔴 güçlü sat`.

Mantık: **AL tarafı fiyatın ucuz/dip tarafında (şişmiş değil), SAT tarafı şişmiş tarafında** olduğunu gösterir.
Örneğin RSI 0'a yaklaşırsa ya da Fear & Greed çok düşerse AL tarafına yazılır.
Bu dereceler göstergelerin değerlerine bakan **mekanik etiketlerdir; emir ya da yatırım tavsiyesi değildir.** Backtest yoktur.

## Dereceler (varsayılan aralıklar)

| Gösterge | 🟢🟢 Güçlü al (≤) | 🟢 Al (≤) | ⚪ Nötr | 🔴 Sat (≥) | 🔴🔴 Güçlü sat (≥) |
|---|---|---|---|---|---|
| RSI(14) | 20 | 30 | arası | 70 | 80 |
| Stochastic RSI (%K) | 10 | 20 | arası | 80 | 90 |
| Bollinger %B | 0.0 | 0.10 | arası | 0.90 | 1.0 |
| MFI(14) | 10 | 20 | arası | 80 | 90 |
| Fiyat / 200 ortalama, günlük (Mayer) | 0.8 | 1.0 | arası | 2.0 | 2.4 |
| Fiyat / 200 ortalama, haftalık | 1.0 | 1.3 | arası | 2.5 | 3.5 |
| Fiyat / 24 ortalama, aylık | 1.0 | 1.3 | arası | 2.5 | 3.5 |
| ATH'den uzaklık (%) | -65 | -50 | arası | -10 | -3 |
| Fear & Greed (yalnızca günlük) | 10 | 24 | arası | 75 | 90 |
| Golden/Dead cross (zaman dilimine özel SMA) | yeni golden cross | golden yapısı | — | dead yapısı | yeni dead cross |

Golden/dead cross aralıkla değil duruma göre puanlanır: hızlı SMA yavaşın üstündeyse AL (+1), altındaysa SAT (-1);
kesişim yeniyse (günlük 14, haftalık 4, aylık 2 mum içinde) güçlü AL/SAT (±2). Kesişim gecikmeli bir onay sinyalidir.
Günlük ve haftalıkta SMA 50/200, aylıkta SMA 6/18 kullanılır.
Yeni bir kesişim olunca raporun dışında ayrıca uyarı gelir (`cross_alerts: false` ile kapatılır).

**Genel derece:** her gösterge -2 (güçlü sat) ile +2 (güçlü al) arasında puan alır; geçerli göstergelerin ortalaması
≥ 1.0 güçlü al, ≥ 0.3 al, -0.3 ile 0.3 arası nötr, ≤ -0.3 sat, ≤ -1.0 güçlü sat demektir.
Günlük, haftalık ve aylık ayrı değerlendirilir. Tüm aralıklar ve sınırlar `config.yaml` içinden değiştirilebilir; bunlar **başlangıç değerleridir**.

> **Sınırlar:** Aşırı satım, dipte olunduğunu garanti etmez; düşüş trendinde göstergeler uzun süre AL tarafında kalabilir.
> Dip arayan (RSI, Mayer vb.) ve trend izleyen (cross) göstergeler zaman zaman zıt yönde okunur; ortalama bunları birbirine karıştırır.
> Rapor bir bağlam bilgisidir, tek başına karar dayanağı değildir.

## Raporda ne var?

- Günlük, haftalık ve aylık: genel derece, puan ortalaması, her göstergenin değeri ve derecesi (güçlü al'dan güçlü sat'a gruplanmış), bir önceki rapora göre derece değişimi
- Fear & Greed (günlük puana dahil) ve haber/hype raporu (kategori ve coin bazında öne çıkan konular)
- Yeni golden/dead cross olunca ayrı bir uyarı mesajı

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
streamlit run app.py                         # panel: tablo, grafik, haberler
```

## Otomatik çalışma (GitHub Actions)

`.github/workflows/check.yml` her gün 08:30 UTC'de (Türkiye saatiyle 11:30) çalışır ve `state.json` dosyasını commit'ler.
Repo ayarlarında **Secrets** olarak `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` tanımlı olmalı.
Aynı anda Windows Görev Zamanlayıcı (`run_check.bat`) kullanma, iki ayrı rapor gelir.

## Testler

```
python tests/test_core.py
python tests/test_check.py
python tests/test_news.py
python tests/test_sentiment.py
```

Hiçbiri internet veya Telegram gerektirmez.

## Dosya yapısı

- `core/zones.py`: göstergeler, derece aralıkları, genel derece, golden/dead cross
- `core/data.py`: borsa verisi (kapanmamış mumlar atılır)
- `core/sentiment.py`: Fear & Greed endeksi
- `core/news.py`: RSS haber/hype raporu
- `core/notifier.py`: Telegram
- `check_signals.py`: rapor ve Telegram akışı · `app.py`: Streamlit paneli
