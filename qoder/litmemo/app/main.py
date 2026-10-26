from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import BASE_DIR, get_settings
from .ingest import parse_document
from .memory import KIND_NOTE, KIND_PROGRESS, MemoryUnavailable, get_memory
from .store import get_store

STATIC_DIR = Path(__file__).resolve().parent / "static"
SAMPLES_DIR = BASE_DIR / "samples"

app = FastAPI(title="LitMemo", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: Literal["hybrid", "vector", "fulltext"] = "hybrid"
    limit: int = Field(default=6, ge=1, le=20)
    year_from: int | None = None
    year_to: int | None = None
    kind: str | None = None
    use_memory: bool = True


class ConstraintsRequest(BaseModel):
    topic: str = ""
    year_from: int | None = None
    exclude_terms: str = ""
    default_limit: int = Field(default=6, ge=1, le=20)


class NoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ForgetRequest(BaseModel):
    artifact_id: str
    revision: int = 1
    state_version: int = 0


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    settings = get_settings()
    try:
        seekdb = {"ok": True, **get_store().ping()}
    except Exception as exc:  # seekdb driver raises several exception types
        seekdb = {"ok": False, "error": str(exc)[:300]}
    return {
        "seekdb": seekdb,
        "powercontext": get_memory().status(),
        "embedding_model": settings.embedding_model,
        "documents": len(get_store().list_documents()) if seekdb["ok"] else 0,
    }


@app.get("/api/documents")
def documents() -> dict:
    return {"documents": get_store().list_documents()}


def _ingest(filename: str, data: bytes, **overrides) -> dict:
    document = parse_document(filename, data, **overrides)
    if not document.chunks:
        return {"title": document.title, "chunks": 0, "skipped": True}
    chunks = get_store().add_document(document)
    return {"doc_id": document.doc_id, "title": document.title, "chunks": chunks}


@app.post("/api/documents/upload")
async def upload(
    files: list[UploadFile] = File(...),
    title: str = Form(""),
    year: int | None = Form(None),
    kind: str = Form(""),
    tags: str = Form(""),
) -> dict:
    imported = []
    for file in files:
        data = await file.read()
        if not data:
            continue
        imported.append(
            _ingest(
                file.filename or "untitled.md",
                data,
                title=title if len(files) == 1 else "",
                year=year,
                kind=kind,
                tags=tags,
            )
        )
    return {"imported": imported}


@app.post("/api/documents/samples")
def import_samples() -> dict:
    if not SAMPLES_DIR.exists():
        raise HTTPException(status_code=404, detail="samples 目录不存在")
    store = get_store()
    imported = []
    for path in sorted(SAMPLES_DIR.glob("*.md")):
        store.delete_by_source(path.name)
        imported.append(_ingest(path.name, path.read_bytes()))
    return {"imported": imported}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str) -> dict:
    get_store().delete_document(doc_id)
    return {"deleted": doc_id}


@app.post("/api/search")
def search(request: SearchRequest) -> dict:
    memory = get_memory()
    memory_report: dict = {"status": {"ok": False}, "applied_constraints": None, "recalled": []}
    constraints = None
    if request.use_memory:
        try:
            constraints = memory.get_constraints()
            memory_report["status"] = memory.status()
        except MemoryUnavailable as exc:
            memory_report["status"] = {"ok": False, "error": str(exc)}

    year_from = request.year_from
    year_to = request.year_to
    limit = request.limit
    exclude_terms: list[str] = []
    if constraints:
        if year_from is None:
            year_from = constraints.get("year_from")
        if constraints.get("default_limit") and request.limit == 6:
            limit = int(constraints["default_limit"])
        exclude_terms = [term.strip() for term in constraints.get("exclude_terms", []) if term.strip()]
        memory_report["applied_constraints"] = {
            key: value for key, value in constraints.items() if not key.startswith("_")
        }

    results = get_store().search(
        request.query,
        mode=request.mode,
        limit=limit,
        year_from=year_from,
        year_to=year_to,
        kind=request.kind,
    )

    excluded = 0
    if exclude_terms:
        kept = []
        for row in results:
            haystack = f"{row['title']}\n{row['text']}".lower()
            if any(term.lower() in haystack for term in exclude_terms):
                excluded += 1
            else:
                kept.append(row)
        results = kept
    memory_report["excluded_by_memory"] = excluded

    if request.use_memory and memory_report["status"].get("ok"):
        try:
            memory_report["recalled"] = [
                {
                    "kind": hit["memory"]["kind"],
                    "text": hit["memory"]["text"],
                    "score": hit.get("score"),
                    "artifact": hit["memory"]["artifact"],
                }
                for hit in memory.recall(request.query, limit=4)
                if hit["memory"]["kind"] != KIND_PROGRESS
            ][:3]
            memory.save_progress(
                {
                    "last_query": request.query,
                    "mode": request.mode,
                    "year_from": year_from,
                    "year_to": year_to,
                    "kind": request.kind,
                    "limit": limit,
                    "result_count": len(results),
                    "top_titles": [row["title"] for row in results[:3]],
                }
            )
            memory_report["progress_saved"] = True
        except MemoryUnavailable as exc:
            memory_report["status"] = {"ok": False, "error": str(exc)}

    return {
        "query": request.query,
        "mode": request.mode,
        "results": results,
        "count": len(results),
        "filters": {"year_from": year_from, "year_to": year_to, "kind": request.kind, "limit": limit},
        "memory": memory_report,
    }


@app.get("/api/memory")
def memories() -> dict:
    memory = get_memory()
    return {
        "status": memory.status(),
        "memories": memory.list_memories(),
        "progress": memory.get_progress(),
        "constraints": memory.get_constraints(),
    }


@app.post("/api/memory/constraints")
def save_constraints(request: ConstraintsRequest) -> dict:
    payload = {
        "topic": request.topic.strip(),
        "year_from": request.year_from,
        "exclude_terms": [term.strip() for term in request.exclude_terms.replace("，", ",").split(",") if term.strip()],
        "default_limit": request.default_limit,
    }
    record = get_memory().save_constraints(payload)
    return {"saved": True, "constraints": payload, "artifact": record.get("artifact")}


@app.post("/api/memory/notes")
def save_note(request: NoteRequest) -> dict:
    record = get_memory().save_note(request.text.strip())
    return {"saved": True, "kind": KIND_NOTE, "artifact": record.get("artifact")}


@app.post("/api/memory/forget")
def forget(request: ForgetRequest) -> dict:
    record = {
        "artifact": {
            "family": "atomic-memory",
            "artifact_id": request.artifact_id,
            "revision": request.revision,
        },
        "state_version": request.state_version,
    }
    get_memory().forget(record)
    return {"forgotten": request.artifact_id}


@app.exception_handler(MemoryUnavailable)
async def memory_unavailable_handler(_, exc: MemoryUnavailable):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=503, content={"detail": str(exc)})
