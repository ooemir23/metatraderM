# MT5 manuel emir ayarları — 12 Ekim 2026

Kullanıcının talebiyle manuel emir ekranının varsayılanı **MT5 kuralları** olarak tamamlandı. İlk sürümün SL/TP fiyat alanları, lot adımı ve sapma ayarlarına ek olarak altı bekleyen emir türü, dört geçerlilik süresi, doldurma seçimi ve bekleyen emir düzenleme eklendi.

## Modlar

**MT5 kuralları:** Manuel emirlere panelin sabit emir/toplam lot, işlem sayısı, günlük zarar ve özsermaye stop riski sınırları uygulanmaz. Gerçek hesapta da manuel SL/TP isteğe bağlıdır. Manuel emri göndermek için günlük geçmişin okunması gerekmez. Stop riski hesaplanabiliyorsa tahmin olarak gösterilir. Netting hesabında manuel ters emir, MT5 brokerının kurallarıyla mevcut pozisyonu azaltır/kapatır/tersine çevirir; başka magic kaynağına ait manuel pozisyonla birleşme panel tarafından engellenmez.

**Broker + panel risk sınırları:** Önceki lot, günlük zarar, işlem sayısı ve stop risk sınırları uygulanır; gerçek hesapta SL gerekir. Eski API istemcilerinin bu modu koruması için API'nin varsayılanı PANEL, yeni arayüzün varsayılanı MT5'tir. Arayüz modu her isteğe açıkça koyar.

HMA/AI otomasyonlarının risk ve hedging kuralları iki modda da korunur. Broker ön kontrolü, lot/fiyat/stop kuralları, hesap kimliği, kullanıcı yetkisi, gerçek hesap TOTP, acil yeni-emir kilidi ve mükerrer gönderimi önleyen kalıcı kimlik devam eder. Modlar, API'nin broker güvenlik kontrollerini atlamaz.

## Emir alanları

- BUY Ask, SELL Bid kullanır. Piyasa emrinde SL/TP BUY için Bid'e, SELL için Ask'a göre doğrulanır. Market yürütmede request'e istemci gerçekleşme fiyatı eklenmez; gerçekleşmeyi broker belirler. Sapma Request/Instant yürütmedeki MT5 alanına aktarılır.
- SL/TP, doğrudan fiyat veya girişten puan olarak girilir. Varsayılan 0'dır. Fiyat ve puan aynı istekte karıştırılamaz. Gösterim modu değişince alanlar 0'a döner; ürün değişince eski fiyat seviyeleri temizlenir. Fiyatlar sessizce broker tick adımına yuvarlanmaz.
- Lot adımı broker kataloğundan gelir; 0.001 gibi hacimler korunur. Doldurma AUTO/FOK/IOC/RETURN seçenekleri yürütme türü ve broker izinlerine göre kapatılır. Yalnız AUTO, açık INVALID_FILL reddinde diğer desteklenen yöntemi deneyebilir; seçilen tek politika değiştirilmez.
- Bekleyen türler BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP, BUY_STOP_LIMIT, SELL_STOP_LIMIT'tir. Stop Limit fiyatı ayrı girilir. BUY limit fiyatı tetik fiyatını aşamaz; SELL limit fiyatı tetik fiyatının altında olamaz. SL/TP'nin puan referansı limit alış/satış seviyesidir. Brokerın son kontrolü belirleyicidir.
- Geçerlilik GTC, DAY, SPECIFIED ve SPECIFIED_DAY'dir. Tarihli emirlerde gelecekte geçerli tarih zorunludur. Tarih/saat alanı cihazın yerel saatinden epoch saniyesine çevrilir. Gün seçeneğinin gün sonu/seans işleyişini broker belirler. Bekleyen emirler RETURN ile saklanır.
- Bekleyen listede **Değiştir**, fiyat/Stop Limit, SL/TP ve geçerliliği düzenler. Tür ve lot değiştirilmez. Güncel broker durumu hem pencere açılışındaki snapshot'la hem `order_check` sonrasında karşılaştırılır. Tetiklenen/değişen emir veya dondurma mesafesi nedeniyle değişiklik reddedilir. Eski hesap editörü yeni hesaba istek gönderemez.

Mod, doldurma, SL/TP gösterimi ve sapma tercihleri tarayıcıda hesap/sunucu/hesap türü bazında saklanır. Önizleme ve gönderim aynı seçimleri kullanır; kontrol sırasında değişen seçim gönderilemez. Yanlışlıkla piyasa endpoint'ine bekleyen emir alanı gönderilmesi 422 ile reddedilir. Eski puan temelli günlük kimlikleri tekrar emir göndermeden kullanılabilir.

## Doğrulama ve yayın

**570 Python testi ve 22 JavaScript test dosyası geçti.** Sözdizimi, Python derleme ve diff biçim kontrolleri temizdir.

Sahte broker testleri altı bekleyen tür × dört geçerlilik seçeneğini, yanlış limit/tarih reddini, seçilen doldurma politikasını, Market yürütmede fiyatın çıkarılmasını, gerçek hesapta isteğe bağlı manuel SL'yi, panel limitlerinden bağımsız manuel lotu, netting ters emrini ve otomasyon kurallarının korunmasını kapsar. Broker reddi ve acil kilit, MT5 modunda da emir göndermez. Bekleyen değişiklikte snapshot yarışları, HTTP veri aktarımı ve eski kimliğin tekrarı kontrol edilir.

Arayüz testleri MT5/panel seçimini, hesap bazlı tercihleri, broker seçeneklerini, Stop Limit/süre alanlarını, bekleyen emri düzenlemeyi ve hesap değişiminde eski editörün reddini kapsar. Testler gerçek broker emri göndermez. İlk fiyat/puan sürümü yerel Browser becerisiyle görülmüştü; bu tamamlanan sürümde Browser yeni sekmeye bağlanamadığından son görsel doğrulama yapılmadı.

Canlı panel bağlı DEMO hesap bildiriyor. Dokploy `/` ve `/dashboard/home` HTTP 500 veriyor. Mevcut yönetim SSH anahtarı kabul edilmiyor; yerelde Docker yönetim istemcisi/bağlantısı yok. Bu yüzden canlı kurulum henüz gerçekleşmedi; sunucu yönetim erişimi bekleniyor. Mevcut canlı sürümün değiştiği iddia edilmez.

Kaynaklar: [MT5 emir penceresi](https://www.metatrader5.com/en/terminal/help/trading/performing_deals), [MQL5 emir/doldurma/geçerlilik özellikleri](https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties), [MT5 netting/hedging ve stop prensipleri](https://www.metatrader5.com/en/terminal/help/trading/general_concept).
