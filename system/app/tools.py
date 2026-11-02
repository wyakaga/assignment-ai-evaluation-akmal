"""Modul Tools — Menyediakan fungsi aksi langsung untuk pengecekan data helpdesk.

Modul ini dipanggil oleh router ketika intent pengguna memerlukan aksi langsung
seperti query data tiket, bukan retrieval semantik dari knowledge base.
"""

from pathlib import Path

import pandas as pd
from langsmith import traceable

BASE_DIR = Path(__file__).resolve().parent.parent
MOCK_TICKETS_PATH = BASE_DIR / "data" / "mock_tickets.csv"


@traceable(run_type="tool", name="Get Ticket Status")
def get_ticket_status(ticket_id: str) -> str:
    """Mencari status tiket berdasarkan ID tiket dari basis data tiket.

    Args:
        ticket_id (str): Nomor identitas tiket helpdesk (misal: 'TICKET-0001').

    Returns:
        str: Pesan status tiket berisi subjek, nama karyawan, status,
            dan waktu pembaruan terakhir; atau pesan kesalahan jika tidak ditemukan.
    """
    if not ticket_id or not isinstance(ticket_id, str):
        return "ID tiket tidak valid atau tidak disertakan."

    ticket_id = ticket_id.strip().upper()

    try:
        df = pd.read_csv(MOCK_TICKETS_PATH)
    except Exception as e:
        return f"Terjadi kesalahan saat membaca database tiket: {e!s}"

    matched = df[df["ticket_id"] == ticket_id]

    if not matched.empty:
        row = matched.iloc[0].to_dict()
        return f"Tiket {ticket_id} ({row['subject']}) atas nama {row['employee_name']} saat ini berstatus '{row['status']}' (Pembaruan terakhir: {row['last_update']})."
    else:
        return f"Tiket dengan ID '{ticket_id}' tidak ditemukan dalam sistem kami. Mohon pastikan nomor tiket sudah benar."
