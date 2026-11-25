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
| 0–20 | Güçlü Dip |
| 21–34 | Dip |
| 35–69 | Nötr |
| 70–79 | Şişmiş |
| 80–100 | Aşırı Şişmiş |

Endeks **günlüktür**: günlük mumda o günün değeri, **haftalık mumda o haftanın 7 günlük ortalaması, aylık mumda o ayın günlük ortalaması** kullanılır.
Ortalama tam sayı olmayabileceği için ara değerler en yakın tam sayıya yuvarlanıp (x,5 yukarı) tabloya uygulanır (< 20,5 Güçlü Dip, < 34,5 Dip, < 69,5 Nötr, < 79,5 Şişmiş, aksi halde Aşırı Şişmiş); tam sayı değerlerde yukarıdaki tabloyla birebir aynıdır.
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

## Grafik Yorumu

Günlük Telegram raporuna, Dip Skoru çubuklarının altına **"📐 Grafik Yorumu"** bölümü eklenir: grafiğin *şu an ne gösterdiğini* anlatan kısa, kural tabanlı
Türkçe bir metin. **LLM veya harici yapay zeka yoktur ve sistem fiyat tahmini yapmaz.** Varsayılan olarak 4 saatlik ve günlük grafik için ayrı birer paragraf üretilir (aşağıdaki örnekte 4 saatlik paragraf sentetik, günlük paragraf gerçek BTC verisinden):

```
📐 Grafik Yorumu

Bitcoin şu an 4 saatlik grafikte yatay seyrediyor (belirgin bir yön sinyali yok). Yükselen üçgen formasyonu oluşuyor (bullish): direnç yatay (110), dipler yükseliyor. Güçlü destek: 107, güçlü direnç: 110.

Bitcoin şu an günlük grafikte kararsız bir görünümde (sinyaller birbiriyle çelişiyor). Belirgin bir formasyon tespit edilmedi. Güçlü destek: 75.000, güçlü direnç: 87.300.

ℹ️ Teknik analiz kesinlik taşımaz, yatırım tavsiyesi değildir.
```

**Nasıl karar verir** (`core/chart_analysis.py`, yalnızca pandas ve numpy; scipy gerekmez):

- **Trend:** 4 sinyal oylanır: EMA50/EMA200 ilişkisi, fiyatın EMA'lara göre yeri, son pivotlarda tepe/dip dizilimi (HH/HL, LH/LL) ve lineer regresyon eğimi. En az 3 sinyal aynı yönü
  göstermeli ve hiçbiri ters olmamalı; aksi halde yön **verilmez** ("yatay", çelişki varsa "kararsız").
- **Destek/direnç:** Pivot tepe ve dipler fiyat yüzdesiyle (varsayılan %0,5) kümelenir; küme dokunuş sayısı, yenilik ve hacimle skorlanır. Fiyatın altındaki en güçlü küme destek,
  üstündeki direnç olur; yoksa "belirgin seviye tespit edilmedi" denir. Fiyattan %25'ten uzak seviyeler raporlanmaz. Güç skoru düşükse "güçlü" denmez.
- **Formasyon:** yükselen/alçalan/simetrik üçgen, yükselen/düşen kama, çift tepe/dip. Tepeler ve dipler ayrı ayrı doğruya oturtulur; eğim, yakınsama, pivot sayısı (her çizgi için en az 3) ve fiyatın çizgiler
  içinde kalması kontrol edilir. Her formasyona 0-1 güven skoru verilir; **eşiğin (0,72) altındakiler raporlanmaz, formasyon uydurulmaz**. Birden çok aday çıkarsa en yüksek skorlu seçilir.
  Çift tepe/dip için ilk zirveye belirgin bir yükseliş/düşüş gelmiş olmalı ve zirve tam iki kez test edilmiş olmalı (yatay aralık çift formasyon sayılmaz). Bayrak/flama desteklenmiyor.

**Güvenilirlik ölçümü (sentetik veriyle, gerçek piyasa kanıtı değildir):** formasyon içermeyen rastgele fiyat yürüyüşlerinde yanlış formasyon çıkma oranı ortalama **%2,8** (eşikler gevşek bırakılsaydı %9,7 idi);
bilerek kurulmuş formasyonların tamamı bulunuyor, gürültü 3 kat artsa bile %90-100'ü bulunuyor. Gerçek piyasada formasyonlar bu kadar temiz olmadığı için tespit oranı daha düşük olabilir; belirsiz durumda sistem sessiz kalmayı seçer.

