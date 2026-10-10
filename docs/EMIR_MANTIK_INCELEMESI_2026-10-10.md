# Mantık incelemesi ve emir güvenliği — 10 Ekim 2026

İnceleme web uygulamasının broker köprüsünü, hesap seçimini, kalıcı emir kimliklerini, manuel/bekleyen emirleri, pozisyon yönetimini, HMA ve AI otomasyonunu ve arayüzdeki işlem akışlarını kapsar. Kaynakta düzeltmeler ve sahte broker regresyonları uygulandı. Ayrı MQL5 EA'da da dar güvenlik düzeltmeleri yapıldı; bunlar statik incelemeyle sınırlıdır. İlk yerel doğrulamada dağıtım yapılmadı; kullanıcının sonraki talebiyle yapılan canlı web kurulumu aşağıda kayıtlıdır. Gerçek broker emri ve EA derlemesi yapılmadı.

## Düzeltilen mantık hataları

| Sorun | Yeni davranış |
| --- | --- |
| Gerçek emir request'i için broker ön kontrolü zorunlu değildi. | Açılış, tam/kısmi kapatma, SL/TP ve iptal request'lerinde `order_check` zorunlu. Eksik, hatalı veya reddedilen kontrolde `order_send` çağrılmaz. Başarılı kontrolün kodu/açıklaması sonuçta saklanır. |
| Hesap veya pozisyon, broker kontrolü sürerken değişebiliyordu. | Kontrol sonrasında hesap, fiyat ve yönetilen pozisyon tekrar okunur. Hacim/sahiplik/seviye değişikliğinde işlem durur. |
| Kuyrukta bekleyen talep yeni seçilen hesaba taşınabiliyordu. | Arayüz emir talebine `[hesap numarası, sunucu, hesap türü]` ekler. Sunucu yetki anındaki hesabı da bağlar ve MT5 kilidi alınca tekrar eşleştirir. |
| Günlük limit, işlem kilidi/devam ve AI otomasyon ayarları, doğrulanan hesap değiştikten sonra uygulanabiliyordu. | Risk ayarları yetki ve ekran hesabına bağlanır. AI otomasyonunun doğrulama ve etkinleştirme adımları aynı işlem kilidinde yürür; eski onay yeni hesaba taşınmaz. Etkin otomasyonun kısmi ayar değişikliği de genel durdurmayı aşamaz. |
| Aynı emir kimliği başka hesapta eski başarı/ticket cevabını döndürebiliyordu. | Açılış günlüğü hesabı da karşılaştırır. Pozisyon ve toplu işlemlerin kimlik içeriği hesapla bağlanır; farklı hesapta çatışma döner. |
| Eski pozisyon listesindeki hacimle kapatma ve yanlış magic kullanılabiliyordu. | Tekli/toplu kapatma güncel broker pozisyonundan hacim, yön ve sahiplik okur; ortak köprüye gider. |
| Kısmi veya bekleyen kapatma tamamen kapanmış gibi gösteriliyordu. | Tam kapatma başarı sayılmaz; kısmi/bekleyen/belirsiz durum ayrı bildirilir ve izleme kimliği korunur. |
| Lot sınırı farklı ürünlerde parasal riski eşitlemiyordu. | Web panelindeki stoplu tüm yeni emirler brokerın `order_calc_profit` hesabıyla ortak özsermaye sınırına tabidir. Gerçek hesapta yeni emir için SL zorunludur. |
| Fiyat basamağı, sıfıra yuvarlanan stop, desteklenmeyen yön/emir türü ve doldurma politikası açıkları vardı. | Broker tick/lot/stop özellikleri, yönsel hacim ve izinleri denetlenir. Doldurma yöntemleri yürütme türüne göre seçilir; yalnız kesin `INVALID_FILL` reddinde desteklenen diğer yöntem denenir. |
| Bot hazırlık kontrolü olmadan çalışıyor durumuna geçebiliyordu. | Başlatmadan önce hesap/hedging, izinler, mumlar, fiyat, belirsiz emir, risk ve teminat doğrulanır. Kontrol sırasında ayar/hesap değişirse başlatılmaz. |
| Otomasyon kapanış sonucunu veya durdurma olayını tam denetlemiyordu. | Belirsiz/kısmi/bekleyen kapanış sonrası ters emir açılmaz ve bot durur. Kuyrukta duran otomatik kapatma durdurma olayını yeniden kontrol eder. |
| Bot ayarları hesaplama sırasında değişebiliyor; `single_position=false` uygulanmıyordu. | Ayarlar doğrulanıp tek snapshot olarak kaydedilir; çalışan döngü bitmeden değiştirilmez. Desteklenen tek pozisyon davranışı açık ve zorunludur. |
| AI sağlayıcısının geç yanıtı başka hesabın profil/tavsiyesine yazılabiliyordu. | Veri snapshot'ı, analiz başlangıcı, yanıt ve emir hesabı bağlanır. Hesap değişince sonuç kullanılamaz; dolum kaydı asıl hesapta kalır. |
| AI tam otomatik modu yeniden başlatmada açık kalabiliyor veya güven eşiği tekrar doğrulanmıyordu. | Yeniden başlatmada otomasyon kapalıdır. Emir anında sunucudaki tavsiye, güven/lot/SL/TP ve ayar sürümü tekrar doğrulanır. Belirsiz sonuç otomasyonu durdurur. |
| Uygulama kapanırken asyncio iptali kuyruktaki AI iş parçacığını durdurmuyordu. | Kapanış başında ortak otomasyon durdurma olayı ve AI durdurma olayı etkinleşir; ardından görevler iptal edilir. Kaydetme başarısız olsa bile kuyruktaki worker broker emri gönderemez. Başlamış broker gönderimi geri alınamaz. |
| Geç gelen HTTP/SSE ve sembol yanıtları yeni ekranı eski veriye döndürebiliyordu. | Hesap/sembol ve veri sürümü denetlenir. Eski cevaplar atılır; kaybolan fiyat eski işlem fiyatını temizler. AI onayı sırasında tavsiye değişirse emir gönderilmez. |
| Risk görünümü programatik lot değişimlerinde eski kalabiliyordu. | Lot kısayolları önizlemeyi yeniler; yanlış veya geç gelen önizleme işlem hazır gibi gösterilmez. |
| Yanlış giriş denemesi eski hesap önbelleğini koruyabiliyordu. | Giriş başlangıcı ve sonunda hesap/pozisyon/fiyat önbelleği temizlenir. Hesap numarası kesir veya ek karakterle kabul edilmez. |
| Mutabakat sonrası geç belirsiz yanıt brokerın kesin sonucunu ezebiliyordu. | Kalıcı broker mutabakatı tamamlandıysa geç gelen belirsiz yanıt kesin sonucu değiştiremez. |
| Ayrı EA ilk yüklendiğinde eski kapanmış mum sinyalinden emir açabiliyordu. | Başlangıç mumu kaydedilir; ilk geçerli veri yalnız başlangıç durumunu kurar. Sonraki mum kapanışı beklenir. |
| EA pozisyon okuma hatasını yokluk sayıyor, hedging kontrolünü kapanıştan sonra yapıyordu. | Okuma hatasında açılış/ters açılış engellenir. Hesap modu ilk kapanıştan önce doğrulanır. |
| EA açık SL seçeneğiyle sıfır mesafeyi veya hesaplanan korumasız seviyeyi gönderebiliyordu. | Parametreler ve sonlu indikatör değerleri doğrulanır; aktif SL/TP pozitif, girişten farklı ve doğru yönde olmalıdır. Gerçek hesapta otomatik EA işlemi için SL zorunludur. |

