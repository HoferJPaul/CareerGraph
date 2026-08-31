"""CareerGraph API entrypoint.

Run from the backend/ directory so relative module imports (api_schemas,
deps, llm_provider, cv_markdown, routes.*) and the root-pipeline sys.path
shim in deps.py both resolve correctly:

    cd backend
    python -m uvicorn main:app --reload --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deps import shutdown_driver
from routes import cv, graph, jobs, requirements


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    shutdown_driver()


app = FastAPI(title="CareerGraph API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(graph.router)
app.include_router(jobs.router)
app.include_router(requirements.router)
app.include_router(cv.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
