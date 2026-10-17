> **17 Ekim — HMA botu ve sinyal onayı:** "Bota Gönder" / "Uygula" ile **elle onaylanan** sinyaller, manuel emir penceresi gibi **MT5 kurallarıyla** gönderilir: broker kontrolleri uygulanır, panelin lot/işlem sayısı/günlük zarar sınırları uygulanmaz. Gerçek hesapta TOTP oturumu, hesap bağlama ve aynı sinyal için tek emir kimliği (çift tıklama ikinci emir açmaz) korunur. **Otomatik** HMA botu panel risk modunda kalır (günlük zarar, tek emir lotu, %2 stop riski, fiyat tazeliği); toplam lot ve işlem sayısı sınırı yalnız **botun kendi** pozisyon ve emirlerini sayar, manuel pozisyonlar botu engellemez. Başlangıç kontrolü izlenen tüm sembolleri doğrular; sembol listesi bot çalışırken değiştirilemez. SL/TP tarayıcıdan fiyat (`sl_price`/`tp_price`) veya puan (`sl_points`/`tp_points`) olarak gönderilir.

> **Bot risk ve koruma:** Bot ayarlarında **Lot hesabı = Özsermayenin %'si** seçilirse lot, stopa gelindiğinde özsermayenin seçilen yüzdesi (0.05–5) kadar zarar edecek şekilde brokerın `order_calc_profit` hesabıyla bulunur ve lot adımına aşağı yuvarlanır; en küçük lot bile riski aşıyorsa işlem atlanır. Bu mod hem otomatik bot hem "Bota Gönder" için geçerlidir. **Koruma kuralları** yalnız otomatik girişlere uygulanır: art arda N kayıpta bot durur (sayım bot başlatılınca sıfırlanır), işlem saatleri/günleri ve günlük (`15:25-15:45`) veya tarihli (`2026-11-06 15:20-15:50`) engelli aralıklar seçilen saat diliminde değerlendirilir. Bot kartındaki **Performans** bölümü MT5 geçmişindeki kapanmış HMA botu pozisyonlarından kazanma oranı, net sonuç, profit factor, işlem başı beklenen değer, ortalama R (risk % modundaki işlemler), en büyük düşüş ve sembol/saat/kaynak kırılımlarını gösterir; geçmiş sonuçtur, tahmin değildir.

> Web panelindeki emir güvenliği, hız düzeltmeleri ve zorunlu panel parolası kurulumu: [Emir güvenliği düzeltmeleri](docs/EMIR_GUVENLIGI_DUZELTMELERI.md). Telegram entegrasyonu kaldırılmıştır.

> 10 Ekim güvenilirlik incelemesi ve yeni davranışlar: [Mantık hataları ve emir güvenliği](docs/EMIR_MANTIK_INCELEMESI_2026-10-10.md). Manuel emir ekranı varsayılan **MT5 kuralları** modundadır: broker lot/stop/teminat/doldurma kuralları uygulanır; SL/TP isteğe bağlıdır. **Broker + panel risk sınırları** seçeneği ek uygulama limitlerini açar. HMA/AI kuralları ayrıca korunur. Panel risk modundaki manuel emirler ve HMA/AI emirlerinde brokerın hesapladığı zarar varsayılan olarak özsermayenin %2'sini (`MAX_TRADE_RISK_PCT`) aşamaz; AI için mevcut %1 sınırı da uygulanır. Komisyon, swap ve kayma bu hesapta yoktur. Web köprüsünde gerçekleştirme öncesinde her işlem için broker `order_check` kontrolü yapılır. Ayrı EA'nın dar kaynak düzeltmeleri için MetaEditor derlemesi ve Demo doğrulaması ayrıca gereklidir.

## XAUUSD HMA–KAMA–ATR araştırması

