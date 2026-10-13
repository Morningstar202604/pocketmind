"""速率限制：slowapi + in-memory，全局保护。

选 default_limits 而非 per-route @limiter.limit 的原因：
慢速 API 的装饰器模式要求端点函数接收 "request: Request" 参数。我们有些
端点的函数签名已经固定，再加侵入性参数改动面太大。default_limits 通过
ASGI 中间件挂在所有端点上，行为等价（超限返回 429）。

速率值：
- 默认 30 次/分钟：足以覆盖正常使用，又能挡住死循环/SSE 风暴；
- 单用户本地 Agent，不需要多级控制。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address


def mount_rate_limit(app: FastAPI, default_limits: list[str] | None = None) -> Limiter:
    """挂载 slowapi 中间件与异常处理器。"""
    limits = default_limits or ["30/minute"]
    limiter = Limiter(key_func=get_remote_address, default_limits=limits)
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    @app.exception_handler(RateLimitExceeded)
    async def _429_handler(request: Request, exc: RateLimitExceeded):
        retry = getattr(exc, "retry_after", 60) or 60
        return JSONResponse(
            status_code=429,
            content={"error": "请求过于频繁，请稍后再试", "retry_after": f"{retry}s"},
            headers={"Retry-After": str(retry)},
        )
    return limiter
