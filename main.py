"""
MOTH's Rollup - FastAPI backend
No authentication required - credentials hardcoded via environment variables.
"""

import os
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from pydantic import BaseModel
from dotenv import load_dotenv

from backend.handicap import calculate_new_handicaps, format_adjustment
from backend.sheets import (
    get_all_players,
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

# --- Config from environment ---
SHEET_ID = os.getenv("SHEET_ID")
IG_USERNAME = os.getenv("IG_USERNAME")
IG_PIN = os.getenv("IG_PIN")

# Shared session context passed to sheets functions
def get_context():
    return {"sheet_id": SHEET_ID}


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/auth/status")
async def auth_status():
    """Return last round date for status display."""
    try:
        last_date = get_last_round_date(SHEET_ID, get_context())
    except Exception:
        last_date = None
    return {"last_round_date": last_date}


# --- Load Players ---

class LoadRequest(BaseModel):
    date: str  # YYYY-MM-DD


@app.post("/api/load-players")
async def load_players(body: LoadRequest):
    """
    Scrape players from Intelligent Golf for the given date,
    look up their handicaps from the History sheet,
    and return the player list ready for score entry.
    """
    if not IG_USERNAME or not IG_PIN:
        raise HTTPException(500, "Intelligent Golf credentials not configured on server.")

    # Scrape player names
    try:
        names = await scrape_players(IG_USERNAME, IG_PIN, body.date)
    except Exception as e:
        raise HTTPException(502, str(e))

    if not names:
        raise HTTPException(404, "No players found for this date.")

    # Look up handicaps from History sheet
    try:
        all_players = get_all_players(SHEET_ID, get_context())
    except Exception as e:
        raise HTTPException(500, f"Could not read player list from sheet: {str(e)}")

    name_to_hc = {p["name"].strip().lower(): p["handicap"] for p in all_players}

    players = []
    new_players = []
    for name in names:
        hc = name_to_hc.get(name.strip().lower())
        if hc is None:
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
async def new_player(body: NewPlayerRequest):
    """Add a new player to the History sheet."""
    try:
        add_new_player(SHEET_ID, get_context(), body.name, body.handicap)
    except Exception as e:
        raise HTTPException(500, f"Could not add player to sheet: {str(e)}")
    return {"ok": True, "name": body.name, "handicap": body.handicap}


# --- Autosave (calculate handicaps, no sheet write) ---

class ScoreUpdate(BaseModel):
    date: str
    players: list[dict]


@app.post("/api/autosave")
async def autosave(body: ScoreUpdate):
    """
    Calculate and return updated handicaps for the current player list.
    Does NOT write to the sheet.
    """
    results = calculate_new_handicaps(body.players)
    for r in results:
        r["adj_display"] = format_adjustment(r.get("adjustment"))
    return {"players": results}


# --- Save Round ---

@app.post("/api/save-round")
async def save_round(body: ScoreUpdate):
    """
    Calculate final handicaps and write results to the History sheet.
    """
    results = calculate_new_handicaps(body.players)
    try:
        save_round_results(SHEET_ID, get_context(), results, body.date)
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
async def last_round():
    """Return last round results from History sheet."""
    try:
        results = get_last_round_results(SHEET_ID, get_context())
        date = get_last_round_date(SHEET_ID, get_context())
    except Exception as e:
        raise HTTPException(500, f"Could not load last round: {str(e)}")
    return {"players": results, "date": date}
