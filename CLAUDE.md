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

## Bot = paneldeki seçim (kullanıcı kararı, 2026-10-22)
Bot; seçili sembolü, grafiğin zaman dilimini ve sinyal tarayıcısının HMA × 2. MA ayarını (tür + periyot) kullanır.
Bot çalışırken de izler (`/api/bot/follow` → `StrategyBot.reinit_live`; panel `syncBotToSelection`). MT5'te EA'nın
grafik sembolü/periyodu (REASON_CHARTCHANGE) ya da girişi (REASON_PARAMETERS) değişince olduğu gibi: OnDeinit + OnInit —
açık pozisyonlar olduğu gibi kalır, değişiklikten önce kapanmış mumdaki kesişim işlenmez. Yeni ayar bot başlangıcıyla
aynı hazırlık kontrolünden (`bot_readiness`) ve gerçek hesapta ikinci doğrulamadan geçer; geçemezse bot mevcut ayarla
çalışmaya devam eder ve sebep bot kartında gösterilir (her durum yenilemesinde tekrar denenmez). Emir gönderilirken
değişiklik yapılmaz (`_check_lock` beklenir). Çalışan botta yalnız `StrategyBot.LIVE_KEYS` değişir; lot, SL/TP vb. için
bot durdurulur. Tarayıcının RSI/ADX/üst zaman dilimi gibi filtreleri bota taşınmaz (EA'da yok). Bot Ayarları'nda
HMA/2. MA/sembol/zaman dilimi kaydedilirse tarayıcıya, grafiğe ve seçime de yazılır; ortak periyot sınırı 2–200.
Sinyal, EA gibi yeni mumun ilk tick'inde (0,5 sn döngü) kapanmış mumda işlenir.

## MT5 EA'dan bilerek farklı iki bot seçeneği (kullanıcı onayı, 2026-10-23)
Kullanıcı ikisini de MT5'ten farklı olduğunu bilerek seçti; varsayılan açık, Bot Ayarları'ndan kapatılabilir.
- `trend_entry_on_start`: bot başlarken ya da seçim değişince (OnInit anı) son kapanmış mumdaki HMA × 2. MA trendi
  yönünde hemen işlem açar; EA yeni kesişimi beklerdi. Reddedilirse (piyasa kapalı vb.) yeni kesişim gelene kadar
  sonraki kapanmış mumda tekrar dener (`_trend_pending`); aynı yönde bot pozisyonu varsa açmaz. Sonrası EA kuralı.
- `copy_manual_trades` / `copy_manual_since` (2026-10-08): lot ve SL/TP, o tarihten sonra açılmış kârlı manuel
  (orijinal MT5) pozisyonlardan gelir (`app/manual_copy.py`, `MT5Client.manual_copy_profile`): lot = 120 sn içinde
  açılan aynı yönlü pozisyonların toplamının (karar) ortancası, lot adımına aşağı yuvarlanır; SL/TP = açılış
  deal'inin sl/tp mesafesinin ortancası; TP kaydı olmayanlarda gerçekleşen çıkış mesafesinin ortancası; SL kaydı
  yoksa botun SL ayarı. En az 3 kârlı pozisyon yoksa bot kendi ayarını kullanır. Otomatik bot lot sınırı aşılmaz
  (sınırda açılır ve loga yazılır). Giriş kuralı yine EA kesişimidir.
- Çalışan bot yalnız bu paneldeki açık seçimle değişir; durum yenilemesi çalışan botu yeniden başlatmaz.
- `extra_timeframes` (2026-10-23): aynı sembolde ek zaman dilimleri = MT5'te aynı EA'yı birden çok grafiğe takmak.
  Her zaman dilimi ayrı örnek: kendi kesişiminde kendi pozisyonunu açar, ters sinyalde yalnız kendisininkini kapatır.
  Sahiplik emir yorumundan (`HMA Bot M5 BUY`); etiketsiz eski bot pozisyonları ana zaman dilimine (grafik) aittir.
  Durum anahtarı: ana dilim `SYM`, ek dilim `SYM@M5`. Bot kartındaki çiplerle çalışırken de eklenir/çıkarılır
  (çalışan örnekler sıfırlanmaz). Lot ve açık işlem sınırları hesabın tamamına uygulanır.
- `fast_ma_type` (2026-10-23, kullanıcı "en mantıklısını yap" dedi): hızlı çizgi HMA (EA) ya da EMA/SMA/LWMA.
  Tarayıcı (`signal_engine.fast_series`, `fast_type`), bot (`calc_fast`), grafik (`fastType`), geçmiş testi
  (`simulate(fast_type=)`) aynı formülü kullanır.
- Stratejiler (dünya) düğmesi: `app/strategy_presets.py` yaygın, yayımlanmış MA kesişimlerini (EMA 9×21, 12×26, 20×50,
  SMA 50×200 …) brokerın mumlarında botun kurallarıyla test eder (`/api/strategy-presets`) ve net sonuca göre sıralar.
  "Dünyanın en kârlısı" iddiası yoktur; doğrulanamaz. Seçilen ayar tarayıcıya yazılır, bot onu izler.
- `auto_lot_limits_demo` (2026-10-23, kullanıcı: hesap demo, "en mantıklısını yap"): yalnız DEMO hesapta bot, manuel
  kopya lotunun tamamını açar; `MT5Client._limits_for(HMA_MAGIC)` bot emirleri için tek emir sınırını kopya lota,
  toplamı kopya lot × zaman dilimi sayısı + kayıtlı toplama çıkarır (`bot_lot_override`). Kayıtlı sınırlar değişmez;
  diğer emirler ve gerçek hesap her zaman kayıtlı sınırları kullanır. Canlıya geçişte sınırlar kullanıcıyla belirlenir.
- `entry_mode` (2026-10-23): `CROSSOVER` (EA kesişimi) ya da `MANUAL_STYLE`. Stratejiler penceresinin en üstündeki
  "Manuel işlemlerim" kartı bunu seçer; yaygın kesişimlerden biri seçilince `CROSSOVER`a döner. MANUAL_STYLE'da bot,
  `app/manual_style.py` canlı sinyali (kart ile aynı analiz ve önbellek, `main.bot_manual_style`) kapanmış mumda
  AL/SAT'a döndüğünde girer (aynı kurulumda tekrar girmez, BEKLE'den sonra yeni kurulum), ters sinyalde kendi
  pozisyonunu kapatır; lot/SL/TP manuel kopyadan. Analiz hesaplanamazsa aynı mum sonraki döngüde tekrar denenir.
  M1–H4'te çalışır. Sinyal deneyseldir (geçmiş testte her mumdan anlamlı farkı yok); kartta bu yazılır.
