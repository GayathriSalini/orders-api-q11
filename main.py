import base64
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

T, R, WINDOW = 40, 18, 10.0

app = FastAPI()
hits = defaultdict(deque)
by_key = {}
lock = threading.Lock()
CATALOG = [{"id": i, "name": f"order-{i}"} for i in range(1, T + 1)]


@app.middleware("http")
async def limiter(request: Request, call_next):
    cid = request.headers.get("x-client-id")
    if request.method == "OPTIONS" or not cid:
        return await call_next(request)
    now = time.monotonic()
    with lock:
        q = hits[cid]
        while q and now - q[0] >= WINDOW:
            q.popleft()
        if len(q) >= R:
            retry = int(WINDOW - (now - q[0])) + 1
            return JSONResponse(
                {"detail": "rate limited"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        q.append(now)
    return await call_next(request)


# Added after the limiter, so CORS is outermost and 429s carry CORS headers too
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)


@app.post("/orders")
async def create_order(idempotency_key: Optional[str] = Header(None)):
    with lock:
        if idempotency_key and idempotency_key in by_key:
            return JSONResponse(by_key[idempotency_key], status_code=201)
        order = {"id": "ord_" + uuid.uuid4().hex[:12], "status": "created"}
        if idempotency_key:
            by_key[idempotency_key] = order
    return JSONResponse(order, status_code=201)


@app.get("/orders")
def list_orders(limit: int = 10, cursor: Optional[str] = None):
    limit = max(1, min(limit, 100))
    try:
        start = int(base64.urlsafe_b64decode(cursor.encode()).decode()) if cursor else 0
        start = max(0, start)
    except Exception:
        start = 0
    items = CATALOG[start:start + limit]
    end = start + len(items)
    nxt = base64.urlsafe_b64encode(str(end).encode()).decode() if end < T else None
    return {"items": items, "next_cursor": nxt}


@app.get("/")
def root():
    return {"status": "ok"}
