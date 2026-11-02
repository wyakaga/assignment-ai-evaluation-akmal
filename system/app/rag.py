"""Modul RAG — Pipeline Retrieval-Augmented Generation untuk IT Helpdesk.

Modul ini mengelola pemuatan knowledge base CSV, pembentukan embedding dokumen,
penyimpanan serta pencarian kemiripan vektor menggunakan FAISS,
dan sintesis jawaban terkontekstualisasi menggunakan LLM Gemini.
"""

import os
from pathlib import Path
from typing import Any

import faiss
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langsmith import traceable

BASE_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_BASE_PATH = BASE_DIR / "data" / "it_helpdesk_knowledge_base.csv"
load_dotenv(BASE_DIR / ".env")

raw_api_key = os.environ.get("GEMINI_API_KEY", "")
api_key = raw_api_key.strip().strip('"\'')
MODEL_NAME = "gemini-3.5-flash-lite"
_client = genai.Client(api_key=api_key)

_index: faiss.IndexFlatL2 | None = None
_documents: list[str] | None = None
_df: pd.DataFrame | None = None


def _get_embedding(text: str) -> list[float]:
    """Menghasilkan vektor embedding untuk teks input menggunakan model Gemini Embedding.

    Args:
        text (str): Teks yang akan diubah menjadi representasi vektor.

    Returns:
        list[float]: Daftar float berdimensi 768 yang merepresentasikan embedding teks.

    Raises:
        ValueError: Jika API mengembalikan respon embedding kosong.
    """
    result = _client.models.embed_content(
        model="gemini-embedding-2",
        contents=text,
        config={"output_dimensionality": 768},
    )

    if result.embeddings is None or len(result.embeddings) == 0:
        raise ValueError(f"Gagal mendapatkan embedding untuk teks: {text[:50]}...")

    embedding_values = result.embeddings[0].values
    if embedding_values is None:
        raise ValueError(f"Nilai embedding kosong untuk teks: {text[:50]}...")

    return embedding_values


def build_index() -> None:
    """Membaca knowledge base CSV dan membangun index pencarian FAISS di memori.

    Fungsi ini membaca berkas CSV knowledge base, membuat embedding representasi
    untuk setiap baris tanya-jawab, dan mengindeksnya menggunakan faiss.IndexFlatL2.
    Fungsi bersifat idempoten; jika index sudah terbangun, pemanggilan berikutnya
    tidak akan membangun ulang index.

    Raises:
        FileNotFoundError: Jika berkas knowledge base CSV tidak ditemukan.
        ValueError: Jika embedding dokumen gagal dihasilkan.
    """
    global _index, _documents, _df

    if _index is not None:
        return

    _df = pd.read_csv(KNOWLEDGE_BASE_PATH)
    _documents = [
        f"Kategori: {row['category']}\nPertanyaan: {row['question']}\nJawaban: {row['answer']}"
        for _, row in _df.iterrows()
    ]
    embeddings = [_get_embedding(doc) for doc in _documents]
    embeddings = np.array(embeddings).astype("float32")
    dimension = embeddings.shape[1]
    _index = faiss.IndexFlatL2(dimension)
    _index.add(embeddings)


