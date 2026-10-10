# HTTPS istekleri ve manuel emir ön kontrolü — 10 Ekim 2026

Kullanıcının BCHUSD ekranında gösterdiği “Farklı kaynaktan işlem isteği reddedildi” mesajı, canlı HTTPS adresinden gelen ve tarayıcı `Origin` başlığını taşıyan, emir göndermeyen `/api/trade/preview` isteğinde tekrar üretildi. TLS ters proxyde sonlandığından uygulama isteği HTTP olarak görüyordu. Önceki dağıtımda GET ve tarayıcı Origin başlığı bulunmayan isteklerin doğrulanması bu hatayı yakalamamıştı.

## Düzeltme

- `DASHBOARD_PUBLIC_ORIGIN`, panelin açıkça tanımlanan yayın adresini kaynak kontrolüne bağlar. Dokploy Compose ek dosyası mevcut HTTPS adresini tanımlar; farklı alan adı, şema veya port reddedilir. `X-Forwarded-*` başlıklarına güvenilmez. Kaynak kontrolü, kimlik doğrulama ve kullanıcı yetkileri korunur.
- Kaynak reddi, sunucu işlem koduna ulaşmadığını `reason_code=untrusted_origin, not_submitted=true` ile açıkça bildirir. Arayüz bu kesin reddi belirsiz broker sonucu olarak saklamaz. Ağda kaybolan emir yanıtları kalıcı işlem kimliğiyle izlenmeye devam eder.
- Al/sat düğmeleri yalnız doğrulanmış broker fiyatı ve ilgili yönün başarılı risk önizlemesiyle açılır. Alış ve satış ayrı doğrulanır. Broker fiyat yaşı, sunucunun saat düzeltmesini içeren teşhisinden alınır; teşhis bağlantısı beş saniye yenilenmezse veya fiyat on saniyeden eskiyse yeni emir girişi kapanır.
- Tıklamada risk yeniden doğrulanır. Doğrulama sırasında hesap, ürün, katalog, lot veya SL/TP değişirse ya da fiyat bayatlarsa yeni emir/kalıcı talep oluşturulmaz. Risk kontrolü zaman aşımı ve çift tıklama yeni emir oluşturmaz. Fiyat/risk iyileşmesi otomatik emir göndermez.
- Bekleyen emir girişi de güncel fiyat doğrulaması ister. Risk azaltan mevcut pozisyon kapatma işlemleri seçili ürünün fiyat kilidine bağlanmaz. Sunucu emir anında mevcut broker/risk kontrollerini uygulamaya devam eder.

## Doğrulama

**512 Python testi ve 22 JavaScript test dosyası** geçti. JavaScript sözdizimi ve diff biçim kontrolü temizdir. Yeni kaynak kontrolü testleri ters proxy, güvenilmeyen yönlendirme başlıkları, hatalı/başka kaynaklar, default HTTPS portu, form istekleri ve yetkisiz erişimi kapsar. Arayüz bütünleşme testi gerçek arayüz fonksiyonlarını sahte HTTP cevaplarıyla çalıştırır; bayat/eksik/gelecek/ters fiyat, her yön için ayrı risk, kontrol sırasında hesap/girdi/katalog değişimi, çift tıklama, zaman aşımı ve kesin/belirsiz sonuç ayrımını doğrular. Testler gerçek broker emri göndermez.

Dağıtım öncesinde canlı Demo hesap bağlantısı, pozisyon/bekleyen emir listeleri ve işlem kilidi kaydedildi. HMA botu ve AI otopilotu kapalıydı. `/config/backups/backup-20261010T114904Z-5c1x8v` durum yedeği alındı ve `Backup verified` sonucuyla doğrulandı. Dağıtım yalnız `web-dashboard` hizmetiyle sınırlıdır; MT5 terminali ve kalıcı veri diski korunur. Kod geri dönüş sürümü `d55b269`dur; kod geri dönüşünde yeni emir günlüğünün üzerine eski durum yedeği yazılmaz.

Yayın sonrası kabul kontrolü: kimlik doğrulamalı HTTPS Origin başlıklı risk önizlemesi ve başka Origin başlıklı negatif kontrol; aynı hesap/ticket/işlem kilidi ve kapalı otomasyonların doğrulanması; sunulan arayüz kaynaklarının yerel SHA-256 özetleriyle karşılaştırılması. Başarılı kaynak kontrolü, güncel olmayan fiyat veya broker risk reddini geçersiz kılmaz. Gerçek emir gerçekleşme testi bu çalışmanın kapsamında yapılmaz.
