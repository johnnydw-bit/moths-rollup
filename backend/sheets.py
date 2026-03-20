"""
Google Sheets helper for MOTH's Rollup.

Sheet structure:
  Sheet: "History"
    Col A: Player name
    Col B: Last round score
    Col C: Next round handicap (what they play off next time)
  Rows start at row 2 (row 1 is the header).

Operations:
  - get_all_players()         → list of {name, handicap}
  - get_player_handicap(name) → int or None
  - save_round_results(results, date_str)
      Updates col B (score) and col C (new handicap) for each player
      who played. Non-playing members are left unchanged.
  - add_new_player(name, handicap)
      Inserts a new player row in alphabetical order.
"""

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials


HISTORY_SHEET = "History"
HEADER_ROW = 1          # Row 1 is headers, data starts at row 2
COL_NAME = "A"
COL_SCORE = "B"
COL_HANDICAP = "C"


def _sheets_service(token_info: dict):
    """Build a Google Sheets service from stored OAuth token info."""
    creds = Credentials(
        token=token_info["access_token"],
        refresh_token=token_info.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_info["client_id"],
        client_secret=token_info["client_secret"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def get_all_players(sheet_id: str, token_info: dict) -> list[dict]:
    """
    Read all players from the History sheet.
    Returns list of {name: str, handicap: int, row: int}
    row is the 1-based sheet row number, needed for targeted updates.
    """
    service = _sheets_service(token_info)
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=sheet_id,
            range=f"{HISTORY_SHEET}!A2:C",
        )
        .execute()
    )
    rows = result.get("values", [])
    players = []
    for i, row in enumerate(rows):
        name = row[0].strip() if len(row) > 0 else ""
        handicap_str = row[2].strip() if len(row) > 2 else ""
        if not name:
            continue
        try:
            handicap = int(float(handicap_str))
        except (ValueError, TypeError):
            handicap = 0
        players.append({
            "name": name,
            "handicap": handicap,
            "row": i + 2,  # +2 because data starts at row 2
        })
    return players


def get_player_handicap(sheet_id: str, token_info: dict, name: str) -> int | None:
    """Look up a single player's current handicap by name (exact match)."""
    players = get_all_players(sheet_id, token_info)
    for p in players:
        if p["name"].strip().lower() == name.strip().lower():
            return p["handicap"]
    return None


def save_round_results(
    sheet_id: str,
    token_info: dict,
    results: list[dict],
    date_str: str,
) -> None:
    """
    Save round results to the History sheet.

    results: list of {name, score, new_handicap}
    Only updates rows for players who played (have a score).
    Non-playing members are left completely unchanged.

    Also writes the round date into cell E1 as a record.
    """
    service = _sheets_service(token_info)
    all_players = get_all_players(sheet_id, token_info)

    # Build a name → row lookup
    name_to_row = {p["name"].strip().lower(): p["row"] for p in all_players}

    data = []
    for r in results:
        if r.get("score") is None or r.get("new_handicap") is None:
            continue
        key = r["name"].strip().lower()
        row_num = name_to_row.get(key)
        if row_num is None:
            continue  # Player not found — should have been added already
        data.append({
            "range": f"{HISTORY_SHEET}!B{row_num}:C{row_num}",
            "values": [[r["score"], r["new_handicap"]]],
        })

    if not data:
        return

    # Write last round date to E1 as a record
    data.append({
        "range": f"{HISTORY_SHEET}!E1",
        "values": [[f"Last round: {date_str}"]],
    })

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id,
        body={
            "valueInputOption": "RAW",
            "data": data,
        },
    ).execute()


def add_new_player(
    sheet_id: str,
    token_info: dict,
    name: str,
    handicap: int,
) -> None:
    """
    Add a new player to the History sheet in alphabetical order.
    Inserts a new row at the correct position.
    """
    service = _sheets_service(token_info)
    all_players = get_all_players(sheet_id, token_info)

    # Find insertion point (alphabetical by name)
    insert_before_row = None
    for p in all_players:
        if name.strip().lower() < p["name"].strip().lower():
            insert_before_row = p["row"]
            break

    if insert_before_row is None:
        # Append at end
        insert_before_row = (all_players[-1]["row"] + 1) if all_players else 2

    sheet_metadata = (
        service.spreadsheets()
        .get(spreadsheetId=sheet_id)
        .execute()
    )
    # Find the sheet ID for "History"
    history_sheet_id = None
    for s in sheet_metadata["sheets"]:
        if s["properties"]["title"] == HISTORY_SHEET:
            history_sheet_id = s["properties"]["sheetId"]
            break

    if history_sheet_id is None:
        raise Exception("Could not find History sheet")

    # Insert a blank row
    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={
            "requests": [{
                "insertDimension": {
                    "range": {
                        "sheetId": history_sheet_id,
                        "dimension": "ROWS",
                        "startIndex": insert_before_row - 1,  # 0-based
                        "endIndex": insert_before_row,
                    },
                    "inheritFromBefore": False,
                }
            }]
        },
    ).execute()

    # Write the player data into the new row
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{HISTORY_SHEET}!A{insert_before_row}:C{insert_before_row}",
        valueInputOption="RAW",
        body={"values": [[name, "", handicap]]},
    ).execute()


def get_last_round_results(sheet_id: str, token_info: dict) -> list[dict]:
    """
    Read last round scores and handicaps from History for display
    on the results screen. Returns players who have a score in col B,
    sorted by score descending.
    """
    players = get_all_players(sheet_id, token_info)
    service = _sheets_service(token_info)

    # Re-read including col B (last score)
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=sheet_id,
            range=f"{HISTORY_SHEET}!A2:C",
        )
        .execute()
    )
    rows = result.get("values", [])
    scored = []
    for row in rows:
        name = row[0].strip() if len(row) > 0 else ""
        score_str = row[1].strip() if len(row) > 1 else ""
        hc_str = row[2].strip() if len(row) > 2 else ""
        if not name or not score_str:
            continue
        try:
            score = int(float(score_str))
            hc = int(float(hc_str)) if hc_str else 0
        except (ValueError, TypeError):
            continue
        scored.append({"name": name, "score": score, "new_handicap": hc})

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def get_last_round_date(sheet_id: str, token_info: dict) -> str:
    """Read the last round date from E1."""
    service = _sheets_service(token_info)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"{HISTORY_SHEET}!E1")
        .execute()
    )
    rows = result.get("values", [])
    if rows and rows[0]:
        return rows[0][0]
    return "No previous round"
