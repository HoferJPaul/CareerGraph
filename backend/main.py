"""CareerGraph API entrypoint.

Run from the backend/ directory so relative module imports (api_schemas,
deps, llm_provider, cv_markdown, routes.*) and the root-pipeline sys.path
shim in deps.py both resolve correctly:

    cd backend
    python -m uvicorn main:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from deps import shutdown_driver
from llm.errors import LLMError
from llm.factory import log_startup_status
from routes import cv, graph, jobs, llm_status, requirements, source_cv
from source_cv.errors import SourceCvError
from upload_limits import BodySizeLimitMiddleware

# Structured, content-free logs for the LLM/API layers (see llm/*.py: they log operation, model,
# attempts, latency and token counts -- never prompts, job descriptions, CVs or provider errors).
_app_log = logging.getLogger("careergraph")
if not _app_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _app_log.addHandler(_handler)
    _app_log.setLevel(logging.INFO)
    _app_log.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_startup_status()
    yield
    shutdown_driver()


app = FastAPI(title="CareerGraph API", version="0.1.0", lifespan=lifespan)

# Added BEFORE CORS so the CORS layer wraps it (a refused upload must still carry CORS headers).
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)



@app.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError) -> JSONResponse:
    """Every LLM-layer failure becomes a clear, non-sensitive JSON error:
    {"detail": {"code", "message", "retryable"}}. Raw provider error bodies, prompts and model
    output never reach this point (see llm/errors.py)."""
    return JSONResponse(status_code=exc.http_status, content={"detail": exc.to_detail()}, headers=exc.headers)


@app.exception_handler(SourceCvError)
async def source_cv_error_handler(request: Request, exc: SourceCvError) -> JSONResponse:
    """Source CV failures use the same non-sensitive {"detail": {"code", "message", "retryable"}} shape.
    Messages are fixed strings: never CV text, contact details, filenames or storage paths."""
    return JSONResponse(status_code=exc.http_status, content={"detail": exc.to_detail()}, headers=exc.headers)


app.include_router(graph.router)
app.include_router(jobs.router)
app.include_router(requirements.router)
app.include_router(cv.router)
app.include_router(llm_status.router)
app.include_router(source_cv.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
