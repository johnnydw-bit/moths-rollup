"""
Intelligent Golf scraper for MOTH's Rollup.
Uses httpx only - no browser required.
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
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_param = dt.strftime("%d-%m-%Y")
    print(f"DEBUG: scrape_players called for {date_str} -> {date_param}", flush=True)

    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=30.0,
    ) as client:

        # Step 1: GET login page for CSRF token
        print("DEBUG: Step 1 - getting login page", flush=True)
        resp = await client.get(LOGIN_URL)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_input = soup.find("input", {"name": "_csrf_token"})
        if not csrf_input:
            raise Exception("Could not find CSRF token on login page.")
        csrf_token = csrf_input.get("value", "")
        print(f"DEBUG: CSRF token found OK", flush=True)

        # Step 2: POST login
        print("DEBUG: Step 2 - posting login", flush=True)
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
        print(f"DEBUG: After login POST, URL is: {resp.url}", flush=True)

        if str(resp.url).endswith("login.php"):
            raise Exception("Login failed. Please check your username and PIN.")

        # Step 3: Accept consent if needed
        if "ttbconsent" in str(resp.url):
            print("DEBUG: Step 3 - accepting consent", flush=True)
            resp = await client.get(f"{CONSENT_URL}?action=accept")
            resp.raise_for_status()
            print(f"DEBUG: After consent URL: {resp.url}", flush=True)

        # Step 4: GET booking page
        print(f"DEBUG: Step 4 - getting booking page for {date_param}", flush=True)
        resp = await client.get(
            BOOKING_URL,
            params={"date": date_param, "course": "1", "group": "1"},
        )
        resp.raise_for_status()
        print(f"DEBUG: Booking page URL: {resp.url}", flush=True)
        print(f"DEBUG: Response length: {len(resp.text)}", flush=True)
        print(f"DEBUG: Contains isRollup: {'isRollup' in resp.text}", flush=True)

        if "login" in str(resp.url).lower():
            raise Exception("Session expired or login failed.")

        # Step 5: Find MOTH's rollup
        soup = BeautifulSoup(resp.text, "html.parser")
        rollup_wrappers = soup.find_all("div", class_="isRollup")
        print(f"DEBUG: isRollup wrappers found: {len(rollup_wrappers)}", flush=True)

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
                print(f"DEBUG: Contact: {contact_div.get_text(strip=True)}", flush=True)

            if contact_div and "MOTH" in contact_div.get_text().upper():
                if not signed_up_div:
                    raise Exception("Found MOTH's Rollup but no players have signed up yet.")
                italic = signed_up_div.find("i")
                if not italic:
                    raise Exception("Found MOTH's Rollup but could not parse player names.")
                names = [n.strip() for n in italic.get_text(strip=True).split(",") if n.strip()]
                if not names:
                    raise Exception("Found MOTH's Rollup but the signed-up list is empty.")
                print(f"DEBUG: Found {len(names)} players", flush=True)
                return names

        raise Exception(
            f"Could not find MOTH's Rollup on the booking page for {date_str}. "
            "The rollup may not be scheduled for this date."
        )
