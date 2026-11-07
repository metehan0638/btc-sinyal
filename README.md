# Kripto Takip Paneli

BTC/USDT, ETH/USDT, SOL/USDT ve XRP/USDT için göstergeleri değerlerine göre **5 dereceden birine** yerleştiren Streamlit paneli ve coin durumu Telegram raporu:
`🟢🟢 güçlü al · 🟢 al · ⚪ nötr · 🔴 sat · 🔴🔴 güçlü sat`.

Mantık: **AL tarafı fiyatın ucuz/dip tarafında (şişmiş değil), SAT tarafı şişmiş tarafında** olduğunu gösterir.
Örneğin RSI 0'a yaklaşırsa ya da Fear & Greed çok düşerse AL tarafına yazılır.
Bu dereceler göstergelerin değerlerine bakan **mekanik etiketlerdir; emir ya da yatırım tavsiyesi değildir.**

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
| Ichimoku (9/26/52) | güçlü yükseliş görünümü | fiyat bulut üstünde | bulut içinde | fiyat bulut altında | güçlü düşüş görünümü |

Golden/dead cross aralıkla değil duruma göre puanlanır: hızlı SMA yavaşın üstündeyse AL (+1), altındaysa SAT (-1);
kesişim yeniyse (günlük 14, haftalık 4, aylık 2 mum içinde) güçlü AL/SAT (±2). Kesişim gecikmeli bir onay sinyalidir.
Günlük ve haftalıkta SMA 50/200, aylıkta SMA 6/18 kullanılır.
Yeni bir kesişim olunca raporun dışında ayrıca uyarı gelir (`cross_alerts: false` ile kapatılır).

Her gösterge kendi verisinden bağımsız derece alır. Panelde göstergeler ayrı ayrı gösterilir ve backtest edilir. Telegram raporunda ise her zaman diliminin göstergeleri iki ayrı **skorda** özetlenir (aşağıya bak).
Ichimoku'da fiyat bulutun üstünde/altında/içinde oluşuna, Tenkan-Kijun ilişkisine ve bulut yönüne bakılır.
Günlük, haftalık ve aylık göstergeler ayrı değerlendirilir. Eşikler `config.yaml` içinden değiştirilebilir; bunlar **başlangıç değerleridir**.

> **Sınırlar:** Aşırı satım, dipte olunduğunu garanti etmez; düşüş trendinde göstergeler uzun süre AL tarafında kalabilir.
> Dip arayan (RSI, Mayer vb.) ve trend izleyen (cross, Ichimoku) göstergeler zaman zaman zıt sinyal verebilir.

Panelde coin seçilebilir; her coin kendi mum geçmişi, fiyatı, dereceleri ve backtest'iyle ayrı hesaplanır. BTC'nin Fear & Greed verisi genel piyasa göstergesi olarak seçili coin'in günlük derecesine eklenir.

## Skor geçmişi paneli (🎯 sekmesi)

Panelin **Skor geçmişi** sekmesi Trend ve Aşırılık skorlarını geçmişteki her mum için hesaplar (raporla aynı kurallar,
yalnızca o muma kadarki veri) ve şunları gösterir:

- Fiyatın altında iki skorun zaman çizgisi (±15 ve ±50 etiket sınırlarıyla).
- Her skor bölgesi için ("güçlü düşüş" … "güçlü yükseliş", "aşırı ısınmış" … "dip bölgesi") sonraki 30/90/180 gün
  (haftalıkta 4/13/26 hafta, aylıkta 1/3/6 ay) getirisinin ortalaması, medyanı ve pozitif olma oranı; tüm mumlar kıyas satırıdır.
- Trend × Aşırılık 3×3 tablosu: ortalama ileri getiri ve her hücredeki mum sayısı.

Bu tablolar tahmin değil, geçmişin tarifidir: bitişik mumların ileri getiri pencereleri üst üste biner, bu yüzden güvenilirliği
**"Ayrı dönem"** sütunu (skorun bölgeye ayrı giriş sayısı) belirler. Getiriler kapanıştan kapanışa ve komisyonsuzdur.
Mantık `core/scores.py` içindedir.

## Geçmiş performans paneli

Her gösterge bağımsız olarak ve ayrı başlangıç bakiyesiyle simüle edilir: derece 0 veya altından AL tarafına geçtiğinde sonraki mumun açılışında alır; hedef fiyata ulaşınca satar. Kâr hedefi ve işlem yönü başına komisyon panelden değiştirilebilir. Zarar kes yoktur; dönem sonunda açık işlem son kapanıştan kapatılır. Spread ve fiyat kayması hesaba katılmaz. Seçili coin'in al-tut performansı kıyas olarak kullanılır. Yeterli geçmişi olmayan gösterge tabloda ayrıca işaretlenir.

