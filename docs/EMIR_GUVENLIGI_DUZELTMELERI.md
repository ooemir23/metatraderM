# Emir güvenliği ve hız düzeltmeleri — 20 Eylül 2026

## Uygulananlar

- Telegram çağrıları ve ayar alanları Python botundan, web arayüzünden ve ayrı MQL5 EA dosyasından kaldırıldı.
- Pozisyon okuma hatası artık boş liste sayılmıyor. API 503 döndürüyor; bot ters pozisyonları doğrulayamadığında yeni emir açmıyor.
- Manuel/HMA/AI emirleri ayrı magic değerleri kullanıyor: 123460 / 123461 / 123462. HMA yalnız kendine ait pozisyonları kapatıyor; sahiplik kapatma anında tekrar doğrulanıyor. Eski ortak magic=123456 pozisyonları otomatik sahiplenilmiyor; kullanıcı tarafından yönetilmeli.
- **Web uygulamasındaki HMA ve AI emirleri hedging hesabı gerektiriyor.** Netting/exchange hesabında pozisyonlar birleştiği için otomatik açma ve HMA kapatma engelleniyor. Manuel işlemler, başka kaynağın mevcut pozisyonuyla birleşmeyecek şekilde kontrol ediliyor.
- Tüm web uygulaması açılışları için ortak günlük zarar kontrolü eklendi. UTC 00:00'dan itibaren gerçekleşen sonuç, komisyon, swap ve fee; ayrıca mevcut açık net zarar kullanılıyor. Açık net kâr gerçekleşen zararı gizlemiyor. Para yatırma/çekme, kredi, düzeltme ve bonus kayıtları bu hesaba katılmıyor. Tutar hesap para biriminde. Bu bir bakiye/equity zirvesinden düşüş ölçümü değildir; geçmiş günden taşınan açık zarar da dahildir. Veri okunamıyorsa yeni emir engellenir, pozisyon kapatma serbest kalır.
- Günlük limit mevcut panel ayarından alınır; başlangıç varsayılanı `DAILY_LOSS_LIMIT=500`. Kalıcı AI ayarı varsa başlangıç değerinin önüne geçer. Günlük limit panelde AI ayarlarının içinde görünse de manuel ve Python HMA açılışlarını da kapsar. Sınıra ulaşmak mevcut pozisyonları otomatik kapatmaz.
- Açılışta eski/geçersiz tick (varsayılan 10 saniye), broker lot adımı/minimum/maksimum hacim ve stop mesafesi kontrol edilir. Desteklenen doldurma politikası doğrudan seçilir; yalnız açık INVALID_FILL reddinde başka desteklenen politika denenir.
- Emir kimlikleri SQLite'a gönderimden önce kaydedilir. Aynı kimlik aynı sonucu döndürür; farklı içerik reddedilir. Süreç gönderim sırasında çökerse kayıt belirsiz kalır ve otomatik tekrar gönderilmez. Bu broker ile dağıtık bir atomik işlem değildir; belirsiz sonuç MT5 üzerinden doğrulanmalıdır.
- Tarayıcı belirsiz manuel emir kimliğini yeniden yükleme sonrasında da saklar. Kullanıcı MT5 durumunu kontrol ettiğini açıkça belirttikten sonra yeni talebe izin verebilir. Kapatma istekleri ve toplu kapatmalar da kimlik taşır; aynı ticket'a eşzamanlı çift tıklama engellenir.
- AI yalnız sunucuda kayıtlı tavsiyeyi uygular; istemci yön/sembol/SL/TP'yi değiştirerek tavsiyeyi değiştiremez. Her tavsiye kimliği bir kere gönderilir; kısmi/bekleyen/belirsiz sonuçlar korunur. Kullanıcı onayında seçilen lot, yapılandırılmış üst sınır dahilinde uygulanır.
- “Tümünü kapat” önce HMA ve AI otomasyonunu durdurur. MT5 erişimini bekleyen otomatik emirler durdurma bayrağını kontrol eder. Gönderim aşamasına zaten girmiş bir işlem geri alınamaz. Ayar değişikliğinden önce başlayan AI analizinin sonradan otomatik işlem açması engellenir.
- Açma/kapatma denemelerinden sonra hesap/pozisyon önbelleği temizlenir. Toplu kapatmada hata ve kısmi başarı açıkça gösterilir. Sembolden bağımsız sabit altın seans mesajı kaldırıldı.
- 100/200 dahil MA periyotları için yeterli mum istenir. Değişmeyen mumda hesap tekrarlanmaz; kontrol aralığı 5 saniyeden 0,5 saniyeye indirildi.
- RPyC tarafında açılış/risk kontrolü, pozisyonlar ve mumlar yerel hesaplanıp tek JSON cevabıyla aktarılır. Geçmiş yanıtı da toplu aktarılır; boş tarih aralığında tüm geçmişe geri dönüş kaldırıldı. Böylece satır/alan başına ağ erişimi azalır.
- MT5 erişim kilidi korunur; **bekleyen emirler bekleyen ekran okumalarına göre önceliklidir**. Başlamış bir çağrı kesilmez. Açılış yanıtına `queue_ms` ve `execution_ms` ölçümleri eklendi; execution süresi MT5 tarafındaki kontrolleri de içerir, saf broker gecikmesi değildir.
- Panel HTML, statik dosyalar ve API HTTP Basic oturumuyla korunur. Parola ayarlanmadıysa erişim kapalıdır ve FULL_AUTO döngüsü işlem açmaz. Farklı origin/form kaynaklı değişiklik istekleri engellenir; debug API varsayılan kapalıdır.
- Ayrı MQL5 EA'da Telegram kaldırıldı, ters kapatma sonucu doğrulanmadan yeni emir açılması engellendi; Buy/Sell dönüş kodu kontrolü eklendi. **Bağımsız EA, web uygulamasının SQLite günlüğünü ve günlük zarar limitini paylaşmaz.**