Yeni araştırma paneli H1, H4 ve H1 giriş/H4 filtre için kesişim onay süresi ve ATR mesafesini 51 seçenekle karşılaştırır. Parametre seçimi ilk %60 eğitim verisinde yapılır; sonraki %20 doğrulama ve son %20 ayrılmış test seçimde kullanılmaz. Tarih aralığıyla uzun broker geçmişi, mum spreadi, kullanıcı maliyet varsayımları ve JSON rapor indirme desteklenir. Araştırma motoru emir göndermez; mevcut HMA botundan ayrıdır. Kaynak kurulum, API, dosyadan çalışma ve simülasyon sınırları: [XAUUSD araştırma kurulumu](docs/XAUUSD_ARASTIRMA_KURULUM.md). Canlı panelde görünmesi için yeni web kaynağı sunucuya kurulup web imajı yeniden oluşturulmalıdır.

## Panel dili / Interface language

Terminal ve AI Danışmanı sayfalarının üst kısmındaki **Türkçe / English** menüsünden dili seçin. Seçim aynı tarayıcıda saklanır ve iki sayfada da uygulanır; ilk açılışta tarayıcı dili kullanılır. Grafik arayüzü, sayı biçimi ve yeni AI analizleri seçilen dili izler. Daha önce farklı dilde oluşturulmuş bir AI işlem profili varsa, seçtiğiniz dilde yeni bir profil oluşturmak için **İşlemlerimi Analiz Et & Öğren / Analyze My Trading History** düğmesini kullanın. Bu işlem AI sağlayıcısına ücretli istek gönderebilir ve mevcut istek sınırlarına tabidir.

## Ürün seçimi ve favoriler

Terminalin üstündeki **Ürün ara / Ekle** veya emir panelindeki ürün düğmesi, bağlı brokerın gerçek ürün kataloğunu açar. Sembol/açıklama araması ve Döviz, Kripto, Metaller, Endeksler, Hisseler ve Emtialar filtreleriyle ürünü bulun. Ürün adını seçmek grafik, fiyat ve emir hedefini birlikte değiştirir; seçim emir göndermez veya bot/AI ayarlarını değiştirmez.

**☆** ile eklenen favoriler üst çubukta tek tıkla erişilir. Favoriler bu tarayıcıda hesap, sunucu ve hesap türüne göre saklanır; **Favorileri yönet** bölümünden kaldırılır. **Yenile** broker kataloğunu yeniden okur. Brokerda bulunmayan ürün uydurulmaz; kapalı veya yalnız kapatmaya açık üründe yeni emir girişi engellenir. Brokerın `EURUSD.a` / `BTCUSDm` gibi ekli ve büyük/küçük harf içeren adları aynen korunur. TradingView eşlemesi olmayan ürünler broker verili MT5 grafiğinde gösterilir.

## Risk, gerçek hesap güvenliği ve strateji testi

