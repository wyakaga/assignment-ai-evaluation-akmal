"""Modul Router — Mengklasifikasikan intent dan merutekan query pengguna.

Alur routing:
    Input pengguna -> Deteksi intent -> RAG (informasi/kebijakan) atau Tool (status tiket).
"""

import os
import re

from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from langsmith import traceable

from app.rag import answer_from_knowledge_base
from app.tools import get_ticket_status

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

raw_api_key = os.environ.get("GEMINI_API_KEY", "")
api_key = raw_api_key.strip().strip('"\'')
MODEL_NAME = "gemini-3.5-flash-lite"
_client = genai.Client(api_key=api_key)


def extract_ticket_id(query: str) -> str:
    """Mengekstraksi nomor identitas tiket dari teks query pengguna.

    Args:
        query (str): Teks pertanyaan atau permintaan dari pengguna.

    Returns:
        str: String ID tiket berformat kapital (misal: 'TICKET-0001') jika ditemukan,
            atau string kosong jika tidak ditemukan pola tiket yang valid.
    """
    pattern = r"\b(TICKET-\d{4,})\b"
    matched = re.search(pattern, query, re.IGNORECASE)

    if matched:
        ticket_id = matched.group(1).upper()
        return ticket_id
    else:
        return ""


@traceable(run_type="chain", name="Detect Intent")
async def detect_intent(query: str) -> str:
    """Mengklasifikasikan maksud (intent) dari query pengguna.

    Menggunakan pendekatan Hybrid Deterministic Router:
    1. Pola regex cepat & kata kunci tiket untuk routing deterministik instan (<1 ms).
    2. Jika pertanyaan umum/kebijakan tanpa rujukan tiket, langsung diarahkan ke 'faq'
       tanpa memanggil API LLM (mengeliminasi ~750 ms latensi & hemat 100% router token).
    3. Klasifikasi fallback cerdas berbasis LLM hanya dipanggil untuk kueri majemuk
       (compound query) yang memuat intensi informasi dan aksi sekaligus.

    Args:
        query (str): Teks pertanyaan atau permintaan dari pengguna.

    Returns:
        str: Nama kategori intent ('faq' atau 'ticket_status').
    """
    clean_query = query.strip()
    ticket_id = extract_ticket_id(clean_query)

    # 1. Jika ada ID tiket eksplisit, pasti merupakan pemeriksaan tiket
    if ticket_id:
        return "ticket_status"

    ticket_pattern = r"\b(status tiket|cek tiket|tiket saya|progres tiket|lacak tiket|tiket|ticket)\b"
    faq_pattern = r"\b(kenapa|mengapa|bagaimana|gimana|berapa|apakah|kapan|siapa|apa|cara|prosedur|syarat|kebijakan|aturan|panduan|bantuan|halo|selamat|pagi|siang|sore|malam)\b"

    has_ticket = bool(re.search(ticket_pattern, clean_query, re.IGNORECASE))
    has_faq = bool(re.search(faq_pattern, clean_query, re.IGNORECASE))

    # 2. Kasus kueri campuran ambigu (Compound Query, contoh EVAL-09): panggil fallback LLM
    if has_ticket and has_faq:
        prompt = f"""
            Klasifikasikan query berikut ke salah satu intent yang paling dominan:
            - 'faq': pertanyaan informasi kebijakan kantor, fasilitas, absensi, cuti, akun, jaringan, software.
            - 'ticket_status': permintaan memeriksa progres/status tiket helpdesk.
            Jawab HANYA dengan kata 'faq' atau 'ticket_status'.
            Pertanyaan: {clean_query}
        """
        try:
            response = await _client.aio.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=10),
            )
            cleaned_response = response.text.strip().lower() if response.text else ""
            if "ticket_status" in cleaned_response:
                return "ticket_status"
            return "faq"
        except (errors.APIError, TimeoutError, ConnectionError):
            return "ticket_status"

    # 3. Kueri tiket murni tanpa unsur pertanyaan FAQ
    if has_ticket:
        return "ticket_status"

    # 4. Kueri umum/kebijakan default instan ke 'faq' tanpa overhead LLM
    return "faq"


@traceable(run_type="chain", name="Handle Query")
async def handle_query(query: str) -> dict[str, str]:
    """Menangani query pengguna dan mengarahkannya ke pipeline yang sesuai.

    Args:
        query (str): Teks pertanyaan atau permintaan dari pengguna.

    Returns:
        dict[str, str]: Dictionary response yang memuat key 'status', 'intent',
            dan 'data' sebagai hasil pemrosesan RAG atau eksekusi tool.
    """
    intent = await detect_intent(query)

    if intent == "ticket_status":
        ticket_id = extract_ticket_id(query)

        if not ticket_id:
            return {
                "status": "success",
                "intent": "ticket_status",
                "data": "Mohon cantumkan ID tiket Anda dengan format TICKET-XXXX (contoh: TICKET-0001) untuk pengecekan status.",
            }

        data = get_ticket_status(ticket_id)
        return {"status": "success", "intent": "ticket_status", "data": data}

    elif intent == "faq":
        data = await answer_from_knowledge_base(query)
        return {"status": "success", "intent": "faq", "data": data}

    else:
        return {
            "status": "error",
            "intent": "unknown",
            "data": "Maaf, saya tidak memahami permintaan Anda.",
        }
