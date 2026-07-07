"""
v5.10.2: Gamma API HTTP 客户端, 带 DNS 污染兜底.

背景: 本机默认 DNS (Tailscale 100.100.100.100 → 上游) 对 *.polymarket.com 间歇性污染,
gamma-api.polymarket.com 会被解析到 Facebook/Dropbox 等假 IP, TLS 证书校验失败
(SSLCertVerificationError: Hostname mismatch). 用正确 IP + 域名 SNI 直连是通的 (实测 200).

策略:
1) 先走正常 requests (系统 DNS). 成功直接返回.
2) SSL / 连接 / 超时失败 → 用 DoH (1.1.1.1 / 8.8.8.8, 纯 IP 访问不经系统 DNS)
   解析真实 A 记录, 用 urllib3 固定 IP 重试. SNI 和证书校验仍按域名进行, 安全性不降级.
3) DoH 结果缓存 10 分钟.

目前只用于 gamma (resolution_check + dashboard 批量现价). CLOB / data-api 不走这里.
"""
import json
import logging
import time
from urllib.parse import urlencode

import certifi
import requests
import urllib3

log = logging.getLogger("gamma_client")

GAMMA_HOST = "gamma-api.polymarket.com"

# DoH 端点按 IP 访问, 自身不依赖 DNS. Cloudflare 主, Google 备.
_DOH_ENDPOINTS = [
    ("https://1.1.1.1/dns-query", {"accept": "application/dns-json"}),
    ("https://8.8.8.8/resolve", {}),
]

_IP_TTL = 600
_NEG_TTL = 60  # DoH 全挂时负缓存 60s, 避免每次 getaddrinfo 都等 DoH 超时
_ip_cache = {}  # host -> (ips, expire_ts)


class GammaError(Exception):
    """gamma 直连 + DoH 兜底都失败."""


def _doh_resolve(host):
    cached = _ip_cache.get(host)
    if cached and cached[1] > time.time():
        return cached[0]
    for url, headers in _DOH_ENDPOINTS:
        try:
            r = requests.get(url, params={"name": host, "type": "A"}, headers=headers, timeout=8)
            r.raise_for_status()
            answers = (r.json() or {}).get("Answer") or []
            ips = [a["data"] for a in answers if a.get("type") == 1]
            if ips:
                _ip_cache[host] = (ips, time.time() + _IP_TTL)
                return ips
        except Exception as e:
            log.debug(f"DoH {url} 失败: {e}")
    _ip_cache[host] = ([], time.time() + _NEG_TTL)
    return []


# === v5.10.2: 进程级 DNS 兜底 (覆盖 requests / urllib3 / httpx / CLOB SDK 全部) ===
_dns_guard_installed = False


def install_polymarket_dns_guard():
    """monkey-patch socket.getaddrinfo: *.polymarket.com 优先用 DoH 解析.

    原理: 各 HTTP 库 (requests/httpx/CLOB SDK) 做 TLS 时用的是原始 hostname
    (SNI + 证书校验), getaddrinfo 只负责供 IP — 所以替换成 DoH 解析出的真实 IP
    不降低任何安全性, 只是绕开被污染的系统 DNS.

    DoH 失败时回退系统 DNS (负缓存 60s); 网络正常时 DoH 答案同样正确, 行为无差别.
    幂等, 重复调用无副作用. 在 main.py 最早处调用一次, 全进程生效."""
    global _dns_guard_installed
    if _dns_guard_installed:
        return
    import socket as _socket
    _orig = _socket.getaddrinfo

    def _guarded_getaddrinfo(host, *args, **kwargs):
        try:
            if isinstance(host, (str, bytes)):
                h = host.decode() if isinstance(host, bytes) else host
                if h.endswith(".polymarket.com"):
                    ips = _doh_resolve(h)
                    if ips:
                        return _orig(ips[0], *args, **kwargs)
        except Exception:
            pass
        return _orig(host, *args, **kwargs)

    _socket.getaddrinfo = _guarded_getaddrinfo
    _dns_guard_installed = True
    log.info("polymarket DNS guard installed: *.polymarket.com 走 DoH 优先, 系统 DNS 兜底")


def _pinned_get(host, path, params, timeout):
    """固定 IP 直连. server_hostname=域名 → SNI 正确; assert_hostname=域名 → 证书校验不降级."""
    last_err = None
    for ip in _doh_resolve(host):
        try:
            pool = urllib3.HTTPSConnectionPool(
                ip, port=443,
                server_hostname=host,
                assert_hostname=host,
                cert_reqs="CERT_REQUIRED",
                ca_certs=certifi.where(),
                timeout=urllib3.Timeout(connect=6, read=timeout),
                retries=False,
            )
            url = path + ("?" + urlencode(params, doseq=True) if params else "")
            r = pool.request("GET", url, headers={"Host": host, "User-Agent": "polymarket-bot/5.10"})
            if r.status == 200:
                return json.loads(r.data.decode("utf-8"))
            last_err = Exception(f"HTTP {r.status}")
        except Exception as e:
            last_err = e
    raise GammaError(f"DoH 固定 IP 兜底也失败: {last_err}")


def gamma_get(path, params=None, timeout=10):
    """GET https://gamma-api.polymarket.com{path}, 返回解析后的 JSON.
    先系统 DNS 直连; SSL/连接类失败 (DNS 污染特征) 自动切 DoH 固定 IP.
    彻底失败 raise GammaError, 调用方自行 try/except."""
    url = f"https://{GAMMA_HOST}{path}"
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except (requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout) as e:
        log.warning(f"gamma 直连失败 ({type(e).__name__}), 切 DoH 固定 IP: {path}")
        return _pinned_get(GAMMA_HOST, path, params, timeout)
    except Exception as e:
        raise GammaError(str(e))
