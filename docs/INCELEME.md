# MT5 kod incelemesi — 19 Eylül 2026

Bu rapor `codex/mt5-reliability` dalında yapılan kod incelemesini ve doğrulamasını açıklar. Canlıdaki `mt5_client.py` önbellek/kilit değişiklikleri ve `start.sh` yapılandırma farkları temel alındı. Sunucunun Dokploy tarafından eklenmiş ağ/domain etiketleri ayrı tutuldu; depo Compose dosyası canlı dosyanın üzerine doğrudan yazılmamalı.

## Düzeltilenler

- Bağlantı denemeleri ortak kilit altında; TCP bağlantısına 5 saniye, RPC isteklerine 15 saniye, MT5 initialize/login çağrılarına 10 saniye sınır eklendi. Bu süreler çağrı başınadır; bütün bağlantı zinciri için tek toplam süre değildir.
- Terminal kontrolü başarısızsa eski bağlantı ve önbellek temizleniyor. Başarısız açılan RPC bağlantıları kapatılıyor. Kullanılmayan ve canlı sürümde yanlışlıkla `finally` içine taşınmış mt5linux geri dönüş yolu kaldırıldı; requirements zaten yalnızca RPyC içeriyordu.
- Web açılışı MT5 bağlantısını beklemiyor. Strateji kontrollerinin bekleten çağrıları ayrı iş parçacığında çalışıyor. Durdurulan iş parçacığı bitmeden yeni bot döngüsü başlatılmıyor. Zaten brokera gönderilmiş bir emir durdurma ile geri alınamaz.
- Yanıt kaybından sonra emir başka bir bağlantı yöntemiyle tekrar gönderilmiyor. Yalnızca açık `INVALID_FILL` reddinde başka doldurma politikası deneniyor. Kısmi gerçekleşme tekrar emir gönderimine yol açmıyor. Kısmi/pending kapatma tam kapatılmış sayılmıyor.
- Emir yönü/hacmi ve negatif SL/TP doğrulanıyor. Yanlış yönün sessizce SELL olarak yorumlanması kaldırıldı.
- Ters pozisyon kapanmazsa bot yeni karşıt emir açmıyor.
- Dakika cinsinden grafik süreleri MT5 TIMEFRAME sabitlerine çevriliyor; örneğin 60 doğrudan gönderilmiyor, TIMEFRAME_H1 kullanılıyor.
- Aynı hesap numarası için parola/server doğrulamasını atlayan hızlı login yolu kaldırıldı; başarılı girişte hesap önbellekleri temizleniyor.
- Kodun terminalde Algo Trading düğmesini otomatik açması kaldırıldı. İzinler kullanıcı tarafından terminalden yönetilir.
- HTTP hata/zaman aşımında arayüzde eski yeşil bağlantı durumu bırakılmıyor.
- Sabit broker şifresi, hesap numarası ve AI anahtarı kaldırıldı. Kimlik dosyası 0600 izinle atomik kaydediliyor. `.env.example` eklendi. VNC şifresi Compose için zorunlu ortam değişkeni oldu.
- Depo Compose dosyasında RPyC portlarının internete yayımlanması kaldırıldı; container içi bağlantı aynı kalır. Mevcut VPS portları bu yerel değişiklikle kapanmış değildir.
- Wine Python kurulumu 64 bit paket ve açık Python39 yolu kullanıyor; PATH üzerinden farklı Python sürümünün seçilmesi önleniyor.