**Veri:** 1d/1w/1M için rapor için zaten çekilen geçmiş yeniden kullanılır. 4 saatlik için mevcut Binance public endpoint çözümüyle (`BINANCE_PUBLIC_URL`) ~600 mum çekilir
(EMA200 için en az 250 mum gerekir; daha azı "yeterli mum yok" sayılır). Yeni veri kaynağı ve yeni bağımlılık yoktur.

**Hata davranışı:** Bir periyodun verisi alınamaz veya hesap başarısız olursa yalnızca o paragraf "…grafik için yorum üretilemedi (…)" olur; hepsi başarısızsa bölüm "Grafik yorumu şu an üretilemedi."
yazar. Rapor ve Dip Skoru her durumda normal gider, hata terminale/log'a yazılır. Mesaj 3800 karakteri aşarsa önce kısa yorum sürümü, o da sığmazsa yorum atlanır. Telegram düz metin
gönderildiği için özel karakter kaçırma gerekmez.

**Eşikleri ayarlama:** `config.yaml` → `chart_comment`:

```yaml
chart_comment:
  enabled: true                    # kapatmak için false
  timeframes: ["4h", "1d"]         # 1h, 2h, 4h, 6h, 12h, 1d, 1w, 1M
  candles: {"4h": 600}             # ek periyotlar için çekilecek mum sayısı
  params: {sr_tolerance_pct: 0.7}  # tüm periyotlar için eşik değişikliği
  timeframe_params:                # yalnızca bir periyot için
    "4h": {pattern_min_score: 0.8}
```

Tüm eşikler ve anlamları `core/chart_analysis.py` dosyasının başındaki `DEFAULT_PARAMS` içindedir (kodda gömülü sayı yoktur). En sık ayarlananlar: `pattern_min_score` (yükselt = daha az ama daha emin formasyon),
`sr_tolerance_pct` (destek/direnç kümeleme genişliği), `sr_max_distance_pct`, `trend_min_agree` (kaç sinyal aynı yönde olmalı), `pivot_order` (pivot hassasiyeti), `pattern_window` (formasyon aranan mum sayısı).
Bilinmeyen bir eşik adı yazarsan (yazım hatası) bölüm atlanır ve log'a "ayar hatası" yazılır, rapor yine gider.

## Haftalık RSI alarmı (50 coin)

Bitcoin Dip Skorundan **bağımsız** ayrı bir özelliktir: `config.yaml` içindeki `rsi_scan.coins` listesindeki (varsayılan 50 coin) coinlerin
**kapanmış haftalık mumlarından Wilder RSI(14)** hesaplanır ve yalnızca **RSI < 28** olan coinler Telegram'a bildirilir.
RSI 28.00 veya üzerindeki hiçbir coin mesajda, panel tablosunda veya durum dosyasındaki alarm listesinde yer almaz.
Karar kuralı koda sabitlenmiştir (`core/rsi_scan.py`: `THRESHOLD = 28.0`, koşul kesinlikle `rsi < 28`); günlük/4 saatlik RSI kullanılmaz.

