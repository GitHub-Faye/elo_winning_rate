from fastapi import FastAPI

app = FastAPI(title="ELO Winning Rate")


@app.get("/")
async def root():
    return {"message": "Hello from ELO Winning Rate!"}


@app.get("/health")
async def health():
    return {"status": "ok"}