- Emir kimliği (`order_id`) her bot başlatma/yeniden başlatmada yeni bir çalışma numarası (`_run_nonce`) içerir; aynı
  çalışmada aynı sinyal tek emirdir. Manuel kopya değerleri başlangıçta bilgi (`start_notes`) olarak gösterilir, uyarı değil.
- `reenter_after_stop` (2026-10-23): botun bir zaman dilimi örneğinin pozisyonu bu çalışmada SL/TP/stop out ile kapanırsa
  (MT5 DEAL_REASON 4/5/6, `mt5_bridge.hma_recent_closes`) ve strateji hâlâ bir yön gösteriyorsa bot bir sonraki kapanmış
  mumda yeniden girer (kapanış başına bir kez; reddedilirse sonraki mumda tekrar). Elle kapatma (masaüstü/mobil/web/
  panel = 0/1/2/3) yeniden giriş tetiklemez. MANUAL_STYLE'da aynı kurulum sürüyorsa yeniden girer.
- Strateji seçimi bütün bir kurulumdur (2026-10-23): "Manuel işlemlerim" = MANUAL_STYLE giriş + manuel kopya lot/SL/TP
  (`copy_manual_trades` açık); yaygın kesişimlerden biri = o kesişim + botun kendi lot/SL/TP'si (kopya kapalı).
  `copy_manual_trades` çalışan botta değişebilir (LIVE_KEYS) ve çalışan örnekleri sıfırlamaz. Yaygın kesişim testi her
  zaman botun kendi SL/TP'siyle yapılır.