- **AI emir koruması:** AI emirlerinde SL ve TP zorunludur. Brokerın `order_calc_profit` hesabı stop zararını doğrulamazsa veya zarar hesap özsermayesinin `AI_MAX_TRADE_RISK_PCT` sınırını (varsayılan %1) aşarsa emir broker'a gönderilmez. Komisyon, swap ve kayma bu hesapta yoktur; gerçekleşen zarar sınırı aşabilir.
- **AI kullanım bütçesi:** Günlük çağrı/token dökümü manuel tavsiye, otomatik tarama, işlem profili ve bağlantı testini ayrı gösterir. Tam otomatik tarama, günlük çağrı veya token sınırının `AI_AUTOPILOT_BUDGET_FRACTION` oranına (varsayılan %80) ulaştığında veya token sonucu bilinmeyen istek varken durur. Manuel istekler kendi günlük sınırına kadar kullanılabilir.
- **Hesap bazlı AI profili:** Broker numarası, sunucu veya hesap türü değişince profil, öneriler ve yeni işlem kayıtları ayrılır. Önceki sürümden kalan hesap kimliği olmayan profil, `ai_memory.json` içinde `legacy_unscoped` olarak saklanır ancak güvenle eşlenemediği için ilk geçişte yeniden analiz edilmelidir. AI performans paneli 90 günlük kapanmış pozisyonlarda net sonuç, komisyon/swap, kayma, düşüş ve güven aralığı gözlemlerini gösterir. Geçmişin dışındaki maliyetler ve brokerın Magic Number'ı korumadığı işlemler eksik kalabilir; model güveni kazanma olasılığı değildir.
- **İki panel kullanıcısı:** `DASHBOARD_USER_2` ve `DASHBOARD_PASSWORD_2` birlikte ayarlanırsa ikinci kişi kendi Basic Auth bilgileriyle giriş yapar. İlk kullanıcı `ADMIN`; ikinci kullanıcı `DASHBOARD_ROLE_2=TRADER` (varsayılan), `VIEWER` veya `ADMIN` rolünü alır. Yetki API'de uygulanır; `TRADER` emir açıp kapatabilir fakat hesap/otomasyon ayarlarını ve acil durdurmayı değiştiremez. Gerçek hesap için ikinci kullanıcının TOTP anahtarı `TRADING_STATE_DIR/trading_totp_secret_2`, ilk kullanıcınınki `trading_totp_secret` dosyasındadır; ikisi ayrı doğrulama uygulamalarına eklenmelidir. Dosyalar doğrulanmış yedeğe dahildir.
- **İşlem öncesi risk** paneli, brokerın `order_calc_profit` ve `order_calc_margin` sonuçlarıyla stop kaybı ve gerekli teminatı gösterir. Broker SL mesafesini ve seçili modda uygulanan risk sınırlarını da bildirir. Komisyon, swap ve kayma stop kaybı tahminine dahil değildir. Stop yoksa parasal üst sınır gösterilmez; stopu olmayan mevcut pozisyon sayısı ayrıca belirtilir. Emir anında broker koşulları yeniden doğrulanır.
- **Günlük zarar limiti:** Panelde kaydedilen limit, panel risk modundaki manuel emirler ve HMA emirlerinde kullanılır; AI emirlerinde hesap limitiyle AI ayarındaki limitin daha düşüğü uygulanır. Kârdaki/zarardaki toplu kapatma, brokerın pozisyon kimliğine bağladığı geçmiş maliyetleri de kullanır; geçmiş okunamazsa filtreli kapatma durur.
- Bu kurulumdaki MT5 terminali EURUSD/XAUUSD fiyat zamanını sunucu UTC saatinden üç saat ileride bildiriyor. `docker-compose.yml` için varsayılan `MT5_TICK_CLOCK_OFFSET_SECONDS=10800` bu farkı düzeltir; farklı broker veya terminalde ölçülen farkı `.env` ile ayarlayın (UTC zaman damgası için `0`). Düzeltmeden sonra fiyat 10 saniyeden eskiyse veya gelecekteyse yeni emir ve risk önizlemesi reddedilir.
- **Gerçek hesap doğrulaması** için panelde **İşlem Güvenliği / Trading Security** düğmesini kullanın. Altı haneli TOTP koduyla açılan sunucu oturumu 15 dakika sürer. Süre bitince gerçek hesaptaki HMA botu ve AI otopilotu durur. Demo hesabı bundan etkilenmez. Gerçek hesaba giriş, emir açma, bekleyen emir yerleştirme, stop değiştirme ve otomasyonu başlatma ikinci doğrulama gerektirir. Pozisyon kapatma ve acil toplu kapatma, risk azaltmayı geciktirmemek için temel panel kimliğiyle mümkündür.
- TOTP anahtarı uygulama açılırken `TRADING_STATE_DIR/trading_totp_secret` dosyasında yalnız sunucuda oluşturulur ve doğrulanan durum yedeğine dahil edilir. Sunucu yöneticisi bu Base32 anahtarı güvenli bir SSH oturumundan okuyup kimlik doğrulama uygulamasına **TOTP / 30 saniye / 6 hane** olarak eklemelidir. Anahtarı sohbet, ekran görüntüsü veya herkese açık loglara koymayın. Oturum kayıtları aynı dizindeki `security_sessions.sqlite3` dosyasında tutulur.
- **İşlem ve sistem olayları** paneli emir sonucu, belirsiz sonuç, doğrulama, MT5 bağlantısı ve günlük zarar sınırının %80/%100 eşiklerini kalıcı olarak gösterir. %100 düzeyinde HMA botu ve AI otopilotu durdurulur; yeni emir sınırı ayrıca broker tarafında uygulanır. Tarayıcı önemli yeni olaylarda uyarı verir; sunucu olayları `/api/operations/events` üzerinden okunabilir.
- **Strateji testi** kapanmış broker mumlarında, sinyalden sonraki mumun açılışında işlem varsayımıyla HMA çaprazlarını simüle eder. İlk %70 ve son %30 ayrı raporlanır. Bugünkü spread sabit varsayılır; geçmiş spread, swap, slippage ve geçmiş döviz dönüşümü yoktur. Sonuçlar hesap para birimi yerine puandır ve canlı performans tahmini olarak kullanılmamalıdır.
- **Brokeri Kontrol Et** seçili sembolün lot, stop mesafesi, emir türü, doldurma yöntemi ve hedging/netting özelliklerini sunucudan okur. MT5 `order_check` ile en küçük lotta alış, satış, FOK/IOC, limit ve stop senaryolarını kuru çalıştırır; **`order_send` çağrısı yapmaz**. Kontrol sonucu gerçekleşme garantisi değildir; canlı broker uyumluluğu için ayrıca kontrollü Demo gerçekleşme senaryoları gerekir.

