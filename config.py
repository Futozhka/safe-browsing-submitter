import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

FORM_URL = os.getenv("FORM_URL", "https://linkreclamatiogrwc.uk/")

CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY", "")

HEADLESS = os.getenv("HEADLESS", "1") == "1"

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".pdf"}
MAX_FILE_BYTES = 2 * 1024 * 1024

CONFIRMATION_TIMEOUT_MS = 60_000



# Ключевые слова для поиска полей на формах
# Используется для гибкого поиска, если ID полей изменятся
KEYWORDS = {
    "submission_type": ["submission type", "type"],
    "target_domain": ["domain", "site"],
    "target_ip": ["ip", "address"],
    "target_url": ["url", "link"],
    "threat_type": ["threat type", "threat"],
    "reason": ["reason", "details", "description", "why"],
    "accept_terms": ["accept terms", "terms and conditions", "i accept"],
    "submit_btn": ["submit", "send", "next"],
}