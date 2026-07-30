from fastapi import FastAPI
from routers.elo import router as elo_router

app = FastAPI(title="ELO Winning Rate")
app.include_router(elo_router, prefix="")


@app.get("/")
async def root():
    return {"message": "Hello from ELO Winning Rate!"}


@app.get("/health")
async def health():
    return {"status": "ok"}
