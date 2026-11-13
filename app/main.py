from fastapi import FastAPI

from app.observability import request_logging_middleware, setup_logging
from app.routers import accounts, instruments, orders, settlements

setup_logging()

app = FastAPI(title="fin-oms-api")
app.middleware("http")(request_logging_middleware)

app.include_router(accounts.router)
app.include_router(instruments.router)
app.include_router(orders.router)
app.include_router(settlements.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
