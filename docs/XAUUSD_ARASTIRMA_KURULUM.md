# XAUUSD HMA–KAMA–ATR araştırması

## Kurulan kaynak

Web panelindeki **XAUUSD · HMA / KAMA / ATR araştırması** bölümünden tarih aralığı ve maliyet varsayımları girilir. Yeni motor işlem açmaz ve mevcut HMA botunun ayarlarını değiştirmez.

- HMA(14), KAMA ER periyodu 10, KAMA hızlı/yavaş 2/30, Wilder ATR(14) başlangıç değerleridir; en iyi parametre iddiası yoktur.
- KAMA ilk değeri ilk ER periyodunun kapanış ortalamasıyla başlatılır. Eğitim başlangıcından önce en büyük gösterge periyodunun beş katı kadar mum ısınma için kullanılır. MT5'in yerleşik iAMA başlangıcıyla birebir eşitlik varsayılmaz.
- Filtreli girişte ER >= 0.30, HMA ve KAMA eğimleri işlem yönünde olmalıdır. H1+H4 modunda son kapanmış H4 KAMA eğimi ve ER'si de aynı yönde olmalıdır. Eski H4 verisiyle yeni giriş yapılmaz.
- Ham kesişim aynı ATR stop/hedef ve maliyetlerle karşılaştırma ölçütüdür. H1+H4 filtresiz ölçütü H1 ham kesişimiyle aynıdır.
- Kesişimin oluştuğu kapanışta onay mümkündür. Azami bekleme 0/1/2/3 sonraki mumdur; minimum mesafe 0/%5/%10/%20 ATR'dir. Kesişim dahil son izinli mumda onay yoksa aday iptal edilir. Ters kesişim önceki adayı geçersiz kılar.
- Onay kapanmış mumdan üretilir; giriş sonraki mumun açılışındadır. Ham ters kesişim açık araştırma pozisyonunu kapatır; ters yönde giriş ancak filtre/onay sağlanırsa yapılır.
- Stop 2 ATR, hedef 3 ATR başlangıç varsayımlarıdır. Hedef 0 ile kapatılabilir. Bunlar testte sabittir; periyot, stop, hedef ve ER ayrıca taranmaz.

## Veri ve seçim

H1, H4 ve H1 giriş/H4 filtre için toplam 51 yapı karşılaştırılır: her modda bir ham kesişim ve 16 filtreli seçenek. Gerçek ortak tarih aralığında H4 kapanışlarına hizalı %60 eğitim, %20 doğrulama ve %20 ayrılmış test kullanılır. Bölümler arasında açık pozisyon veya bekleyen aday taşınmaz; göstergeler yalnız önceki fiyat geçmişini kullanır.

En az 20 eğitim işlemi ve pozitif eğitim net puanı sağlayan filtreli seçenekler arasında net puan/azami düşüş oranı en yüksek aday seçilir. Hiçbiri sağlamazsa **uygun aday yok** sonucu verilir. Doğrulama ve ayrılmış test seçimde kullanılmaz. Ayrılmış test yalnız seçilen aday ve onun ham kesişim ölçütü için hesaplanır. Pozitif eğitim tek başına canlı işlem uygunluğu değildir.

Geçmiş istekleri en fazla beş yılı ve her zaman diliminde 50000 mumu destekler. Brokerın terminalde mevcut geçmişi daha kısa olabilir. Rapor gerçek mum sayısını, veri başlangıç/bitişini, ortak test dönemini ve uzun aralıkları gösterir. Uzun aralıklar hafta sonu/seans kapanışı da olabilir; eksik mum olduğu kesin olarak söylenmez. H1/H4 karşılaştırması aynı ortak dönemde yapılır; farklı dönem sonuçları zaman dilimi seçimi için kullanılmaz.

## Maliyet ve gerçekleşme sınırları

MT5 OHLC bid fiyatı kabul edilir; alış ask'tan açılır, satış ask'tan kapanır. Tarihsel mum spreadi mum içinde sabit kabul edilir. Komisyon gidiş-dönüş puan/işlem, kayma piyasa ve stop gerçekleşmesi başına puandır. Limit hedefe kayma eklenmez; aynı mum stop ve hedef görürse stop önce sayılır. Stopun ötesindeki açılış boşluğu daha kötü fiyatla sayılır.

Swap kullanıcı tarafından girilen, takvim günü sınırı başına puan maliyetidir; pozitif değer maliyet, negatif değer kredidir. Bu yaklaşık model brokerın üçlü swap ve tatil takvimini taklit etmez. Sıfır değerler sıfır maliyet varsayımıdır; broker maliyetleri otomatik tespit edilmez.

Sonuçlar sabit hacimde puandır; lot, teminat, hesap para birimine dönüşüm veya bileşik risk boyutlandırması yapılmaz. Düşüş kapanmış sonuçlarla birlikte mum içi olumsuz fiyat hareketi ve kapanış işaretlemelerini içerir; gerçek tick sırası bilinmez. Canlı kullanımdan önce broker gerçek tick verisiyle MT5 Strategy Tester ve Demo doğrulaması gerekir.

## API ve dosyadan çalışma

