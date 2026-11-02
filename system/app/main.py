"""Entry point aplikasi FastAPI — IT Helpdesk Assistant.

Alur pemrosesan request:
    1. Menerima pertanyaan atau pengecekan tiket dari pengguna.
    2. Meneruskan ke router (router.py) untuk klasifikasi intent.
    3. Router mengeksekusi pipeline RAG (rag.py) atau Tool (tools.py).
    4. Mengembalikan respons terstruktur AskResponse ke pengguna.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

from fastapi import FastAPI, status
from langsmith import traceable
from pydantic import BaseModel, Field

from app.rag import build_index
from app.router import handle_query

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("it-helpdesk-api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Mengelola siklus hidup aplikasi FastAPI saat startup dan shutdown.

    Args:
        app (FastAPI): Instansi aplikasi FastAPI.

    Yields:
        None: Menyerahkan kontrol kembali ke framework selama server berjalan.

    Raises:
        Exception: Jika inisialisasi FAISS index gagal saat startup.
    """
    logger.info("Memulai inisialisasi aplikasi & membangun FAISS index...")
    try:
        build_index()
        logger.info("FAISS index berhasil dibangun. Service siap menerima request.")
    except Exception as e:
        logger.critical(
            "Gagal menginisialisasi FAISS index saat startup: %s", e, exc_info=True
        )
        raise

    yield
    logger.info("Aplikasi dimatikan.")


app = FastAPI(
    title="IT Helpdesk Assistant API",
    description="Backend API berbasis RAG dan Mini-Agent Router untuk layanan Helpdesk IT & People Ops.",
    version="1.0.0",
    lifespan=lifespan,
)


class AskRequest(BaseModel):
    """Skema data request untuk endpoint /ask.

    Attributes:
        query (str): Teks pertanyaan pengguna atau instruksi pengecekan tiket.
    """

    query: str = Field(
        ...,
        min_length=1,
        description="Query pertanyaan pengguna atau pengecekan status tiket",
        json_schema_extra={"example": "Berapa jatah cuti tahunan saya?"},
    )


class AskResponse(BaseModel):
    """Skema data response untuk endpoint /ask.

    Attributes:
        status (str): Status pemrosesan respons ('success' atau 'error').
        intent (str): Intent terdeteksi ('faq', 'ticket_status', atau 'unknown').
        data (str): Hasil jawaban FAQ dari RAG atau informasi data tiket.
    """

    status: str = Field(..., description="Status response ('success' atau 'error')")
    intent: str = Field(
        ..., description="Intent terdeteksi ('faq', 'ticket_status', atau 'unknown')"
    )
    data: str = Field(..., description="Hasil jawaban RAG atau detail data tiket")


@app.get("/health", status_code=status.HTTP_200_OK, tags=["Health"])
async def health() -> dict[str, str]:
    """Memeriksa status kesehatan server API.

    Returns:
        dict[str, str]: Dictionary berisi status operasional service, misal `{"status": "ok"}`.
    """
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse, tags=["Helpdesk"])
@traceable(run_type="chain", name="Ask Helpdesk")
async def ask(request: AskRequest) -> AskResponse:
    """Menerima dan memproses pertanyaan pengguna atau pengecekan status tiket.

    Menerima query teks pengguna, memvalidasinya, lalu meneruskannya ke modul
    router untuk klasifikasi intent serta eksekusi pipeline yang sesuai (RAG atau Tool).

    Args:
        request (AskRequest): Objek payload request berisi query pengguna.

    Returns:
        AskResponse: Objek respons terstruktur yang memuat status, kategori intent,
            dan teks jawaban atau status tiket.
    """
    user_query = request.query.strip()

    if not user_query:
        return AskResponse(
            status="error",
            intent="unknown",
            data="Pertanyaan tidak boleh kosong. Mohon ajukan pertanyaan yang valid.",
        )

    try:
        result = await handle_query(request.query)

        return AskResponse(**result)
    except Exception:
        logger.exception(
            "Terjadi kegagalan saat memproses query '%s'",
            user_query,
        )
        return AskResponse(
            status="error",
            intent="unknown",
            data="Maaf, terjadi kendala saat memproses permintaan Anda. Silakan coba beberapa saat lagi.",
        )
