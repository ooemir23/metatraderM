# Hız ve mantık incelemesi — 20 Eylül 2026

Bu belge düzeltme öncesi bulgulardır. Uygulanan değişiklikler ve kalan kapsam için [düzeltme notlarına](EMIR_GUVENLIGI_DUZELTMELERI.md) bakın.

Kapsam: yerel Python/FastAPI uygulaması, tarayıcı arayüzü ve ayrı MQL5 EA dosyası. Çalışan uygulama kodu değiştirilmedi. Canlı broker, üretim sunucusu ve gerçek emir gecikmesi ölçülmedi. Aşağıdaki süreler kodun bekleme/yenileme ayarlarıdır; uçtan uca ölçüm değildir.

## Hız

1. **Emir öncesinde Telegram bekleniyor.** `app/strategy_bot.py:220,244` sinyal bildirimi tamamlanmadan kapatma/açma adımına geçmiyor. HTTP isteğinde `timeout=5` var; bu, bütün işlem için kesin beş saniyelik üst sınır değildir. Aynı sıra MQL5 `ProcessSignal` içinde de mevcut. Bildirimler emir yolundan çıkarılıp ayrı işleyicide gönderilmeli. Sahte bildirimde eklenen 120 ms bekleme doğrudan emir öncesine eklendi; çağrı sırası `telegram_start → telegram_end → order_send` oldu.
2. **Python botu her kontrolden sonra beş saniye uyuyor.** `app/strategy_bot.py:168`. Yeni mumun algılanması normal koşullarda yaklaşık 0–5 saniye, ayrıca veri alma ve işlem süresi kadar gecikebilir. Mum kapanışı stratejisini koruyarak yeni mum olayına daha hızlı tepki verilmeli; yalnızca uyku süresini azaltmak RPC yükünü artırır.
3. **Veri okumaları ve emirler aynı RLock üzerinde.** `app/mt5_client.py:172,507,699,727`. Geçmiş alma, uzak nesneleri satır satır okuma veya yeniden bağlantı işlemi emri bekletebilir. RPC istek başına 15 saniye zaman aşımı var; kilit beklemesi dahil toplam emir süresine tek bir sınır yok. Kilidi kaldırmak yerine tek MT5 işleyicisi, emir önceliği, kısa veri okuma işleri ve toplu veri aktarımı tasarlanmalı. Devam eden bir MT5 çağrısı bu şekilde de anında kesilemez.
4. **Emir açma yolunda çok sayıda RPC etkileşimi var.** `app/mt5_client.py:512–549`. Sembol seçimi, fiyat, sembol özellikleri ve `order_send` ayrı adımlar; uzak nesne alanlarını okumak da ek iletişim yaratabilir. Kapatmadaki sunucu tarafı yardımcı fonksiyon yaklaşımı açmaya ve veri okumalarına taşınmalı; sade Python sözlükleri tek seferde aktarılmalı. Hesap/sembol değişiminde geçersizleşen metadata önbelleği ve desteklenen filling politikasının seçilmesi gereksiz çağrı/reddetmeleri azaltabilir.
5. **Ekran fiyatı 1,5, pozisyonlar iki saniyede yenileniyor.** `app/static/app.js:430–432`. Bu, butona basılınca emrin gönderilmesine eklenen sabit bir gecikme değildir; görünen verinin güncelliğini etkiler. Merkezi veri toplayıcı + WebSocket/SSE ile fiyat ve emir sonucu dağıtımı önerilir. Her tarayıcı için ayrı yoğun MT5 sorgusu üretilmemeli.

## Öncelikli mantık hataları

### P1 — Önce giderilmeli

