from playwright.sync_api import sync_playwright


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.facebook.com/marketplace")
        print("Log in to Facebook in the browser window, then press Enter here...")
        input()
        context.storage_state(path="auth_state.json")
        browser.close()
        print("Saved auth_state.json")


if __name__ == "__main__":
    main()
