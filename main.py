from fastapi import FastAPI
from routers.elo import router as elo_router
from routers.prediction import router as prediction_router
from routers.head_to_head import router as head_to_head_router
from routers.rating import router as rating_router

app = FastAPI(title="ELO Winning Rate")
app.include_router(elo_router, prefix="")
app.include_router(prediction_router, prefix="")
app.include_router(head_to_head_router, prefix="")
app.include_router(rating_router, prefix="")


@app.get("/")
async def root():
    return {"message": "Hello from ELO Winning Rate!"}


@app.get("/health")
async def health():
    return {"status": "ok"}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)