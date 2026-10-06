# Uygulama genel incelemesi — 4 Ekim 2026

Kapsam: Python backend, JavaScript arayüz kaynakları, MT5 emir köprüsü, HMA ve AI otomasyonu, araştırma/Excel, hesap raporları, kimlik doğrulama, Compose ve yedekleme. Üretim API'leri salt okunur kontrol edildi. Bu incelemede bot başlatılmadı, emir verilmedi ve mevcut risk ayarları değiştirilmedi. Bütün ekranlarda görsel/tıklama kabulü veya bütün brokerlarda işlem kabulü yapılmış değildir.

## Doğrulama

- 225 Python testi geçti; iki bağımlılık deprecation uyarısı var.
- 14 JavaScript test dosyasının tamamı geçti.
- Bağlı hesap DEMO, hesap eşleşmesi doğru, bakiye/özkaynak 246,48 USD.
- HMA botu durmuş, AI otopilotu kapalı. Günlük zarar koruması kapalı, yeni emir kilidi açık değil.
- Kimlik doğrulamasız hesap API erişimi HTTP 401 ile reddedildi. Gerçek hesap ikinci doğrulaması aktif, mevcut oturum kilitli.
- Önceki aynı günkü Demo ön kontrolünde XAUUSD bayat fiyatı emir önizlemesini durdurdu. Broker teşhisi HTTP 503 genel mesaj döndürdü; kaynakta aynı bayat fiyat kontrolü teşhis yolunda da var. Broker seans takvimiyle piyasa kapanışı ayrıca doğrulanmadı.

## Öncelikli açıklar

| Öncelik | Eksik / kanıt | Gerekli davranış |
| --- | --- | --- |
| P1 | HMA/manual için AI ile aynı işlem başına özkaynak risk denetimi yok. `mt5_bridge.py:352` parasal stop bütçesini yalnız AI magic için uyguluyor. HMA stopu kapatılabiliyor. | Ortak risk motoru: seçilen stopun parasal riski, işlem başına sınır, broker lot adımına göre lot hesabı ve gerçek otomasyon için korunan risk profili. Stop riski kayma/gap nedeniyle kesin azami zarar değildir. |
| P1 | Günlük zarar koruması kapatılabiliyor; mevcut Demo hesabında kapalı. Toplam lot sınırı farklı ürünlerin parasal riskini eşitlemez. | Günlük/haftalık zarar, özkaynak düşüşü, toplam açık stop riski ve stop olmayan pozisyonlar için hesap politikası. Yeni emir durdurma ile mevcut pozisyonları kapatma seçeneklerini açık ayırma. |
| P1 | `/api/bot/toggle` bağlantı, hedging, yeni fiyat, yeterli mum ve risk ön kontrolü olmadan çalışıyor durumuna geçebiliyor. Sonraki emir yolunda korumalar var. | Başlatmadan önce hazırlık denetimi; durumlar: durmuş, sinyal bekliyor, piyasa kapalı, veri bekliyor, risk nedeniyle engelli, emir belirsiz. Çalışıyor etiketi işlem yapabilir anlamına gelmemeli. |
| P1 | `order_check` ayrı teşhis yolunda; açılacak gerçek emrin her gönderiminden önce zorunlu değil. | Gerçek request için broker ön kontrolü, retcode ve açıklama kaydı; başarısız kontrol sonrası gönderimi kesme. Ön kontrol başarılı olsa da gerçekleşme garantisi yoktur. |
| P1 | Araştırma OHLC; aynı mumdaki olay sırası ve gerçek Bid/Ask değişimi bilinmiyor. Güncel broker teminat kuralları geçmişe uygulanıyor. | Stratejinin MT5 gerçek tick karşılığıyla kıyas; tarihli broker özellikleri, teminat ve gerçek maliyet kayıtları. |
| P1 | Mum zamanlarının UTC etiketi ile broker saat kaydırmasının doğruluğu bağımsız doğrulanmamış. | Terminal/API örnekleriyle zaman eşleştirme; UTC normalleştirme ve ham zamanların saklanması. Haber ve rollover filtrelerinden önce çözülmeli. |
| P2 | Bayat fiyat ile planlı seans kapanışı ayrılmıyor. Teşhis yolunda genel 503 altta yatan sebebi gizleyebiliyor. | Broker sembol seansları, tatiller ve fiyat yaşını birleştiren durum; brokerın sebep mesajını güvenli biçimde kullanıcıya aktarma. |
| P2 | `single_position=false` bot seviyesindeki kontrolü kaldırıyor fakat köprü aynı magic/sembolde mevcut pozisyonu yine reddediyor (`mt5_bridge.py:322`). | Tek pozisyonu zorunlu ve açık göstermek ya da gerçekten desteklenen çoklu pozisyon modeli kurmak. Checkbox mevcut davranışta çoklu pozisyon izni sağlamıyor. |
| P2 | Araştırma komisyon/kayma kullanıcı girdisi; swap takvim günü yaklaşık hesabı. Üçlü swap, gerçek rollover ve tatil modeli yok. | Broker gerçekleşmelerinden maliyet modeli; tarihli swap özellikleri; normal ve stres maliyet karşılaştırması. |
| P2 | Eski HMA puan testi ile gelişmiş araştırma ayrı motorlar (`backtest.py`, `strategy_research.py`). | Test türü ve varsayımları açık gösterme; ortak yürütme motoruna geçiş. Puan testi broker hesap simülasyonu olarak sunulmamalı. |
| P2 | Tek eğitim/doğrulama/ayrılmış test bölünmesi dayanıklılık için sınırlı. | Çoklu walk-forward, parametre komşuluğu, maliyet stresi, Monte Carlo ve örneklem uyarıları. Başarısız araştırma değerlendirmesi ayar aktarımında zaten korunuyor. |
| P2 | Araştırma izole tek pozisyon; diğer semboller, bekleyen emirler ve botların ortak teminat etkisi modellenmiyor. | Portföy simülasyonu ve hesap toplam risk rezervasyonu; netting otomasyonu desteklenmediği açık gösterilmeli. |
| P2 | Bot ayarları kalıcı fakat adlandırılmış profil ve ayar sürümü/işlem ilişkisi yok. | Demo/gerçek profil ayrımı, profil kopyalama, her işleme kullanılan ayar sürümü; strateji değişiminin açık pozisyona etkisini tanımlama. |
| P2 | Tarayıcı olay ekranı var; bağımsız dış bildirim kanalı yok. | Bağlantı kaybı, bot hatası, risk sınırı ve belirsiz emir için isteğe bağlı dış bildirim; tekrarlı alarm sınırı. Sağlayıcı hesabı kurulmadan mesaj gönderilmemeli. |
| P2 | Panel HTTP Basic kullanıyor; uygulama katmanında ana parola denemeleri için hız sınırı ve normal panel oturumu/çıkış modeli yok. TOTP için ayrı deneme sınırı mevcut. | Panel giriş hız sınırı, yönetilebilir oturumlar ve çıkış; yönetim panelinin erişim/MFA politikasını ayrı doğrulama. Mevcut TLS/rol/TOTP korumaları korunmalı. |
| P2 | Günlük yerel ve şifreli dış yedek mekanizması mevcut; panelde son yedek/geri yükleme başarısı yok. Yedek MT5/Wine ve Dokploy ortam sırlarının tamamını kapsamıyor. | Yedek sağlık görünümü, dış yedek son başarı, bağımsız alarm ve temiz ortam tam kurtarma tatbikatı; sırlar için ayrı kurtarma planı. |
| P2 | Süreç içi emir/risk kilitleri, feed ve AI bütçesi tek süreç tasarımına göre. Compose healthcheck ve depo CI iş akışı yok. | Broker durumunu web canlılığından ayıran sağlık kontrolleri ve CI; worker/replica artırılmadan önce merkezi kilit/risk rezervasyonu. |
| P3 | XLSX sayfa/kolon ve konfigürasyon etiketleri sabit Türkçe (`research_excel.py:63–75`). | İndirilen rapor dilini seçili UI diliyle eşleme. Arayüz çevirileri mevcut olsa da dışa aktarım dili ayrı açık. |
| P3 | Araştırma mevcut temel getiri/düşüş metriklerinin yanında MAE/MFE ve düşüş süresini raporlamıyor. | Özkaynak zaman serisi, işlem içi azami olumlu/olumsuz hareket, maruziyet ve düşüş süresi. Otomatik trailing stop/başa baş isteğe bağlı strateji özelliği olarak ayrıca test edilmeli. |