## MevO yeniden yazıldı (kullanıcı kararı, 2026-10-23)
Kullanıcı "eski MevO'yu komple sil, yeniden yap" dedi. Eski DeepSeek danışman/otopilot (`ai_advisor.py`, `ai_performance.py`,
`/api/ai/*`, ana sayfadaki AI sekmesi/otopilot penceresi, FULL_AUTO döngüsü) silindi. Yeni `app/mevo.py` (`/ai` sayfası,
`/api/mevo/status|learn|opportunities`) YALNIZ DANIŞMANDIR: yalnız orijinal MT5'ten elle açılan kapalı işlemleri
(`MT5Client.get_manual_trades`, 90 gün, en az 5) inceler; istatistikleri kod hesaplar, DeepSeek yorumlar. Fırsatlar kapanmış
mumlara göre sunulur, SL/TP yönle doğrulanır (uyumsuzsa BEKLE); **MevO emir açmaz**. Profil hesap bazlıdır
(`mevo.json`). API anahtarı yalnız ortam değişkeni `DEEPSEEK_API_KEY` (koda/depoya/duruma yazılmaz); günlük sınır
`MEVO_DAILY_CALL_LIMIT`. `AI_MAGIC` (123462) yalnız geçmişte AI etiketli işlemleri tanımak için bırakıldı.
MevO API anahtarı (2026-10-23): sayfadaki "DeepSeek API anahtarı" alanından girilir; ücretsiz `GET /models` ile doğrulanır, durum
klasörüne 0600 `mevo_key.json` olarak yazılır, panel anahtarı ortam değişkeninden önceliklidir, hiçbir yanıtta geri dönmez.
Token tasarrufu: aynı geçmiş = yeniden öğrenme yok; aynı kapanmış mum + aynı profil = model çağrılmaz (önbellek); modele ham mum
yerine son 24 kapanış + özet gider; kısa prompt ve düşük `max_tokens`.
MevO bot-manuel karşılaştırması (2026-10-23, kullanıcı isteği): `MT5Client.get_bot_trades` (magic 123461, kapalı pozisyonlar) manuel KÂRLI
işlemlerin sembol bazlı referansıyla (saatler, baskın yön, lot, süre, SL mesafesi) kodda karşılaştırılır (`mevo.compare_bot_with_manual`);
zarar sebepleri saat / yön / erken stop / dar SL / büyük lot / referans yok. Modele yalnız özet sayılar gider. Fırsatlara `warnings`
(`mevo.trade_warnings`, token harcamaz) eklenir. Yalnız bilgi; emir/bot davranışı değişmez.
MevO zarar analizi (2026-10-23, kullanıcı: "kârı değil zararı da incele"): hem manuel ZARARLAR hem bot zararları, manuel kârlı işlemlerin
referansıyla kodda karşılaştırılır (saat, yön, erken stop, dar SL, büyük lot, uzun tutma, SL yok). Uyarılar ikisinin geçmişinden beslenir.
MevO — emir açma ve bot girişi (2026-10-23, kullanıcı kararı; ÖNCEKİ "MevO emir açmaz" kuralının yerine geçer):
1) /ai sayfasında fırsat kartından "Pozisyon olarak aç…": lot/SL/TP düzenlenir, onay penceresinden sonra normal `/api/order/open`
   (kullanıcı eylemi; gerçek hesapta TOTP kilidi geçerli). 2) Semboller uygulamadaki ürün seçiciyle (`MT5Markets`) eklenir.
3) Bot `entry_mode='MEVO'` ("Stratejiler" penceresindeki "MevO'yu kullan"): MT5 EA'dan FARKLIDIR (kullanıcı istedi). Her kapanan mumda,
   o zaman diliminin pozisyonu yokken `mevo.signal_for` ile sorulur (mum başına en çok 1 DeepSeek isteği, önbellekli; son girişten
   3 mum bekleme; M1 yok: M5–H4). Giriş koşulu: model güveni ≥ %65 ve geçmiş ZARARA dayanan uyarı yok (`mevo.blocking_warnings`;
   salt sapma uyarıları engellemez). SL/TP = MevO seviyelerinin giriş mesafesi (puan), lot = manuel kopya (kopya açık). Çıkış SL/TP;
   ters sinyalde kapatma yok. Günlük istek sınırı dolarsa bot girmez, durum kartında yazar.
MevO sayfasında emir lot/işlem sınırına (reason_code `exposure_limit` / `order_lot_limit`) takılırsa sayfa yeni sınırı gösterip onay ister;
onaylanırsa `/api/risk/lot-limits` ile (admin + gerçek hesapta TOTP kilidi) kayıtlı sınır artırılır ve emir yeni istek kimliğiyle yeniden gönderilir.
Bot sınırları kendiliğinden artırmaz.

