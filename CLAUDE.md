# Proje kuralı: Her şey MetaTrader 5 ile birebir aynı

Bu uygulama bir MT5 terminali/EA'sının web panelidir. Kullanıcının kalıcı kuralı:
**İstenen her özellik, hesap ve davranış MT5 ile birebir aynı olmalı.** Bu kural her istek için geçerlidir;
kullanıcı ayrıca söylemese de uygulanır.

## Nasıl uygulanır
1. **Önce MT5'te nasıl çalıştığını belirle.** Bir şeyi eklemeden veya değiştirmeden önce MT5 terminalinin,
   MQL5 dokümantasyonunun ya da MT5'in hazır gösterge kaynak kodunun (Moving Average.mq5, RSI.mq5,
   ATR.mq5, ADX.mq5, AMA.mq5 …) davranışını esas al. Emin değilsen araştır; tahmin etme.
2. **Kendi formülünü/kuralını uydurma.** Gösterge, kâr/zarar, marj, swap, lot, SL/TP, emir türü,
   doldurma politikası, zaman ve gün hesabı MT5'in tanımıyla aynı olmalı. Mümkünse değeri broker/MT5'ten
   al (`order_calc_profit`, `order_calc_margin`, `order_check`, `symbol_info`, `copy_rates_*`).
3. **Göstergeler**
   - SMA/LWMA: iMA MODE_SMA/MODE_LWMA. EMA: iMA MODE_EMA, ilk fiyatla başlar (SMA ile değil).
   - RSI: iRSI (Wilder), yuvarlama yok; yuvarlama yalnız ekranda.
   - ATR: iATR = True Range'in basit ortalaması (Wilder değil).
   - ADX: iADX, ExponentialMA, 0. bar 0. AMA/KAMA: iAMA, ilk değer bar period-1 fiyatı.
   - HMA: `HMA_Crossover_EA.mq5` ile aynı hesap.
   - Özyinelemeli göstergeler (EMA, RSI, ADX, AMA) MT5'te tüm grafik geçmişinde hesaplanır; yeterli mum
     iste (`signal_engine.history_bars`, `StrategyBot.history_bars`, grafikte `HISTORY_BARS`).
4. **Mumlar ve sinyaller**: MT5 mumları Bid fiyatıdır, saatler broker sunucu saatidir. EA'lar gibi sinyal
   yalnız kapanmış mumda verilir; oluşan mum ok/sinyal üretmez (onay kapalı değilse). Tick hacmi yalnız
   MT5'ten gelir, uydurulmaz.
5. **Zaman**: "Gün" = broker sunucu günü (sunucu 00:00), UTC değil. Rapor ve günlük sınırlar buna göre.
6. **Emirler**: MT5 kuralları — alış Ask'tan açılır, Bid'den kapanır; SL/TP alışta Bid'e, satışta Ask'a göre
   `trade_stops_level`/`trade_freeze_level` ile kontrol edilir; lot `volume_min/max/step`, fiyat
   `trade_tick_size` ile hizalanır; son tick yaşına bakılmaz, kapalı piyasayı broker reddeder.
7. **Aynı hesap tek yerde**: Bot, tarayıcı, grafik, backtest ve EA aynı formülü kullanmalı. Birinde
   değişiklik yaparsan hepsinde yap.
8. **Test**: Her MT5 davranışı için MT5'in değeri/kuralıyla karşılaştıran test ekle
   (`python3 -m pytest -q`, `for f in tests/*.cjs; do node "$f"; done`).

## İstisnalar
Uygulamanın MT5'te olmayan **ek güvenlik korumaları** (günlük zarar sınırı, lot/işlem sınırları, gerçek
hesapta TOTP, acil durdurma, otomatik bot için hedging şartı) MT5'in bir kuralını değiştirmez; onlara ek
olarak çalışır ve kalabilir. Bunları kaldırmak ya da MT5'ten farklı bir davranış eklemek gerekiyorsa
kullanıcıya bunun MT5'ten farklı olduğunu açıkça söyle ve onay al.

## HMA bot = HMA_Crossover_EA (kullanıcı kararı, 2026-10-20)
Kullanıcı "EA'da olmayan her şeyi kaldır" dedi. Bot yalnız EA girişleriyle çalışır: HMA periyodu, 2. MA
(HMA/EMA/SMA/LWMA) ve periyodu, lot, ters sinyalde kapat, sapma 10 puan, günlük zarar sınırı (her zaman),
tek emir / toplam lot / açık işlem sınırı (hesabın tamamı sayılır), SL/TP puan, gerçek hesapta SL zorunlu.
KAMA, ATR stop/filtre, H4 filtresi, onay mumu, RISK lotu, %2 parasal risk sınırı, art arda kayıp, saat/gün/haber
engeli, RSI/spread/başabaş/iz süren kuralları bot için kapalıdır (`main.EA_ONLY_BOT_SETTINGS`); geri eklenmez.
Başlangıçtan önce kapanmış mumdaki kesişim işlenmez (EA OnInit). "Şimdi AL/SAT" manuel magic (123460) kullanır.