- **Pozisyon sorgusu hatası, boş hesap gibi değerlendiriliyor.** `app/mt5_client.py:424–431,452–454` hata/bağlantısızlıkta `[]` döndürüyor. `app/strategy_bot.py:262–272` bunu ters pozisyonların başarıyla kapandığı şeklinde yorumlayıp yeni emre izin veriyor. Sahte `positions_get=None` ile `close_positions_by_type('SELL') == True` doğrulandı. Hata, gerçek boş sonuçtan ayrılmalı; durum doğrulanamıyorsa yeni emir engellenmeli.
- **AI günlük zarar sınırı uygulanmıyor.** `app/ai_advisor.py:327–389`. `daily_loss_limit` ayarlanıyor fakat `execute_recommendation` içinde zarar hesabı/kontrolü yok. Günlük gerçekleşen net sonuç, açık zarar ve gün sınırı açıkça tanımlanmalı; kontrol tüm yeni emir yollarını kapsamalı. Risk kontrolü pozisyon azaltma/kapatmayı engellememeli.
- **Python botu manuel veya başka botun pozisyonunu kapatabilir.** `app/strategy_bot.py:263–269` yalnızca sembol/yön filtreliyor. Pozisyon yanıtında magic yok; manuel/HMA/AI açılışları `app/mt5_client.py:540` üzerinde aynı magic değerini kullanıyor. Kaynak bazında ayrı kimlik ve sahiplik kontrolü gerekli; netting hesaplarında aynı sembolde birleşen pozisyonlar için ayrıca politika gerekir. Ayrı MQL5 EA dosyasında magic filtresi mevcut.
- **API içinde kimlik doğrulama bulunmuyor.** `app/main.py` emir, hesap giriş, bot ve debug yollarını uygulama seviyesinde doğrulamıyor. Dış proxy koruması bu incelemede doğrulanmadı. Servise erişebilen kişi emir gönderebilir. Oturum/yetki kontrolü ve debug yollarının erişim kısıtı öncelikli.
- **Aynı emir niyetinin tekrar işlenmesini engelleyen sunucu kaydı yok.** `/api/order/open` ve `/api/ai/execute` her çağrıyı yeni işlem olarak ele alıyor. Manuel al/satta aynı sayfa için çift tıklama koruması var; iki sekme veya bağlantı belirsizliğinden sonra tekrar gönderim korunmuyor. AI tavsiyesi de süresi dolana kadar tekrar uygulanabiliyor. İstemci emir kimliği, kalıcı durum kaydı ve broker sonucu uzlaştırması gerekli; belirsiz sonucu otomatik yeniden göndermek çözüm değildir.
- **Ayrı MQL5 EA, ters pozisyon kapanmasını doğrulamıyor.** `HMA_Crossover_EA.mq5:215–218,356–366`. `PositionClose` sonucu kontrol edilmeden karşı yöne emir açılabilir. Ayrıca `Buy/Sell` bool sonucu tek başına gerçekleşme kanıtı sayılıyor. Broker dönüş kodu ve gerçekleşen hacim doğrulanmalı. Bu bulgu Python botundan ayrı çalıştırılabilen EA içindir.

### P2 — Doğruluk ve kullanım

- **Başarılı emir sonrası önbellek temizlenmiyor.** `app/mt5_client.py:419–422,554–559`. Pozisyon/hesap verileri 0,5 saniyeye kadar eski kalabilir. Sahte başarılı açılıştan sonraki sorgu `[]` döndürdü ve MT5'ten yeni okuma yapmadı. Arayüzün anlık yenilemesi bu eski cevaba denk gelirse sonraki iki saniyelik yenilemeye kadar işlem görünmeyebilir. Gerçekleşme sonrası ilgili önbellekler geçersizleştirilmeli; bekleyen emir ayrıca takip edilmeli.
- **85 üzerindeki MA periyotlarında Python botu sessizce işlem yapmıyor.** `app/strategy_bot.py:179–181`: daima 100 mum isteniyor, en az `max(period)+15` mum şartı var. 100/200 periyot tanımlanabiliyor ancak veri kontrolü sürekli başarısız. Periyoda göre yeterli geçmiş istenmeli veya desteklenen sınırlar doğrulanmalı.
- **AI sonucu kısmi/bekleyen durumunu kaybediyor.** `app/ai_advisor.py:356–377` alt katmandan gelen `partial`, `retcode`, `volume`, `uncertain` alanlarını korumuyor. Ana manuel al/sat arayüzü bu ayrımları yapabiliyorken AI yolu “emir açıldı” mesajına indirgeniyor. Tüm emir yolları aynı durum modelini kullanmalı.
- **Toplu kapatmada hata başarı gibi görünebiliyor.** `app/static/app.js:1061–1075`: backend `success:false,total_matched:0,errors:[...]` döndürdüğünde önce “uygun pozisyon bulunamadı” gösteriliyor. Bazıları kapanıp bazıları kapanmadığında da yalnızca başarı sayısı gösteriliyor. Kısmi sonuç ve başarısız ticket'lar görünmeli.
- **Piyasa kapalı mesajı sabit XAUUSD saati gösteriyor.** `app/static/app.js:1033–1035,1067–1069`. Başka sembolün market-closed cevabı da aynı altın mesajını üretebilir. Seans bilgisi broker/sembolden doğrulanmalı; doğrulanamıyorsa saat verilmemeli.
- **Raporlar pozisyon yerine kapanış deal'lerini işlem sayıyor ve profit'i net kâr gibi sunma riski taşıyor.** `app/mt5_client.py:750–781,791–835`. Kısmi kapanışlar işlem sayısını artırır; win rate yalnız profit'e bakar, komisyon/swap/fee netleştirilmez. Açılış deal komisyonları da mevcut filtreden dışlanabilir. Pozisyon yaşam döngüsü bazında net rapor ve açıkça etiketlenmiş brüt/net sonuç gerekli. Geçmiş boşsa tüm geçmişe geri dönüş de seçilen gün aralığını genişletebiliyor.