## Arayüz düzeni (2026-10-23, kullanıcı isteği)
Ana ekran anahtarı: Grafik | İşlemler | Bot | MevO (`main-view.js`; eski `work` = İşlemler). MevO, `/ai?embed=1` sayfasının gömülmesidir.
Açık pozisyonlar tablosunda "Açılış saati" (MT5 sunucu saati) vardır; Detay penceresi açanı (bot/MevO/web/MT5/EA) ve botta giriş türü +
stratejiyi gösterir (`positionOrigin`; bot `signal_history` kayıtlarına `entry_mode`/`strategy` yazılır). Bakiye/varlık/K-Z/teminat
özeti üst çubukta "Varlık" düğmesinin açılır kutusundadır (üzerine gelince açılır, tıklayınca sabitlenir, dışarı tıklayınca kapanır).

## MevO sonuç analizi ve sade sayfa (2026-10-23, kullanıcı isteği)
MevO kendi açtırdığı işlemlerin neden kâr/zarar ettiğini analiz eder (`mevo.review_trades`, `/api/mevo/review`): MevO sayfasından
açılan ("MevO" yorumlu) ve botta `entry_mode='MEVO'` ile açılan kapalı pozisyonlar (`mt5_bridge.mevo_lifetimes`, bot kayıtlarındaki
pozisyon numaraları). Sebepler kodla, kârlı manuel referansla (saat/yön/lot/süre/SL) ve kapanış nedeniyle (SL/TP/elle) bulunur; model çağrılmaz.
Önlem (`own_blocks`): aynı sembol+yönde art arda 3 MevO zararı ya da bir sembolde son 5 işlemin kazanç oranı <%30 ve net zarar ise fırsat
kartında uyarı çıkar ve bot MevO girişi o sembol/yönde yapmaz (uyarı "zarar edildi" ifadesi taşır → `blocking_warnings`). Seri kırılınca kalkar.
MevO sayfası: tek düğmeyle "Fırsatları getir" (profil yoksa önce kendisi öğrenir) → "MevO fırsatlarının sonucu" → kapalı "ne öğrendi" bölümü.
Üst çubuk hesap kutusu: her rakamın yıldızı (favoriler üst çubukta görünür), göz düğmesi tutarları bulanıklaştırır (tarayıcıda saklanır).

## Saat kuralı kaldırıldı, kâr hedefi / zarar sınırı (2026-10-23, kullanıcı isteği)
- MevO artık işlem saatini hiçbir yerde kullanmaz (uyarı, zarar sebebi, kazanç sebebi): kullanıcının saatleri değişiyor ve bot o uyurken de çalışacak.
  Fırsatı engelleyen uyarılar yalnız yön / dar SL / lot gibi sebepler ve MevO'nun kendi art arda zararlarıdır.
