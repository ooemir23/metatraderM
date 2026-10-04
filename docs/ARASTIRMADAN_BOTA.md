# Araştırma ayarlarını Demo ve gerçek hesaba aktarma

1. XAUUSD araştırmasını çalıştırın. Sonuçta seçilmiş aday varsa **Bot ayarlarını incele** açılır. Düğme raporun ayar anlık görüntüsünü kullanır; rapordan sonra formda değiştirilen değerleri sessizce kullanmaz.
2. Bot durmuş olmalıdır. Açılan pencerede aktif hesap türü, sembol ve lot görünür. Araştırmanın değerlendirme mesajı da korunur; başarısız doğrulama mesajı aktarım sırasında kaybolmaz.
3. HMA/KAMA periyotları, KAMA hızlı/yavaş, ATR, ER, bekleme süresi, ATR mesafesi, H1/H4 seçimi, lot ve ATR stop/hedef değerlerini inceleyin. H1 giriş + H4 filtre seçeneği kapanmış H4 mumu ile doğrulanır. H4 filtresi H1 + KAMA + kesişim onayı gerektirir; yanlış birleşim sunucuda reddedilir.
4. **Kaydet** ayarları sunucuda kalıcı saklar. Botu başlatmaz. Ayarlar yedeğe dahil edilir. Sunucu yeniden başladığında ayarlar geri yüklenir, bot durmuş kalır.
5. Önce Demo hesapta izleyin. Gerçek hesap için doğru hesap/sunucu eşleşmesi, hedging hesap, gerçek hesap ek doğrulaması ve uygulamadaki risk kontrolleri geçerlidir. **Botu Başlat** ayrı bir işlemdir; araştırma düğmesi işlem açmaz.

Aktarılan aday aynı sembolde tek HMA bot pozisyonu tutar. Mevcut pozisyon veya bekleyen bot emri yeni girişi engeller. Bot ilk açılışta mevcut mumu referans alır; eski kesişimi hemen işlem olarak açmaz. Sonucu belirsiz/pending/kısmi emir botu durdurur; aynı hesap/sembol/stratejide belirsiz açılış mutabakatı tamamlanmadan yeni giriş engellenir.

## Araştırma ile gerçek işlem farkları

- Araştırma başlangıç sermayesi gerçek hesap bakiyesini değiştirmez. Gerçek özkaynak ve teminatı broker belirler.
- Araştırma tarih aralığı, son dönemdeki zorunlu kapatma ve eğitim/doğrulama bölümleri botun çalışma süresi değildir. Bot stop/hedef/ters sinyal kurallarıyla sürekli çalışır.
- Araştırma komisyon/swap/kayma varsayımları brokera gönderilmez. Gerçek maliyetleri broker tahsil eder; gerçekleşme fiyatları farklı olabilir.
- Stop-out brokerın gerçek hesap teminatına göre uygulanır. Araştırmanın izole tek pozisyon hesabı, hesabınızdaki diğer işlemlerin ortak teminat riskini temsil etmez.
- Sabit lot aktarılır; araştırmanın sermayesine göre lotu gerçek bakiyeye otomatik oranlayan bir hesap yapılmaz. Lot ve stop mesafesini gerçek hesaptaki parasal risk açısından kontrol edin. Uygulamanın üst lot/toplam maruziyet ve günlük risk politikası ayrıca geçerlidir.
- HMA/KAMA başlangıç koşulları, erişilebilen geçmiş uzunluğu, mumlar arasındaki gecikme ve OHLC/tick farkları sonuçları etkileyebilir. Ayar aktarımı birebir getiri eşitliği anlamına gelmez.

Bu değişiklik H4 filtre paritesini, tek pozisyon davranışını, güvenli aktarım önizlemesini, kalıcı bot ayarlarını, eski sinyalin başlangıçta atlanmasını ve belirsiz emir engelini ekler. Gerçek tick motoru, tarihsel teminat/rollover takvimi ve çoklu portföy simülasyonu için kalan bulgular `BROKER_SIMULASYON_INCELEMESI.md` içindedir; bunlar uygulanmış özellik olarak sunulmaz.
