//+------------------------------------------------------------------+
//|                                           HMA_Crossover_EA.mq5   |
//|                        MetaTrader 5 Expert Advisor               |
//|            HMA & 2. Indikator Kesisim Algoritmasi                |
//+------------------------------------------------------------------+
#property copyright "Algorithmic Trading EA"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property description "HMA ve 2. Hareketli Ortalama Kesisimi ile Al/Sat ve Sinyal Ureten Robot"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\SymbolInfo.mqh>

//--- 2. Indikator Tipi Secimi
enum ENUM_SECOND_MA_TYPE
{
   TYPE_SECOND_HMA = 0, // Hull Moving Average (HMA)
   TYPE_EMA        = 1, // Exponential Moving Average (EMA)
   TYPE_SMA        = 2, // Simple Moving Average (SMA)
   TYPE_LWMA       = 3  // Linear Weighted Moving Average (LWMA)
};

//+------------------------------------------------------------------+
//|                       GIRDI PARAMETRELERI                        |
//+------------------------------------------------------------------+

//=== 1. INDIKATOR AYARLARI (HMA) ===
input group "=== 1. Indikator: Hull Moving Average (HMA) ==="
input int                InpHMAPeriod        = 14;              // HMA Periyodu
input ENUM_APPLIED_PRICE InpHMAPrice         = PRICE_CLOSE;     // HMA Uygulanan Fiyat

//=== 2. INDIKATOR AYARLARI ===
input group "=== 2. Indikator Ayarlari ==="
input ENUM_SECOND_MA_TYPE InpSecondMAType    = TYPE_EMA;        // 2. Indikator Turu (HMA / EMA / SMA / LWMA)
input int                InpSecondMAPeriod   = 34;              // 2. Indikator Periyodu
input ENUM_APPLIED_PRICE InpSecondMAPrice    = PRICE_CLOSE;     // 2. Indikator Uygulanan Fiyat

//=== ISLEM & RISK YONETIMI ===
input group "=== Islem ve Risk Yonetimi ==="
input double             InpLotSize          = 0.01;            // Islem Lot Miktari
input bool               InpAllowTrading     = true;            // Otomatik Al/Sat Yapilsin mi? (false = Sadece Sinyal)
input bool               InpCloseOpposite    = true;            // Ters Sinyalde Mevcut Pozisyonu Kapat
input ulong              InpMagicNumber      = 882211;          // EA Magic Number (Benzersiz Numara)
input ulong              InpDeviation        = 10;              // Maksimum Slippage (Sapma)

//=== STOP LOSS & TAKE PROFIT ===
input group "=== Stop Loss ve Take Profit Ayarlari ==="
input bool               InpUseStopLoss      = true;            // Stop Loss Acik mi? (true/false)
input int                InpStopLossPoints   = 200;             // Stop Loss Mesafesi (Point / Puan)
input bool               InpUseTakeProfit    = false;           // Take Profit Acik mi? (true/false)
input int                InpTakeProfitPoints = 400;             // Take Profit Mesafesi (Point / Puan)

//=== BILDIRIM & SINYAL AYARLARI ===
input group "=== Bildirim ve Sinyal Ayarlari ==="
input bool               InpScreenAlert      = true;            // Ekrana Popup Uyarisi Ver
input bool               InpPushNotification = true;            // MT5 Mobil Uygulamasina Bildirim Gonder
input bool               InpPlaySound        = false;           // Sesli Uyari Ver
input string             InpSoundFile        = "alert.wav";     // Calinacak Ses Dosyasi

//+------------------------------------------------------------------+
//| Global Nesneler ve Degiskenler                                  |
//+------------------------------------------------------------------+
CTrade         m_trade;
CPositionInfo  m_position;
CSymbolInfo    m_symbol;

