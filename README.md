# BTC Uzun Vade Sinyal Projesi

## Kurulum
    pip install -r requirements.txt
    (.env.example dosyasını ".env" adıyla kopyala, token ve chat id'yi doldur)

## Çalıştırma
    python -m streamlit run app.py            # panel (grafik, backtest)
    python check_signals.py --dry-run         # bildirimleri ekrana yazar
    python check_signals.py --get-chat-id     # botuna mesaj attıktan sonra chat id bulur
    python check_signals.py --test            # Telegram test mesajı
    python check_signals.py --status          # güncel durumu Telegram'a gönderir
    python check_signals.py                   # normal kontrol (günde bir kez zamanlanır)

## Test (internet gerekmez)
    python tests/test_core.py
    python tests/test_check.py
