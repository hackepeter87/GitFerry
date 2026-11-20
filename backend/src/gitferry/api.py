"""Minimal iteration-1 REST boundary."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gitferry.blueprints import MAX_BLUEPRINT_BYTES, BlueprintValidationError, validate_text

app = FastAPI(
    title="GitFerry API",
    version="0.1.0-dev",
    description="Iteration-1 validation API; no general migration endpoint exists.",
)


@app.get("/healthz")
def health() -> dict[str, object]:
    return {"status": "ok", "iteration": 1}


@app.post("/api/v1/blueprints/validate")
async def validate_blueprint(request: Request) -> JSONResponse:
    declared_length = request.headers.get("content-length")
    if declared_length is not None and declared_length.isdigit():
        if int(declared_length) > MAX_BLUEPRINT_BYTES:
            return _oversized_response()

    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_BLUEPRINT_BYTES:
            return _oversized_response()
    try:
        content = chunks.decode("utf-8")
        document = validate_text(content)
    except (UnicodeDecodeError, BlueprintValidationError) as exc:
        return JSONResponse(status_code=422, content={"valid": False, "errors": [str(exc)]})
    return JSONResponse(
        status_code=200,
        content={
            "valid": True,
            "apiVersion": document["apiVersion"],
            "kind": document["kind"],
            "errors": [],
        },
    )


def _oversized_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"valid": False, "errors": ["blueprint exceeds the 1 MiB input limit"]},
    )