## Doğrulama

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
node --check app/static/app.js
bash -n start.sh
```

22 test (gerçek RPyC el sıkışması dahil): bağlantı kaybı, bağlantıların serileştirilmesi, başarısız initialize temizliği, kısmi/belirsiz emir sonucu, güvenli tekrar koşulu, saatlik grafik, giriş doğrulama, ters pozisyon kapatma hatası, bot durdurma ve MT5 beklerken web açılışı. Sahte broker yanıtları kullanılır; gerçek emir gönderilmez. Yerel Python 3.14 ile çalıştırıldı; Docker üretim hedefi Python 3.11/Wine üzerinde uçtan uca işlem testi yapılmadı.

## Öncelikli öneriler ve açık kalanlar

1. **Panel/API kimlik doğrulaması:** Mevcut API emir ve debug yollarında oturum doğrulaması bulunmuyor. Yayından önce kimlik doğrulaması, yetkilendirme ve cookie oturumu kullanılıyorsa CSRF koruması eklenmeli. RPyC yalnızca özel Docker ağında tutulmalı; MT5 masaüstü erişimi de korunmalı.
2. **Sızmış sırları yenile:** Kaynak koddan kaldırmak eski Git geçmişini temizlemez. Önceden kodda bulunan AI anahtarı, broker ve VNC şifreleri yenilenmeli. Yeni değerler Dokploy ortam değişkenleri/secret yönetiminden verilmeli.
3. **AI günlük zarar sınırı:** `daily_loss_limit` saklanıyor, ancak emir gönderimi öncesinde uygulanmıyor. FULL_AUTO açılmadan önce güvenilir işlem geçmişi, gerçekleşen/gerçekleşmeyen zarar tanımı ve veri alınamazsa işlem engeli tasarlanmalı.
4. **Strateji sahipliği:** Bot karşıt pozisyonları sembol/yön üzerinden seçiyor; manuel veya başka botun pozisyonlarını ayırmıyor. Ayrı magic numarası ve yalnızca kendi işlemlerini yönetme kuralı eklenmeli. Netting hesapları ayrıca ele alınmalı.
5. **Dağıtım:** Canlı Compose etiketleri korunmalı, `.env.example` değerleri sağlanmalı ve önce demo ortamında test edilmeli. MT5/Wine bağımlılıkları ve container image sürümleri sabitlenmeli. Açılış gözeticileri kalıcı s6 servisleri olarak yönetilmeli. Canlı dağıtımda `compose.dokploy.yml` ek dosyası kullanılır; sırlar sunucudaki git dışı `.env` dosyasında tutulur.

MT5 resmi referansları: [emir dönüş kodları](https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes), [TIMEFRAME kullanımı](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrompos_py), [initialize timeout](https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py).

## AI maliyet kontrolü

- Otomatik tarama işlem zamanından ayrıldı: M15 zaman aralığı başına en fazla bir döngü. HOLD ve hatalarda da bu aralık tüketilir.
- Aynı sembol/zaman dilimi/kapanmış mum/profil/pozisyon bilgisi için analiz yeniden kullanılabilir. Yeni mum yoksa eski sonuç gösterilir; süresi dolan tavsiye işlem açamaz. Yeniden başlatmada önbellek korunur.
- Son 100 işlemin özeti yerelde hesaplanır, modele yalnızca gruplar ve 8 örnek gönderilir. Aynı geçmiş yeniden analiz edilmez.
- Tavsiye çıktı sınırı 400, geçmiş analizi 800 token. Kesilen JSON otomatik tekrar edilmez. Aynı analiz hatasından sonra en az 60 saniye beklenir.
- `AI_DAILY_CALL_LIMIT=100`: UTC günü için kalıcı çağrı sayısı sınırı; başarısız istekler de sayılır.
- `AI_DAILY_TOKEN_LIMIT=100000`: sağlayıcının bildirdiği toplam token bu eşiğe ulaşınca **sonraki** ücretli çağrı engellenir; son isteğin tokenlarıyla eşik aşılabilir. Yanıtı alınamayan isteklerin harcaması bilinmez, ayrı sayaçta gösterilir. Bu bir kesin dolar bütçesi değildir.
- Arayüz sayacı bu sürümden itibaren tutulan verileri gösterir; eski sağlayıcı harcamalarını geriye dönük içermez. Durum yenilemeleri ücretli AI çağrısı yapmaz.
- Sayaç tek Uvicorn süreci için tasarlanmıştır. Birden çok worker/replica kullanılacaksa merkezi Redis/DB kilidi ve sayaç gerekir.
- Her iki arayüz backend'in `recommendation`, `autopilot` ve profil alanlarıyla uyumlu hale getirildi. Manuel emir onayında gösterilen 0.01 lot istekle birlikte gönderilir; başka bir sembolün son tavsiyesine sessiz geri dönüş kaldırıldı.
- Ek test: `node tests/test_ai_ui.cjs`. Testler yapay API yanıtları kullanır; ücretli sağlayıcı çağrısı veya canlı emir göndermez.