Arayüzde kısmi/belirsiz işlem sonuçları, broker reddi ve otomasyonun bekleme/durma gerekçesi açık gösterilir. Çift tıklamayla tekrar emir/ayar kaydı engellenir. AI ayarındaki günlük limitin yalnız AI yeni emirlerini sınırlandırdığı doğru açıklanır; ortak hesap limitiyle karıştırılmaz. Kâr artışı vaat eden bölüm başlığı strateji ve işlem disiplini olarak düzeltildi.

## Risk ayarının kapsamı

`MAX_TRADE_RISK_PCT=2` ortak başlangıç üst sınırıdır; finansal öneri veya güvenli işlem yüzdesi garantisi değildir. AI ayrıca `AI_MAX_TRADE_RISK_PCT=1` sınırına tabidir; daha düşük değer uygulanır. `.env` ve Compose ortamında yapılandırılır. Geçersiz ortak risk yüzdesinde uygulama başlamaz. Seçilen lot sessizce değiştirilmez; fazla riskli talep reddedilir.

Demo manuel/HMA emirlerinde stop kapatma tercihi korunur. Stop olmayan emirde parasal stop riski hesaplanamaz. Gerçek hesapta yeni piyasa/bekleyen emir için geçerli stop zorunludur. Risk azaltan kapatma günlük zarar veya yeni emir kilidi nedeniyle engellenmez.

