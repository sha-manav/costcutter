"""Shared Playwright helpers.

Both the demonstration generator and the baseline browser agent drive the
same browser stack, so any browser-side cost is identical between them.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from shadow.config import Config, get_config

LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage",
               "--disable-background-networking",
               "--disable-features=Translate,BackForwardCache"]


@contextmanager
def browser_page(cfg: Config | None = None, proxy_url: str | None = None,
                 record: bool = False) -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    cfg = cfg or get_config()
    with sync_playwright() as pw:
        launch: dict[str, Any] = {
            "headless": cfg.browser.headless,
            "args": list(LAUNCH_ARGS),
            "slow_mo": cfg.browser.slow_mo_ms,
        }
        if cfg.browser.executable_path:
            launch["executable_path"] = cfg.browser.executable_path
        if proxy_url:
            launch["proxy"] = {"server": proxy_url}
        browser = pw.chromium.launch(**launch)
        w, h = cfg.browser.viewport
        context = browser.new_context(viewport={"width": w, "height": h},
                                      ignore_https_errors=True)
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


def login(page: Any, cfg: Config | None = None) -> None:
    """Log into the desk UI. Idempotent."""
    cfg = cfg or get_config()
    page.goto(f"{cfg.app.base_url}/login", wait_until="domcontentloaded")
    if "/app" in page.url:
        return
    page.fill("#login_email", cfg.app.username)
    page.fill("#login_password", cfg.app.password)
    page.click("button.btn-login")
    page.wait_for_url("**/app**", timeout=45000)
    page.wait_for_load_state("networkidle")


def goto_app(page: Any, route: str, cfg: Config | None = None) -> None:
    cfg = cfg or get_config()
    page.goto(f"{cfg.app.base_url}/app/{route.lstrip('/')}",
              wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
