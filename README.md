# BTC Dip / Şişkinlik Raporu

Bitcoin fiyatının **dipte mi, şişmiş mi** olduğunu birkaç göstergeyle ölçen ve her gün Telegram'a rapor gönderen bir araç.
**AL/SAT sinyali üretmez, backtest yapmaz.** Amaç: fiyatın ucuz tarafta olup olmadığını, şişmediğini görmek.

Mantık basit: her gösterge için değer, "ucuz / normal / şişmiş" bölgelerinden birine yerleştirilir.

| Gösterge | 🟢 Ucuz / dip (≤) | 🔴 Şişmiş (≥) |
|---|---|---|
| RSI(14) | 30 | 70 |
| Stochastic RSI (%K) | 20 | 80 |
| Bollinger %B | 0.10 | 0.90 |
| MFI(14) | 20 | 80 |
| Fiyat / 200 ortalama (günlük) | 0.85 | 2.0 |
| Fiyat / 200 ortalama (haftalık) | 1.2 | 3.5 |
| ATH'den uzaklık | %-50 | %-10 |
| Fear & Greed (yalnızca günlük) | 24 | 75 |

Genel karar: göstergelerin **en az yarısı** ucuz bölgedeyse `🟢 DİP BÖLGESİ`, en az yarısı şişmiş bölgedeyse `🔴 ŞİŞMİŞ BÖLGE`, değilse `⚪ NORMAL`.
Günlük ve haftalık mumlar ayrı değerlendirilir. Bu eşikler **başlangıç değerleridir**; `config.yaml` içinden değiştirilebilir.

> **Sınırlar:** Aşırı satım, dipte olunduğunu garanti etmez; düşüş trendinde göstergeler uzun süre ucuz bölgede kalabilir.
> "Şişmiş değil" de "ucuz" demek değildir. Rapor bir bağlam bilgisidir, tek başına karar dayanağı değildir. Yatırım tavsiyesi değildir.

## Raporda ne var?

- Günlük ve haftalık: genel bölge, her göstergenin değeri ve hangi bölgede olduğu, bir önceki rapora göre bölge değişimi
- Fear & Greed (günlük puana dahil) ve haber/hype raporu (kategori ve coin bazında öne çıkan konular)

## Kurulum

```
pip install -r requirements.txt
cp .env.example .env      # TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID değerlerini doldur (.env'i paylaşma!)
```

Chat ID'ni bulmak için: botuna Telegram'dan bir mesaj at, sonra `python check_signals.py --get-chat-id`.

## Kullanım

```
python check_signals.py --dry-run     # raporu ekrana yazar, göndermez
python check_signals.py --report      # raporu hemen Telegram'a gönderir
python check_signals.py               # günde bir rapor (zamanlanmış çalışma bunu kullanır)
streamlit run app.py                  # panel: tablo, grafik, haberler
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

- `core/zones.py`: göstergeler, bölge eşikleri ve genel karar
- `core/data.py`: borsa verisi (kapanmamış mumlar atılır)
- `core/sentiment.py`: Fear & Greed endeksi
- `core/news.py`: RSS haber/hype raporu
- `core/notifier.py`: Telegram
- `check_signals.py`: rapor ve Telegram akışı · `app.py`: Streamlit paneli
