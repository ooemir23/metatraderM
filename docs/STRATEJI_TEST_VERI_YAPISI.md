# Strateji testi için veri ve bağlantı yapısı

3 Ekim 2026 incelemesi. Aşağıdaki eksikler inceleme sırasındaki canlı sürüme aittir. Daha sonra yerel kaynağa eklenen araştırma motoru ve canlı kurulum durumu için [XAUUSD araştırma kurulumu](XAUUSD_ARASTIRMA_KURULUM.md) belgesine bakın.

## Kurulum ve doğrulanan erişim

- `docker-compose.yml`: `metatrader5` servisi (`mt5-server`) Wine içinde Windows MT5 terminalini çalıştırır. `start.sh` terminal ve RPyC köprüsü için yeniden başlatma döngülerini kurar.
- `web-dashboard` (`volta-trading-web`), Docker iç ağındaki `metatrader5:8001` adresine bağlanır. Bu servis adı yerel geliştirme bilgisayarının DNS adresi değildir.
- `compose.dokploy.yml`, HTTPS panelini `trade.46.225.94.54.sslip.io` adresinde yayınlayacak şekilde yapılandırır. MT5 köprüsü dışarıya port olarak yayınlanmaz.
- Canlı HTTPS panelinin `/api/account` yanıtında bağlantı açık, hesap türü DEMO, sunucu `Tickmill-Demo`, hesap uyuşmazlığı false ve para birimi USD olarak doğrulandı. Hesap numarası ve parolalar bu kayda alınmadı.
- `/api/price/XAUUSD` başarılı yanıt verdi. Bu kontrol geçmiş veri erişiminden ayrıdır; fiyatın güncel işlem yapılabilir olduğunu göstermez.
- `/api/bot/status` kontrolünde HMA botu kapalıydı. Bu, diğer otomasyonların durumuna ilişkin bir tespit değildir.
- `/api/backtest`, XAUUSD için H1 ve H4 zaman dilimlerinde başarılı simülasyon yanıtı verdi. Her istekte 1000 mum talep edildi; mevcut yanıt şeması kullanılan gerçek mum sayısını ve tarih aralığını bildirmiyor.
- Yerel varsayılan SSH erişimi kimlik doğrulamasında reddedildi. HTTPS panel erişimi çalıştığından veri erişimi için SSH zorunlu değildir.

## Geçmiş veri yolu

`POST /api/backtest` → `MT5Client.get_rates()` → RPyC → `mt5_bridge.rates()` → `MetaTrader5.copy_rates_from_pos()` → broker/terminal mum geçmişi.

API, son oluşmakta olan mumu çıkarır. Simülatör kapanmış mumdan sinyal üretir ve sonraki mum açılışından işlem varsayar. H1 için dakika değeri 60, H4 için 240 kullanılır.

## Mevcut testin sınırları

- İkinci ortalama seçenekleri EMA, SMA, LWMA ve HMA; KAMA mevcut değil.
- ATR, Efficiency Ratio, onay bekleme ve H4/H1 birlikte değerlendirme mevcut değil.
- API tek istekte en fazla 1000 mum kabul ediyor; tarih aralığı veya geçmiş sayfalama endpoint'i yok.
- İlk %70 ve son %30 ayrı raporlanıyor; otomatik parametre seçimi veya kayan dönem doğrulaması yapılmıyor.
- Güncel spread sabit varsayılıyor. Komisyon puan olarak dışarıdan veriliyor; swap, kayma ve tarihsel spread dahil değil.
- Mum köprüsü OHLC, zaman ve tick hacmini döndürüyor; mum spread alanını döndürmüyor.
- H1 ve H4 kontrolü mevcut HMA(14)/EMA(34) ile, SL/TP olmadan ve sıfır komisyon varsayımıyla yalnızca test/veri yolunu doğrulamak için yapıldı. Sonuçları HMA–KAMA stratejisinin performans kanıtı olarak kullanmamak gerekir.

## XAUUSD karşılaştırması için gereken geliştirmeler

1. KAMA ve ER ile ATR hesaplamalarını eklemek; onay adayı, azami bekleme ve iptal kurallarını açıkça tanımlamak.
2. H1, H4 ve H1 giriş/H4 filtre seçeneklerini aynı tarih aralığında karşılaştırmak. H4 filtresine yalnızca karar anında kapanmış H4 mumlarını dahil etmek.
3. Daha uzun geçmişi tarih aralığı/sayfalama ile almak; gerçek mum sayısı, başlangıç/bitiş ve veri boşluklarını raporlamak.
4. Hemen giriş ile 1–3 mum bekleme ve ATR mesafe eşiklerini diğer kurallar sabitken karşılaştırmak. Parametre seçimi yapılan dönemden ayrı doğrulama dönemi tutmak.
5. Spread, komisyon, swap ve kayma varsayımlarını raporlamak; son doğrulamayı MT5 Strategy Tester'da brokerın mevcut gerçek tick geçmişiyle yapmak.

Bu incelemede canlı emir, hesap değişikliği, bot başlatma veya dağıtım yapılmadı.
