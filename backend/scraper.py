"""
Intelligent Golf scraper for MOTH's Rollup.
Uses httpx only — no browser required.

Flow:
1. GET login page to extract CSRF token
2. POST credentials to login.php
3. Accept T&C consent if needed
4. GET memberbooking page for the target date
5. Find the rollup whose contact name contains "MOTH"
6. Return the signed-up player names
"""

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
        page_lower = resp.text.lower()
        # Check for login failure
        if "login" in str(resp.url).lower() and "memberbooking" not in str(resp.url).lower():
            raise Exception(
                "Login failed. Please check your username and PIN."
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

        # Step 5: Find the MOTH's rollup by contact name
        soup = BeautifulSoup(resp.text, "html.parser")
        # DEBUG - remove after testing
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Booking URL requested: {resp.url}")
        logger.error(f"Page title: {soup.find('title').get_text() if soup.find('title') else 'no title'}")
        rollups_found = soup.find_all("div", class_="isRollup")
        logger.error(f"isRollup divs found: {len(rollups_found)}")
        rollup_wrappers = soup.find_all("div", class_="isRollup")

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

            # Match on contact name containing MOTH
            if contact_div and "MOTH" in contact_div.get_text().upper():
                if not signed_up_div:
                    raise Exception(
                        "Found MOTH's Rollup but no players have signed up yet."
                    )
                italic = signed_up_div.find("i")
                if not italic:
                    raise Exception(
                        "Found MOTH's Rollup but could not parse player names."
                    )
                signed_up_text = italic.get_text(strip=True)
                names = [n.strip() for n in signed_up_text.split(",") if n.strip()]
                if not names:
                    raise Exception(
                        "Found MOTH's Rollup but the signed-up list is empty."
                    )
                return names

        raise Exception(
            f"Could not find MOTH's Rollup on the booking page for {date_str}. "
            "The rollup may not be scheduled for this date."
        )