datetime       m_lastBarTime = 0;
int            m_ma2_handle = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   // Sembol bilgilerini yukle
   if(!m_symbol.Name(_Symbol))
   {
      Print("Hata: Sembol bilgileri yuklenemedi: ", _Symbol);
      return INIT_FAILED;
   }
   m_symbol.Refresh();

   // Trade sinifini ayarla
   m_trade.SetExpertMagicNumber(InpMagicNumber);
   m_trade.SetDeviationInPoints(InpDeviation);
   m_trade.SetTypeFillingBySymbol(_Symbol);

   // 2. Indikator HMA degilse (EMA, SMA, LWMA) dahili handle olustur
   if(InpSecondMAType != TYPE_SECOND_HMA)
   {
      ENUM_MA_METHOD method = MODE_EMA;
      if(InpSecondMAType == TYPE_SMA)  method = MODE_SMA;
      if(InpSecondMAType == TYPE_LWMA) method = MODE_LWMA;

      m_ma2_handle = iMA(_Symbol, _Period, InpSecondMAPeriod, 0, method, InpSecondMAPrice);
      if(m_ma2_handle == INVALID_HANDLE)
      {
         Print("Hata: 2. Hareketli Ortalama olusturulamadi!");
         return INIT_FAILED;
      }
   }

   Print("HMA Crossover EA basariyla baslatildi. Sembol: ", _Symbol, " | Stop Loss: ", (InpUseStopLoss ? "AKTIF" : "KAPALI"));
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(m_ma2_handle != INVALID_HANDLE)
      IndicatorRelease(m_ma2_handle);

   Comment("");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Yeni mum kontrolu (Sinyalleri mum kapanisinda hesaplayip sahte sinyalleri onler)
   datetime currentBarTime = iTime(_Symbol, _Period, 0);
   if(currentBarTime == m_lastBarTime)
      return; // Ayni mum icerisinde tekrar hesaplama yapma

   m_symbol.RefreshRates();

   // Indikator degerlerini hesapla
   // Son kapanan mum (index 1) ve bir onceki kapanan mum (index 2)
   double hma_val1 = CalculateHMA(_Symbol, _Period, InpHMAPeriod, InpHMAPrice, 1);
   double hma_val2 = CalculateHMA(_Symbol, _Period, InpHMAPeriod, InpHMAPrice, 2);

   double ma2_val1 = 0.0;
   double ma2_val2 = 0.0;

   if(InpSecondMAType == TYPE_SECOND_HMA)
   {
      ma2_val1 = CalculateHMA(_Symbol, _Period, InpSecondMAPeriod, InpSecondMAPrice, 1);
      ma2_val2 = CalculateHMA(_Symbol, _Period, InpSecondMAPeriod, InpSecondMAPrice, 2);
   }
   else
   {
      double buffer[2];
      if(CopyBuffer(m_ma2_handle, 0, 1, 2, buffer) < 2)
      {
         Print("2. Indikator verisi okunamadi!");
         return;
      }
      ma2_val2 = buffer[0]; // index 2
      ma2_val1 = buffer[1]; // index 1
   }

   // Eger degerler hesaplanamadiysa cik
   if(hma_val1 == 0 || hma_val2 == 0 || ma2_val1 == 0 || ma2_val2 == 0)
      return;

   // Kesisim Kontrolu (Crossover Logic)
   bool buySignal  = (hma_val2 <= ma2_val2 && hma_val1 > ma2_val1); // Yukari kesme (Bullish Crossover)
   bool sellSignal = (hma_val2 >= ma2_val2 && hma_val1 < ma2_val1); // Asagi kesme (Bearish Crossover)

   // Grafikte durum bilgisi goster
   UpdateChartComment(hma_val1, ma2_val1, buySignal, sellSignal);

   m_lastBarTime = currentBarTime;

   // Sinyal Yonetimi ve Islem Acma
   if(buySignal)
   {
      m_lastBarTime = currentBarTime;
      ProcessSignal(ORDER_TYPE_BUY, hma_val1, ma2_val1);
   }
   else if(sellSignal)
   {
      m_lastBarTime = currentBarTime;
      ProcessSignal(ORDER_TYPE_SELL, hma_val1, ma2_val1);
   }
}

