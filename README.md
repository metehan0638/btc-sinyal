# Kripto Takip Paneli

Bitcoin (BTC/USDT) için fiyatın **dipte mi yoksa aşırı yükselmiş mi** olduğunu ölçen bir Streamlit paneli ve Telegram raporu.
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

## Tanımlı göstergeler

### 1. RSI

RSI(14), Wilder yöntemiyle (TradingView ile aynı) hesaplanır ve şu etiketlere çevrilir. RSI yalnızca etiket üretir, ayrı bir skoru yoktur.

| RSI | Etiket |
|---|---|
| < 30 | Güçlü Dip |
| 30 ≤ RSI ≤ 35 | Dip |
| 35 < RSI < 65 | Nötr |
| 65 ≤ RSI ≤ 70 | Şişmiş |
| > 70 | Aşırı Şişmiş |

### 2. Bollinger %B

%B, fiyatın Bollinger bantları içindeki konumunu ölçer. Bantlar SMA(20) ± 2 standart sapmadır (popülasyon sapması, TradingView'daki varsayılan ayar).

%B = (kapanış − alt bant) / (üst bant − alt bant)

- %B = 0 alt banda, 0,5 orta banda, 1 üst banda eşittir; %B < 0 alt bandın altı, %B > 1 üst bandın üstüdür.
- Fiyat 20 mum boyunca hiç değişmediyse bant genişliği 0 olur, %B tanımsızdır ve gösterge o mumda hesaba girmez.

| %B | Etiket |
|---|---|
| < 0 | Güçlü Dip |
| 0 ≤ %B ≤ 0,10 | Dip |
| 0,10 < %B < 0,90 | Nötr |
| 0,90 ≤ %B ≤ 1 | Şişmiş |
| > 1 | Aşırı Şişmiş |

Periyot ve çarpan `config.yaml` içindeki `bollinger:` bölümünden değiştirilebilir (varsayılan 20 ve 2).
İki gösterge eşit ağırlıkla ortalanır: örneğin RSI Nötr (0) ve %B Güçlü Dip (+100) ise genel skor **+50 (Dip)** olur.
Bollinger %B ilk 19 mumda henüz hesaplanamaz; o mumlarda skor yalnızca diğer göstergelerden gelir (nötr sayılmaz).

### 3. MFI (Money Flow Index)

RSI benzeri bir momentum göstergesidir ama fiyat hareketinin yanında **işlem hacmini** de hesaba katar. Standart periyot 14, değer aralığı 0–100.
Tipik fiyat = (yüksek + düşük + kapanış) / 3; para akışı = tipik fiyat × hacim. Tipik fiyat bir öncekinden yüksekse pozitif, düşükse negatif akış sayılır.
MFI = 100 − 100 / (1 + son 14 mumun pozitif akış toplamı / negatif akış toplamı) (TradingView `ta.mfi` ile aynı).

| MFI | Etiket |
|---|---|
| < 20 | Güçlü Dip |
| 20 ≤ MFI ≤ 30 | Dip |
| 30 < MFI < 70 | Nötr |
| 70 ≤ MFI ≤ 80 | Şişmiş |
| > 80 | Aşırı Şişmiş |

### 4. Fear & Greed endeksi

Kripto piyasasındaki yatırımcı psikolojisini 0–100 arasında ölçer (düşük = korku, yüksek = açgözlülük). Kaynak: alternative.me, anahtar gerekmez.

| Fear & Greed | Etiket |
|---|---|
| 0–10 | Güçlü Dip |
| 11–34 | Dip |
| 35–65 | Nötr |
| 66–89 | Şişmiş |
| 90–100 | Aşırı Şişmiş |

Endeks **günlüktür**: günlük mumda o günün değeri, **haftalık mumda o haftanın 7 günlük ortalaması, aylık mumda o ayın günlük ortalaması** kullanılır.
Ortalama tam sayı olmayabileceği için sınırlar sürekli uygulanır (≤ 10 Güçlü Dip, 10–35 Dip, 35–65 Nötr, 65–90 Şişmiş, ≥ 90 Aşırı Şişmiş); tam sayı değerlerde yukarıdaki tabloyla birebir aynıdır.
Haftanın veya ayın bir günü bile eksikse o mum için değer üretilmez (yarım veriden ortalama alınmaz).

### 5. MVRV Z-Score (yalnızca Bitcoin)

Piyasa değerinin gerçekleşmiş değere (Realized Cap) göre tarihsel olarak ne kadar yüksek/düşük olduğunu ölçer.

MVRV Z-Score = (Piyasa değeri − Gerçekleşmiş değer) / Piyasa değerinin standart sapması

Gerçekleşmiş değer = piyasa değeri / MVRV; standart sapma verinin başından o güne kadarki tüm piyasa değeri geçmişinden hesaplanır (bir günün skoru yalnızca o güne kadarki veriye dayanır).

| MVRV Z-Score | Etiket |
|---|---|
| < 0 | Güçlü Dip |
| 0 – 1 | Dip |
| 1 – 3 | Nötr |
| 3 – 7 | Şişmiş |
| ≥ 7 | Aşırı Şişmiş |

Sınır değerlerde alt sınır dahildir (Z = 1 Nötr, Z = 7 Aşırı Şişmiş). Fear & Greed gibi günlük veridir; haftalık/aylıkta dönemin günlük ortalaması alınır (`config.yaml`'da `aggregate: last` yazarsan dönemin son günü).

**Veri kaynağı:** Coin Metrics Community API (`CapMrktCurUSD` ve `CapMVRVCur`, ücretsiz, anahtarsız). Lisansı **ticari olmayan kullanım** içindir (CC BY-NC 4.0).
Yayın gecikmesine karşı son bilinen değer en fazla 2 gün ileri taşınır (`max_stale_days`). Veri alınamazsa en fazla 5 gün eski önbellek kullanılır; yoksa gösterge o çalışmada hesaba girmez.

> **Dış veri göstergeleri hakkında:** Fear & Greed ve MVRV internetten gelir. Alınamazlarsa hata vermez, o gösterge o çalışmada **hesaba girmez** (nötr sayılmaz) ve rapor satırında `(4/5 gösterge)` gibi aktif sayı görünür.

Fear & Greed değeri ayrıca rapor sonunda ve panelde bilgi satırı olarak gösterilir (`config.yaml`: `fear_greed_info`).

## Yeni gösterge eklemek

Genel skor algoritmasına dokunmak gerekmez:

1. `core/indicators.py` içinde `Indicator`'dan türeyen bir sınıf yaz: `compute(df, tf)` varlığın kapanmış mumlarıyla aynı indekste ham değer serisini, `classify(değer)` 5 etiketten birini (değer yoksa `None`) döndürür.
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
python tests/test_core.py        # RSI, Bollinger %B, MFI, Fear & Greed, MVRV: hesap ve etiket sınırları
python tests/test_external.py    # günlük verinin haftalık/aylık mumlara hizalanması
python tests/test_mvrv.py        # MVRV Z-Score hesabı ve Coin Metrics veri çekimi
python tests/test_scoring.py     # genel skor, nihai etiket, skor geçmişi
python tests/test_backtest.py
python tests/test_check.py       # rapor ve durum akışı
python tests/test_news.py
python tests/test_sentiment.py
```

Hiçbiri internet veya Telegram gerektirmez.

## Dosya yapısı

- `core/indicators.py`: etiketler, puanlar, gösterge sınıfları (RSI, Bollinger %B, MFI, Fear & Greed, MVRV) ve kayıt defteri
- `core/external.py`: günlük dış verileri mumlara hizalama (haftalık/aylık ortalama) ve süreç içi önbellek
- `core/mvrv.py`: MVRV Z-Score verisi ve hesabı (Coin Metrics)
- `core/scoring.py`: genel skor, nihai etiket, skor geçmişi ve ileri getiri tabloları
- `core/data.py`: borsa verisi (kapanmamış mumlar atılır)
- `core/backtest.py`: gösterge ve genel skor backtestleri, coin al-tut kıyası
- `core/sentiment.py`: Fear & Greed endeksi verisi (alternative.me)
- `core/news.py`: RSS haberlerini UI'da sınıflandırma ve hype analizi
- `core/notifier.py`: Telegram
- `check_signals.py`: rapor ve Telegram akışı · `app.py`: Streamlit paneli
