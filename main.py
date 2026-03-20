"""
MOTH's Rollup - FastAPI backend
"""

import os
import json
import hashlib
import secrets
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

import httpx
from cryptography.fernet import Fernet
import base64

from backend.handicap import calculate_new_handicaps, format_adjustment
from backend.sheets import (
    get_all_players,
    get_player_handicap,
    save_round_results,
    add_new_player,
    get_last_round_results,
    get_last_round_date,
)
from backend.scraper import scrape_players

load_dotenv()

app = FastAPI(title="MOTH's Rollup")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
templates = Jinja2Templates(directory="frontend/templates")

# Simple in-memory session store (use Redis in production)
sessions: dict[str, dict] = {}

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", secrets.token_hex(32))
SHEET_ID = os.getenv("SHEET_ID")

# Encryption for storing IG credentials
def _get_fernet():
    key = base64.urlsafe_b64encode(
        hashlib.sha256(APP_SECRET_KEY.encode()).digest()
    )
    return Fernet(key)

def encrypt(text: str) -> str:
    return _get_fernet().encrypt(text.encode()).decode()

def decrypt(text: str) -> str:
    return _get_fernet().decrypt(text.encode()).decode()


# --- Session helpers ---

def get_session_id(request: Request) -> str | None:
    return request.cookies.get("session_id")

def get_session(request: Request) -> dict | None:
    sid = get_session_id(request)
    if sid and sid in sessions:
        return sessions[sid]
    return None

def require_session(request: Request) -> dict:
    session = get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the main PWA."""
    session = get_session(request)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "authenticated": session is not None},
    )


# --- Google OAuth ---

@app.get("/auth/google")
async def auth_google():
    """Redirect to Google OAuth consent screen."""
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": (
            "openid email profile "
            "https://www.googleapis.com/auth/spreadsheets"
        ),
        "access_type": "offline",
        "prompt": "consent",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(
        f"https://accounts.google.com/o/oauth2/v2/auth?{query}"
    )


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str):
    """Handle Google OAuth callback."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
    token_data = resp.json()
    if "error" in token_data:
        raise HTTPException(400, f"OAuth error: {token_data['error']}")

    # Get user email
    async with httpx.AsyncClient() as client:
        user_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
    user_info = user_resp.json()

    sid = secrets.token_hex(32)
    sessions[sid] = {
        "email": user_info.get("email"),
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "ig_username": None,
        "ig_pin_enc": None,
        "sheet_id": SHEET_ID,
    }

    response = RedirectResponse("/")
    response.set_cookie("session_id", sid, httponly=True, samesite="lax")
    return response


@app.get("/auth/status")
async def auth_status(request: Request):
    session = get_session(request)
    if not session:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "email": session.get("email"),
        "has_ig_credentials": bool(session.get("ig_username")),
        "last_round_date": get_last_round_date(
            session["sheet_id"], session
        ) if session.get("sheet_id") else None,
    }


# --- IG Credentials ---

class IGCredentials(BaseModel):
    username: str
    pin: str


@app.post("/api/ig-credentials")
async def save_ig_credentials(
    creds: IGCredentials,
    request: Request,
    session: dict = Depends(require_session),
):
    """Save Intelligent Golf credentials (PIN encrypted at rest)."""
    sid = get_session_id(request)
    sessions[sid]["ig_username"] = creds.username
    sessions[sid]["ig_pin_enc"] = encrypt(creds.pin)
    return {"ok": True}


# --- Load Players ---

class LoadRequest(BaseModel):
    date: str  # YYYY-MM-DD


@app.post("/api/load-players")
async def load_players(
    body: LoadRequest,
    session: dict = Depends(require_session),
):
    """
    Scrape players from Intelligent Golf for the given date,
    look up their handicaps from the History sheet,
    and return the player list ready for score entry.
    """
    ig_user = session.get("ig_username")
    ig_pin_enc = session.get("ig_pin_enc")
    if not ig_user or not ig_pin_enc:
        raise HTTPException(400, "Intelligent Golf credentials not set")

    ig_pin = decrypt(ig_pin_enc)
    sheet_id = session["sheet_id"]

    # Scrape player names
    try:
        names = await scrape_players(ig_user, ig_pin, body.date)
    except Exception as e:
        raise HTTPException(502, str(e))

    if not names:
        raise HTTPException(404, "No players found for this date")

    # Get all players from History
    all_players = get_all_players(sheet_id, session)
    name_to_hc = {p["name"].strip().lower(): p["handicap"] for p in all_players}

    players = []
    new_players = []
    for name in names:
        hc = name_to_hc.get(name.strip().lower())
        if hc is None:
            # New player — flag for handicap entry
            new_players.append(name)
            players.append({"name": name, "handicap": None, "score": None, "new_player": True})
        else:
            players.append({"name": name, "handicap": hc, "score": None, "new_player": False})

    return {
        "date": body.date,
        "players": players,
        "new_players": new_players,
    }


# --- Add New Player ---

class NewPlayerRequest(BaseModel):
    name: str
    handicap: int


@app.post("/api/new-player")
async def new_player(
    body: NewPlayerRequest,
    session: dict = Depends(require_session),
):
    """Add a new player to the History sheet."""
    add_new_player(session["sheet_id"], session, body.name, body.handicap)
    return {"ok": True, "name": body.name, "handicap": body.handicap}


# --- Save Score (autosave single score) ---

class ScoreUpdate(BaseModel):
    date: str
    players: list[dict]   # current full player list with scores


@app.post("/api/autosave")
async def autosave(
    body: ScoreUpdate,
    session: dict = Depends(require_session),
):
    """
    Calculate and return updated handicaps for the current player list.
    Does NOT write to the sheet — that happens on /api/save-round.
    Used to show live New HC updates as scores are entered.
    """
    results = calculate_new_handicaps(body.players)
    for r in results:
        r["adj_display"] = format_adjustment(r.get("adjustment"))
    return {"players": results}


# --- Save Round ---

@app.post("/api/save-round")
async def save_round(
    body: ScoreUpdate,
    session: dict = Depends(require_session),
):
    """
    Calculate final handicaps and write results to the History sheet.
    """
    results = calculate_new_handicaps(body.players)
    try:
        save_round_results(session["sheet_id"], session, results, body.date)
    except Exception as e:
        raise HTTPException(500, f"Failed to save to Google Sheets: {str(e)}")

    for r in results:
        r["adj_display"] = format_adjustment(r.get("adjustment"))

    return {
        "ok": True,
        "players": results,
        "date": body.date,
    }


# --- Last Round Results ---

@app.get("/api/last-round")
async def last_round(session: dict = Depends(require_session)):
    """Return last round results from History sheet."""
    results = get_last_round_results(session["sheet_id"], session)
    date = get_last_round_date(session["sheet_id"], session)
    for r in results:
        r["adj_display"] = ""  # not needed for display
    return {"players": results, "date": date}


# --- Logout ---

@app.post("/auth/logout")
async def logout(request: Request):
    sid = get_session_id(request)
    if sid and sid in sessions:
        del sessions[sid]
    response = JSONResponse({"ok": True})
    response.delete_cookie("session_id")
    return response