## Çalıştırma

`.env.example` içindeki yeni alanları gerçek `.env` / Dokploy ortamına ekleyin:

```dotenv
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=<kendi-uzun-panel-parolanız>
ENABLE_DEBUG_API=0
DAILY_LOSS_LIMIT=500
```

Panel parolası broker parolasından ayrıdır. İlk sayfa açılışında tarayıcının oturum penceresi gelir. Dış erişimde HTTPS kullanılmalı; düz HTTP Basic parolayı şifrelemez. Sunucunun mevcut domain/ağ/proxy yapılandırması bu değişiklikle dağıtılmadı veya doğrulanmadı.

Docker'da `TRADING_STATE_DIR=/config`; `orders.sqlite3` kalıcı volume'de tutulur. Docker dışı varsayılan `~/.local/state/metatraderm`. Emir günlüğünü silmek eski kimliklerin tekrarını engelleyen bilgiyi siler. Uygulama MT5 erişimi ve otomasyon için tek Uvicorn worker/tek aktif instance ile çalıştırılmalı; aynı broker hesabında birden çok bağımsız işlem motoru koordine edilmez.

## Doğrulama

- 69 Python testi geçti: günlük limit, veri hatası, sahiplik/netting, stop sırasında bekleyen emir, kalıcı/eşzamanlı emir kimliği, çökme sonrası tekrar engeli, öncelikli kilit, HTTP oturumu/CSRF, AI sonuçları ve gerçek yerel RPyC aktarımı dahil.
- `node tests/test_order_notice.cjs`, `node tests/test_ai_ui.cjs`, `node tests/test_trade_ui.cjs` geçti.
- Her iki JavaScript dosyasının sözdizimi kontrolü geçti.
- Canlı broker emri, canlı dağıtım ve MetaEditor derlemesi yapılmadı. Gerçek broker gecikmesi veya üretim hız kazanımı ölçülmedi.

## Sonraki kapsam

Bu tur kritik emir akışı düzeltmeleridir. WebSocket/SSE, SL/TP düzenleme, limit/stop emir ekranı, iz süren stop, tam broker uzlaştırması ve pozisyon yaşam döngüsü bazında net performans raporu henüz uygulanmadı. Rapor ekranının deal/pozisyon ayrımı ve açılış komisyonlarını kapsayan yeniden tasarımı ayrı kalır.