//+------------------------------------------------------------------+
//| Sinyal ve Emir Yonetimi                                         |
//+------------------------------------------------------------------+
void ProcessSignal(ENUM_ORDER_TYPE orderType, double val1, double val2)
{
   string signalTypeStr = (orderType == ORDER_TYPE_BUY) ? "ALIM (BUY)" : "SATIS (SELL)";
   string message = StringFormat("[%s] Sinyal: %s | Fiyat: %s | HMA: %.5f | 2.Indikator: %.5f",
                                 _Symbol, signalTypeStr, DoubleToString(m_symbol.Ask(), _Digits), val1, val2);

   // 1. Ekran Bildirimi
   if(InpScreenAlert)
      Alert(message);

   // 2. MT5 Mobil Push Bildirimi
   if(InpPushNotification)
      SendNotification(message);

   // 3. Ses Uyarisi
   if(InpPlaySound)
      PlaySound(InpSoundFile);

   // 5. Otomatik Alim/Satim Kapaliysa sadece sinyali iletip cik
   if(!InpAllowTrading)
      return;

   // Ters pozisyon kontrolu
   if(InpCloseOpposite)
   {
      if(!ClosePositionsByDirection(orderType == ORDER_TYPE_BUY ? ORDER_TYPE_SELL : ORDER_TYPE_BUY))
         return;
   }

   // Halihazirda ayni yonde acik pozisyon varsa tekrar acma (Opsiyonel guvenlik)
   if(HasOpenPosition(orderType))
      return;

   if(!m_symbol.RefreshRates()) return;

   // Stop Loss ve Take Profit Seviyelerini Hesapla
   double sl = 0.0;
   double tp = 0.0;
   double point = m_symbol.Point();

   if(orderType == ORDER_TYPE_BUY)
   {
      double price = m_symbol.Ask();
      if(InpUseStopLoss && InpStopLossPoints > 0)
         sl = NormalizeDouble(price - (InpStopLossPoints * point), _Digits);

      if(InpUseTakeProfit && InpTakeProfitPoints > 0)
         tp = NormalizeDouble(price + (InpTakeProfitPoints * point), _Digits);

      if(m_trade.Buy(InpLotSize, _Symbol, price, sl, tp, "HMA Buy Signal") && m_trade.ResultRetcode() == TRADE_RETCODE_DONE)
      {
         Print("Basarili BUY Emri: ", _Symbol, " Lot: ", InpLotSize, " SL: ", sl, " TP: ", tp);
      }
      else
      {
         Print("BUY Emri Acilamadi! Hata: ", m_trade.ResultRetcodeDescription());
      }
   }
   else if(orderType == ORDER_TYPE_SELL)
   {
      double price = m_symbol.Bid();
      if(InpUseStopLoss && InpStopLossPoints > 0)
         sl = NormalizeDouble(price + (InpStopLossPoints * point), _Digits);

      if(InpUseTakeProfit && InpTakeProfitPoints > 0)
         tp = NormalizeDouble(price - (InpTakeProfitPoints * point), _Digits);

      if(m_trade.Sell(InpLotSize, _Symbol, price, sl, tp, "HMA Sell Signal") && m_trade.ResultRetcode() == TRADE_RETCODE_DONE)
      {
         Print("Basarili SELL Emri: ", _Symbol, " Lot: ", InpLotSize, " SL: ", sl, " TP: ", tp);
      }
      else
      {
         Print("SELL Emri Acilamadi! Hata: ", m_trade.ResultRetcodeDescription());
      }
   }
}

//+------------------------------------------------------------------+
//| Hull Moving Average (HMA) Hesaplama Fonksiyonu                  |
//| Formül: WMA(2*WMA(n/2) - WMA(n), sqrt(n))                       |
//+------------------------------------------------------------------+
double CalculateHMA(string symbol, ENUM_TIMEFRAMES timeframe, int period, ENUM_APPLIED_PRICE price, int shift)
{
   if(period < 2) return 0.0;

   int halfPeriod = (int)MathFloor(period / 2.0);
   int sqrtPeriod = (int)MathFloor(MathSqrt(period));

   // WMA array'i hesaplamak icin gereken toplam bar sayisi
   int requiredBars = period + sqrtPeriod + shift + 10;
   
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   if(CopyRates(symbol, timeframe, 0, requiredBars, rates) < requiredBars)
      return 0.0;

   double diffArray[];
   ArrayResize(diffArray, sqrtPeriod + shift + 1);
   ArraySetAsSeries(diffArray, true);

   // Fark serisini olustur: 2 * WMA(n/2) - WMA(n)
   for(int i = 0; i <= sqrtPeriod + shift; i++)
   {
      double wmaHalf = CalculateWMA(rates, halfPeriod, price, i);
      double wmaFull = CalculateWMA(rates, period, price, i);
      diffArray[i] = 2.0 * wmaHalf - wmaFull;
   }

   // Son asamada diffArray uzerinden WMA(sqrt(n)) hesapla
   double hma = 0.0;
   double weightSum = 0.0;
   for(int k = 0; k < sqrtPeriod; k++)
   {
      double weight = sqrtPeriod - k;
      hma += diffArray[shift + k] * weight;
      weightSum += weight;
   }

   if(weightSum == 0.0) return 0.0;
   return(hma / weightSum);
}

