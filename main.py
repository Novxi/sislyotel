from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import sqlite3
import datetime

DB_PATH = "reservations.db"

# -------------------- DB SETUP -------------------- #

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guest_name TEXT NOT NULL,
            guest_email TEXT NOT NULL,
            guest_phone TEXT,
            check_in TEXT NOT NULL,
            check_out TEXT NOT NULL,
            check_in_time TEXT,
            check_out_time TEXT,
            room_type TEXT NOT NULL,
            room_count INTEGER NOT NULL,
            adults INTEGER NOT NULL,
            children INTEGER NOT NULL,
            total_price REAL NOT NULL,
            payment_status TEXT NOT NULL,
            special_requests TEXT,
            experiences TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

# -------------------- MODELLER -------------------- #

class ReservationCreate(BaseModel):
    guest_name: str
    guest_email: EmailStr
    guest_phone: Optional[str] = None
    check_in: str
    check_out: str
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    room_type: str
    room_count: int
    adults: int
    children: int
    total_price: float
    payment_status: str
    special_requests: Optional[str] = None
    experiences: Optional[str] = None

class Reservation(ReservationCreate):
    id: int
    created_at: str

# -------------------- APP -------------------- #

app = FastAPI(title="Sisly Resort API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    init_db()

# -------------------- PUBLIC ENDPOINTS -------------------- #

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

@app.post("/api/public/reservations", response_model=Reservation)
def create_reservation(payload: ReservationCreate):
    conn = get_connection()
    cur = conn.cursor()

    created_at = datetime.datetime.utcnow().isoformat()

    cur.execute(
        """
        INSERT INTO reservations (
            guest_name,
            guest_email,
            guest_phone,
            check_in,
            check_out,
            check_in_time,
            check_out_time,
            room_type,
            room_count,
            adults,
            children,
            total_price,
            payment_status,
            special_requests,
            experiences,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.guest_name,
            payload.guest_email,
            payload.guest_phone,
            payload.check_in,
            payload.check_out,
            payload.check_in_time,
            payload.check_out_time,
            payload.room_type,
            payload.room_count,
            payload.adults,
            payload.children,
            payload.total_price,
            payload.payment_status,
            payload.special_requests,
            payload.experiences or "",
            created_at,
        ),
    )
    conn.commit()
    new_id = cur.lastrowid

    cur.execute("SELECT * FROM reservations WHERE id = ?", (new_id,))
    row = cur.fetchone()
    conn.close()

    return Reservation(**dict(row))

# -------------------- ADMIN ENDPOINTS -------------------- #

@app.get("/api/admin/reservations", response_model=List[Reservation])
def list_reservations():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM reservations ORDER BY datetime(created_at) DESC")
    rows = cur.fetchall()
    conn.close()
    return [Reservation(**dict(r)) for r in rows]

@app.get("/api/admin/reservations/{reservation_id}", response_model=Reservation)
def get_reservation(reservation_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return Reservation(**dict(row))

@app.patch("/api/admin/reservations/{reservation_id}", response_model=Reservation)
def update_reservation(
    reservation_id: int,
    payment_status: Optional[str] = None,
    special_requests: Optional[str] = None,
    experiences: Optional[str] = None,
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Reservation not found")

    current = dict(row)
    new_payment_status = payment_status or current["payment_status"]
    new_special_requests = (
        special_requests if special_requests is not None else current["special_requests"]
    )
    new_experiences = experiences if experiences is not None else current["experiences"]

    cur.execute(
        """
        UPDATE reservations
        SET payment_status = ?, special_requests = ?, experiences = ?
        WHERE id = ?
        """,
        (new_payment_status, new_special_requests, new_experiences, reservation_id),
    )
    conn.commit()

    cur.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
    updated_row = cur.fetchone()
    conn.close()

    return Reservation(**dict(updated_row))

@app.delete("/api/admin/reservations/{reservation_id}")
def delete_reservation(reservation_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM reservations WHERE id = ?", (reservation_id,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()

    if deleted == 0:
        raise HTTPException(status_code=404, detail="Reservation not found")

    return {"status": "ok", "deleted_id": reservation_id}

# -------------------------------------------------------------------
# GEÇMİŞ REZERVASYONLARI TEMİZLE (CHECK-OUT < BUGÜN)
# -------------------------------------------------------------------
@app.post("/api/admin/reservations/cleanup-expired")
def cleanup_expired_reservations():
    """
    check_out tarihi BUGÜNDEN eski olan tüm rezervasyonları siler.
    Tarih formatı 'YYYY-MM-DD' veya 'YYYY-MM-DD HH:MM' olsa da
    ilk 10 karakteri alarak date() ile karşılaştırıyoruz.
    """
    conn = get_connection()
    cur = conn.cursor()

    # Kaç tane silinecek? (bilgi amaçlı)
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM reservations
        WHERE date(substr(check_out, 1, 10)) < date('now')
        """
    )
    row = cur.fetchone()
    to_delete = row["cnt"] if row else 0

    # Asıl silme
    cur.execute(
        """
        DELETE FROM reservations
        WHERE date(substr(check_out, 1, 10)) < date('now')
        """
    )
    conn.commit()
    deleted = cur.rowcount
    conn.close()

    # Buraya kadar geldiysek 200 OK garanti
    return {
        "status": "ok",
        "to_delete": to_delete,
        "deleted_count": deleted,
    }