MT5 alanları ve işlem türleri [MetaQuotes deal referansı](https://www.mql5.com/en/docs/constants/tradingconstants/dealproperties), doldurma/lot/stop özellikleri [sembol referansı](https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants) üzerinden doğrulandı.

## İkinci aşama — 21 Eylül 2026

Önceki “Sonraki kapsam” listesinden aşağıdakiler tamamlandı:

- **SL/TP düzenleme:** Açık pozisyon satırındaki **Yönet** penceresinde mutlak fiyat girilir; 0 ilgili seviyeyi kaldırır. Değişmeyen TP/SL korunur. Kullanıcı pencereyi açtıktan sonra seviyeler başka yerden değişmişse eski değerle üzerine yazılmaz. Fiyat adımı, stop/freeze mesafesi ve fiyat güncelliği doğrulanır.
- **Kısmi kapatma:** Aynı pencerede kapatılacak lot girilir. Hem kapatılacak miktar hem kalan miktar broker lot kurallarına uymalıdır. Açık hacmin tamamı için mevcut Kapat düğmesi kullanılır. Bu işlem günlük zarar limiti nedeniyle engellenmez.
- **Bekleyen emirler:** BUY_LIMIT, SELL_LIMIT, BUY_STOP ve SELL_STOP; fiyat, lot, SL/TP puanıyla oluşturulabilir. Sembolün desteklediği emir türü, GTC süresi, fiyat yönü ve minimum mesafesi doğrulanır. Bekleyen emirler listelenir ve iptal edilebilir. Tümünü Kapat / İptal, otomasyonu durdurur, önce bekleyen emirleri iptal eder, sonra açık pozisyonları kapatır; kısmi başarısızlıkları saklamaz.
- **Bekleyen emir sınırı:** Günlük risk kontrolü emir yerleştirilirken çalışır. Brokerda duran emir panel kapalıyken de tetiklenebilir; tetiklenme anında web uygulaması kontrolü garanti edilemez. Bu davranış emir formunda açıkça gösterilir. Stop emirlerinin gerçekleşme fiyatı istenen fiyattan farklı olabilir; ek bir fiyat garantisi verilmez.
- **Canlı veri akışı:** `/api/live` SSE bağlantısı, 0,5 saniyelik aralıklarla tek MT5 snapshot'ını bağlı panellere dağıtır. Yavaş istemcinin biriken eski mesajları atılır; en son durum korunur. Bağlantı koparsa tarayıcı yeniden bağlanır ve mevcut sorgulama yolu devreye girer. Fiyat yaşı gösterilir. Reverse proxy SSE yanıtlarını tamponlamamalı; `X-Accel-Buffering: no` yanıtı gönderilir.
- **Net raporlar:** Dönem net tutarı, seçilen dönemin gerçekleşen kâr/zarar + komisyon + swap + fee toplamıdır; bakiye/kredi/düzeltme/bonus hareketleri hariçtir. Başarı oranı ve kâr faktörü dönemde tamamen kapanan pozisyonların tüm yaşam döngüsüne göre hesaplanır. Açılış önceki tarihte olsa da o pozisyonun açılış gideri başarı hesabına katılır. Kısmi kapanışlar ayrı pozisyon sayılmaz. Pozisyona bağlanamayan giderler yalnız dönem toplamına girer; eksik yaşam döngüleri başarı oranından çıkarılıp sayısı gösterilir. Bu iki ölçümün tarih kapsamı arayüzde açıklanır.
- Kapanış hareketleri tablosu artık ayrı deal'ler gösterdiğini belirtir. Net satır tutarına satırın swap/komisyon/fee değerleri katılır. Tam yaşam döngüsü değerlendirmesi rapor sekmesindedir. Hesap, pozisyon, hareket ve rapor tutarlarında gerçek hesap para birimi kullanılır.

**Doğrulama:** 99 Python testi; dört JavaScript test dosyası (`test_order_notice`, `test_ai_ui`, `test_trade_ui`, `test_management_ui`) geçti. Yeni testler SL/TP çakışması, lot/kalan hacim, yanlış bekleyen emir fiyatı, zarar limiti, belirsiz sonuçta tekrar engeli, açılış giderleri, kısmi kapanışların sayılması, SSE paylaşımı/bağlantı kapanması ve iptal-kapat sırasını kapsar.

Browser becerisi ile yerel sahte broker sunucusunda SL/TP değişimi, 0,03 lottan 0,01 lot kapatma, bekleyen emir oluşturma/iptal ve SSE ekran güncellemesi doğrulandı. Canlı hesaba bağlanılmadı; gerçek emir gönderilmedi. Üretim sunucusuna dağıtım ve bağımsız MQL5 EA derlemesi hâlâ yapılmadı.

**Kalan ileri özellikler:** Otomatik iz süren stop/başabaşa taşıma, belirsiz emirlerin broker geçmişiyle otomatik uzlaştırılması, ayrı gecikme yüzdelikleri paneli. Mevcut belirsiz sonuç koruması ve MT5 üzerinden kullanıcı doğrulaması devam eder.

Yeni işlem alanları [MetaQuotes işlem türleri](https://www.mql5.com/en/docs/constants/tradingconstants/enum_trade_request_actions), [emir özellikleri](https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties) ve [pozisyon geçmişi sorgusu](https://www.mql5.com/en/docs/python_metatrader5/mt5historydealsget_py) ile karşılaştırıldı.