//+------------------------------------------------------------------+
//| Agirlikli Ortalama (WMA) Yardimci Fonksiyonu                    |
//+------------------------------------------------------------------+
double CalculateWMA(const MqlRates &rates[], int period, ENUM_APPLIED_PRICE price, int shift)
{
   if(period <= 0) return 0.0;

   double sum = 0.0;
   double weightSum = 0.0;

   for(int i = 0; i < period; i++)
   {
      int idx = shift + i;
      double val = GetAppliedPrice(rates[idx], price);
      double weight = period - i;
      sum += val * weight;
      weightSum += weight;
   }

   if(weightSum == 0.0) return 0.0;
   return(sum / weightSum);
}

//+------------------------------------------------------------------+
//| Fiyat Turunu Donduren Yardimci Fonksiyon                        |
//+------------------------------------------------------------------+
double GetAppliedPrice(const MqlRates &rate, ENUM_APPLIED_PRICE price)
{
   switch(price)
   {
      case PRICE_OPEN:     return rate.open;
      case PRICE_HIGH:     return rate.high;
      case PRICE_LOW:      return rate.low;
      case PRICE_MEDIAN:   return (rate.high + rate.low) / 2.0;
      case PRICE_TYPICAL:  return (rate.high + rate.low + rate.close) / 3.0;
      case PRICE_WEIGHTED: return (rate.high + rate.low + 2.0 * rate.close) / 4.0;
      case PRICE_CLOSE:
      default:             return rate.close;
   }
}

//+------------------------------------------------------------------+
//| Belirli Yondeki Pozisyonlari Kapatma                             |
//+------------------------------------------------------------------+
bool ClosePositionsByDirection(ENUM_ORDER_TYPE oppositeType)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Symbol() == _Symbol && m_position.Magic() == InpMagicNumber)
         {
            if((ENUM_ORDER_TYPE)m_position.PositionType() == oppositeType)
            {
               if(!m_trade.PositionClose(m_position.Ticket()) || m_trade.ResultRetcode() != TRADE_RETCODE_DONE)
               {
                  Print("Ters pozisyon kapanmadi; yeni emir engellendi: ", m_trade.ResultRetcodeDescription());
                  return false;
               }
            }
         }
      }
   }
   return true;
}

//+------------------------------------------------------------------+
//| Belirli Yonde Acik Pozisyon Kontrolu                             |
//+------------------------------------------------------------------+
bool HasOpenPosition(ENUM_ORDER_TYPE orderType)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Symbol() == _Symbol && m_position.Magic() == InpMagicNumber)
         {
            if((ENUM_ORDER_TYPE)m_position.PositionType() == orderType)
               return true;
         }
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Grafik bilgi paneli                                             |
//+------------------------------------------------------------------+
void UpdateChartComment(double hma, double ma2, bool buySig, bool sellSig)
{
   string ma2Name = "EMA";
   if(InpSecondMAType == TYPE_SECOND_HMA) ma2Name = "HMA 2";
   else if(InpSecondMAType == TYPE_SMA)   ma2Name = "SMA";
   else if(InpSecondMAType == TYPE_LWMA)  ma2Name = "LWMA";

   string lastSig = "BEKLEMEDE (YOK)";
   if(buySig) lastSig = ">>> GUCLU AL (BUY) <<<";
   if(sellSig) lastSig = ">>> GUCLU SAT (SELL) <<<";

   string info = "========================================\n" +
                 "     HMA CROSSOVER EXPERT ADVISOR       \n" +
                 "========================================\n" +
                 StringFormat("Sembol / Periyot : %s [%s]\n", _Symbol, EnumToString(_Period)) +
                 StringFormat("1. Indikator (HMA %d) : %.5f\n", InpHMAPeriod, hma) +
                 StringFormat("2. Indikator (%s %d) : %.5f\n", ma2Name, InpSecondMAPeriod, ma2) +
                 "----------------------------------------\n" +
                 StringFormat("Son Sinyal Durumu : %s\n", lastSig) +
                 StringFormat("Al/Sat Durumu     : %s\n", (InpAllowTrading ? "TAM OTOMATIK" : "SADECE SINYAL")) +
                 StringFormat("Stop Loss         : %s (%d point)\n", (InpUseStopLoss ? "AKTIF" : "KAPALI"), InpStopLossPoints) +
                 StringFormat("Take Profit       : %s (%d point)\n", (InpUseTakeProfit ? "AKTIF" : "KAPALI"), InpTakeProfitPoints) +
                 StringFormat("Islem Lotu        : %.2f\n", InpLotSize) +
                 "========================================";

   Comment(info);
}
//+------------------------------------------------------------------+