- `GET /api/research/history?symbol=XAUUSD&timeframe_minutes=60&start=...&end=...`: H1 veya H4 kapanmış mumlar ve spread; zamanlar epoch saniyesidir, bitiş hariçtir.
- `POST /api/research/compare`: tarih aralığı, periyotlar ve maliyetlerle karşılaştırma. Kimliği doğrulanmış ADMIN/TRADER/VIEWER kullanabilir. Aynı anda bir araştırma çalışır; diğer istek 429 alır.
- Kurulumun mevcut `MT5_TICK_CLOCK_OFFSET_SECONDS` değeri yalnız devam eden mumu dışlamak için kullanılır. Dönen ham zaman damgaları yeniden kaydırılmaz. Broker değişirse bu mevcut uygulama ayarı ayrıca doğrulanmalıdır.

Depo kökünde:

```sh
.venv/bin/python scripts/strategy_research.py fetch --start 2023-10-01 --end 2026-10-01 --output /tmp/xauusd-history.json
.venv/bin/python scripts/strategy_research.py compare --history /tmp/xauusd-history.json --output /tmp/xauusd-report.json
```

Fetch mevcut `production.credentials.json` dosyasını bellekte kullanır; parola yazdırmaz. Sunucuda yeni endpoint kurulu değilse HTTP 404 ile açıkça durur. Mevcut çıktı dosyaları üzerine yazılmaz. Alternatif olarak `--h1-csv ... --h4-csv ...` kullanılır; sütunlar `time,open,high,low,close,spread`, zamanlar epoch saniyesidir. Spread yoksa `--settings` dosyasında `historical_spread:false` ve sabit spread belirtilmelidir. Dosyadan çalışmada brokerın gerçek `point` değerini Settings JSON'unda belirtin; varsayılan 0.01'dir.

## Canlı sunucuya kurulum

Bu değişiklik için ek Python paketi veya yeni MT5 terminali gerekmez. Mevcut web imajı yeniden oluşturulmalıdır. Paket yalnız uygulama kaynaklarını taşır; hesap parolalarını, Docker ortam değişkenlerini ve MT5 veri diskini içermez.

1. `artifacts/xauusd-research-install.tar.gz` paketini sunucuda boş bir klasöre açın.
2. Paket içindeki `install_research_bundle.py --project-root SUNUCUDAKI_KAYNAK_DIZINI` komutunu Python 3.11 veya üzeriyle çalıştırın. Kurucu tüm kaynak SHA-256 değerlerini önce kontrol eder. Sunucuda farklı kod varsa üzerine yazmaz; önce birleştirme gerekir. Eski kaynakların yedeğini alır.
3. Sunucudaki mevcut Compose/Dokploy ayarlarıyla yalnız `web-dashboard` hizmetini yeniden oluşturun. Dokploy özel komutunun mevcut biçimi:

```text
compose -p mt5-mt5platform-jtahyh -f docker-compose.yml -f compose.dokploy.yml up -d --build --no-deps web-dashboard
```

4. `/api/account` bağlantısını ve yeni araştırma panelini doğrulayın, H1/H4 geçmişini alın ve raporu çalıştırın. Kaynak Git dışı kurulmuşsa sonraki Dokploy kaynak dağıtımında korunması için aynı değişikliği dağıtılan Git kaynağına da alın.

3 Ekim 2026: 201 yerel Python testi ve mevcut JavaScript kontrolleri geçti; izole tarayıcıda örnek veriyle panel doğrulandı. Başlangıçtaki SSH erişim engeli Dokploy yönetim oturumuyla aşıldı. Canlı kaynak dosyalarının başlangıç Git sürümüyle SHA-256 eşleşmesi kontrol edildi; uygulama durum yedeği ve kaynak arşivi `/config/backups` altında alındı. Güncelleme GitHub `main` üzerinden mevcut web-only Compose komutuyla kuruldu; MT5 terminali yeniden oluşturulmadı. Geçmiş endpoint'inin açık H1/H4 query parametreleri de canlıda doğrulandı.

İlk gerçek broker çalışmasında 1 Ekim 2023–1 Ekim 2026 istek aralığında 17738 H1 ve 4641 H4 kapanmış mum alındı. Ortak test dönemi 6 Kasım 2023–1 Ekim 2026 oldu. Eğitimde H4, 0 mum bekleme, %5 ATR mesafe seçildi; sonraki doğrulama 8 işlemde -1690.81 puan, ayrılmış test 9 işlemde -19713.37 puan üretti. Komisyon/kayma/swap sıfır varsayıldı; tarihsel mum spreadi dahildi. Sunucu ve yerel hesaplama birebir eşleşti. Bu sonuç doğrulanmış işlem ayarı değildir; düşük işlem sayısı ayrıca sınırlayıcıdır.

Raporun `assessment` alanı, seçimi değiştirmeden doğrulama ve ayrılmış testte pozitif net sonucu ve her iki bölümde minimum işlem sayısını kontrol eder. Negatif sonuçta `failed_validation`, az işlemde `insufficient_evidence` gösterilir. Pozitif simülasyon da canlı gerçekleşme garantisi değildir. Görülmüş ayrılmış test verisi, sonraki strateji değişiklikleri için yeni ve görülmemiş veri olarak kabul edilmemelidir.