**Telegram mesajı** (RSI'a göre küçükten büyüğe; gerçek veriyle):

```
🔔 HAFTALIK RSI TARAMA RAPORU

📅 Tarama Tarihi: 17.11.2026

🚨 Haftalık RSI 28'in Altındaki Coinler:

1. SOL/USDT — RSI: 21.75
2. BTC/USDT — RSI: 24.63

📊 Toplam Tespit: 2 / 50
```

Hiçbir coin 28'in altında değilse `✅ Haftalık RSI değeri 28'in altında olan coin bulunamadı.` ve `📊 Taranan Coin: 50` gönderilir.
Bazı coinlerin verisi alınamazsa tarama durmaz: o coinler taramaya dahil edilmez, adları mesajda yer almaz ve mesajın sonuna yalnızca
`⚠️ Verisi alınamayan coin: N` satırı eklenir (ayrıntı panelde ve terminalde). Hiçbir coinin verisi alınamazsa "coin bulunamadı" denmez, tarama hata sayılır.
Mesajdaki RSI değeri normal yuvarlanır; yalnızca 28'in altındaki bir değer yuvarlanınca 28.00 olacaksa 27.99 yazılır.

**Veri kuralları:** yalnızca kapanmış haftalık mumlar kullanılır (içinde bulunulan haftanın mumu RSI'a dahil edilmez); tarihler sıralanır, tekrarlar atılır,
eksik haftası veya bozuk fiyatı olan veri kısaltılır. En az **100 kapanmış haftalık mum** şarttır, daha kısa geçmişli coin "veri yetersiz" sayılıp taramadan çıkar.
Bu sınır ölçülerek seçildi: Wilder RSI başlangıç tohumundan etkilendiği için 52 haftalık geçmişle eşik civarında RSI %95 olasılıkla 1,7 puan sapar,
75 haftada 0,4 puan, **100 haftada 0,06 puan**, 150 haftada ~0 puan. Bu yüzden 200 hafta çekilir. RSI projedeki `wilder_rsi` fonksiyonuyla hesaplanır
(Bitcoin göstergeleriyle aynı yöntem; TradingView ile aynı). Geçici ağ/hız sınırı hatalarında coin başına bir kez yeniden denenir, coinler arasında kısa bekleme vardır.

### Nasıl çalıştırılır (birini seç, ikisini birden kullanma)

1. **Streamlit paneli açıkken uygulama içi zamanlayıcı (varsayılan):** `streamlit run app.py` ile paneli aç; **🔔 Haftalık RSI Alarmı** sekmesi arka plan zamanlayıcısını çalıştırır.
   Her gün Türkiye saatiyle `rsi_scan.time` (varsayılan **09:00**) saatinde bir kez tarar. Uygulama yeniden başlayınca zamanlama yeniden kurulur; bugünkü saat geçmiş ve bugün otomatik
   tarama yapılmamışsa hemen çalışır. Aynı gün ikinci otomatik tarama yapılmaz (durum `rsi_scan_state.json` dosyasında tutulur). Telegram gönderimi başarısız olursa gün "yapıldı"
   sayılmaz; 15 dakika arayla günde en çok 3 kez denenir. Yalnızca uygulama (Streamlit sunucusu) çalıştığı sürece tarar.
2. **Komut satırı:** `python scan_rsi.py` (bugün otomatik tarama yapılmadıysa tarar ve gönderir; saat kontrolü yapmaz, zamanı Windows Görev Zamanlayıcı/cron belirler).
   `--force` hemen tarar ve gönderir, `--dry-run` mesajı ekrana yazar (göndermez, kaydetmez), `--test-telegram` bağlantı testi gönderir.
3. **GitHub Actions:** `.github/workflows/rsi-scan.yml` (varsayılan olarak yalnızca elle çalışır; günlük çalışma için dosyadaki `schedule` satırlarını aç).

Aynı anda yalnızca bir tarama çalışır (`rsi_scan.lock` kilit dosyası). Aynı makinede çift mesajı bu kilit ve durum dosyası önler; farklı makinelerde (örn. yerel panel + GitHub Actions) önleyemez.

### Panel bölümü

Panel sekmesi şunları gösterir: otomatik taramanın aktif/pasif durumu (açıp kapatabilirsin), tarama saati (değiştirebilirsin), bir sonraki tarama zamanı, son taramanın tarihi/saati,
taranan coin sayısı, **yalnızca RSI < 28 olan coinler ve RSI değerleri** tablosu, **Şimdi tara** düğmesi (arka planda çalışır, arayüzü kilitlemez; ilerleme çubuğu gösterilir;
sonucu Telegram'a göndermek için kutu işaretlenir) ve **Telegram test mesajı gönder** düğmesi. Sonuçlar tarama bitince kendiliğinden güncellenir.

### Ayarlar (`config.yaml` → `rsi_scan`)

`enabled`, `time` (SS:DD, Türkiye saati), `period`, `min_weeks`, `fetch_weeks`, `retries`, `retry_delay`, `request_pause` ve `coins` (kısaltma listesi, örn. `BTC` veya `BTC/USDT`).
**Varsayılan 50 coin listesi benim tahminimdir** (Binance'te uzun süredir USDT paritesi olan büyük coinler); kendi listeni yaz. Pariteye ulaşılamayan coin atlanır ve "verisi alınamayan" sayılır.
Telegram için `.env` dosyasına `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` yazılır (aşağıdaki Kurulum bölümü).

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
python scan_rsi.py --dry-run                 # haftalık RSI taramasını yapar, mesajı ekrana yazar (göndermez)
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
python tests/test_rsi_scan.py    # haftalık RSI alarmı: eşik, mesaj, veri bütünlüğü, zamanlayıcı, kilit, komut satırı
python tests/test_chart_analysis.py  # grafik yorumu: trend, destek/direnç, formasyonlar, uç durumlar, metin
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
- `core/chart_analysis.py`: grafik yorumu (trend, destek/direnç, formasyon tespiti ve Türkçe metin; saf fonksiyonlar)
- `core/rsi_scan.py`: haftalık RSI alarmı (tarama, mesaj, durum, kilit, zamanlayıcı) · `scan_rsi.py`: komut satırı
- `check_signals.py`: rapor ve Telegram akışı · `app.py`: Streamlit paneli
