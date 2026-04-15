"""
DOM inspector — opens Edge, waits for you to log in to Sleeper,
intercepts GraphQL API calls, then dumps .player-meta-container structure.

HOW TO USE:
  1. Run this script — an Edge window will open
  2. If redirected to login, log in to Sleeper in that window
  3. You should land on your league's players page automatically
  4. The script will then print the DOM structure and API calls it intercepted
"""
import asyncio
import json
from playwright.async_api import async_playwright

LEAGUE_ID = "1347804040074919936"
URL = f"https://sleeper.com/leagues/{LEAGUE_ID}/players"
EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            executable_path=EDGE_EXE,
            args=["--no-sandbox", "--start-maximized"],
        )
        context = await browser.new_context(viewport=None)
        page = await context.new_page()

        # Capture all API responses
        graphql_responses = []
        async def on_response(response):
            url = response.url
            if "sleeper" in url and response.status == 200:
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        body = await response.json()
                        graphql_responses.append({"url": url, "body": body})
                        print(f"  [API] {url[:90]}")
                except Exception:
                    pass
        page.on("response", on_response)

        print("Opening browser...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)

        print("\n" + "="*60)
        print("INSTRUCTIONS:")
        print("  - An Edge browser window has opened.")
        print("  - If you see a login page, please log in to Sleeper.")
        print("  - After logging in you should land on your league's players page.")
        print("  - The script is waiting (up to 5 minutes) for the player list.")
        print("="*60 + "\n")

        # Wait for player-meta-container with a long timeout — no auto-close
        try:
            await page.wait_for_selector(
                ".player-meta-container",
                timeout=300000,  # 5 minutes
                state="visible",
            )
        except Exception as e:
            print(f"Timed out or error waiting for player list: {e}")
            print("Current URL:", page.url)
            # Dump classes for debugging
            classes = await page.evaluate("""
                () => [...new Set([...document.querySelectorAll('*')]
                    .flatMap(el => [...el.classList])
                    .filter(c => c.includes('player')))].sort()
            """)
            print("Player-related classes found on page:", classes)
            await browser.close()
            return

        print(f"\nPlayer list found! URL: {page.url}")
        await page.wait_for_timeout(2000)  # let remaining data load

        # --- Dump DOM structure ---
        containers = await page.query_selector_all(".player-meta-container")
        print(f"\n=== {len(containers)} .player-meta-container elements found ===\n")

        for i, c in enumerate(containers[:5]):
            text = await c.inner_text()
            html = await c.inner_html()
            print(f"--- Container {i+1} inner_text: {repr(text)}")
            print(f"--- Container {i+1} inner_html:\n{html[:600]}\n")

        # Full row structure
        if containers:
            row_html = await containers[0].evaluate("""el => {
                let p = el;
                for (let i = 0; i < 6; i++) {
                    p = p.parentElement;
                    if (!p) break;
                    if (p.className && (
                        p.className.includes('row') || p.className.includes('item') ||
                        p.className.includes('player') || p.tagName === 'LI'
                    )) return p.outerHTML.substring(0, 3000);
                }
                return el.parentElement?.outerHTML?.substring(0, 3000) || '';
            }""")
            print("=== Full player row HTML ===")
            print(row_html)

        # --- Dump intercepted GraphQL ---
        print(f"\n=== Intercepted {len(graphql_responses)} API responses ===")
        for r in graphql_responses[:10]:
            body_str = json.dumps(r["body"])
            print(f"\nURL: {r['url']}")
            print(f"Body (first 400 chars): {body_str[:400]}")

        await browser.close()
        print("\nDone.")

asyncio.run(main())
