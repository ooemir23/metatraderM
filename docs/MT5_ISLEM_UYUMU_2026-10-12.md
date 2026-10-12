# MT5 işlem davranışları: 12 Ekim 2026

Manuel işlem bölümündeki MT5 uyumu genişletildi. Mevcut altı bekleyen emir türü, dört geçerlilik seçeneği, isteğe bağlı manuel SL/TP, broker lot/fiyat adımı, netting ve hedging desteğinin üzerine aşağıdaki değişiklikler geldi.

- **Close By:** Aynı sembolde iki karşıt pozisyon tek `TRADE_ACTION_CLOSE_BY` isteğiyle kapatılır. Hedging hesap, FIFO kısıtı ve sembolün Close By izni doğrulanır. Hacimler farklıysa fark brokerda açık kalır. İki bağımsız piyasa kapatmasıyla taklit edilmez. Broker ön kontrolü sonrasında iki pozisyonun durumu ve hesap yeniden kontrol edilir. Belirsiz sonuç aynı kimlikle yeniden gönderilmez.
- **Kapatma ayarları:** Pozisyon penceresinde tam/kısmi kapatma için sapma ve AUTO/FOK/IOC/RETURN seçilir. Market yürütmede istemci gerçekleşme fiyatı gönderilmez; seçilen tek doldurma politikası başka politikaya çevrilmez. Eski API isteğinin 50 puan/AUTO varsayılanı ve günlük kimliği uyumluluğu korunur.
- **Açık pozisyonda iz süren stop:** Mesafe puan olarak girilir. Yeterli kâr oluşunca BUY için Bid, SELL için Ask üzerinden SL hesaplanır. Broker tick adımına yönü koruyarak hizalanır; mevcut SL genişletilmez, TP korunur. Bir pozisyonda en az 10 saniye gönderim aralığı vardır. Sunucudaki uygulama süreci çalışırken takip eder; tarayıcı kapanması takibi durdurmaz. Süreç yeniden başladığında veya pozisyon/hesap değiştiğinde yeniden etkinleştirme gerekir. 0 iz sürmeyi kapatır; brokerdaki SL silinmez. Belirsiz/bekleyen stop değişikliği otomatik tekrarlanmaz. Gerçek hesapta etkinleştirme ikinci doğrulama gerektirir.
- **Borsa Limit emirleri:** Exchange yürütmede piyasa fiyatını geçen Limit emri sırf fiyat tarafı nedeniyle yerel kontrolde reddedilmez. Broker `order_check` karar verir. Forex/Market yürütme kontrolü korunur.
- **BOC:** Broker doldurma bayrağı izin veriyorsa Limit/Stop Limit için ayrı pasif doldurma seçeneği vardır. Stop/piyasa emrine BOC uygulanamaz. Normal bekleyen emir RETURN kullanır; BOC açıkça `type_filling=3` olarak gönderilir.
- **Piyasa derinliği:** MT5 `market_book_add/get/release` verisi okunur. Gerçek emir defteri sağlanmıyorsa uydurulmaz. Fiyat seçimi bekleyen formunu doldurur ve emir göndermez; eski hesap/sembol verisi formu değiştiremez. Abonelik her okumadan sonra bırakılır.
- **Requote:** Brokerın fiyat değişimi reddi ve verdiği yeni Bid/Ask yanıtı gösterilir. Yeni fiyat otomatik kabul edilip ikinci emir gönderilmez.
- **Netting SL/TP:** Pozisyon azaltan ters manuel MT5 emri, yeni ters pozisyonun stop kuralıyla yanlışlıkla engellenmez. Broker son kontrolü devam eder. Netting hesabında fiyat temelli yeni emir formu mevcut SL/TP'yi başlangıçta doldurur; kullanıcı düzenlemesi sonraki güncellemelerde ezilmez.

## Doğrulama

608 Python testi, 23 JavaScript test dosyası, Python derleme, JS sözdizimi ve diff kontrolleri geçti. Yeni testler hesap/pozisyon yarışlarını, kayıp broker yanıtının tekrar gönderilmemesini, işlem yetkisini, gerçek hesap ikinci doğrulamasını, trailing aralığını, broker kitap aboneliğinin temizlenmesini, BOC ve borsa Limit farkını kapsar.

Browser becerisiyle izole yerel sahte broker ekranında pozisyon penceresi görsel olarak incelendi. Close By ve trailing alanları, brokerın desteklemediği RETURN kapatma seçeneğinin kapanması, piyasa derinliği ve defter fiyatının forma taşınması görüldü. Görsel kontrol gerçek broker gerçekleşmesi değildir. Testlerde canlı broker emri gönderilmedi.

## Kapsam sınırı

Bu sürüm MT5 terminalinin tam kopyası değildir. Bekleyen emre trailing bağlama ve tetiklenince aktarımı, aynı semboldeki trailing pozisyonlarının terminale özgü tick sıralaması, Close All sırasında otomatik Close By eşleştirmesi ve Request yürütmenin ayrı dealer fiyat isteme penceresi bu sürümde yoktur. Close By kullanıcı tarafından pozisyon penceresinden seçilir. Grafik, MQL5/MetaEditor, Strategy Tester ve Market/Signals MT5 terminaliyle aynı değildir. Dolayısıyla tüm MT5 işlevleri veya tüm gerçek broker senaryoları için birebir eşdeğerlik iddia edilmez.

Panelin hesap doğrulaması, gerçek hesap TOTP, acil kilidi ve HMA/AI güvenlik politikaları uygulamaya özgüdür. MT5 modunda manuel panel risk limitlerinin kaldırılması broker ve terminal kurallarını kaldırmaz.

Kaynaklar: [MT5 işlem penceresi](https://www.metatrader5.com/en/terminal/help/trading/performing_deals), [netting/hedging ve trailing davranışları](https://www.metatrader5.com/en/terminal/help/trading/general_concept), [MQL5 emir ve doldurma özellikleri](https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties).
