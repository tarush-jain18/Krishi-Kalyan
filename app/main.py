from fastapi import FastAPI

app = FastAPI(
    title="Krishi Kalyan API",
    version="1.0.0",
    description="Multilingual AI Farm Advisor"
)


@app.get("/")
def home():
    return {
        "project": "Krishi Kalyan",
        "status": "Running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }