> Web panelindeki emir güvenliği, hız düzeltmeleri ve zorunlu panel parolası kurulumu: [Emir güvenliği düzeltmeleri](docs/EMIR_GUVENLIGI_DUZELTMELERI.md). Telegram entegrasyonu kaldırılmıştır.

## Panel dili / Interface language

Terminal ve AI Danışmanı sayfalarının üst kısmındaki **Türkçe / English** menüsünden dili seçin. Seçim aynı tarayıcıda saklanır ve iki sayfada da uygulanır; ilk açılışta tarayıcı dili kullanılır. Grafik arayüzü, sayı biçimi ve yeni AI analizleri seçilen dili izler. Daha önce farklı dilde oluşturulmuş bir AI işlem profili varsa, seçtiğiniz dilde yeni bir profil oluşturmak için **İşlemlerimi Analiz Et & Öğren / Analyze My Trading History** düğmesini kullanın. Bu işlem AI sağlayıcısına ücretli istek gönderebilir ve mevcut istek sınırlarına tabidir.

## Web paneli için güncel güvenlik davranışı

- Broker girişinde **Demo Hesap** veya **Gerçek Hesap** açıkça seçilir. MT5'in bildirdiği gerçek hesap türü, numara ve sunucu eşleşmeden giriş kabul edilmez. Sunucu adı brokerın verdiği tam adla girilir; uygulama adı otomatik değiştirmez. Eski kayıtlarda hesap türü yoksa panelden yeniden giriş gerekir.
- Panelde broker hesabına giriş yapılmadan veya MT5'teki aktif hesap kayıtlı hesapla uyuşmadan yeni emir ve kapatma gönderilmez. Hesap değiştirildiğinde panel uyarı gösterir.
- **Tümünü Kapat / İptal** yeni emirleri kalıcı olarak durdurur. Brokerda kalan pozisyon ve bekleyen emirler son kez doğrulanır. MT5 durumunu kontrol ettikten sonra panelde **Yeni Emirlere Devam Et** düğmesini kullanın. Durdurma durumu yeniden başlatmada da korunur.
- Yeni emir başına, toplam açık ve bekleyen lota ve toplam işlem sayısına sınır uygulanır. Varsayılan değerler sırasıyla `0.10`, `0.50` ve `10`; `.env` içinde `MAX_ORDER_LOTS`, `MAX_TOTAL_OPEN_LOTS`, `MAX_OPEN_ORDERS` ile ayarlanır. Bunlar farklı sembollerde aynı parasal riski temsil etmez; günlük zarar sınırı ve broker teminat kontrolleri ayrıca geçerlidir. Sınır okunamayan broker verisinde yeni emir engellenir.
- Compose portları yalnız sunucunun `127.0.0.1` adresine bağlar. Dokploy panel yönlendiricisi yalnız HTTPS kullanır. MT5 container'ı paylaşılan proxy ağından çıkarıldı ve masaüstü için herkese açık HTTP yönlendiricisi kaldırıldı; gerektiğinde SSH tüneliyle yerel `3001` portuna erişin. Dağıtımda container yeniden oluşturulmadan mevcut port, ağ ve yönlendiriciler değişmez.
- Testler: `.venv/bin/python -m pytest -q` ve `for t in tests/*.cjs; do node "$t"; done`.

# HMA Crossover Expert Advisor (MetaTrader 5)

Bu robot (Expert Advisor), **Hull Moving Average (HMA)** ile seçeceğiniz **2. bir Hareketli Ortalama (İkinci HMA, EMA, SMA veya LWMA)** kesişimini baz alarak otomatik alım-satım yapan ve sinyal üreten profesyonel bir algoritmadır.

**Mac, Linux ve Windows** üzerindeki tüm MetaTrader 5 terminallerinde hiçbir ek yazılıma gerek duymadan doğrudan çalışır.

---

## 🌟 Temel Özellikler

1. **Gelişmiş Kesişim Algoritması:**
   * **1. İndikatör:** Yerel olarak hesaplanan, gecikmesiz **Hull Moving Average (HMA)**.
   * **2. İndikatör:** Menüden tek tıkla seçilebilir:
     * İkinci bir HMA (Hızlı HMA + Yavaş HMA kesişimi)
     * EMA (Üstel Ortalama)
     * SMA (Basit Ortalama)
     * LWMA (Ağırlıklı Ortalama)
2. **Açılıp Kapanabilen Stop Loss (SL) & Take Profit (TP):**
   * Stop Loss tamamen tercihe bağlıdır (`InpUseStopLoss = true/false`).
   * İster manuel olarak kapatıp sadece ters sinyalde pozisyonu kapatabilirsiniz, isterseniz istediğiniz puan mesafesinde koruyucu stop koyabilirsiniz.
