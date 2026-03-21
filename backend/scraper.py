"""
Intelligent Golf scraper for MOTH's Rollup.
Uses httpx only - no browser required.
"""

from datetime import datetime
import logging
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

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
    Scrape MOTH's Rollup player names for the given date.

    Args:
        username: club website login email
        pin: 4-digit PIN
        date_str: date in YYYY-MM-DD format

    Returns:
        List of player name strings from the MOTH's rollup signed-up list.

    Raises:
        Exception with descriptive message on failure.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_param = dt.strftime("%d-%m-%Y")
    logger.error(f"DEBUG scrape_players called for date: {date_str} -> {date_param}")

    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=30.0,
    ) as client:

        # Step 1: GET login page to extract CSRF token
        logger.error("DEBUG Step 1: Getting login page")
        resp = await client.get(LOGIN_URL)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_input = soup.find("input", {"name": "_csrf_token"})
        if not csrf_input:
            raise Exception("Could not find CSRF token on login page.")
        csrf_token = csrf_input.get("value", "")
        logger.error(f"DEBUG CSRF token found: {csrf_token[:10]}...")

        # Step 2: POST login credentials
        logger.error("DEBUG Step 2: Posting login credentials")
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
        logger.error(f"DEBUG After login POST, URL is: {resp.url}")

        # Check for login failure
        if str(resp.url).endswith("login.php"):
            raise Exception("Login failed. Please check your username and PIN.")

        # Step 3: Accept T&C consent if redirected there
        if "ttbconsent" in str(resp.url):
            logger.error("DEBUG Step 3: Accepting T&C consent")
            resp = await client.get(f"{CONSENT_URL}?action=accept")
            resp.raise_for_status()
            logger.error(f"DEBUG After consent, URL is: {resp.url}")

        # Step 4: GET booking page for the target date
        logger.error(f"DEBUG Step 4: Getting booking page for {date_param}")
        resp = await client.get(
            BOOKING_URL,
            params={"date": date_param, "course": "1", "group": "1"},
        )
        resp.raise_for_status()
        logger.error(f"DEBUG Booking page URL: {resp.url}")
        logger.error(f"DEBUG Response length: {len(resp.text)}")
        logger.error(f"DEBUG Contains isRollup: {'isRollup' in resp.text}")

        # Check still logged in
        if "login" in str(resp.url).lower():
            raise Exception("Session expired or login failed.")

        # Step 5: Find the MOTH's rollup by contact name
        soup = BeautifulSoup(resp.text, "html.parser")
        rollup_wrappers = soup.find_all("div", class_="isRollup")
        logger.error(f"DEBUG isRollup wrappers found: {len(rollup_wrappers)}")

        if not rollup_wrappers:
            raise Exception(
                f"No rollups found on the booking page for {date_str}. "
                "Check the date is a Monday or Thursday."
            )

        for wrapper in rollup_wrappers:
            entrant_divs = wrapper.find_all("div", class_="rollup-entrants-list")
            contact_div = None
            signed_up_div = None
            for div in entrant_divs:
                t = div.get_text(strip=True)
                if "Roll up Contact" in t:
                    contact_div = div
                elif "Signed up" in t:
                    signed_up_div = div

            if contact_div:
                logger.error(f"DEBUG Contact div text: {contact_div.get_text(strip=True)}")

            if contact_div and "MOTH" in contact_div.get_text().upper():
                if not signed_up_div:
                    raise Exception("Found MOTH's Rollup but no players have signed up yet.")
                italic = signed_up_div.find("i")
                if not italic:
                    raise Exception("Found MOTH's Rollup but could not parse player names.")
                names = [n.strip() for n in italic.get_text(strip=True).split(",") if n.strip()]
                if not names:
                    raise Exception("Found MOTH's Rollup but the signed-up list is empty.")
                logger.error(f"DEBUG Found {len(names)} players: {names}")
                return names

        raise Exception(
            f"Could not find MOTH's Rollup on the booking page for {date_str}. "
            "The rollup may not be scheduled for this date."
        )
