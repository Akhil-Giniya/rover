from playwright.sync_api import sync_playwright, expect

def test_dashboard_ui():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Navigate to dashboard
        try:
            page.goto("http://127.0.0.1:8080", timeout=30000)
        except Exception as e:
            print(f"Failed to load page: {e}")
            return

        # Wait for title
        expect(page).to_have_title("Rover Command Center")

        # Verify Key Elements
        expect(page.get_by_text("ROVER COMMAND")).to_be_visible()
        expect(page.get_by_text("Hardware Control")).to_be_visible()
        expect(page.get_by_text("System Logs")).to_be_visible()

        # Verify Modern styling via computed style checks
        # Check background color/gradient of body
        bg_color = page.evaluate("getComputedStyle(document.body).backgroundImage")
        print(f"Body Background: {bg_color}")

        # Check Card styling (blur)
        card_blur = page.evaluate("getComputedStyle(document.querySelector('.card')).backdropFilter")
        print(f"Card Blur: {card_blur}")

        # Take screenshot for visual inspection
        page.screenshot(path="verification/dashboard_modern.png", full_page=True)
        print("Screenshot saved to verification/dashboard_modern.png")

        browser.close()

if __name__ == "__main__":
    try:
        test_dashboard_ui()
    except Exception as e:
        print(f"Test failed: {e}")
        exit(1)