3. **Esnek Çalışma Modu:**
   * **Tam Otomatik:** Şartlar sağlandığında hem sinyal verir hem de emri doğrudan açar (`InpAllowTrading = true`).
   * **Sadece Sinyal:** İşlem açmaz, sadece ekrana ve telefona sinyal gönderir (`InpAllowTrading = false`).
4. **Bildirim Sistemi:**
   * MT5 Ekran Pop-up Uyarısı (`Alert`)
   * MT5 Mobil Uygulaması Push Bildirimi (`SendNotification`)
   * İsteğe bağlı sesli uyari (`PlaySound`)
5. **Güvenli İşlem Yönetimi:**
   * Sinyaller **mum kapanışında** teyit edilir (Repaint ve mum içi sahte kesişimler önlenir).
   * Ters sinyal geldiğinde mevcut açık pozisyonu otomatik kapatıp yeni yöne dönebilir (`InpCloseOpposite = true`).
   * Grafik üzerinde anlık indikatör değerlerini ve son sinyal durumunu gösteren bilgi paneli içerir.

---

## 🚀 Kurulum (Mac, Linux & Windows)

1. MetaTrader 5 terminalinizi açın.
2. Klavyeden **F4** tuşuna basarak (veya menüden `Araçlar` -> `MetaQuotes Dil Düzenleyicisi`) **MetaEditor**'ü açın.
3. Sol taraftaki *Navigator* panelinde **`Experts`** klasörüne sağ tıklayıp **Yeni Dosya (New)** deyin veya:
   * [`HMA_Crossover_EA.mq5`](HMA_Crossover_EA.mq5) dosyasını kopyalayıp doğrudan `MQL5/Experts/` klasörünün içine yapıştırın.
4. Dosyayı MetaEditor içinde açın ve üst menüdeki **Derle (Compile)** butonuna basın (Klavye kısayolu: **F7**).
   * Alttaki pencerede `0 errors, 0 warnings` mesajını görmelisiniz.
5. MetaTrader 5 terminaline geri dönün:
   * Sol taraftaki *Kılavuz (Navigator)* penceresinde **Uzman Danışmanlar (Expert Advisors)** altında **`HMA_Crossover_EA`** görünecektir.
   * Bu robotu çalıştırmak istediğiniz grafiğin (Örn: EURUSD, XAUUSD, BTCUSD vb.) üzerine sürükleyip bırakın.
6. Açılan pencerede **"Algo Trading'e İzin Ver" (Allow Algo Trading)** kutucuğunu işaretleyin ve `Tamam`'a basın.
7. MT5 üst menüsündeki **"Algo Trading"** butonunun yeşil yandığından emin olun.

---

## ⚙️ Parametre Ayarları (Girdiler / Inputs)

| Parametre Grubu | Parametre Adı | Varsayılan | Açıklama |
| :--- | :--- | :--- | :--- |
| **1. İndikatör (HMA)** | `InpHMAPeriod` | `14` | HMA periyodu (Örn: 9, 14, 21) |
| | `InpHMAPrice` | `PRICE_CLOSE` | Hesaplanacak fiyat tipi (Kapanış, Açılış vb.) |
| **2. İndikatör** | `InpSecondMAType` | `TYPE_EMA` | 2. İndikatör türü (`HMA`, `EMA`, `SMA`, `LWMA`) |
| | `InpSecondMAPeriod` | `34` | 2. İndikatör periyodu (Örn: 34, 50, 200) |
| **İşlem & Risk** | `InpLotSize` | `0.01` | Açılacak işlem lot büyüklüğü |
| | `InpAllowTrading` | `true` | `true` = Otomatik al/sat, `false` = Sadece sinyal |
| | `InpCloseOpposite` | `true` | Ters sinyalde mevcut pozisyon kapatılsın mı? |
| **Stop Loss / TP** | `InpUseStopLoss` | `true` | **Stop Loss'u Aç / Kapat** |
| | `InpStopLossPoints`| `200` | Stop mesafesi (Point cinsinden, örn: 20 pip = 200 point) |
| | `InpUseTakeProfit` | `false` | Take Profit Aç / Kapat |
| | `InpTakeProfitPoints`| `400`| Kâr al mesafesi |
| **Bildirimler** | `InpScreenAlert` | `true` | MT5 pop-up uyarısı |
| | `InpPushNotification`| `true` | MT5 mobil uygulamasına anlık bildirim |

---

## 🧪 Strateji Test Cihazı (Backtest) ile Test Etme

Robotun geçmiş performansını görmek için:
1. MT5'te klavyeden **Ctrl + R** tuşlarına basarak **Strateji Test Cihazı'nı (Strategy Tester)** açın.
2. Uzman danışman olarak `HMA_Crossover_EA` seçin.
3. İstediğiniz sembolü (Örn: `EURUSD` veya `XAUUSD`) ve zaman dilimini (Örn: `M15`, `H1`) seçip **Başlat (Start)** düğmesine tıklayın.
