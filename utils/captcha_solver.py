"""
CapSolver API integration for automatic reCAPTCHA v2 solving.
Used by DataIngestionAgent to automate Overleaf login.
"""

import time
import logging
import requests
from config import Config

logger = logging.getLogger("CaptchaSolver")


def solve_recaptcha_v2(page_url: str, site_key: str) -> str | None:
    """
    Sends a reCAPTCHA v2 challenge to CapSolver and returns the solution token.
    Returns None if CAPSOLVER_API_KEY is not set or solving fails.

    Args:
        page_url: The URL of the page containing the reCAPTCHA
        site_key: The reCAPTCHA site key extracted from the page
    """
    if not Config.CAPSOLVER_API_KEY:
        logger.warning("CAPSOLVER_API_KEY not set. Cannot auto-solve CAPTCHA.")
        return None

    try:
        # Step 1: Create task
        create_response = requests.post(
            "https://api.capsolver.com/createTask",
            json={
                "clientKey": Config.CAPSOLVER_API_KEY,
                "task": {
                    "type": "ReCaptchaV2TaskProxyLess",
                    "websiteURL": page_url,
                    "websiteKey": site_key
                }
            },
            timeout=15
        )
        create_data = create_response.json()

        if create_data.get("errorId", 0) != 0:
            logger.error(
                "CapSolver createTask error: %s", create_data.get("errorDescription")
            )
            return None

        task_id = create_data.get("taskId")
        logger.info("CapSolver task created: %s. Waiting for solution...", task_id)

        # Step 2: Poll for result (max 120 seconds)
        for attempt in range(24):
            time.sleep(5)
            result_response = requests.post(
                "https://api.capsolver.com/getTaskResult",
                json={
                    "clientKey": Config.CAPSOLVER_API_KEY,
                    "taskId": task_id
                },
                timeout=15
            )
            result_data = result_response.json()

            if result_data.get("status") == "ready":
                token = result_data.get("solution", {}).get("gRecaptchaResponse")
                logger.info("CapSolver returned token successfully.")
                return token

            if result_data.get("errorId", 0) != 0:
                logger.error(
                    "CapSolver getTaskResult error: %s",
                    result_data.get("errorDescription")
                )
                return None

        logger.error("CapSolver timed out after 120 seconds.")
        return None

    except Exception as e:
        logger.error("CapSolver request failed: %s", str(e))
        return None


def inject_recaptcha_token(page, token: str) -> None:
    """
    Injects a solved reCAPTCHA token into the page and triggers the callback.
    Works with both standard reCAPTCHA v2 widget implementations.
    """
    page.evaluate(f'''
        document.querySelector('[name="g-recaptcha-response"]').innerHTML = "{token}";
        if (typeof grecaptcha !== "undefined") {{
            var response = grecaptcha.getResponse();
            if (!response) {{
                document.querySelector('[name="g-recaptcha-response"]').value = "{token}";
            }}
        }}
    ''')
