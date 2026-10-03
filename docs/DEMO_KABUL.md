# Demo kabul kaydı — 24 Eylül 2026

Hesap: Tickmill-Demo, sonu **3161**. Broker hesap türü `DEMO` olarak doğrulandı. Her canlı deneme öncesinde açık pozisyon ve bekleyen emir listesi boştu. İşlemler yalnız EURUSD üzerinde küçük hacimle yapıldı. Test sonlarında iki liste yeniden boş olarak doğrulandı ve `new_orders_halted=true` geri yüklendi. 25 Eylül'de Demo kullanımı için yeni emirler panel API'si üzerinden yeniden açıldı; HMA botu ve AI otopilotu kapalı kaldı.

| Senaryo | Broker sonucu |
| --- | --- |
| Alış, 0,02 lot; stop ve kâr al | Kabul edildi; pozisyon görüldü ve kapandı |
| Satış, 0,01 lot; stop ve kâr al | Kabul edildi; pozisyon görüldü ve kapandı |
| Açık alış pozisyonunun stopunu değiştirme | Broker kabul etti; yeni stop ve korunan TP okundu |
| 0,02 lot pozisyonu 0,01 lot kısmi kapatma | Broker kabul etti; kalan 0,01 lot okundu |
| BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP | Her tür ayrı oluşturuldu, broker listesinde görüldü ve iptal edildi |
| Aynı alış talebini aynı kimlikle yeniden gönderme | Kayıttan önceki sonuç döndü; yeni pozisyon oluşmadı |
| AI kapanmış pozisyon performansı | Açılış ve kapanış kayıtları broker geçmişinde eşleşti; komisyon net sonuca yansıdı |

Bu kayıt işlevsel Demo kabulüdür; gerçek hesapta, başka brokerlarda, piyasa kapalıyken veya gerçek ağ kesintisinde aynı sonucun garantisi değildir. Kısmi broker gerçekleşmesi ve bağlantı kopması yerel sahte broker testleriyle doğrulanır; gerçek brokerda kasıtlı kesinti yapılmadı. Bekleyen emirlerin fiyat tetiklenerek gerçekleşmesi bu oturumda denenmedi.

Tekrar kontrol sırası: hesap türü/sunucu/numara eşleşmesi → risk önizlemesi → tek emir → broker pozisyon veya emir listesi → değişiklik/iptal/kapatma → geçmiş ve rapor → sıfır açık test işlemi → önceki emir durdurma durumunu geri yükleme. Belirsiz bir sonuçta aynı talep yeni kimlikle gönderilmez; broker kayıtları kontrol edilir.

## 25 Eylül 2026 yeniden kabul ön kontrolü

Son risk ve EA değişiklikleri yerelde doğrulandı; web sürümü Demo sunucusuna dağıtıldı. Dağıtım sonrası canlı panel Demo hesap türünü ve doğru hesap eşleşmesini bildiriyor; HMA botu ve AI otopilotu kapalı. Yeni risk önizlemesi, uygulama üst lot sınırını aşan talebi reddetti. Ancak bir açık EURUSD pozisyonu var ve günlük zarar koruması kapalı. API'nin bildirdiği günlük zarar, yapılandırılmış sınırın üzerinde. Mevcut pozisyonun sahibi ve bu durum netleşmeden yeni emir, bekleyen emir veya toplu kapatma testi yapılmadı. Mevcut pozisyona müdahale edilmedi.

Yeniden kabul için önce hesap sahibinin mevcut pozisyon ve günlük limit durumunu doğrulaması gerekir. Temiz Demo hesapta en küçük uygun hacimde tek emir, broker pozisyon listesi, geçmiş ve kapatma sonucu karşılaştırılır. AI sınırı ve komisyonlu kapatma seçimi, önceden bilinen Demo verisiyle doğrulanır. Bekleyen emrin fiyatla tetiklenmesi ayrı izlenir; yalnız oluşturma/iptal testi tetiklenmeyi kanıtlamaz. Test sonunda açık test işlemleri sıfırlanır ve başlangıçtaki güvenlik durumu belgelenir.