@traceable(run_type="retriever", name="Semantic Search")
def semantic_search(
    query: str, top_k: int = 3, threshold: float = 0.70
) -> list[dict[str, Any]]:
    """Melakukan pencarian semantik pada index FAISS berdasarkan kedekatan jarak L2.

    Menyaring hasil pencarian menggunakan ambang relevansi L2 (threshold). Dokumen
    yang memiliki jarak L2 melebihi ambang batas disingkirkan untuk mencegah halusinasi
    dan menghindari pembengkakan token konteks.

    Args:
        query (str): Teks pertanyaan atau kata kunci pencarian.
        top_k (int, optional): Jumlah dokumen teratas yang ingin diambil. Defaults to 3.
        threshold (float, optional): Jarak L2 maksimal untuk dianggap relevan. Defaults to 0.70.

    Returns:
        list[dict[str, Any]]: Daftar dokumen paling relevan yang lolos ambang batas. Setiap elemen
            berisi 'rank', 'question', 'answer', 'category', dan 'distance'.

    Raises:
        RuntimeError: Jika FAISS index atau data dokumen belum diinisialisasi.
    """
    if _index is None or _documents is None or _df is None:
        raise RuntimeError(
            "FAISS index belum dibangun. Pastikan build_index dipanggil saat aplikasi startup."
        )

    query_embedding = np.array([_get_embedding(query)]).astype("float32")
    distances, indices = _index.search(query_embedding, top_k)

    results = []
    for rank, idx in enumerate(indices[0]):
        dist = float(distances[0][rank])
        # Saring hanya dokumen yang berada di dalam ambang batas relevansi
        if dist <= threshold:
            results.append(
                {
                    "rank": rank + 1,
                    "question": _df.iloc[idx]["question"],
                    "answer": _df.iloc[idx]["answer"],
                    "category": _df.iloc[idx]["category"],
                    "distance": dist,
                }
            )
    return results


@traceable(run_type="chain", name="Answer From Knowledge Base")
async def answer_from_knowledge_base(query: str) -> str:
    """Menjawab pertanyaan pengguna menggunakan pipeline Retrieval-Augmented Generation (RAG).

    Fungsi ini melakukan pencarian semantik ke index FAISS dengan ambang batas jarak L2 (0.70).
    Jika tidak ada dokumen relevan yang ditemukan (pertanyaan di luar cakupan), sistem langsung
    mengembalikan penolakan santun (early exit) tanpa memanggil API LLM (hemat latensi & token).
    Jika dokumen ditemukan, LLM dipanggil secara asinkron (non-blocking) untuk sintesis respons.

    Args:
        query (str): Pertanyaan pengguna terkait kebijakan atau fasilitas perusahaan.

    Returns:
        str: Teks jawaban hasil sintesis LLM atau pesan penolakan resmi jika di luar cakupan.

    Raises:
        RuntimeError: Jika pemanggilan LLM menghasilkan respon kosong atau terjadi kegagalan API.
    """
    results = semantic_search(query, top_k=3, threshold=0.70)

    # Early Exit: Jika kueri di luar cakupan (tidak ada FAQ relevan <= 0.70)
    if not results:
        return (
            "Mohon maaf, kami tidak menemukan informasi terkait hal tersebut dalam panduan FAQ internal kami. "
            "Silakan menghubungi tim People Operations atau IT Support terkait untuk bantuan lebih lanjut."
        )

    context = "\n\n".join(
        f"FAQ: {r['question']}\nJawaban: {r['answer']}" for r in results
    )
    prompt = f"""Kamu adalah helpdesk specialist internal perusahaan yang ramah dan membantu.

Tugas: Jawab pertanyaan pengguna HANYA berdasarkan informasi FAQ berikut. Jika jawaban tidak ada dalam konteks atau query di luar topik IT/People helpdesk, nyatakan dengan sopan bahwa kamu tidak memiliki informasi tersebut dan sarankan menghubungi tim terkait. Jangan berhalusinasi.

Konteks FAQ relevan:
{context}

Format: Maksimal 2-3 kalimat yang padat & jelas. Gunakan teks biasa tanpa markdown maupun emoji. Gunakan kata ganti 'kami' alih-alih 'saya'. Sebutkan poin FAQ yang dijadikan rujukan.

Pertanyaan pengguna: {query}

Jawaban:"""

    try:
        response = await _client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=300),
        )
        if not response.text:
            raise RuntimeError("Respons dari model kosong")
        return response.text.strip()
    except Exception as e:
        raise RuntimeError(f"Gagal memproses pesan LLM: {e}") from e
