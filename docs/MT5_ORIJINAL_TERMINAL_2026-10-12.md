# Uygulama içinde orijinal MT5 terminali

Kullanıcının bütün MT5 işlevlerinin aynı şekilde çalışması talebi için, kurulu **orijinal MetaTrader 5 programının ekranı** uygulamaya eklendi. Özel emir panelinin davranışları ayrıca geliştirilmiştir; orijinal ekran ise terminalin kendi menü ve işlem pencerelerini kullanır.

Ana ekrandaki **Orijinal MetaTrader 5 terminali** bölümünü açıp **Orijinal terminali aç / Yenile** düğmesini kullanın. Ekran aynı sayfada açılır ve tam ekrana alınabilir. Kaynak, mevcut `mt5-server` konteynerindeki MT5'tir; ayrı bir broker/TradingView hesabı açılmaz. `terminal64.exe`, `MetaEditor64.exe` ve `metatester64.exe` kuruludur. Grafik, gösterge, şablon, MQL5/MetaEditor, test ve alım satım seçeneklerini orijinal program yönetir. Brokerın desteklemediği işlemler orijinal programda da brokerın kararına tabidir. MQL5 derlemesi/test çalıştırması bu yayında otomatik yapılmadı.

## Davranış

- Orijinal ekran açıkken özel panelin yeni piyasa/bekleyen emir girişi kapanır. Native grafikte başka sembol seçilmişken özel panelin önceki sembole yanlış emir göndermesi önlenir. Ekran kapatıldığında özel emir paneli kendi seçili broker sembolüyle devam eder.
- Native emirler doğrudan MT5'in kendi işlem penceresinden gider. Özel panelin risk limitleri, TOTP ve acil emir kilidi bu pencerede uygulanmaz. Bunun bilgisi açma düğmesinin yanında gösterilir. MT5/broker izinleri, hesap türü ve işlem kuralları programın kendi kontrolleridir.
- Native açma işlemi açık bir yönetici POST isteğidir; sıradan GET sayfa okuması hesap yönetimini değiştirmez. Açıldığında panelin kayıtlı eski hesapla otomatik giriş denemesi durdurulur. Böylece MT5 penceresinde broker hesabı değiştirirken web paneli eski hesaba geri giriş yapmaz. Bu tercih mevcut özel `credentials.json` içine saklanır; web yeniden başlasa da korunur. Panelden doğrulanmış broker girişi yapılınca normal giriş yönetimine dönülür.
- Terminalde hesap değiştirildiğinde özel panelin seçili hesap doğrulaması ayrı kalır; o paneli kullanmak için aynı hesabı panelden doğrulayın. Eski hesaba ait talep yeni hesaba taşınmaz.
- Ekranı kapatmak MT5 programını durdurmaz. Broker emirleri ve terminalde etkinleştirdiğiniz MQL5 programları, sunucudaki MT5 çalıştığı sürece kendi kurallarıyla devam eder. Ayarlar mevcut kalıcı MT5 veri diskindedir.

## Erişim

Yalnız ADMIN kullanıcısı açabilir. Görüntü ve giriş paketleri aynı HTTPS adresinin `/native-mt5/` yolu üzerinden taşınır. Masaüstü parolası yalnız sunucuda `MT5_DESKTOP_PASSWORD` ortam değişkenindedir; iframe adresine veya tarayıcı JavaScript'ine koyulmaz. Dokploy Compose bu değeri mevcut `VNC_PASSWORD` ayarından alır.

WebSocket için yöneticiye imzalanmış, HttpOnly/Secure/SameSite=Strict, 30 dakikalık cookie ve doğru Origin gerekir. Rol/parola değişimi veya web yeniden başlaması eski erişimi geçersiz kılar. En fazla iki görüntü bağlantısı vardır; kapanan bağlantı temizlenir. Diğer panel sayfalarının iframe koruması DENY olarak kalır; yalnız native içerik SAMEORIGIN'dir. MT5 masaüstü portu dışarıya açılmadı ve engine konteyneri yeniden oluşturulmadı.

## Doğrulama

Yetkisiz rol, sahte/süresi dolmuş cookie, farklı Origin, yol geçişi, dış yönlendirme, çift yönlü binary bağlantı, bağlantı temizliği ve native/özel emir ayrımı testlerle kontrol edilir. Orijinal upstream HTML ve VNC protokol el sıkışması yalnız görüntü bağlantısı seviyesinde doğrulanır; mouse/klavye ile canlı broker emri gönderilmez.
