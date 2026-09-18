import os
import json
import time
import logging
import requests
from typing import Dict, Any, List, Optional

logger = logging.getLogger("DeepSeekAdvisor")
logger.setLevel(logging.INFO)

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_API_KEY = ""
DEFAULT_MODEL = "deepseek-chat"

MEMORY_PATHS = [
    "/config/ai_memory.json",
    "/tmp/ai_memory.json",
    os.path.join(os.path.dirname(__file__), "ai_memory.json")
]

class DeepSeekAdvisor:
    def __init__(self, mt5_client=None, api_key: Optional[str] = None):
        self.mt5 = mt5_client
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", DEFAULT_API_KEY)
        self.model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
        
        # Autopilot configuration
        self.autopilot = {
            "enabled": False,
            "mode": "ADVISORY",  # "ADVISORY" (manual confirm), "SEMI_AUTO", "FULL_AUTO"
            "min_confidence": 75,
            "max_lot": 0.05,
            "daily_loss_limit": 500.0,
            "allowed_symbols": ["EURUSD", "XAUUSD", "GBPUSD", "BTCUSD"],
            "last_auto_trade_time": 0
        }
        
        # Learned Memory state
        self.memory: Dict[str, Any] = {
            "last_analyzed": None,
            "analyzed_trades_count": 0,
            "learning_status": "Henüz Analiz Yapılmadı",
            "persona": {
                "title": "Analiz Bekleniyor",
                "summary": "Kullanıcının işlem alışkanlıklarını öğrenmek için 'İşlemlerimi Analiz Et & Öğren' butonuna basınız.",
                "style": "Bilinmiyor",
                "risk_profile": "Orta"
            },
            "strengths": [
                "İşlem geçmişi analiz edildiğinde kâr getiren paternler burada listelenecek."
            ],
            "weaknesses": [
                "Gereksiz zarara yol açan alışkanlıklar tespit edilip burada uyarılacak."
            ],
            "learned_rules": [
                "Kullanıcının en çok kazandığı setup ve saat dilimleri stratejiye dönüştürülecek."
            ],
            "revenue_tips": [
                "Kazançları artırmak için yapay zekanın önerdiği optimizasyonlar."
            ],
            "latest_recommendation": None
        }

        self.load_memory()

    def load_memory(self):
        for path in MEMORY_PATHS:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if "memory" in data:
                        self.memory.update(data["memory"])
                    if "autopilot" in data:
                        self.autopilot.update(data["autopilot"])
                    logger.info(f"Loaded AI memory from {path}")
                    return
                except Exception as e:
                    logger.warning(f"Failed to read AI memory from {path}: {e}")

    def save_memory(self):
        data = {
            "memory": self.memory,
            "autopilot": self.autopilot,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        for path in MEMORY_PATHS:
            try:
                parent = os.path.dirname(path)
                if parent and not os.path.exists(parent):
                    try:
                        os.makedirs(parent, exist_ok=True)
                    except Exception:
                        continue
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.info(f"Saved AI memory to {path}")
                return
            except Exception as e:
                logger.debug(f"Could not save AI memory to {path}: {e}")

    def test_connection(self) -> Dict[str, Any]:
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You are a financial AI assistant."},
                    {"role": "user", "content": "Test ping. Respond in JSON: {\"status\": \"OK\"}"}
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 50
            }
            res = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=10)
            if res.status_code == 200:
                return {"success": True, "data": res.json()}
            return {"success": False, "error": f"HTTP {res.status_code}: {res.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def analyze_user_trades(self, deals: List[Dict[str, Any]], stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not deals or len(deals) == 0:
            return {
                "success": False,
                "error": "Analiz edilecek kapalı işlem geçmişi bulunamadı. Lütfen önce birkaç işlem yapın veya MT5 geçmişinizi kontrol edin."
            }

        clean_deals = []
        total_p = 0.0
        winning_count = 0
        losing_count = 0

        for d in deals[:100]:
            profit = float(d.get("profit", 0.0))
            total_p += profit
            if profit > 0:
                winning_count += 1
            elif profit < 0:
                losing_count += 1

            clean_deals.append({
                "ticket": d.get("ticket"),
                "symbol": d.get("symbol"),
                "type": d.get("type"),
                "volume": d.get("volume"),
                "price": d.get("price"),
                "profit": profit,
                "time": d.get("time_str") or d.get("time")
            })

        system_prompt = """Sen profesyonel bir Kantitatif Yatırımcı ve Yapay Zeka Ticaret Mentorüsün.
Görevin: Bir kullanıcının MetaTrader 5 üzerinden yaptığı manuel işlemlerin geçmişini derinlemesine incelemek,
kullanıcının ticaret mantığını, psikolojisini, kullandığı yapıyı ve alışkanlıklarını ÖĞRENMEK.

Analiz hedeflerin:
1. Kullanıcının işlem tarzını (Scalper, Day Trader, Swing Trader, Trend Takipçisi, Agresif/Muhafazakar) tanımla.
2. Hangi paritelerde (örneğin XAUUSD veya EURUSD) ve hangi işlem türlerinde (BUY/SELL) en çok kâr ettiğini belirle.
3. Kullanıcının kâr getiren "Başarılı Yapı ve Paternlerini" (Strengths) çıkar.
4. Kullanıcının zarar etmesine yol açan "Hatalarını ve Zayıf Noktalarını" (Weaknesses - örneğin zararı kesmeme, aşırı lot, ters işlem açma) açık ve net şekilde belirle.
5. Kullanıcının mantığını temel alarak daha çok GELİR KAZANMASI için uygulanabilir 3-5 adet altın kural (learned_rules) formüle et.
6. Gelir artırma tavsiyeleri (revenue_tips) sun.

Yanıtını SADECE geçerli bir JSON nesnesi olarak ver. Format:
{
  "persona": {
    "title": "Örn: Agresif Altın Scalperı",
    "summary": "Kullanıcının ticaret tarzının 2-3 cümlelik özeti",
    "style": "Scalping / Day Trading / vb.",
    "risk_profile": "Yüksek / Orta / Düşük"
  },
  "learning_status": "Öğrenildi (%92 Model Uyumu)",
  "strengths": [
    "Güçlü yön 1...",
    "Güçlü yön 2..."
  ],
  "weaknesses": [
    "Tespit edilen hata veya risk 1...",
    "Tespit edilen hata veya risk 2..."
  ],
  "learned_rules": [
    "Öğrenilen Kural 1: ...",
    "Öğrenilen Kural 2: ..."
  ],
  "revenue_tips": [
    "Gelir artırma tavsiyesi 1...",
    "Gelir artırma tavsiyesi 2..."
  ]
}
Dil: Türkçe. Analiz samimi, profesyonel ve verilere dayalı olmalı."""

        user_content = json.dumps({
            "total_deals_analyzed": len(clean_deals),
            "total_net_profit": round(total_p, 2),
            "winning_trades": winning_count,
            "losing_trades": losing_count,
            "win_rate": round((winning_count / len(clean_deals) * 100), 1) if clean_deals else 0,
            "summary_stats": stats or {},
            "sample_trades": clean_deals[:50]
        }, ensure_ascii=False)

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"İşte kullanıcının işlem geçmişi verileri:\n{user_content}"}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
                "max_tokens": 1500
            }

            logger.info("Sending trade history to DeepSeek for behavioral learning...")
            res = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30)
            if res.status_code != 200:
                logger.error(f"DeepSeek API error: {res.status_code} - {res.text}")
                return {"success": False, "error": f"DeepSeek API hatası: {res.status_code}"}

            data = res.json()
            content_str = data["choices"][0]["message"]["content"]
            parsed = json.loads(content_str)

            self.memory["last_analyzed"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.memory["analyzed_trades_count"] = len(clean_deals)
            self.memory["learning_status"] = parsed.get("learning_status", "Öğrenildi")
            self.memory["persona"] = parsed.get("persona", self.memory["persona"])
            self.memory["strengths"] = parsed.get("strengths", [])
            self.memory["weaknesses"] = parsed.get("weaknesses", [])
            self.memory["learned_rules"] = parsed.get("learned_rules", [])
            self.memory["revenue_tips"] = parsed.get("revenue_tips", [])

            self.save_memory()
            return {"success": True, "memory": self.memory}

        except Exception as e:
            logger.error(f"Error during DeepSeek trade analysis: {e}")
            return {"success": False, "error": str(e)}

    def get_market_advice(
        self,
        symbol: str,
        timeframe_name: str = "M15",
        tick: Optional[Dict[str, Any]] = None,
        rates: Optional[List[Dict[str, Any]]] = None,
        open_positions: Optional[List[Dict[str, Any]]] = None,
        hma_val: Optional[float] = None,
        ma2_val: Optional[float] = None
    ) -> Dict[str, Any]:
        symbol = symbol.upper()
        current_price = tick.get("bid") if tick else 0.0
        ask_price = tick.get("ask") if tick else 0.0

        recent_candles = []
        if rates and len(rates) > 0:
            for r in rates[-15:]:
                recent_candles.append({
                    "time": r.get("time"),
                    "open": round(r.get("open", 0.0), 5),
                    "high": round(r.get("high", 0.0), 5),
                    "low": round(r.get("low", 0.0), 5),
                    "close": round(r.get("close", 0.0), 5),
                    "vol": r.get("tick_volume", 0)
                })

        system_prompt = """Sen dünyanın en iyi Finansal Yapay Zeka İşlem Stratejistisin.
Kullanıcının işlem tarzını, kurallarını ve zayıf/güçlü yönlerini öğrendin.
Şu an canlı piyasa verilerini analiz edip kullanıcıya net, kârlı ve disiplinli bir alım-satım tavsiyesi vereceksin.

Kurallar:
1. "action" değeri SADECE "BUY", "SELL" veya "HOLD" (Bekle) olabilir.
2. "confidence" 0 ile 100 arasında bir yüzde olmalıdır. Net bir fırsat yoksa "HOLD" ve düşük güven ver.
3. Kullanıcının öğrendiğin tarzına ve kurallarına uygun önerilerde bulun.
4. SL (Stop Loss) ve TP (Take Profit) seviyelerini kesinlikle mantıklı risk/ödül oranına göre belirle.
5. Açıklaman (reasoning) net, ikna edici ve Türkçe olsun.

Yanıtını SADECE şu JSON formatında ver:
{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 85,
  "symbol": "EURUSD",
  "entry_price": 1.14780,
  "sl_points": 200,
  "tp_points": 400,
  "sl_price": 1.14580,
  "tp_price": 1.15180,
  "suggested_lot": 0.05,
  "reasoning": "HMA eğimi yukarı döndü ve kullanıcının başarılı olduğu yapıyla uyumlu...",
  "risk_reward_ratio": "1:2",
  "action_title": "Güçlü Alım Fırsatı / Long Setup"
}"""

        user_content = json.dumps({
            "target_symbol": symbol,
            "timeframe": timeframe_name,
            "current_bid": current_price,
            "current_ask": ask_price,
            "spread_points": round((ask_price - current_price) * 100000, 1) if ask_price and current_price else 0,
            "current_hma": hma_val,
            "current_second_ma": ma2_val,
            "user_learned_persona": self.memory.get("persona"),
            "user_learned_rules": self.memory.get("learned_rules"),
            "active_open_positions_count": len(open_positions) if open_positions else 0,
            "recent_candles": recent_candles
        }, ensure_ascii=False)

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Piyasa durumu ve kullanıcının öğrenilen profili:\n{user_content}"}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
                "max_tokens": 800
            }

            logger.info(f"Requesting DeepSeek market advice for {symbol}...")
            res = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=25)
            if res.status_code != 200:
                logger.error(f"DeepSeek advice error: {res.status_code} - {res.text}")
                return {"success": False, "error": f"DeepSeek API hatası: {res.status_code}"}

            data = res.json()
            content_str = data["choices"][0]["message"]["content"]
            rec = json.loads(content_str)
            rec["generated_at"] = time.strftime("%H:%M:%S")
            rec["symbol"] = symbol

            self.memory["latest_recommendation"] = rec
            self.save_memory()

            return {"success": True, "recommendation": rec}

        except Exception as e:
            logger.error(f"Error getting market advice: {e}")
            return {"success": False, "error": str(e)}

    def execute_recommendation(self, rec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.mt5:
            return {"success": False, "error": "MT5 istemcisi bağlı değil."}

        target_rec = rec or self.memory.get("latest_recommendation")
        if not target_rec:
            return {"success": False, "error": "Uygulanacak geçerli bir tavsiye bulunamadı."}

        action = target_rec.get("action", "").upper()
        if action not in ("BUY", "SELL"):
            return {"success": False, "error": f"Bu işlem türü açılamaz ({action}). Sadece BUY veya SELL emirleri açılabilir."}

        symbol = target_rec.get("symbol", "EURUSD").upper()
        volume = float(target_rec.get("suggested_lot") or self.autopilot.get("max_lot", 0.01))
        volume = min(volume, float(self.autopilot.get("max_lot", 0.10)))
        sl = int(target_rec.get("sl_points", 0))
        tp = int(target_rec.get("tp_points", 0))

        logger.info(f"Executing DeepSeek AI Trade: {symbol} {action} {volume} lot (SL:{sl}, TP:{tp})")
        conf_score = target_rec.get("confidence", 80)
        res = self.mt5.open_order(
            symbol=symbol,
            order_type=action,
            volume=volume,
            sl_points=sl,
            tp_points=tp,
            comment=f"DeepSeek AI ({conf_score}%)"
        )

        if res.get("success"):
            self.autopilot["last_auto_trade_time"] = time.time()
            self.save_memory()
            ticket_num = res.get("ticket")
            deal_price = res.get("price")
            return {
                "success": True,
                "ticket": ticket_num,
                "price": deal_price,
                "message": f"✅ DeepSeek AI Emri Açıldı: {symbol} {action} #{ticket_num} @ {deal_price}"
            }
        else:
            return {
                "success": False,
                "error": res.get("error", "MT5 emir gönderimi başarısız oldu.")
            }

    def update_autopilot(self, config: Dict[str, Any]) -> Dict[str, Any]:
        if "enabled" in config:
            self.autopilot["enabled"] = bool(config["enabled"])
        if "mode" in config:
            self.autopilot["mode"] = str(config["mode"]).upper()
        if "min_confidence" in config:
            self.autopilot["min_confidence"] = max(50, min(99, int(config["min_confidence"])))
        if "max_lot" in config:
            self.autopilot["max_lot"] = max(0.01, min(5.0, float(config["max_lot"])))
        if "daily_loss_limit" in config:
            self.autopilot["daily_loss_limit"] = max(10.0, float(config["daily_loss_limit"]))
        if "allowed_symbols" in config and isinstance(config["allowed_symbols"], list):
            self.autopilot["allowed_symbols"] = [str(s).upper() for s in config["allowed_symbols"]]

        self.save_memory()
        return {"success": True, "autopilot": self.autopilot}

    def get_status(self) -> Dict[str, Any]:
        return {
            "api_configured": bool(self.api_key),
            "model": self.model,
            "memory": self.memory,
            "autopilot": self.autopilot,
            "has_analyzed": self.memory.get("last_analyzed") is not None
        }
