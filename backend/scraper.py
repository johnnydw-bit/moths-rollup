"""
Intelligent Golf scraper for MOTH's Rollup.

Flow:
1. Navigate to www.bramleygolfclub.co.uk/memberbooking
2. Log in with username + PIN
3. Go to My Golf → Book a Tee Time
4. Select the target date
5. Find the "Moth's Rollup" booking block
6. Click "Show Tee Times"
7. Scrape and return player names
"""

import asyncio
import os
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/opt/render/project/src/.playwright"
from playwright.async_api import async_playwright, TimeoutError as PWTimeout


async def scrape_players(username: str, pin: str, date_str: str) -> list[str]:
    """
    Scrape player names from Intelligent Golf for the given date.

    Args:
        username: IG login email
        pin: 4-digit PIN
        date_str: date in YYYY-MM-DD format

    Returns:
        List of player name strings as they appear on the booking page.

    Raises:
        Exception with a descriptive message on failure.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            viewport={"width": 390, "height": 844},
        )
        page = await context.new_page()

        try:
            # Step 1: Load the booking page
            await page.goto(
                "https://www.bramleygolfclub.co.uk/memberbooking",
                wait_until="networkidle",
                timeout=30000,
            )

            # Step 2: Log in
            # Fill username
            await page.fill('input[name="username"], input[type="email"], #username', username)
            # Fill PIN
            await page.fill('input[name="pin"], input[type="password"], #pin', pin)
            # Submit
            await page.click('input[type="submit"], button[type="submit"], .login-btn')
            await page.wait_for_load_state("networkidle", timeout=15000)

            # Check for login failure
            error_el = await page.query_selector('.error, .alert-danger, .login-error')
            if error_el:
                msg = await error_el.inner_text()
                raise Exception(f"Login failed: {msg.strip()}")

            # Step 3: Navigate to My Golf → Book a Tee Time
            # Try clicking "My Golf" menu item
            await page.click('text=My Golf', timeout=10000)
            await page.click('text=Book a Tee Time', timeout=10000)
            await page.wait_for_load_state("networkidle", timeout=15000)

            # Step 4: Select the date
            # Intelligent Golf typically has a date picker or date navigation
            # Format needed may vary — try common formats
            from datetime import datetime
            dt = datetime.strptime(date_str, "%Y-%m-%d")

            # Look for a date input or date navigation links
            date_input = await page.query_selector('input[type="date"]')
            if date_input:
                await date_input.fill(date_str)
                await page.keyboard.press("Enter")
            else:
                # Try clicking a date link formatted as the day number
                # Navigate to the correct month first if needed
                day_str = str(dt.day)
                await page.click(f'text="{day_str}"', timeout=10000)

            await page.wait_for_load_state("networkidle", timeout=15000)

            # Step 5: Find Moth's Rollup block
            # Look for the booking block containing "Moth" (case-insensitive)
            rollup_block = await page.query_selector(
                ':has-text("Moth"), :has-text("MOTH"), :has-text("Rollup")'
            )
            if not rollup_block:
                raise Exception(
                    "Could not find MOTH's Rollup on the booking page for "
                    f"{date_str}. The round may not be booked yet."
                )

            # Step 6: Click "Show Tee Times" within that block
            show_btn = await rollup_block.query_selector(
                'text=Show Tee Times, text=Show tee times, button, a'
            )
            if show_btn:
                await show_btn.click()
                await page.wait_for_load_state("networkidle", timeout=10000)

            # Step 7: Scrape player names
            # Names are typically in table cells, list items, or divs
            # within the tee time rows
            names = []

            # Try common Intelligent Golf selectors for player names
            selectors = [
                ".tee-time-player",
                ".player-name",
                ".booking-player",
                "td.player",
                ".member-name",
            ]

            for sel in selectors:
                els = await page.query_selector_all(sel)
                if els:
                    for el in els:
                        txt = (await el.inner_text()).strip()
                        if txt and txt not in names:
                            names.append(txt)
                    break

            if not names:
                # Fallback: look for names near the Moth's Rollup heading
                # by getting all text in the booking block
                block_text = await page.query_selector_all(
                    '.tee-time-block, .booking-block, .sheet-booking'
                )
                for block in block_text:
                    text = await block.inner_text()
                    if "Moth" in text or "MOTH" in text:
                        # Extract lines that look like names
                        lines = [l.strip() for l in text.split('\n') if l.strip()]
                        for line in lines:
                            # Simple heuristic: 2+ words, not all caps labels
                            words = line.split()
                            if (
                                2 <= len(words) <= 4
                                and not line.isupper()
                                and not any(
                                    kw in line.lower()
                                    for kw in ["tee", "time", "book", "show", "moth", "rollup"]
                                )
                            ):
                                if line not in names:
                                    names.append(line)

            if not names:
                raise Exception(
                    "Found MOTH's Rollup but could not extract player names. "
                    "The page structure may have changed."
                )

            return names

        except PWTimeout as e:
            raise Exception(f"Page timed out: {str(e)}")
        finally:
            await browser.close()


def scrape_players_sync(username: str, pin: str, date_str: str) -> list[str]:
    """Synchronous wrapper for use in FastAPI background tasks."""
    return asyncio.run(scrape_players(username, pin, date_str))
