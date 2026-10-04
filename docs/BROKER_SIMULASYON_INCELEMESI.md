# Broker kuralları ve test doğruluğu incelemesi — 4 Ekim 2026

Kapsam: veri → gösterge → sinyal → giriş → açık pozisyon riski → çıkış → rapor/Excel → canlı emir güvenliği → işletim. Bu uygulama MT5 üzerinden çalışan bir işlem terminalidir; brokerın muhasebe, eşleştirme ve denetim sisteminin yerine geçmez. Kod incelemesi ve otomatik testler yapıldı; canlı emir verilmedi.

## Bu değişiklikte eklenenler

- MT5 `account_info` üzerinden brokerın stop-out ölçüm türü, margin call, stop-out ve kaldıraç okunur. Sabit %20 veya başlangıç sermayesinin yüzdesi varsayılmaz. Yüzde türünde özkaynak / kullanılan teminat × 100; para türünde özkaynak karşılaştırılır.
- Seçilen lot için BUY ve SELL teminatları `order_calc_margin` ile geçmiş fiyat aralığında beşer noktada örneklenir. Doğrusal/sabit model doğrulanır; farklı yön teminatları korunur. Okuma başarısızsa veya desteklenmeyen model varsa test reddedilir, sıfır teminatla devam edilmez.
- İzole tek pozisyon için teminat giriş fiyatında sabitlenir. Spread ve toplam işlem komisyonu girişte kullanılabilir sermayeden düşülerek yetersiz teminatlı giriş reddedilir. Toplam komisyonu girişte ayırmak konservatif bir varsayımdır; brokerın gerçek tahsilat takvimi değildir.
- Açık zararla özkaynak ve teminat seviyesi izlenir. Stop-out, stop ve hedef ayrı çıkış nedenleridir. Adverse hareket üzerinde daha önce ulaşılan stop/stop-out seçilir; fiyat boşluğunda mevcut pozisyon stop-out eşiğinin ötesinde ilk fiyatla kapatılabilir. Kayma sonucu eşikten daha düşük bakiye oluşabilir. Negatif bakiyeyi otomatik sıfırlayan bir koruma uydurulmaz.
- Broker minimum stop mesafesi ve tick adımı uygulanır. Geçersiz stop/target girişleri sayılır. Lot minimum/maksimum/adım kontrolü API'de zaten vardı.
- Her dönem için stop-out, margin call, teminat reddi, stop mesafesi reddi, minimum özkaynak/teminat seviyesi ve azami teminat raporlanır. Excel broker kural anlık görüntüsünü ve risk sayaçlarını saklar; stop-out çıkış nedenini taşır. Türkçe/İngilizce arayüz karşılıkları eklenmiştir.
- Eski yerel CSV/puan testleri broker kuralları verilmeden çalışabilir; bu mod broker teminat testi olarak sunulmaz. Web araştırması broker kurallarını zorunlu alır.

## Bulgular ve sonraki gereksinimler