## Mevcut özellikler — yeniden eksik sayılmamalı

Kimlik doğrulama ve rol kontrolü; TLS yönlendirmesi; gerçek hesap TOTP; hesap/sunucu eşleşmesi; bayat fiyat engeli; tekrar emir kimliği ve belirsiz emir mutabakatı; lot/toplam hacim/emir sayısı sınırları; strateji sahipliği; kapanmış H4 filtre; bot ayarlarını kalıcı saklama; araştırmadan ayar önizlemesi; broker stop-out araştırması; kısmi kapatma ve bekleyen emirler; net maliyetli raporlar; XLSX; araştırma form kalıcılığı, sıfırlama ve tam ekran; yerel/dış yedek araçları mevcut.

## Uygulama sırası önerisi

1. Ortak parasal risk motoru ve hesap risk profili.
2. Bot hazırlık denetimi, seans/veri durumları ve açık hata sebepleri.
3. Her emir için broker ön kontrolü ve tek pozisyon seçeneği tutarlılığı.
4. Zaman doğruluğu, gerçek tick ve maliyet modeli doğrulaması.
5. Piyasa açıkken izole Demo kabulü: strateji sinyali → açılış → SL/TP/ters sinyal → geçmiş → net rapor; ardından restart/kopma senaryoları.
6. Profil sürümleri, dış bildirim, yedek sağlık görünümü, oturum ve CI iyileştirmeleri.

## Resmi referanslar

- https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py
- https://www.metatrader5.com/en/terminal/help/algotrading/testing_features
- https://www.mql5.com/en/book/automation/account/account_margin
