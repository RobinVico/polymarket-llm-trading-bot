import os
import requests
import json
import logging
from dotenv import load_dotenv
# v4.1: 迁移到 py_clob_client_v2 (修复 order_version_mismatch, GitHub issue #340/337)
from py_clob_client_v2 import (
    ClobClient, ApiCreds, OrderArgs, OrderType, MarketOrderArgs,
    PartialCreateOrderOptions, Side
)
# v1 兼容字符串 (供其他地方使用)
BUY = "BUY"
SELL = "SELL"
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("executor")
load_dotenv("<sibling-v3-dir>/.env")

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
DATA_API = "https://data-api.polymarket.com"
FUNDER = os.getenv("POLY_FUNDER")
PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY")
SIGNATURE_TYPE = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))

def _s():
    s = requests.Session()
    s.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[500,502,503,504])))
    return s

_instance = None

class Executor:
    def __init__(self):
        self.client = None
        try:
            key = os.getenv("POLY_PRIVATE_KEY")
            funder = os.getenv("POLY_FUNDER")
            sig = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))
            if not key:
                log.error("POLY_PRIVATE_KEY not found in .env")
                return
            # v2: 两步初始化 (L1 拿 creds, 然后 L1+L2 重建 client)
            tmp = ClobClient(host=HOST, chain_id=CHAIN_ID, key=key, signature_type=sig, funder=funder)
            creds = tmp.create_or_derive_api_key()
            self.client = ClobClient(host=HOST, chain_id=CHAIN_ID, key=key, 
                                     signature_type=sig, funder=funder, creds=creds)
            log.info("CLOB v2 client OK")
        except Exception as e:
            log.error(f"CLOB init failed: {e}")

    @classmethod
    def get(cls):
        global _instance
        if _instance is None:
            _instance = cls()
        return _instance

    def get_positions(self):
        try:
            resp = _s().get(f"{DATA_API}/positions", params={"user": FUNDER, "limit": 100, "sizeThreshold": 0}, timeout=30).json()
            if isinstance(resp, str):
                log.warning(f"API returned string: {resp[:100]}")
                return []
            if not isinstance(resp, list):
                log.warning(f"API returned unexpected type: {type(resp)}")
                return []
            positions = []
            for p in resp:
                size = float(p.get("size", 0))
                if size <= 0: continue
                positions.append({
                    "title": p.get("title", "unknown"),
                    "side": p.get("outcome", ""),
                    "size": size,
                    "avg_price": float(p.get("avgPrice", 0)),
                    "cur_price": float(p.get("curPrice", 0)),
                    "pnl_pct": float(p.get("percentPnl", 0)),
                    "asset": p.get("asset", ""),
                    "condition_id": p.get("conditionId", ""),
                })
            log.info(f"{len(positions)} positions")
            return positions
        except Exception as e:
            log.warning(f"get_positions failed: {e}")
            return []

    def get_prices_history(self, token_id, interval="1d", fidelity="60", force=False, max_age=600):
        """拉 Polymarket prices-history. force=True 跳缓存. max_age 单位秒, 默认 10 分钟."""
        import time
        if not hasattr(Executor, "_prices_cache"):
            Executor._prices_cache = {}
        key = (token_id, interval, fidelity)
        if not force:
            cached = Executor._prices_cache.get(key)
            if cached and time.time() - cached[0] < max_age:
                return cached[1]
        try:
            r = _s().get("https://clob.polymarket.com/prices-history",
                         params={"market": token_id, "interval": interval, "fidelity": fidelity},
                         timeout=10).json()
            hist = r.get("history") or [] if isinstance(r, dict) else []
            Executor._prices_cache[key] = (time.time(), hist)
            return hist
        except Exception as e:
            log.warning(f"get_prices_history failed for {token_id[:20]}: {e}")
            return []

    def get_cash_balance(self):
        """USDC 余额 (没锁在仓位里的可用现金). Polymarket USDC 是 6 decimals.
        失败返回 None (跟 "真无现金 = 0.0" 区分, 让调用方能判断 API 健康).
        v5.1: 之前返回 0.0 时, monitor / dashboard 把失败状态当成"真无现金", 写入
        污染 portfolio_snapshot (assets_total 漏算 cash) 并误导 sweep 守卫."""
        try:
            if not self.client:
                return None
            from py_clob_client_v2 import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            r = self.client.get_balance_allowance(params)
            bal_raw = r.get("balance") if isinstance(r, dict) else getattr(r, "balance", None)
            if bal_raw is None:
                return None
            return float(bal_raw) / 1_000_000
        except Exception as e:
            log.warning(f"get_cash_balance failed: {e}")
            return None

    def get_best_ask(self, token_id):
        """拉 orderbook 返回 best_ask (最低卖价). 失败返回 None."""
        try:
            if not self.client:
                return None
            book = self.client.get_order_book(token_id)
            asks = book.asks if hasattr(book, "asks") else (book.get("asks") if isinstance(book, dict) else None)
            if not asks:
                return None
            def _p(a):
                if hasattr(a, "price"): return float(a.price)
                if isinstance(a, dict): return float(a["price"])
                if isinstance(a, (list, tuple)): return float(a[0])
                return None
            prices = [p for p in (_p(a) for a in asks) if p is not None and p > 0]
            return min(prices) if prices else None
        except Exception as e:
            log.warning(f"get_best_ask failed for {token_id[:20]}: {e}")
            return None

    def get_best_bid(self, token_id):
        """拉 orderbook 返回 best_bid (最高买价 = 你 sell 时真能拿到的价). 失败返回 None.
        v5.1 用这个替代 polymarket curPrice 做止盈/止损触发判断, 防 Dell 类低流动假象."""
        try:
            if not self.client:
                return None
            book = self.client.get_order_book(token_id)
            bids = book.bids if hasattr(book, "bids") else (book.get("bids") if isinstance(book, dict) else None)
            if not bids:
                return None
            def _p(b):
                if hasattr(b, "price"): return float(b.price)
                if isinstance(b, dict): return float(b["price"])
                if isinstance(b, (list, tuple)): return float(b[0])
                return None
            prices = [p for p in (_p(b) for b in bids) if p is not None and p > 0]
            return max(prices) if prices else None
        except Exception as e:
            log.warning(f"get_best_bid failed for {token_id[:20]}: {e}")
            return None

    def buy(self, token_id, usdc_amount, reason=""):
        """
        市价 FAK 买入 (best_ask 限价 + 美元金额输入).
        - 拉 best_ask 当限价
        - size = floor(usdc / best_ask, 2) 向下截避免余额不足
        - FAK: 能成交多少立刻成交, 剩余自动 cancel
        """
        import math
        try:
            if not self.client:
                log.error("buy: client未初始化")
                return False, "client未初始化"
            usdc_amount = float(usdc_amount)
            if usdc_amount < 1:
                return False, f"金额过小: ${usdc_amount}"

            best_ask = self.get_best_ask(token_id)
            if not best_ask or best_ask <= 0:
                return False, "盘口无 ask 或获取失败"
            if best_ask >= 1.0:
                return False, f"best_ask={best_ask} 异常 (>=1)"

            size = math.floor(usdc_amount / best_ask * 100) / 100
            if size < 0.01:
                return False, f"size={size} 过小 (USDC={usdc_amount}, ask={best_ask})"

            log.info(f"BUY token={token_id[:20]}... usdc=${usdc_amount} ask=${best_ask:.4f} size={size} reason={reason}")

            try:
                result = self.client.create_and_post_order(
                    order_args=OrderArgs(
                        token_id=token_id,
                        price=best_ask,
                        side=Side.BUY,
                        size=size,
                    ),
                    options=PartialCreateOrderOptions(tick_size="0.01"),
                    order_type=OrderType.FAK,
                )
            except Exception as e:
                log.exception(f"buy: create_and_post_order异常: {e}")
                return False, f"下单异常: {e}"

            log.info(f"buy raw result: {result}")
            if not isinstance(result, dict):
                return False, f"result不是dict: {result!r}"[:200]
            if not result.get("success", False):
                return False, result.get("errorMsg", "(no errorMsg)")
            try:
                making_amount = float(result.get("makingAmount", 0))
            except (ValueError, TypeError):
                making_amount = 0.0
            if making_amount <= 0:
                return False, f"0成交 status={result.get('status')} order_id={result.get('orderID')}"

            usd_spent = making_amount * best_ask
            msg = f"买成功: {making_amount:.2f}股 @ {best_ask*100:.1f}% ≈ ${usd_spent:.2f}"
            log.info(msg)
            return True, msg
        except Exception as e:
            log.exception(f"buy exception: {e}")
            return False, str(e)

    def sell(self, token_id, size, reason=""):
        """
        市价卖出 (FAK + best_bid限价 + 滑点保护).
        - 拉盘口找best_bid作为限价
        - OrderType.FAK: 能成交多少立刻成交,剩余自动cancel(不会挂着)
        - 严格检查API返回,只有真实makingAmount>0才return True
        """
        # v2 import (顶部已 import, 这里 noop)
        pass
        try:
            if not self.client:
                log.error("sell: client未初始化")
                return False
            
            # === Step 1: size 2位小数 + 向下截 (避免 round() 向上导致 >持仓) ===
            # Polymarket SELL 精度: 0.01 股. round() 会向上 (8.4175 → 8.42) 触发余额不足.
            # 必须 floor 向下截到 2 位小数, 保证下单数量 ≤ 实际持仓.
            import math
            size = math.floor(size * 100) / 100
            if size < 0.01:
                log.warning(f"sell: size={size} 过小, 跳过")
                return False
            
            # === Step 2: 拉盘口 ===
            try:
                book = self.client.get_order_book(token_id)
            except Exception as e:
                log.error(f"sell: get_order_book失败: {e}")
                return False
            
            # 兼容dict和OrderBookSummary对象
            bids = None
            if isinstance(book, dict):
                bids = book.get("bids")
            elif hasattr(book, "bids"):
                bids = book.bids
            
            if bids is None:
                log.error(f"sell: 无法解析book, type={type(book).__name__}, repr={book!r}"[:300])
                return False
            if len(bids) == 0:
                log.error(f"sell: 盘口无bid (无买家), token={token_id[:20]}")
                return False
            
            # === Step 3: 找best_bid (最高买价) ===
            def _price(b):
                if hasattr(b, "price"): return float(b.price)
                if isinstance(b, dict): return float(b["price"])
                if isinstance(b, (list, tuple)): return float(b[0])
                raise ValueError(f"unknown bid format: {b}")
            try:
                best_bid = max(_price(b) for b in bids)
            except Exception as e:
                log.error(f"sell: 解析bids失败 {e}, sample={bids[:1]}")
                return False
            if best_bid <= 0:
                log.error(f"sell: best_bid={best_bid} 异常")
                return False
            
            log.info(f"SELL token={token_id[:20]}... size={size} best_bid=${best_bid:.4f} reason={reason}")
            
            # === Step 4: v2 SDK FAK 限价 sell (一步搞定) ===
            try:
                # 用 best_bid 当限价, FAK = 能成交多少立刻成交, 剩余自动 cancel
                # tick_size 0.01 (大多数市场), 如果是 0.001 市场也兼容 (传 "0.01" 不会拒)
                result = self.client.create_and_post_order(
                    order_args=OrderArgs(
                        token_id=token_id, 
                        price=best_bid, 
                        side=Side.SELL,
                        size=size
                    ),
                    options=PartialCreateOrderOptions(tick_size="0.01"),
                    order_type=OrderType.FAK,
                )
            except Exception as e:
                log.exception(f"sell: create_and_post_order异常: {e}")
                return False
            
            log.info(f"sell raw result: {result}")
            
            # === Step 5: 严格检查 ===
            if not isinstance(result, dict):
                log.error(f"sell: result不是dict, type={type(result).__name__} val={result!r}"[:200])
                return False
            
            success = result.get("success", False)
            err_msg = result.get("errorMsg", "")
            status = result.get("status", "unknown")
            order_id = result.get("orderID", "?")
            try:
                making_amount = float(result.get("makingAmount", 0))
            except (ValueError, TypeError):
                making_amount = 0.0
            
            if not success:
                log.error(f"sell FAILED: {err_msg or '(no errorMsg)'} | order_id={order_id} | full={result}")
                return False
            
            if making_amount <= 0:
                log.error(f"sell: 0成交 status={status} order_id={order_id} (盘口太薄/价格slip) result={result}")
                return False
            
            pct = making_amount / size * 100 if size > 0 else 0
            if making_amount < size * 0.95:
                log.warning(f"sell部分成交: 请求{size} 实际{making_amount:.4f}股 ({pct:.0f}%) order_id={order_id}")
            else:
                log.info(f"sell成功: 卖{making_amount:.4f}股 order_id={order_id} status={status}")
            return True
        
        except Exception as e:
            log.exception(f"sell exception: {e}")
            return False