## Önerilen özellik sırası

1. **Emir izleme ve güvenilirlik:** gönderiliyor/kabul edildi/kısmi/gerçekleşti/reddedildi/belirsiz durumları; kalıcı emir günlüğü; tekrar işleme koruması; bağlantı sonrası broker ile uzlaştırma.
2. **Merkezi risk kontrolü:** günlük zarar limiti, toplam açık risk/lot, pozisyon sayısı, sembol bazında spread ve fiyat yaşı sınırı; broker lot adımı/minimum stop mesafesi doğrulaması. Yeni otomatik emirleri durdurma ve tümünü kapatma ayrı eylemler olmalı; toplu kapatma sırasında otomasyonun yeniden pozisyon açması engellenmeli.
3. **Hız görünürlüğü:** tıklama → API, kuyruk, MT5/broker yanıtı ve ekran güncellemesi ayrı ölçülsün; p50/p95/p99, ret oranı ve fiyat kayması gösterilsin. Monotonik saat kullanılsın; farklı cihazların saatleri doğrudan çıkarılmasın. Demo ortamında normal ve rapor yükü altındaki sonuçlar karşılaştırılsın.
4. **İşlem araçları:** pozisyon SL/TP düzenleme, kısmi kapatma, limit/stop emirleri, bekleyen emir iptali; ardından isteğe bağlı başabaşa taşıma ve iz süren stop. Otomatik stop özellikleri bağlantı kopmasındaki davranışını açıkça göstermeli.
5. **Arayüz:** fiyatın yaşı, spread, bağlantı/işlem izni durumu; isteğe bağlı hızlı işlem kısayolları ve lot hazır değerleri; başarısız toplu işlemlerin ticket bazlı görünümü.
6. **Strateji değerlendirme:** net komisyon/swap dahil rapor, düşüş ölçümü, demo ileri test ve strateji sürümüne bağlı işlem günlüğü. AI değerlendirmesi düşük gecikmeli emir yolundan ayrı kalmalı; otomatik döngü zaten M15 aralığı başına çalışıyor.

## Doğrulama ve sınırlar

- `.venv/bin/python -m pytest -q tests`: **35 geçti**, iki bağımlılık kullanım dışı bırakma uyarısı.
- `node tests/test_order_notice.cjs`, `node tests/test_ai_ui.cjs`: geçti.
- `node --check app/static/app.js`: geçti.
- Ek bellek içi sahte istemci denemeleri: pozisyon sorgusu hatasında devam, Telegram'ın emirden önce bekletmesi, 100 periyotta yetersiz veri ve emir sonrası eski önbellek doğrulandı. Bu denemeler brokera bağlanmadı.
- Mevcut testlerin geçmesi yeni bulunan senaryoları kapsadıkları anlamına gelmiyor. MQL5 derleme/Strategy Tester ve canlı performans testi yapılmadı.
- Olumlu mevcut davranışlar: belirsiz emir sonucunda başka taşıma üzerinden otomatik tekrar yok; yalnız INVALID_FILL reddinde doldurma yöntemi değişiyor; Python botu açık kapatma hatasında karşı emir açmıyor; manuel al/satta sayfa içi çift tıklama koruması var.

Resmî referanslar: [MT5 order_send alanları ve magic](https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py), [positions_get hata sonucu](https://www.mql5.com/en/docs/python_metatrader5/mt5positionsget_py), [CTrade Buy sonucunun doğrulanması](https://www.mql5.com/en/docs/standardlibrary/tradeclasses/ctrade/ctradebuy), [WebRequest senkron davranışı](https://www.mql5.com/en/docs/network/webrequest), [OnTick olay kuyruğu](https://www.mql5.com/en/docs/event_handlers/ontick).