- `stop_profit_amount` / `stop_loss_amount` (Bot Ayarları; 0 = kapalı; MT5 EA'da YOK, kullanıcı istedi): bot bu çalıştırmadaki net sonuç
  (başlangıçtan beri kapanan bot pozisyonları + açık bot pozisyonlarının anlık K/Z + swap + komisyon, magic 123461) kâr hedefine ya da
  zarar sınırına ulaşınca durur (`StrategyBot.check_session_target`, döngüde; kapananlar 5 sn'de bir okunur). Açık pozisyonlar kapatılmaz:
  SL/TP ile sürer (durmuş EA gibi). Durum: PROFIT_TARGET / LOSS_LIMIT. Çalışan botta değiştirilemez (önce durdur).
- Yeniden giriş: pozisyon SL/TP ile kapanınca bot, `reenter_after_stop` açıkken (varsayılan) strateji hâlâ yön gösteriyorsa sonraki mumda yeniden girer;
  MevO modunda pozisyon yokken her kapanan mumda (3 mum beklemeyle) yeniden sorulur.
- `copy_lot_override` (2026-10-23, kullanıcı isteği; MT5 EA'da YOK): manuel kopya açıkken lot, manuel işlemlerden hesaplanan lot yerine bu sabit lot olur
  (0 = kapalı); SL/TP yine manuel kopyadan gelir. Bot Ayarları'ndan girilir, çalışan botta değiştirmek için önce durdurulur.

## Mobil görünüm (2026-10-23, kullanıcı isteği)
<760px: uygulama gibi kabuk (`mobile.css`, `mobile-shell.js`). Alt sekme çubuğu Grafik | İşlemler | Bot | MevO (masaüstündeki `MainView` ile aynı ekranlar),
tek satırlık üst bar (logo · hesap özeti · ☰ menü; araçlar/hesap/dil menü sayfasında), hızlı SAT/AL çubuğu yalnız Grafik ekranında, emir formunda
"Gelişmiş ayarlar" katlanır (doldurma/sapma/yorum), pozisyonlar 2 sütunlu kompakt kart. Eski kaydırmalı bölüm çubuğu (`#mx-nav`) gizli. Emir/hesap davranışı değişmedi.

MevO kaynak etiketi (2026-10-23): MT5'e giden emir yorumu idempotency etiketidir (`vm:<hash>`); MevO sayfasından onaylanan emirlerde yorum `mevo:<hash>`
olur (`MT5Client.open_order`), böylece pozisyon/geçmişte "MevO (Manuel)" rozeti ve `mevo_lifetimes` çalışır. Bu değişiklikten önce açılanlar `vm:` kalır (HMA Sitesi).
MevO sonuç analizi bot girişlerini de kapsar (entry_mode='MEVO'); pozisyon numaraları `mevo.json` içinde kalıcıdır (`_remember_bot_ids`), bot geçmişi (50 kayıt) kırpılsa/temizlense de kaybolmaz.

## MevO kendini geliştirme (2026-10-23, kullanıcı isteği; saat analizi hâlâ YOK)
Zarar/kâr sebeplerine broker mumlarından bağlam eklendi (`mt5_bridge.trade_contexts`, `MT5Client.get_trade_contexts`, kapanmış işlem başına bir kez okunup
`mevo.json` `ctx` içinde saklanır): MAE/MFE (M1 Bid), girişte spread ve M5 ortalama aralık (ATR benzeri), eşzamanlı açık işlem sayısı/lotu
(`enrich_trades`). Yeni zarar sebepleri (`context_causes`): `noise_sl` (SL, kârlı işlemlerin MAE p75'inden dar ve SL ile stop), `wide_spread`,
`low_vol`/`high_vol`, `crowded`, `giveback` (hedefin yarısına gelip zarar). Kârlı manuel işlemlerden sembol başına referans/koruma sınırları
(`build_guards`). Ders defteri: tekrarlayan (≥2) sebepler `build_lessons` ile kalıcı ders+önlem olur, `journal` kazanç/net/eşik trendini tutar.
Fırsatlar: SL, MAE sınırının (`min_sl_pct`) altındaysa kod SL'yi genişletir (`apply_sl_guard`, TP aynı; yalnız MevO önerisi), canlı spread/volatilite/açık
işlem uyarıları eklenir (geçmiş ≥2 zarar varsa engeller), modele guards/lessons/trend/spread gider. `adaptive_policy`: son 5 MevO işlemi kötüyse bot güven
eşiği %65 → %75'e, net zararda %70'e çıkar (`item['min_confidence']`; bot `max(65, eşik)` kullanır). Üst çubuktaki "MevO AI" düğmesi kaldırıldı (MevO artık ana ekran anahtarında).
MevO saat temizliği (2026-10-23): modele işlem saati/saat dağılımı gitmez (`strip_hours`), eski profildeki saat/zaman maddeleri prompt'tan süzülür (`no_hour_rules`),
profil `version` < 2 ise fırsat istenince bir kez yeniden öğrenilir. Prompt: SL/TP'yi MevO verir, "SL/TP olmadan işlem yasağı" BEKLE sebebi değildir.

## Pozisyon başına otomatik kapat (2026-10-24, kullanıcı isteği; MT5'te YOK)
Pozisyon tablosundaki "Hedef" düğmesi: kâr hedefi ve/veya zarar sınırı, her biri yüzde (pozisyon teminatının %'si, `order_calc_margin`) ya da tutar.
`app/position_rules.py` (1 sn döngü, `/api/position-rules`): net sonuç (kâr + swap) hedefe/sınıra ulaşınca `close_position` ile piyasadan kapatır
(Kapat düğmesi gibi, journal korumalı). Kurallar yalnız bellekte tutulur; yeniden başlatma/hesap değişimi kuralı düşürür (iz süren stop gibi), pozisyon kapanınca silinir.
Gerçek hesapta TOTP kilidi (`require_real_unlock`) geçerlidir.

## Filtre karşılaştırma testi (2026-10-24, kullanıcı isteği; yalnız bilgi)
"Strateji testi" bölümündeki "Filtreleri Karşılaştır" (`/api/backtest/filters`, `app/filter_compare.py`): aynı mumlarda botun kesişimini saf hâliyle,
üst zaman dilimi trendi (`signal_engine.HtfTrend`) ve RSI aşırı bölge koruması (`rsi_buy_max/rsi_sell_min`) ile yan yana test eder (`backtest.simulate(entry_filter=)`).
Bot kuralları DEĞİŞMEZ; filtreler bota eklenmedi (EA'da yok). Eklenmesi istenirse MT5'ten farklı olduğu için ayrıca onay alınır.

## Zarar profili ve emir öncesi uyarı (2026-10-24, kullanıcı isteği; yalnız bilgi)
`app/loss_profile.py`, `/api/loss-profile` (+ `/check`): kapanmış manuel ve bot işlemlerini sembol, yön, lot (sembolün ortanca lotuna oranı), SL mesafesi (% giriş)
ve tutma süresine göre gruplar. Riskli grup = en az 5 işlem + kazanma ≤%35 + net zarar. Bot panelinde "Zarar profili" kartı; emir kartında (lot/SL/sembol
değişince) uyarı satırı çıkar. Emri ENGELLEMEZ, bot davranışını değiştirmez (bota engel eklemek MT5'ten farklı olur, ayrıca onay gerekir). 5 dk önbellek.

## Bildirim çanı (2026-10-26, kullanıcı isteği)
Üst çubukta çan (`notify-center.js`): her `showToast` mesajı tarayıcıda (localStorage, son 100) geçmişe yazılır; okunmamış sayı rozeti, "Tümünü sil".
Bildirimler 6 sn (hata/uyarı 9 sn) görünür. Mobilde çan menü sayfasındadır, ☰ düğmesinde okunmamış noktası vardır. Emir/hesap davranışı değişmedi.

## Sistem sağlığı (2026-10-26, kullanıcı isteği; yalnız okur)
`app/health.py` (`evaluate`, saf fonksiyon) + `GET /api/health`: MT5 bağlantısı, işlem kilidi, fiyat tazeliği, bot durumu (hata/belirsiz emir/kâr-zarar hedefi/takılma/atlanan sinyal/ret),
toplam lot ve işlem sayısı sınırı, bot lotu-sınır uyumu, gerçek hesapta SL, manuel kopya kullanılamıyor. Hiçbir ayarı değiştirmez, emir göndermez.
`health.js`: dakikada bir kontrol → yeni uyarı/hata bildirim çanına düşer (aynı bulgu çözülene kadar tekrar edilmez); sayfanın JS hataları ve 5xx (503 hariç)
API cevapları da "Teknik hata" olarak çana yazılır. Araçlar > "Sistem sağlığı" panelinde elle kontrol edilir.
Bildirim çanı satırları tıklanabilir (`notify-center.js` `route`): bilet numarası → açıksa Pozisyonlar (satır vurgulanır), değilse Geçmiş; bot/sinyal → Bot; MevO → MevO;
Sistem sağlığı → Araçlar'da sağlık paneli; teknik hata → Araçlar; emir → Grafik emir kartı. Yönlendirilemeyen bildirim yalnız bilgidir.
Ağ/sunucu kesintisi (2026-10-26): `health.js` istek başına değil kesinti başına tek bildirim yazar (ağ hatası, 502/504 → "Sunucuya ulaşılamıyor"; sonra "geri geldi"); 503 sayılmaz.
Kesinti teşhisi (2026-10-26): `/api/health` yanıtı `started_at` (uygulama işlem başlangıcı) döner; `health.js` iki kontrol arasında değer değişirse "Uygulama yeniden başlatıldı (saat)" yazar,
panel başlığında açılış saati görünür. Böylece kesinti yeniden başlatmadan mı ağdan mı anlaşılır. Yalnız bilgi.
