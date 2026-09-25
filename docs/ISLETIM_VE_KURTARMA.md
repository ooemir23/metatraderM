# İşletim ve kurtarma

## Otomatik emir doğrulama

Yeni piyasa ve bekleyen emirlerin broker açıklamasına benzersiz `vm:` etiketi eklenir. Hesap (login + sunucu), etiket, sembol, magic, yön/tür ve başlangıç lotu birlikte eşleşmelidir. Emir günlüğü gönderimden önce kalıcı olarak yazılır. Yanıt kaybolursa aynı istek tekrar gönderilmez.

30 saniyelik bakım döngüsü, en az 30 saniye önce başlayan belirsiz/kabul edilmiş talepleri brokerın açık emirleri ve emir geçmişiyle kontrol eder. Her tur en fazla 20 kayıt kontrol edilir; eski kayıtların sırada bekleyip hiç kontrol edilmemesi önlenir. Kanıt bulunamaması ret sayılmaz. Broker etiketi değiştirirse, birden fazla eşleşme varsa veya hesap farklıysa sonuç belirsiz kalır. Gerçekleşen miktar ve kalan miktar ayrı değerlendirilir.

Panel açıkken ilgili sembolün açılış/bekleyen emir sonucu 15 saniyede bir kontrol edilir. Kesin sonuçta yerel talep kilidi temizlenir, sonuç gösterilir. Bu süreç emir göndermez. Eski etiketsiz kayıtlar ve SL/TP/kapatma gibi geçmişten aynı talebe kesin bağlanamayan işlemler manuel MT5 kontrolü gerektirir.