| Öncelik | Bulgu / kanıt | Gereken çalışma |
| --- | --- | --- |
| P1 | `strategy_research.py`: H1/H4 OHLC kullanılır. Aynı mumdaki stop, hedef ve stop-out olaylarının gerçek sırası bilinmez. Kapatma saatleri mum sonuna yaklaşık atanır; gap açılışında gerçek mum açılışı kullanılır. | Aynı stratejinin MT5 EA karşılığını gerçek tick modunda çalıştırmak; gerçek Bid/Ask, olay sırası ve yürütme gecikmesiyle kıyaslamak. Mevcut rapor bir tick doğrulaması değildir. |
| P1 | Yeni teminat modeli güncel broker kurallarını geçmiş fiyatlara uygular. Tarihi kaldıraç, seans bazlı teminat artışı, kademeli teminat ve kur dönüşümü değişikliklerinin arşivi yoktur. | Tarihli sözleşme/teminat anlık görüntülerini arşivlemek ve tarihsel kuralları kullanmak. Exchange ve farklı maintenance margin modelleri şu anda açıkça reddedilir. |
| P1 | `strategy_research.py:swap`: maliyet takvim günü sınırına göre yaklaşık hesaplanır; üçlü swap günü, tatil takvimi ve gerçek rollover saati yoktur. | Broker swap türü, long/short oranı, üçlü gün ve tatil takvimini eklemek; puan/para/yüzde türlerini ayırmak. Oranların zaman içinde değiştiğini kaydetmek. |
| P1 | Araştırmada komisyon ve kayma kullanıcı girdisidir; varsayılan sıfır gerçek maliyetin sıfır olduğunun kanıtı değildir. | Broker gerçekleşmelerinden komisyon modeli ve yön/lot/seans bazlı kayma dağılımı çıkarmak. Normal ve stres maliyetleriyle sonuçları karşılaştırmak. |
| P1 | H4 mum spreadi tüm mum boyunca sabit kabul edilir. Haber/rollover spread açılması görülmez. | Tick Bid/Ask geçmişi ve spread stres senaryoları. Haber filtresi ayrı veri kaynağı ve zaman dilimi doğrulaması gerektirir. |
| P1 | Canlı terminalde günlük zarar koruması kullanıcı tarafından kapatılabilir (`daily_limit_enabled`). 4 Ekim read-only kontrolde bağlı hesap DEMO ve günlük limit kapalıydı. | Kullanıcının seçtiği risk politikasını açık göstermek; canlı otomasyon için zorunlu korunmuş profil ve hesap toplam stop riski tasarlamak. Bu inceleme mevcut limitleri değiştirmedi. |
| P1 | `mt5_bridge.open_deal` brokera gönderimden önce birçok yerel kontrol yapar; `order_check` ayrı broker uyumluluk ekranında kullanılır, her gerçek emir öncesi merkezi zorunlu preflight değildir. | Her emir için broker preflight sonucunu kaydetmek; başarısız margin/filling/trade-mode denetiminde gönderimi kesmek. Preflight başarısı gerçekleşme garantisi olarak sunulmamalı. |
| P2 | Araştırma tek izole pozisyon simüle eder; mevcut canlı pozisyonlar, bekleyen emirler ve başka botların ortak teminatı yoktur. | Çoklu sembol/pozisyon portföy motoru, netting/hedging muhasebesi, toplam risk ve zorunlu kapatma sırası. |
| P2 | Araştırma minimum stop ve tick adımı ekledi; seans takvimi, close-only/long-only/short-only, freeze level değişiklikleri, hacim limitleri ve kısmi gerçekleşme tarihi modellenmez. | Tarihli işlem izinleri, broker retcode olayları, emir durum makinesi ve kısmi dolum senaryoları. Freeze level özellikle sonradan SL/TP değiştirme için gereklidir; başlangıç minimum stop mesafesiyle karıştırılmamalı. |
| P2 | Eğitim/doğrulama/holdout ayrımı ve seçimde holdout kullanmama mevcut; bir tarih aralığının %60/%20/%20 ayrımı tek başına dayanıklılık kanıtı değildir. | Birden fazla walk-forward pencere, parametre komşuluğu, maliyet stresi, işlem sırası Monte Carlo ve yeterli örneklem. |
| P2 | `backtest.py` eski HMA puan testi spread/swap/kaymayı tam kapsamaz; gelişmiş araştırma testiyle aynı motor değildir. | Test türlerini görünür ayırmak; aynı yürütme/maliyet motorunda birleştirmek. İki testten çıkan parasal sonuçlar eşdeğer kabul edilmemeli. |
| P2 | Sonuçlar PF, kazanma oranı ve düşüş içerir; günlük getiri serisi ve risk düzeltilmiş metrikler eksiktir. | Özkaynak eğrisi, maruziyet süresi, beklenen getiri, MAE/MFE, Sharpe/Sortino ve düşüş süresi; işlem sayısı/güven aralığıyla beraber göstermek. |
| P2 | Canlı bot sabit lot kullanabilir; AI için işlem başına özkaynak riski kontrolü var, HMA/manual için aynı zorunlu parasal stop bütçesi yok. | Hesap özkaynağına göre lot hesaplama, açık tüm pozisyonların toplam stop riski, aynı sembol ve korelasyon sınırları. |
| P2 | Demo kabul kayıtları bazı gerçek işlevleri doğruluyor; ağ kopması/kısmi gerçekleşme çoğunlukla sahte broker testleridir. | İzole Demo hesabında senaryo matrisi: ret, gecikme, yeniden başlatma, broker kayıtlarıyla mutabakat. Güncel pozisyonlara müdahale edilmeden yürütülmeli. |
| P2 | SQLite emir kimliği ve sonuç belirsizliği kayıtları var; olay kayıtları değiştirilemez dış denetim defteri değildir. | Ayrı dayanıklı denetim deposu, tutarlılık kontrolü, saklama politikası ve hesap bazlı günlük mutabakat. |
| P2 | Süreç içi kilitler/AI bütçesi tek süreç mimarisine göre tasarlanmış. | Birden fazla web worker/replica öncesinde merkezi kilit, atomik risk rezervasyonu ve merkezi olay kuyruğu. |
| P2 | Yedekleme/şifreli dış kopya ve geri yükleme araçları mevcut; araç varlığı düzenli çalışan işin ve RTO/RPO'nun kanıtı değildir. | Zamanlanmış yedeğin son başarı kaydı, bağımsız uyarı, düzenli temiz ortam geri yükleme ve ölçülmüş kurtarma süresi. |
| P2 | TLS yönlendirmesi, roller, gerçek hesap ek doğrulaması ve origin denetimi mevcut. Yönetim paneli kullanımı ve kullanıcı kimlik bilgileri ayrı işletim yüzeyidir. | Yönetim erişimini kısıtlama, MFA/oturum yönetimi ve parola/anahtar rotasyonu; gerçek erişim ve rol testleri. Bu çalışma kullanıcı hesaplarını değiştirmedi. |

## Mevcut korumalar

Emir kimliği ile tekrar gönderimi engelleme; belirsiz emirlerin broker kanıtıyla mutabakatı; hesaba/sunucuya bağlı işlemler; bayat fiyat reddi; lot ve toplam maruziyet sınırları; risk kilidi; botun pozisyon sahipliği; gerçek hesap ek doğrulaması; rol denetimi; kaynak kontrolleri; yerel yedek ve geri yükleme testleri mevcut. Broker emri nihai olarak kabul/red eden taraftır. Bu özellikler bir finansal düzenleme veya broker lisansı anlamına gelmez.

## Kaynaklar

- [MT5 hesap teminatı ve stop-out türleri](https://www.mql5.com/en/book/automation/account/account_margin)
- [OrderCalcMargin](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcmargin_py)
- [Sembol özellikleri, tick ve teminat](https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants)
- [MT5 gerçek tick testinin özellikleri](https://www.metatrader5.com/en/terminal/help/algotrading/testing_features)
- [MT5 strateji testindeki hesap ve gecikme ayarları](https://www.metatrader5.com/en/terminal/help/algotrading/testing)