## Web paneli için güncel güvenlik davranışı

- HTTPS ters proxy arkasında `.env` içinde `DASHBOARD_PUBLIC_ORIGIN=https://panel-adresi` ayarlayın; şema, alan adı ve varsa port tarayıcının panel adresiyle aynı olmalı. Dokploy ek Compose dosyası mevcut yayın adresini açıkça tanımlar. Kaynak kontrolü kapatılmaz ve istemcinin `X-Forwarded-*` başlıklarına güvenilmez.
- Manuel al/sat, brokerın güncel fiyatı ve seçili yönün risk önizlemesi doğrulanmadan açılmaz. Tıklamada risk tekrar kontrol edilir; hesap, ürün veya lot/SL/TP değişirse emir gönderilmez. Fiyat/risk düzeldiğinde otomatik emir gönderilmez.

- Broker girişinde **Demo Hesap** veya **Gerçek Hesap** açıkça seçilir. MT5'in bildirdiği gerçek hesap türü, numara ve sunucu eşleşmeden giriş kabul edilmez. Sunucu adı brokerın verdiği tam adla girilir; uygulama adı otomatik değiştirmez. Eski kayıtlarda hesap türü yoksa panelden yeniden giriş gerekir.
- Panelde broker hesabına giriş yapılmadan veya MT5'teki aktif hesap kayıtlı hesapla uyuşmadan yeni emir ve kapatma gönderilmez. Hesap değiştirildiğinde panel uyarı gösterir.
- **Tümünü Kapat / İptal** yeni emirleri kalıcı olarak durdurur. Brokerda kalan pozisyon ve bekleyen emirler son kez doğrulanır. MT5 durumunu kontrol ettikten sonra panelde **Yeni Emirlere Devam Et** düğmesini kullanın. Durdurma durumu yeniden başlatmada da korunur.
- Yeni emir başına, toplam açık ve bekleyen lota ve toplam işlem sayısına sınır uygulanır. Varsayılan değerler sırasıyla `0.10`, `0.50` ve `10`; `.env` içinde `MAX_ORDER_LOTS`, `MAX_TOTAL_OPEN_LOTS`, `MAX_OPEN_ORDERS` ile ayarlanır. Bunlar farklı sembollerde aynı parasal riski temsil etmez; günlük zarar sınırı ve broker teminat kontrolleri ayrıca geçerlidir. Sınır okunamayan broker verisinde yeni emir engellenir.
- Compose portları yalnız sunucunun `127.0.0.1` adresine bağlar. Dokploy panel yönlendiricisi yalnız HTTPS kullanır. MT5 container'ı paylaşılan proxy ağından çıkarıldı; masaüstünün bağımsız herkese açık yönlendiricisi yoktur. Orijinal terminal panel içindeki yönetici bölümü üzerinden açılır: [erişim ve işlem davranışı](docs/MT5_ORIJINAL_TERMINAL_2026-10-12.md). SSH tüneliyle yerel `3001` erişimi de korunur. Native terminalin kendi emirleri panelin ek risk sınırı/TOTP/kilidini kullanmaz; açıldığında özel panelin yeni emir düğmeleri kapanır.
- Testler: `.venv/bin/python -m pytest -q` ve `for t in tests/*.cjs; do node "$t"; done`.

