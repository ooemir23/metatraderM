import os
import json
import time
import logging
import requests
import hashlib
import threading
import tempfile
import uuid
import math
from app.mt5_bridge import AI_MAGIC
from functools import wraps
from datetime import datetime, timezone
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

def serialized_analysis(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if not self._analysis_lock.acquire(blocking=False):
            return {"success": False, "error": "Bir AI analizi sürüyor; tamamlanmasını bekleyin."}
        try:
            return method(self, *args, **kwargs)
        finally:
            self._analysis_lock.release()
    return wrapped


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


class DeepSeekAdvisor:
    def __init__(self, mt5_client=None, api_key: Optional[str] = None):
        self._analysis_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self.daily_call_limit = max(1, int(os.getenv("AI_DAILY_CALL_LIMIT", "100")))
        self.daily_token_limit = max(1000, int(os.getenv("AI_DAILY_TOKEN_LIMIT", "100000")))
        self.cost_state = {"usage_days": {}, "last_requests": {}, "advice_cache": {},
                           "learn_digest": None, "autopilot_bucket": None}
        self.mt5 = mt5_client
        self.autopilot_generation = 0
        self._auto_stop = threading.Event()
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", DEFAULT_API_KEY)
        self.model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
        
        # Autopilot configuration
        self.autopilot = {
            "enabled": False,
            "mode": "ADVISORY",  # "ADVISORY" (manual confirm), "SEMI_AUTO", "FULL_AUTO"
            "min_confidence": 75,
            "max_lot": 0.05,
            "daily_loss_limit": float(os.getenv("DAILY_LOSS_LIMIT", "500")),
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
        if not self.autopilot.get("enabled"):
            self._auto_stop.set()
        if self.mt5:
            self.mt5.daily_loss_limit = float(self.autopilot["daily_loss_limit"])

    def load_memory(self):
        for path in MEMORY_PATHS:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data.get("cost_state"), dict):
                        self.cost_state.update(data["cost_state"])
                    if "memory" in data:
                        self.memory.update(data["memory"])
                    if "autopilot" in data:
                        self.autopilot.update(data["autopilot"])
                    logger.info(f"Loaded AI memory from {path}")
                    return
                except Exception as e:
                    logger.warning(f"Failed to read AI memory from {path}: {e}")

    def save_memory(self):
        with self._state_lock:
            data = {"memory": self.memory, "autopilot": self.autopilot,
                    "cost_state": self.cost_state,
                    "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            for path in MEMORY_PATHS:
                temp_path = None
                try:
                    parent = os.path.dirname(path) or "."
                    os.makedirs(parent, exist_ok=True)
                    with tempfile.NamedTemporaryFile(mode="w", dir=parent, delete=False,
                                                     encoding="utf-8") as f:
                        temp_path = f.name
                        json.dump(data, f, ensure_ascii=False)
                    os.replace(temp_path, path)
                    return True
                except Exception as exc:
                    logger.debug("Could not save AI state: %s", exc)
                finally:
                    if temp_path and os.path.exists(temp_path):
                        os.unlink(temp_path)
            return False

    def _usage(self, day=None):
        day = day or datetime.now(timezone.utc).date().isoformat()
        return self.cost_state["usage_days"].setdefault(day, {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "cache_hit_tokens": 0, "unknown_usage_calls": 0,
        })

    def _request_json(self, payload, timeout, operation):
        if not self.api_key:
            raise ValueError("AI API anahtarı tanımlı değil.")
        day = datetime.now(timezone.utc).date().isoformat()
        with self._state_lock:
            usage = self._usage(day)
            if usage["calls"] >= self.daily_call_limit or usage["total_tokens"] >= self.daily_token_limit:
                raise ValueError("Günlük AI kullanım sınırına ulaşıldı (UTC).")
            last = self.cost_state["last_requests"].get(operation, 0)
            if time.time() - last < 60:
                raise ValueError("Aynı analiz için en az 60 saniye bekleyin.")
            self.cost_state["last_requests"][operation] = time.time()
            usage["calls"] += 1  # Reserve before sending, including failed/ambiguous requests.
            usage["unknown_usage_calls"] += 1
            self.cost_state["usage_days"] = dict(sorted(self.cost_state["usage_days"].items())[-7:])
            if not self.save_memory():
                raise RuntimeError("AI kullanım sayacı kaydedilemedi; istek gönderilmedi.")
        response = requests.post(DEEPSEEK_API_URL,
                                 headers={"Authorization": f"Bearer {self.api_key}",
                                          "Content-Type": "application/json"},
                                 json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        reported = data.get("usage")
        if isinstance(reported, dict):
            with self._state_lock:
                usage = self._usage(day)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    usage[key] += max(0, int(reported.get(key, 0)))
                usage["cache_hit_tokens"] += max(0, int(reported.get("prompt_cache_hit_tokens", 0)))
                usage["unknown_usage_calls"] = max(0, usage["unknown_usage_calls"] - 1)
                self.save_memory()
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("AI yanıtı uzunluk sınırında kesildi; otomatik tekrar yapılmadı.")
        parsed = json.loads(choice["message"]["content"])
        if not isinstance(parsed, dict):
            raise ValueError("AI geçerli bir JSON nesnesi döndürmedi.")
        return parsed

    def claim_autopilot_cycle(self):
        # Claim each M15 wall-clock bucket before any analysis, even on HOLD/error.
        with self._state_lock:
            bucket = int(time.time() // 900)
            if self.cost_state.get("autopilot_bucket") == bucket:
                return False
            self.cost_state["autopilot_bucket"] = bucket
            return self.save_memory()

    @serialized_analysis
    def test_connection(self) -> Dict[str, Any]:
        try:
            data = self._request_json({"model": self.model,
                "messages": [{"role": "user", "content": 'JSON: {"status":"OK"}'}],
                "response_format": {"type": "json_object"}, "max_tokens": 50}, 10, "ping")
            return {"success": True, "data": data}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @serialized_analysis
    def analyze_user_trades(self, deals: List[Dict[str, Any]], stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not deals or len(deals) == 0:
            return {
                "success": False,
                "error": "Analiz edilecek kapalı işlem geçmişi bulunamadı. Lütfen önce birkaç işlem yapın veya MT5 geçmişinizi kontrol edin."
            }

        clean_deals = []
        groups = {}
        for d in deals[:100]:
            profit = sum(float(d.get(k, 0) or 0) for k in ("profit", "commission", "swap", "fee"))
            symbol, side = str(d.get("symbol", "")), str(d.get("type", ""))
            group = groups.setdefault(symbol + ":" + side, {"count": 0, "wins": 0, "net": 0})
            group["count"] += 1
            group["wins"] += int(profit > 0)
            group["net"] = round(group["net"] + profit, 2)
            clean_deals.append({"ticket": d.get("ticket"), "symbol": symbol, "side": side,
                                "lot": d.get("volume"), "net": round(profit, 2),
                                "time": d.get("time")})
        learning_digest = digest({"model": self.model, "version": 2, "trades": clean_deals})
        if self.cost_state.get("learn_digest") == learning_digest:
            return {"success": True, "cached": True, "memory": self.memory}
        system_prompt = """İşlem geçmişini özetleyen bir analiz yardımcısısın. Sadece verinin desteklediği
bulguları yaz; psikoloji, strateji başarısı veya kâr garantisi uydurma. Türkçe, kısa JSON üret:
{"persona":{"title":"","summary":"","style":"","risk_profile":""},
"learning_status":"Analiz edildi","strengths":[],"weaknesses":[],"learned_rules":[],"revenue_tips":[]}.
Her listede en fazla 3 kısa madde, summary en fazla 2 cümle olsun."""
        user_content = json.dumps({"count": len(clean_deals), "by_symbol_side": groups,
                                  "recent_examples": clean_deals[:8]},
                                 ensure_ascii=False, separators=(",", ":"))

        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"İşte kullanıcının işlem geçmişi verileri:\n{user_content}"}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
                "max_tokens": 800
            }

            parsed = self._request_json(payload, 30, "learn")
            if not isinstance(parsed.get("persona"), dict) or not isinstance(parsed.get("learned_rules"), list):
                raise ValueError("AI öğrenme yanıtı geçersiz.")
            self.cost_state["learn_digest"] = learning_digest

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

    @serialized_analysis
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
        if not rates or len(rates) < 2 or not tick or not tick.get("bid"):
            return {"success": False, "error": "Güncel fiyat ve kapanmış mum verisi gerekli."}
        closed = rates[:-1][-10:]
        candle_time = int(closed[-1]["time"])
        positions = [{k: p.get(k) for k in ("ticket", "symbol", "type", "volume", "sl", "tp")}
                     for p in (open_positions or [])]
        profile = {"persona": self.memory.get("persona"), "rules": self.memory.get("learned_rules", [])[:3]}
        cache_key = digest({"symbol": symbol, "timeframe": timeframe_name, "candle": candle_time,
                            "positions": positions, "profile": profile, "model": self.model})
        entry = self.cost_state["advice_cache"].get(cache_key)
        if entry:
            return {"success": True, "cached": True, "stale": entry["expires_at"] <= time.time(),
                    "recommendation": entry["recommendation"]}
        system_prompt = """Verilen kapanmış mumları ve profil özetini değerlendir. Kâr garantisi verme;
veri yetersizse HOLD seç. Türkçe en fazla 2 kısa cümle gerekçe ver. Yalnızca şu JSON alanlarını üret:
action (BUY/SELL/HOLD), confidence (0-100), entry_price, sl_points (>=0), tp_points (>=0),
sl_price, tp_price, suggested_lot, reasoning, risk_reward_ratio, action_title.
Fiyat, puan ve lot birimlerini karıştırma. Belirsizlikte HOLD kullan."""
        user_content = json.dumps({
            "symbol": symbol, "timeframe": timeframe_name,
            "bid": tick.get("bid"), "ask": tick.get("ask"), "spread_points": tick.get("spread"),
            "hma": hma_val, "second_ma": ma2_val,
            "profile": {"style": (profile["persona"] or {}).get("style"), "rules": profile["rules"]},
            "open_positions": len(positions),
            "candle_columns": ["time", "open", "high", "low", "close", "tick_volume"],
            "closed_candles": [[r.get(k) for k in ("time", "open", "high", "low", "close", "tick_volume")]
                               for r in closed]}, ensure_ascii=False, separators=(",", ":"))

        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Piyasa durumu ve kullanıcının öğrenilen profili:\n{user_content}"}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
                "max_tokens": 400
            }

            rec = self._request_json(payload, 25, "advice:" + symbol + ":" + timeframe_name)
            if rec.get("action") not in ("BUY", "SELL", "HOLD"):
                raise ValueError("AI tavsiye yönü geçersiz.")
            rec["id"] = str(uuid.uuid4())
            rec["generated_at"] = time.strftime("%H:%M:%S")
            rec["symbol"] = symbol

            rec["expires_at"] = time.time() + 900
            self.cost_state["advice_cache"][cache_key] = {"expires_at": rec["expires_at"], "recommendation": rec}
            self.cost_state["advice_cache"] = dict(sorted(
                self.cost_state["advice_cache"].items(), key=lambda item: item[1]["expires_at"])[-64:])
            self.memory["latest_recommendation"] = rec
            self.save_memory()

            return {"success": True, "recommendation": rec}

        except Exception as e:
            logger.error(f"Error getting market advice: {e}")
            return {"success": False, "error": str(e)}

    def execute_recommendation(self, rec=None, *, automatic=False, generation=None):
        if not self.mt5:
            return {"success": False, "error": "MT5 istemcisi bağlı değil."}
        candidate = (self.memory.get("latest_recommendation") or {}) if rec is None else rec
        # Use the server-side recommendation; only the requested lot may be overridden.
        target = next((entry["recommendation"] for entry in self.cost_state["advice_cache"].values()
                       if entry["recommendation"].get("id") and entry["recommendation"]["id"] == candidate.get("id")), None)
        if not target:
            return {"success": False, "error": "Tavsiye doğrulanamadı; güncel analiz alın."}
        auto_stop = self._auto_stop
        if automatic and (generation != self.autopilot_generation or not self.autopilot.get("enabled")
                          or self.autopilot.get("mode") != "FULL_AUTO"):
            return {"success": False, "error": "Otomatik işlem durduruldu veya ayarları değişti."}
        try:
            if float(target.get("expires_at", 0)) <= time.time():
                raise ValueError("Tavsiyenin süresi doldu; güncel analiz alın.")
            action = target.get("action", "").upper()
            if action not in ("BUY", "SELL"):
                raise ValueError("Bekleme tavsiyesinde işlem açılamaz.")
            symbol = target["symbol"].upper()
            if automatic and symbol not in self.autopilot["allowed_symbols"]:
                raise ValueError("Sembol otomatik işlem listesinde değil.")
            volume = float(candidate.get("suggested_lot") or target.get("suggested_lot") or .01)
            volume = min(volume, float(self.autopilot["max_lot"]))
            if not math.isfinite(volume) or volume <= 0:
                raise ValueError("Geçersiz lot miktarı.")
            sl, tp = int(target.get("sl_points", 0)), int(target.get("tp_points", 0))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return {"success": False, "error": str(exc)}
        res = self.mt5.open_order(symbol, action, volume, sl_points=sl, tp_points=tp,
                                  comment="AI Trade", magic=AI_MAGIC,
                                  request_id="ai-" + target["id"],
                                  stop_event=auto_stop if automatic else None)
        if res.get("success") or res.get("uncertain"):
            self.autopilot["last_auto_trade_time"] = time.time()
            self.save_memory()
        return res

    def update_autopilot(self, config: Dict[str, Any]) -> Dict[str, Any]:
        self.autopilot_generation += 1
        self._auto_stop.set()
        if "enabled" in config:
            self.autopilot["enabled"] = bool(config["enabled"])
        if "mode" in config:
            self.autopilot["mode"] = str(config["mode"]).upper()
        if "min_confidence" in config:
            self.autopilot["min_confidence"] = max(50, min(99, int(config["min_confidence"])))
        if "max_lot" in config:
            self.autopilot["max_lot"] = float(config["max_lot"])
        if "daily_loss_limit" in config:
            self.autopilot["daily_loss_limit"] = float(config["daily_loss_limit"])
        if "allowed_symbols" in config and isinstance(config["allowed_symbols"], list):
            self.autopilot["allowed_symbols"] = [str(s).upper() for s in config["allowed_symbols"]]

        if self.mt5:
            self.mt5.daily_loss_limit = float(self.autopilot["daily_loss_limit"])
            if self.autopilot["enabled"]:
                self.mt5.automation_stopped.clear()
        if self.autopilot["enabled"]:
            # Old in-flight analyses/orders retain the signalled event.
            self._auto_stop = threading.Event()
        if not self.save_memory():
            self._auto_stop.set()
            self.autopilot["enabled"] = False
            return {"success": False, "error": "Ayarlar diske kaydedilemedi; otopilot durduruldu."}
        return {"success": True, "autopilot": self.autopilot}

    def get_status(self) -> Dict[str, Any]:
        return {
            "success": True,
            "usage": dict(self._usage()),
            "limits": {"daily_calls": self.daily_call_limit, "daily_tokens": self.daily_token_limit,
                       "timezone": "UTC"},
            "risk_day_timezone": "UTC",
            "risk_scope": "Tüm yeni emirler; gerçekleşen net sonuç + açık net zarar; hesap para birimi",
            "api_configured": bool(self.api_key),
            "model": self.model,
            "memory": self.memory,
            "autopilot": self.autopilot,
            "has_analyzed": self.memory.get("last_analyzed") is not None
        }
