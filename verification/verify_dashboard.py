from playwright.sync_api import sync_playwright, expect

def test_dashboard_gpio_controls():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Navigate to dashboard
        page.goto("http://127.0.0.1:8080")

        # Verify page title
        expect(page).to_have_title("Rover Dashboard (UART Relay)")

        # Verify GPIO Controls section exists
        expect(page.get_by_text("GPIO Controls")).to_be_visible()

        # Verify Servo Sliders
        servo1 = page.get_by_text("Servo 1").locator("..").locator("input[type=range]")
        expect(servo1).to_be_visible()

        # Interact with Servo 1 slider
        servo1.fill("45")
        expect(page.get_by_text("45°")).to_be_visible()

        # Verify Momentary Button
        momentary_btn = page.get_by_text("HOLD: HIGH Signal")
        expect(momentary_btn).to_be_visible()

        # Verify Blink Toggle
        blink_toggle = page.locator("#chk-blink")
        expect(blink_toggle).to_be_visible()

        # Toggle blink
        blink_toggle.check()

        # Wait for status update
        page.wait_for_timeout(1000)
        expect(page.locator("#status")).to_contain_text("Blink State: ON")

        # Take screenshot
        page.screenshot(path="verification/dashboard_gpio.png")
        print("Screenshot saved to verification/dashboard_gpio.png")

        browser.close()

if __name__ == "__main__":
    try:
        test_dashboard_gpio_controls()
        print("Verification script passed!")
    except Exception as e:
        print(f"Verification script failed: {e}")
        exit(1)