# HMA Crossover Expert Advisor (MetaTrader 5)

Bu robot (Expert Advisor), **Hull Moving Average (HMA)** ile seçeceğiniz **2. bir Hareketli Ortalama (İkinci HMA, EMA, SMA veya LWMA)** kesişimini baz alarak otomatik alım-satım yapan ve sinyal üreten profesyonel bir algoritmadır.

**Mac, Linux ve Windows** üzerindeki tüm MetaTrader 5 terminallerinde hiçbir ek yazılıma gerek duymadan doğrudan çalışır.

---

## 🌟 Temel Özellikler

1. **Gelişmiş Kesişim Algoritması:**
   * **1. İndikatör:** Yerel olarak hesaplanan, gecikmesiz **Hull Moving Average (HMA)**.
   * **2. İndikatör:** Menüden tek tıkla seçilebilir:
     * İkinci bir HMA (Hızlı HMA + Yavaş HMA kesişimi)
     * EMA (Üstel Ortalama)
     * SMA (Basit Ortalama)
     * LWMA (Ağırlıklı Ortalama)
2. **Açılıp Kapanabilen Stop Loss (SL) & Take Profit (TP):**
   * Demo hesapta Stop Loss tercihe bağlıdır (`InpUseStopLoss = true/false`). Gerçek hesapta otomatik işlem için SL açık ve mesafe pozitif olmalıdır; aksi durumda EA başlatılmaz.
   * Aktif SL/TP için sıfır veya negatif mesafe kabul edilmez; hesaplanan seviye sıfır, girişe eşit veya yanlış yöndeyse emir gönderilmez.
3. **Esnek Çalışma Modu:**
   * **Tam Otomatik:** Şartlar sağlandığında hem sinyal verir hem de emri doğrudan açar (`InpAllowTrading = true`). Hesap genelindeki EA risk sınırları ayrıca uygulanır.
   * **Sadece Sinyal:** Varsayılandır; işlem açmaz, sadece ekrana ve telefona sinyal gönderir (`InpAllowTrading = false`).
4. **Bildirim Sistemi:**
   * MT5 Ekran Pop-up Uyarısı (`Alert`)
   * MT5 Mobil Uygulaması Push Bildirimi (`SendNotification`)
   * İsteğe bağlı sesli uyari (`PlaySound`)
5. **Güvenli İşlem Yönetimi:**
   * Sinyaller **mum kapanışında** teyit edilir (Repaint ve mum içi sahte kesişimler önlenir).
   * Ters sinyal geldiğinde mevcut açık pozisyonu otomatik kapatıp yeni yöne dönebilir (`InpCloseOpposite = true`).
   * Grafik üzerinde anlık indikatör değerlerini ve son sinyal durumunu gösteren bilgi paneli içerir.

EA kendi broker verisini kontrol eder; web panelinin kalıcı durdurma kilidini veya emir günlüğünü okuyamaz. Aynı broker hesabında web HMA/AI otomasyonu ile EA otomatik işlemini birlikte açmayın. Panelde günlük limit değiştirirseniz EA girdisini de güncelleyin; iki ayar otomatik eşitlenmez.

EA ilk yüklendiğinde geçmiş kesişimden emir açmaz; sonraki mum kapanışını bekler. Pozisyon okunamazsa yeni/ters emir engellenir. EA kaynak düzeltmeleri bu ortamda MetaEditor ile derlenmedi veya MT5'te çalıştırılmadı.

---

## 🚀 Kurulum (Mac, Linux & Windows)

