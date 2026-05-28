# Bramley Rollup - backend/scraper.py
# Playwright-based scrapers for Intelligent Golf (bramleygolfclub.co.uk instance)

import re
from playwright.async_api import async_playwright


async def _login(page, ig_username: str, ig_pin: str,
               ig_url: str = "https://www.bramleygolfclub.co.uk"):
    """Shared login helper — logs into the club's IG instance."""
    await page.goto(f"{ig_url}/member/index.php")
    await page.fill('input[name="memberid"]', ig_username)
    await page.fill('input[name="pin"]', ig_pin)
    await page.click('input[type="submit"]')
    await page.wait_for_load_state("networkidle")

    # Handle consent/cookie page if it appears
    current = page.url
    if "consent" in current or "cookie" in current:
        try:
            await page.click('input[type="submit"], button[type="submit"]')
            await page.wait_for_load_state("networkidle")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# scrape_players — scrape the booking list for a specific date
# ---------------------------------------------------------------------------

async def scrape_players(
    ig_username: str,
    ig_pin: str,
    date_str: str,
    ig_search_term: str,
    ig_url: str = "https://www.bramleygolfclub.co.uk",
) -> dict:
    """
    Log in to the club's IG instance and scrape the booking list for a given date.

    date_str: dd-mm-yyyy format
    ig_search_term: contact name search term e.g. "MOTH"
    ig_url: base URL of the club's IG instance e.g. "https://www.bramleygolfclub.co.uk"

    Returns:
        {
            "names":     [str, ...],   # player names in booking order
            "tee_times": int,          # number of distinct tee time slots
            "tee_start": str,          # first tee time e.g. "08:00"
        }
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        )
        page = await context.new_page()
        try:
            await _login(page, ig_username, ig_pin, ig_url)

            # ── Navigate to booking sheet ────────────────────────────────────
            await page.goto(
                f"{ig_url}/memberbooking/"
                f"?date={date_str}&searchterm={ig_search_term}"
            )
            await page.wait_for_load_state("networkidle")

            # ── Extract player names ─────────────────────────────────────────
            name_elements = await page.query_selector_all(
                "td.booking-player a, .booking-name a, .player-name, "
                "td.members a, .member-name"
            )
            names = []
            for el in name_elements:
                text = (await el.inner_text()).strip()
                if text and text not in names:
                    names.append(text)

            # ── Extract tee time info ────────────────────────────────────────
            tee_time_elements = await page.query_selector_all(
                "td.tee-time, .booking-time, td.time, td.teetime"
            )
            tee_times_raw = []
            for el in tee_time_elements:
                text = (await el.inner_text()).strip()
                if text and re.match(r'\d{1,2}:\d{2}', text):
                    tee_times_raw.append(text)

            unique_tee_times = sorted(set(tee_times_raw))
            tee_start = unique_tee_times[0] if unique_tee_times else ""

            return {
                "names":     names,
                "tee_times": len(unique_tee_times),
                "tee_start": tee_start,
            }
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# scrape_whs_indices — scrape the club handicap index list
# ---------------------------------------------------------------------------

async def scrape_whs_indices(
    ig_username: str,
    ig_pin: str,
    ig_url: str = "https://www.bramleygolfclub.co.uk",
) -> dict:
    """
    Log in to the club's IG instance and scrape the full member WHS handicap
    index list from /hcaplist.php.

    ig_url: base URL of the club's IG instance
    Returns: {"indices": {"John Smith": 14.2, ...}}
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        )
        page = await context.new_page()
        try:
            await _login(page, ig_username, ig_pin, ig_url)

            # ── Navigate to handicap list ────────────────────────────────────
            await page.goto(
                f"{ig_url}/hcaplist.php?action=masterhcap&filter=&sort=0"
            )
            await page.wait_for_load_state("networkidle")

            # ── Parse all rows ───────────────────────────────────────────────
            # Structure: <table class="table table-striped">
            #   <tbody><tr>
            #     <td><a href="...">Player Name</a></td>
            #     <td style="text-align:center;">14.2</td>  ← may be <span> for away HC
            #   </tr></tbody>
            rows = await page.query_selector_all("table.table tbody tr")
            indices = {}
            for row in rows:
                name_el = await row.query_selector("td:first-child a")
                idx_el  = await row.query_selector("td:last-child")
                if not name_el or not idx_el:
                    continue
                name     = (await name_el.inner_text()).strip()
                idx_text = (await idx_el.inner_text()).strip()
                try:
                    indices[name] = float(idx_text)
                except ValueError:
                    pass  # skip malformed rows

            return {"indices": indices}
        finally:
            await browser.close()
