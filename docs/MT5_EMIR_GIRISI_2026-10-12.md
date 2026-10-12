# MT5 emir girişi uyumu — 12 Ekim 2026

Manuel piyasa ve mevcut dört bekleyen emir türünde SL/TP, MT5 yeni emir penceresi gibi **fiyat seviyesi** olarak girilebilir. **Girişten uzaklık (puan)** seçeneği de korunur. Fiyat modunda girilen seviyeler puana çevrilmez ve piyasa hareket ettikçe yeniden taşınmaz. Puan modunda seviyeler gönderim anındaki giriş fiyatından hesaplanır. İki gösterim aynı istekte birlikte kabul edilmez.

Varsayılan SL/TP artık 0'dır; her sembole sabit 200/400 puan eklenmez. Mod değiştirilince alanlar 0'a döner: bir fiyat yanlışlıkla puan veya bir puan yanlışlıkla fiyat olarak gönderilmez. Ürün değişince açık fiyat seviyeleri ve bekleyen emir giriş fiyatı temizlenir. Bu işlemler emir göndermez.

- BUY güncel Ask, SELL güncel Bid kullanır. Yeni piyasa emrinin SL/TP mesafesi BUY için Bid'e, SELL için Ask'a göre kontrol edilir. Bekleyen emirde mesafe emir giriş fiyatına göre kontrol edilir.
- Broker lot minimumu/maksimumu/adımı katalogdan gelir; artırma/azaltma gerçek adıma göre çalışır ve 0.001 gibi hacimleri korur. Girilen fiyatlar broker tick adımına uymalıdır; sessiz yuvarlama yapılmaz.
- Azami sapma puan olarak girilir ve MT5 request'ine aktarılır. Request/Instant yürütmedeki kullanım, Market yürütmede gerçekleşme fiyatı garantisi olarak sunulmaz. Bekleyen emirler RETURN doldurma politikasıyla brokerda tutulur.
- Önizleme ve gönderim aynı fiyat seviyelerini/sapmayı taşır. Kontrol sırasında hesap, sembol, lot, SL/TP, gösterim modu veya sapma değişirse emir gönderilmez. Bekleyen emir seçimi sırasında değişen alanlar ve çift tıklama da korunur.
- Kalıcı emir kimliği SL/TP fiyatını ve sapmayı içerir; farklı bir istek eski kimlikle gerçekleşmiş gibi gösterilmez. Broker `order_check` ve tek gönderim/sonuç mutabakatı korunur.

## Uygulama kuralları ile MT5 kurallarının ayrımı

Bu değişiklik tüm MT5 terminal özelliklerinin kopyası değildir. Mevcut bekleyen türler BUY_LIMIT, SELL_LIMIT, BUY_STOP ve SELL_STOP, süre ise GTC'dir; Stop Limit ve diğer geçerlilik türleri eklenmedi. Hesap kimliği, oturum/yetki, acil işlem kilidi, günlük zarar ve lot/işlem sınırları korunur. Gerçek hesapta SL zorunluluğu ve stop riskinin özsermaye sınırı uygulamaya aittir; bunlar MT5'in genel bir zorunluluğu olarak gösterilmez. HMA/AI otomasyonlarının puan tabanlı SL/TP davranışı korunur.

## Doğrulama ve yayın durumu

Sahte broker regresyonları alış/satış fiyatlarını, açık fiyat seviyelerini, 0 korumayı, 1400 puan Bitcoin spreadini, tick adımı reddini, bekleyen emir RETURN politikasını, sapmayı ve kalıcı kimlik çatışmasını kapsar. Arayüz testleri fiyat/puan/sapma değişiminde eski önizlemenin kullanılamamasını ve 0.001 lot adımını doğrular. Browser becerisiyle gerçek HTML'nin yalıtılmış yerel sunucuda açılması ve fiyat/puan değişiminde alanların güvenle sıfırlanması kontrol edildi. Yerel sunucu broker bağlantısı kurmaz ve emir kabul etmez.

**527 Python testi ve 22 JavaScript test dosyası geçti.** JavaScript sözdizimi, Python derleme ve diff biçim kontrolü temizdir.

Canlı panel HTTP 200 ile bağlı DEMO hesap, sıfır açık pozisyon ve sıfır bekleyen emir bildirdi. Dokploy yönetim sayfası ve yönetim ana sayfası HTTP 500 veriyor; mevcut SSH anahtarı yönetim girişi sağlamıyor. Bu nedenle bu sürümün canlı dağıtımı henüz yapılmadı. Canlı emir gönderilmedi.

Kaynaklar: [MT5 emir penceresi](https://www.metatrader5.com/en/terminal/help/trading/performing_deals), [MT5 alış/satış ve stop prensipleri](https://www.metatrader5.com/en/terminal/help/trading/general_concept), [MetaQuotes order_send](https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py).