1. MetaTrader 5 terminalinizi açın.
2. Klavyeden **F4** tuşuna basarak (veya menüden `Araçlar` -> `MetaQuotes Dil Düzenleyicisi`) **MetaEditor**'ü açın.
3. Sol taraftaki *Navigator* panelinde **`Experts`** klasörüne sağ tıklayıp **Yeni Dosya (New)** deyin veya:
   * [`HMA_Crossover_EA.mq5`](HMA_Crossover_EA.mq5) dosyasını kopyalayıp doğrudan `MQL5/Experts/` klasörünün içine yapıştırın.
4. Dosyayı MetaEditor içinde açın ve üst menüdeki **Derle (Compile)** butonuna basın (Klavye kısayolu: **F7**).
   * Alttaki pencerede `0 errors, 0 warnings` mesajını görmelisiniz.
5. MetaTrader 5 terminaline geri dönün:
   * Sol taraftaki *Kılavuz (Navigator)* penceresinde **Uzman Danışmanlar (Expert Advisors)** altında **`HMA_Crossover_EA`** görünecektir.
   * Bu robotu çalıştırmak istediğiniz grafiğin (Örn: EURUSD, XAUUSD, BTCUSD vb.) üzerine sürükleyip bırakın.
6. Açılan pencerede **"Algo Trading'e İzin Ver" (Allow Algo Trading)** kutucuğunu işaretleyin ve `Tamam`'a basın.
7. MT5 üst menüsündeki **"Algo Trading"** butonunun yeşil yandığından emin olun.

---

## ⚙️ Parametre Ayarları (Girdiler / Inputs)

| Parametre Grubu | Parametre Adı | Varsayılan | Açıklama |
| :--- | :--- | :--- | :--- |
| **1. İndikatör (HMA)** | `InpHMAPeriod` | `14` | HMA periyodu (Örn: 9, 14, 21) |
| | `InpHMAPrice` | `PRICE_CLOSE` | Hesaplanacak fiyat tipi (Kapanış, Açılış vb.) |
| **2. İndikatör** | `InpSecondMAType` | `TYPE_EMA` | 2. İndikatör türü (`HMA`, `EMA`, `SMA`, `LWMA`) |
| | `InpSecondMAPeriod` | `34` | 2. İndikatör periyodu (Örn: 34, 50, 200) |
| **İşlem & Risk** | `InpLotSize` | `0.01` | Açılacak işlem lot büyüklüğü |
| | `InpAllowTrading` | `false` | `true` = Otomatik al/sat, `false` = Sadece sinyal |
| | `InpCloseOpposite` | `true` | Ters sinyalde mevcut pozisyon kapatılsın mı? |
| | `InpDailyLossLimit` | `500` | Broker sunucu günü (MT5 günü) için hesap para birimindeki EA zarar sınırı |
| | `InpMaxOrderLots` | `0.10` | EA'nın tek emir üst sınırı |
| | `InpMaxTotalLots` | `0.50` | Hesaptaki açık pozisyon ve bekleyen emir toplamı |
| | `InpMaxOpenOrders` | `10` | Hesaptaki açık pozisyon ve bekleyen emir sayısı |
| **Stop Loss / TP** | `InpUseStopLoss` | `true` | **Stop Loss'u Aç / Kapat** |
| | `InpStopLossPoints`| `200` | Stop mesafesi (Point cinsinden, örn: 20 pip = 200 point) |
| | `InpUseTakeProfit` | `false` | Take Profit Aç / Kapat |
| | `InpTakeProfitPoints`| `400`| Kâr al mesafesi |
| **Bildirimler** | `InpScreenAlert` | `true` | MT5 pop-up uyarısı |
| | `InpPushNotification`| `true` | MT5 mobil uygulamasına anlık bildirim |

---

## 🧪 Strateji Test Cihazı (Backtest) ile Test Etme

Robotun geçmiş performansını görmek için:
1. MT5'te klavyeden **Ctrl + R** tuşlarına basarak **Strateji Test Cihazı'nı (Strategy Tester)** açın.
2. Uzman danışman olarak `HMA_Crossover_EA` seçin.
3. İstediğiniz sembolü (Örn: `EURUSD` veya `XAUUSD`) ve zaman dilimini (Örn: `M15`, `H1`) seçip **Başlat (Start)** düğmesine tıklayın.