Alan referansları: [MetaQuotes emir özellikleri](https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties), [emir geçmişi](https://www.mql5.com/en/docs/python_metatrader5/mt5historyordersget_py).

## Yedekleme

Uygulama açılışında ve her 24 saatte bir `/config/backups` altında yedek oluşturur; son 14 yedek saklanır. `BACKUP_DIR` ile başka bir bağlı diske yönlendirilebilir. Kapsam: `orders.sqlite3`, `credentials.json`, `ai_memory.json` ve iki TOTP anahtarı. SQLite çevrimiçi backup API ile tutarlı kopyalanır; JSON ve SHA-256 özeti, SQLite bütünlüğü doğrulanır. Parolalı dosyalar 0600, yedek klasörü 0700 izinleriyle saklanır. Başarı/hata uygulama günlüğüne yazılır.

Sunucudaki yalnızca yedek dışa aktarma komutuna izin veren SSH anahtarı, yerel bilgisayara günlük GPG şifreli kopya indirir. `scripts/offsite_backup.py` her kopyayı açıp SHA-256 ve SQLite bütünlüğünü doğrular, boş dizine geri yükleyip dosyaları karşılaştırır; son 30 arşivi saklar. Codex uygulamasında günlük 03:30 otomasyonu kuruludur. Yerel bilgisayar kapalıysa çalışma gecikir; son başarılı yedeğin tarihi düzenli kontrol edilmelidir. Arşivler `/home/ooemir/.config/metatraderm/offsite/archives` altındadır. SSH özel anahtarı ve GPG özel anahtarı aynı bilgisayardaki korumalı dizindedir; ikinci bir güvenli yerde ayrıca saklanmalıdır. Bu yedekler MT5/Wine kurulumunun tamamını veya Dokploy ortam sırlarını içermez.

Manuel işlemler (container içinde):

```sh
python -m app.maintenance backup
python -m app.maintenance verify --source /config/backups/YEDEK_ADI
```

Kurtarma (uygulama kapalıyken, mevcut olmayan boş hedefe):

```sh
python -m app.maintenance restore --source /config/backups/YEDEK_ADI --destination /restore/new-state
```

Çalışan veya daha yeni emir günlüğünün üzerine eski yedek yazılmaz. Felaket kurtarmasında yedek zamanından sonraki broker işlemleri ayrıca kontrol edilmeden otomatik işlem açılmaz. Yedekler hesap sırları içerir; Git'e eklenmez.

## Sürüm geri dönüşü

25 Eylül risk değişikliklerinin yerel doğrulaması: 167 Python testi, yedi JavaScript test dosyası ve MetaEditor derlemesi başarılı. Güncel durum yedeği alındı; web sürümü ayrı hazırlık dizininden oluşturulup yalnız `web-dashboard` konteyneri yenilendi. Dağıtım sonrası Demo hesap ve pozisyon listesi okundu, üst lot sınırını aşan risk önizlemesi reddedildi. Açık pozisyon ve kapalı günlük zarar koruması nedeniyle yeni emirle kabul testi yapılmadı. Ayrı EA varsayılan olarak yalnız sinyal üretir; web otomasyonuyla eşzamanlı otomatik işlem için ortak kilit bulunmaz. Dokploy'un sonraki kaynak dağıtımı bu ayrı hazırlık dizinini kullanmayabilir; kalıcı sürüm için aynı değişiklikler izlenen Git dalına alınmalıdır.

Kod geri dönüşünde **kalıcı `/config` diski ve en yeni emir günlüğü korunur**. Normal sürüm geri dönüşü için durum yedeğini geri yüklemek gerekmez. Otomatik botlar durdurulur; o anki imaj kimliği kaydedilir ve yedek alınır. Önceki sürümden web imajı hazırlanır, yalnızca `web-dashboard` yeniden oluşturulur; MT5 veri diski silinmez ve `down -v` kullanılmaz.

Bu değişiklikten önceki sürüm: `2ff85ee`. Yeni SQLite yan tabloları eski sürüm tarafından yok sayılır; mevcut `orders` tablosunun biçimi değişmez. Geri dönüşten sonra HTTPS/parola, hesap bağlantısı, pozisyon ve bekleyen emir listeleri kontrol edilir. Botlar kendiliğinden başlatılmaz.

Dokploy özel komutu `docker` sözcüğünü kendisi ekler:

```text
compose -p mt5-mt5platform-jtahyh -f docker-compose.yml -f compose.dokploy.yml up -d --build --no-deps web-dashboard
```

## Sürümler ve ölçüm

Python 3.11 ve MT5/Wine imajları digest ile, 29 Python bağımlılığı `app/requirements.lock` ile sabittir. Güncelleme bilinçli yapılmalı ve Python 3.11 testleri tekrar çalıştırılmalıdır. MT5/Wine imajı bu değişiklikte yükseltilmez.

Paneldeki “Emir gecikmesi ve doğrulama” alanı son 500 kaydın ortanca, %95 ve en yüksek sunucu işleme süresini gösterir. Kuyruk ölçümü yeni açılış talepleri içindir. Tekrar sorgular yeni örnek sayılmaz. Bu değerler brokerın gerçek gerçekleşme süresi veya kullanıcı ağ gecikmesi değildir.

## Doğrulama kapsamı

24 Eylül 2026 tarihli gerçek Demo broker senaryoları ve sınırları: [Demo kabul kaydı](DEMO_KABUL.md).

Testler; gerçek TCP/RPyC bağlantısının yanıt sırasında kesilmesini (sahte broker), yeniden bağlantıyı, süreç/günlük yeniden açılışını, aynı talebin tekrar gönderilmemesini, yanlış hesap/etiket ve kısmi gerçekleşmeleri, yedek bütünlüğünü ve geri yüklenen günlüğün tekrar gönderimi engellemesini kapsar. Gerçek hesapta emir göndererek kesinti testi yapılmaz.

Doğrulama sonucu: Son yerel çalışmada 153 Python testi ve beş JavaScript test dosyası geçti. İzole yerel ortamda önceki `2ff85ee` sürümü yeni şemalı günlükle başlatıldı; parola korumalı HTTP erişimi ve eski isteğin tekrar gönderilmeden okunması doğrulandı. Üretim Demo kabul sonuçları ayrı kayıttadır.