Gerçek hesapta pozisyon stopunun kaldırılması da reddedilir. Stop genişletilirse güncel çıkış fiyatından yeni stopa kadar brokerın hesapladığı zarar yeniden sınanır; AI pozisyonunda daha sıkı AI sınırı korunur. Stopu daraltma ve mevcut stopu koruyarak yalnız TP değiştirme, mevcut risk daha yüksek olsa bile risk artışı sayılmaz.

Hesap bazlı günlük zarar, toplam hacim ve emir sayısı kontrolleri korunur. İşlem başına stop riski portföy toplam riskinin yerine geçmez. Açık pozisyonların toplam parasal riski için bu tur yeni bir portföy bütçesi uygulanmadı.

## Doğrulama

Regresyonlar gerçekçi sahte brokerlarla hesap geçişini, broker kontrolü reddini, fiyat/hacim değişimini, kısmi/bekleyen/belirsiz sonucu, aynı kimliğin tekrarını, stop riskini, otomasyon durdurmayı ve geç arayüz yanıtlarını kapsar.

```sh
.venv/bin/python -m pytest -q
for t in tests/*.cjs; do node "$t" || exit; done
for f in app/static/*.js; do node --check "$f" || exit; done
.venv/bin/python -m compileall -q app
git diff --check
```

Son doğrulamada **489 Python testi** ve **21 JavaScript test dosyası** geçti. Python test bağımlılıklarından iki deprecation uyarısı vardır. JavaScript sözdizimi, Python derleme ve diff biçim kontrolü temizdir. Bu test sayıları MQL5 EA derlemesini veya çalışmasını kapsamaz; ortamda MetaEditor yoktur.

Browser becerisiyle yalnız yerel sahte sunucuda terminal/AI sayfaları kontrol edildi. Lot kısayolunun risk önizlemesini yenilemesi, hesabın emir payload'ında korunması, broker reddinin açık gösterilmesi, botun sinyal bekleme durumu ve seçilen sembole ait AI onay/iptal akışı doğrulandı. Tarayıcı konsolunda hata yoktu. Bu kontrol gerçek broker gerçekleşmesi değildir.

Ürün seçimi için aynı türde yalıtılmış test ortamında Bitcoin araması, kripto filtresi, favorilerin sayfa yenilendiğinde korunması ve favoriden seçimle grafik/emir panelinin aynı broker sembolüne geçmesi doğrulandı. Brokerın basamak sayısına göre fiyat gösterimi, doğrulanmış TradingView eşlemesi olmayan ürünlerde MT5 grafiğine geçiş ve yalnız pozisyon kapatmaya açık üründe yeni emir düğmelerinin kilitlenmesi kontrol edildi. Bu kontrollerde gerçek emir gönderilmedi; tarayıcı konsolunda hata yoktu.

