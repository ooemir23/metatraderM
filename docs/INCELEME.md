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
