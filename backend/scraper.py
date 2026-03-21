"""
Intelligent Golf scraper for MOTH's Rollup.
Uses httpx only — no browser required.

Flow:
1. GET login page to extract CSRF token
2. POST credentials to login.php
3. Accept T&C consent
4. GET memberbooking page for the target date
5. Parse rollup entrants from the HTML
"""

import re
from datetime import datetime
import httpx
from bs4 import BeautifulSoup


BASE_URL = "https://www.bramleygolfclub.co.uk"
LOGIN_URL = f"{BASE_URL}/login.php"
CONSENT_URL = f"{BASE_URL}/ttbconsent.php"
BOOKING_URL = f"{BASE_URL}/memberbooking/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


async def scrape_players(username: str, pin: str, date_str: str) -> list[str]:
    """
    Scrape rollup player names for the given date.

    Args:
        username: club website login email
        pin: 4-digit PIN
        date_str: date in YYYY-MM-DD format

    Returns:
        List of player name strings from the rollup entrants list.

    Raises:
        Exception with descriptive message on failure.
    """
    # Convert date from YYYY-MM-DD to DD-MM-YYYY for the booking URL
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_param = dt.strftime("%d-%m-%Y")

    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=30.0,
    ) as client:

        # Step 1: GET login page to extract CSRF token
        resp = await client.get(LOGIN_URL)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_input = soup.find("input", {"name": "_csrf_token"})
        if not csrf_input:
            raise Exception(
                "Could not find CSRF token on login page. "
                "The login page structure may have changed."
            )
        csrf_token = csrf_input.get("value", "")

        # Step 2: POST login credentials
        login_data = {
            "task": "login",
            "topmenu": "1",
            "memberid": username,
            "pin": pin,
            "cachemid": "1",
            "_csrf_token": csrf_token,
            "Submit": "Login",
        }
        resp = await client.post(LOGIN_URL, data=login_data)
        resp.raise_for_status()

        # Check for login failure
        if "Invalid" in resp.text or "incorrect" in resp.text.lower():
            raise Exception(
                "Login failed. Please check your Intelligent Golf "
                "username and PIN."
            )

        # Step 3: Accept T&C consent if redirected there
        if "ttbconsent" in str(resp.url):
            resp = await client.get(f"{CONSENT_URL}?action=accept")
            resp.raise_for_status()

        # Step 4: GET booking page for the target date
        resp = await client.get(
            BOOKING_URL,
            params={"date": date_param, "course": "1", "group": "1"},
        )
        resp.raise_for_status()

        # Check still logged in
        if "login" in str(resp.url).lower():
            raise Exception(
                "Session expired or login failed. "
                "Please check your credentials."
            )

        # Step 5: Parse rollup entrants
        soup = BeautifulSoup(resp.text, "html.parser")

        # Find the rollup entrants list — looks for any isRollup block
        # containing MOTH or Saturday Morning rollup
        names = []

        # First try: look for rollup-entrants-list divs inside isRollup blocks
        rollup_wrappers = soup.find_all("div", class_="isRollup")
        for wrapper in rollup_wrappers:
            # Check it's a MOTH/Saturday Morning rollup, not Ladies etc.
            comp_name = wrapper.find("span", class_="comp-name-text")
            if comp_name:
                name_text = comp_name.get_text(strip=True).lower()
                # Skip Ladies rollup
                if "lady" in name_text or "ladies" in name_text:
                    continue

            entrants_div = wrapper.find("div", class_="rollup-entrants-list")
            if entrants_div:
                # Look for the "Signed up:" div
                italic = entrants_div.find("i")
                if italic:
                    signed_up_text = italic.get_text(strip=True)
                    if signed_up_text:
                        raw_names = [
                            n.strip()
                            for n in signed_up_text.split(",")
                            if n.strip()
                        ]
                        names.extend(raw_names)

        if names:
            # Deduplicate while preserving order
            seen = set()
            unique_names = []
            for name in names:
                if name.lower() not in seen:
                    seen.add(name.lower())
                    unique_names.append(name)
            return unique_names

        # Fallback: check if rollup exists at all for this date
        if soup.find("div", class_="isRollup"):
            raise Exception(
                "Found a rollup on the booking page but could not extract "
                "player names. The page structure may have changed."
            )

        raise Exception(
            f"No rollup found on the booking page for {date_str}. "
            "The rollup may not be scheduled for this date, or players "
            "may not have signed up yet."
        )