## Canlı web dağıtımı

10 Ekim 2026'da kullanıcının dağıtım talebiyle test edilmiş kaynak GitHub `main` dalına gönderildi. Dokploy'un mevcut `compose -p mt5-mt5platform-jtahyh -f docker-compose.yml -f compose.dokploy.yml up -d --build --no-deps web-dashboard` komutu kullanıldı. MT5 terminali yeniden oluşturulmadı; kalıcı veri diski korundu. `.dockerignore` yalnız web uygulama kaynaklarını imaj oluşturma bağlamına alır; yerel hesap sırları ve çalışma çıktıları dışarıda kalır.

Güncellemeden önce `/config/backups/backup-20261010T095413Z-xz_l87` durum yedeği alındı ve SQLite bütünlüğü/dosya özetleri doğrulandı. Önceki uygulama kaynakları `/config/backups/pre-market-watch-source-20261010T095413Z.tar.gz` altında saklandı. İlk güncellemenin Dokploy kaydı `1b5dc72`, önceki kaynak sürümü `a734d0f` olarak doğrulandı. Normal kod geri dönüşünde en yeni emir günlüğü korunur; eski durum yedeği çalışan veri diskinin üzerine yazılmaz.

Kimlik doğrulamalı canlı HTTP kontrollerinde Demo hesap bağlantısı, güncelleme öncesiyle aynı hesap/sunucu/tür, 619 ürün içeren broker kataloğunun hesap eşleşmesi, pozisyon ve bekleyen emir ticket'larının korunması, işlem kilidinin korunması ve HMA/AI otomasyonunun kapalı olduğu doğrulandı. Canlı arayüz kaynaklarının SHA-256 değerleri test edilen yerel dosyalarla eşleşti. Otomasyon başlatılmadı ve gerçek emir gönderilmedi. Uygulama içi tarayıcı canlı alan adına erişimi engellediğinden canlı görsel kontrol yapılamadı; ürün/favori etkileşimleri önceki yalıtılmış tarayıcı doğrulamasıyla sınırlıdır.

## Canlı kullanımın sınırları

Ön kontrol broker kabulü veya gerçekleşme garantisi değildir. Kontrol ile gönderim arasındaki piyasa değişimi, ağ kopması, boşluk/kayma, komisyon ve swap nedeniyle gerçekleşen zarar hesaplanan stop riskini aşabilir. Brokerda duran bekleyen emirler web uygulaması kapalıyken de tetiklenebilir.

Tek aktif uygulama süreci/instance tasarımı korunur. Aynı hesabı dış terminal, bağımsız EA veya başka uygulama eşzamanlı yönetiyorsa uygulama bunlarla atomik işlem koordinasyonu kuramaz. Bağımsız `HMA_Crossover_EA.mq5` web köprüsünün yeni parasal risk politikasını/günlüğünü paylaşmaz. EA'daki dar düzeltmeler web köprüsüyle tam eşdeğerlik sağlamaz; EA MetaEditor'de derlenip izole Demo hesabında doğrulanmadan otomatik kullanım için hazır sayılmaz.

Kaynak düzeltmeleri canlı hesabın bütün broker koşullarında kabul edildiğini göstermez. Üretime geçmeden önce izole Demo hesapta piyasa/bekleyen emir, SL/TP, kısmi/tam kapatma, iptal, yeniden başlatma ve bağlantı kopması kabul senaryoları yürütülmelidir. Hatasız gerçekleşme veya kâr garantisi verilmez.

Broker request kontrolü ve doldurma kuralları [MetaQuotes order_check](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py), [emir özellikleri](https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties) ve [order_send](https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py) belgeleriyle karşılaştırıldı.