## Raporda ne var?

Telegram raporunda gösterge listesi yoktur; her coin için yalnızca skor çubukları gösterilir:

- Coin başlığının altında **günlük kapanış fiyatı** (son kapanmış günlük mumun tarihiyle).
- Günlük, haftalık ve aylık için ayrı ayrı **Trend** ve **Aşırılık** skoru (-100 … +100) ve metin çubukları.
- Son mesajın sonunda çubukların kısa açıklaması ve **Fear & Greed** değeri (dün / 7 / 30 gün önceki değerleriyle).

**Neden iki skor?** Göstergelerin yarısı "yön" (trend), yarısı "ucuz/pahalı" (aşırılık) ölçer. Güçlü bir yükselişte trend göstergeleri
yukarı, aşırılık göstergeleri aşağı yönlü olur; tek ortalamada birleşince skor 0'a düşüp yanıltıcı olurdu. İki skor ayrı gösterilir:

| Skor | Göstergeler | +100 | -100 |
|---|---|---|---|
| 📈 **Trend** | Ichimoku, golden/dead cross, fiyatın uzun ortalamaya göre yönü (üstünde +1, altında -1) | güçlü yükseliş | güçlü düşüş |
| 🌡️ **Aşırılık** | Momentum osilatörleri (RSI, Stochastic RSI, Bollinger %B, MFI: önce kendi içinde ortalanıp **tek oy**), fiyat/uzun ortalama oranı, ATH'den uzaklık, Fear & Greed (yalnızca günlük) | dip bölgesi (ucuz) | aşırı ısınmış (pahalı) |

**Hesap:** Gruptaki geçerli oyların dereceleri (-2 … +2) eşit ağırlıkla ortalanır, 50 ile çarpılır. Verisi olmayan oy hesaba katılmaz.
RSI, StochRSI, Bollinger ve MFI hepsi kısa vadeli momentum aşırılığını ölçtüğü için dördü ayrı oy sayılmaz: önce ortalanıp tek oy olur. Böylece Aşırılık skorunun çoğunu tek bir bilgi (kısa vadeli momentum) belirlemez; oylar osilatörler, fiyat/ortalama oranı, ATH'den uzaklık ve (günlükte) Fear & Greed olur.
Etiket sınırları: ≥ +50 güçlü · ≥ +15 orta · -15 … +15 nötr (trendde "yatay", aşırılıkta "dengeli") · ≤ -15 · ≤ -50 güçlü zıt yön.
Bir okuma örneği: *Trend +80, Aşırılık -60* = güçlü yükseliş sürüyor ama fiyat ısınmış.
Skorlar mekanik bir özettir; emir ya da yatırım tavsiyesi değildir.

```
🪙 BTC — Bitcoin
💰 Günlük kapanış (05.11.2026): 78,002.14 USDT

📅 GÜNLÜK
📈 Trend: +33 · 🟢 Yükseliş
-100 ░░░░░░░░░░│░░●░░░░░░░ +100
🌡️ Aşırılık: -20 · 🔴 Isınmış
-100 ░░░░░░░░░●░│░░░░░░░░░░ +100
```

- Haberler ve hype özeti yalnızca panelin Haber / Hype sekmesinde gösterilir.
- Telegram'da yalnızca bu skor raporu ve yeni golden/dead cross uyarıları gönderilir.

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
python tests/test_charting.py
python tests/test_backtest.py
python tests/test_check.py
python tests/test_scores.py
python tests/test_news.py
python tests/test_sentiment.py
```

Hiçbiri internet veya Telegram gerektirmez.

## Dosya yapısı

- `core/zones.py`: göstergeler, derece aralıkları, golden/dead cross
- `core/data.py`: borsa verisi (kapanmamış mumlar atılır)
- `core/backtest.py`: bağımsız gösterge backtestleri ve coin al-tut kıyası
- `core/scores.py`: Trend/Aşırılık skorunun tarihsel serisi ve ileri getiri tabloları
- `core/sentiment.py`: Fear & Greed endeksi
- `core/news.py`: RSS haberlerini UI'da sınıflandırma ve hype analizi
- `core/notifier.py`: Telegram
- `check_signals.py`: rapor ve Telegram akışı · `app.py`: Streamlit paneli
