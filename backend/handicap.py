"""
Handicap calculation for MOTH's Rollup.

Adjustment table (Stableford score → handicap change):
  0-17  → +2
  18-29 → +1
  30-37 → 0
  38-42 → -1
  43+   → -2

Additionally, the round winner (highest score) gets an extra -1.
If there is a tie for top score, only the first occurrence in the
player list (as returned by Intelligent Golf) gets the winner bonus.
"""

def get_adjustment(score: int) -> int:
    """Return handicap adjustment for a given Stableford score."""
    if score <= 17:
        return 2
    elif score <= 29:
        return 1
    elif score <= 37:
        return 0
    elif score <= 42:
        return -1
    else:
        return -2


def calculate_new_handicaps(players: list[dict]) -> list[dict]:
    """
    Given a list of player dicts with keys:
        name: str
        handicap: int
        score: int | None

    Returns the same list with an added key:
        new_handicap: int | None  (None if no score entered)
        adjustment: int | None
        winner: bool

    Only players with a score are ranked for the winner bonus.
    """
    # Find the winner — highest score among players who have a score
    scored = [p for p in players if p.get("score") is not None]

    winner_name = None
    if scored:
        max_score = max(p["score"] for p in scored)
        # First player in the list with the max score gets the winner bonus
        for p in scored:
            if p["score"] == max_score:
                winner_name = p["name"]
                break

    result = []
    for p in players:
        score = p.get("score")
        hc = p["handicap"]
        is_winner = (p["name"] == winner_name)

        if score is None:
            result.append({**p, "new_handicap": None, "adjustment": None, "winner": False})
            continue

        adj = get_adjustment(score)
        if is_winner:
            adj -= 1  # extra -1 for the winner

        new_hc = hc + adj
        # Handicap floor of 0
        new_hc = max(0, new_hc)

        result.append({
            **p,
            "adjustment": adj,
            "new_handicap": new_hc,
            "winner": is_winner,
        })

    return result


def format_adjustment(adj: int | None) -> str:
    """Return a display string like (+1), (-1), (0) or empty string."""
    if adj is None:
        return ""
    if adj > 0:
        return f"(+{adj})"
    elif adj < 0:
        return f"({adj})"
    else:
        return "(0)"