## Panel içi MT5 grafiği

Grafik kaynağı varsayılan olarak **MT5 · Sağ tuş** kullanır. Grafik üzerindeki sağ tuş veya
**Grafik ayarları** düğmesi; zaman dilimi, mum/çubuk/çizgi/alan türü, tema, renkler,
ızgara, tick hacmi, logaritmik ölçek ve SMA ayarlarını açar. Sağ tıklanan fiyat seviyesine
yatay çizgi eklenebilir. Ayarlar tarayıcının yerel depolamasına, yatay çizgiler sembol
bazında kaydedilir. Başka cihazlara veya TradingView hesabına eşitlenmez.

Mumlar `/api/chart/candles` üzerinden gerçek MT5 geçmişinden gelir; ilk yüklemede 600 mum,
ardından beş saniyede bir son üç mum alınır. Grafik saatleri broker saatidir.
Grafik ayarları emir veya bot ayarlarını değiştirmez. Kaynak seçiminden eski
**TradingView · Gömülü** grafiğine dönülebilir; bu sürümün sağ tuş kısıtlaması devam eder.
Orijinal TradingView menüsüne geçiş için ileride resmi Advanced Charts entegrasyonu gerekir.
Grafik kütüphanesi: TradingView Lightweight Charts 5.2.1 (Apache-2.0); lisans ve bildirim
`app/static/vendor` klasöründedir.

Sağ tuş menüsü ayrıca seçilen fiyatı kopyalama, normal/logaritmik/yüzde/100 bazlı
ölçek, ters ölçek, artı imleç ve son fiyat çizgisi kontrolleri, EMA (SMA ile başlatılır),
göstergeleri kaldırma, son muma dönme, PNG görüntüsü ve CSV mum verisi indirme,
görünüm şablonu kaydetme/uygulama seçeneklerini içerir. CSV saatleri `time_broker`
alanında broker zaman damgası olarak korunur. Şablon grafik ayarlarını kapsar;
sembol bazındaki yatay çizgiler ayrı saklanır. TradingView hesabına bağlı alarmlar,
Pine Script ve TradingView'in tüm çizim araçları bu sürümün parçası değildir.

## MT5 manuel emir ayarları

**MT5 kuralları** modu manuel emirlere panelin sabit lot, toplam lot, işlem sayısı, günlük zarar ve parasal stop riski limitlerini uygulamaz. Gerçek hesapta da manuel SL/TP isteğe bağlıdır. Broker `order_check`, hesap kimliği, kullanıcı yetkisi/TOTP, fiyat doğrulaması, acil işlem kilidi ve kalıcı emir kimliği korunur. HMA/AI otomasyonlarının risk kuralları bu seçimle kapanmaz.

Piyasa emrinde BUY Ask, SELL Bid kullanılır. SL/TP fiyat veya puan olarak girilebilir; sapma puan cinsindedir. AUTO/FOK/IOC/RETURN seçimleri brokerın yürütme türü ve izinlerine göre gösterilir. Netting hesabında manuel ters emirler broker tarafından mevcut pozisyonu azaltabilir, kapatabilir veya tersine çevirebilir; hedging hesabında ayrı pozisyon açılır.

Bekleyen emirler BUY/SELL Limit, Stop ve Stop Limit; geçerlilik ise GTC, DAY, SPECIFIED ve SPECIFIED_DAY seçeneklerini içerir. Brokerın desteklemediği seçenekler kapatılır. **Değiştir** ile bekleyen emrin giriş/limit fiyatı, SL/TP'si ve süresi güncellenebilir. Başka terminalin değiştirdiği veya tetiklenen emir için eski editör kullanılamaz. Emir türü/hacmi bu editörle değişmez; yeni emir gerekir. Ayar tercihleri bu tarayıcıda hesap bazında saklanır.

Ayrıntılar ve canlı yayın durumu: [MT5 emir girişi](docs/MT5_EMIR_GIRISI_2026-10-12.md).
