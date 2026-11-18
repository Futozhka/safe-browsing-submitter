import time
import requests
import logging

logger = logging.getLogger(__name__)

class TwoCaptchaError(Exception):
    pass

class TwoCaptcha:
    BASE_URL = "https://2captcha.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.session.allow_redirects = True

    def solve_recaptcha_v2(self, googlekey: str, pageurl: str, timeout: int = 180) -> str:
        # 1. Отправка задачи
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/in.php",
                params={
                    "key": self.api_key,
                    "method": "userrecaptcha",
                    "googlekey": googlekey,
                    "pageurl": pageurl,
                    "json": 1
                },
                timeout=30
            )
        except Exception as e:
            raise TwoCaptchaError(f"Ошибка сети (отправка): {e}")

        if resp.status_code != 200:
            raise_TWO_captcha_error(f"2captcha вернул {resp.status_code}. Ответ: {resp.text[:50]}")
        
        try:
            data = resp.json()
        except:
            logger.error(f"Ошибка парсинга JSON: {resp.text[:100]}")
            raise TwoCaptchaError("Некорректный ответ при отправке задачи")

        if data.get("status") != 1:
            raise TwoCaptchaError(f"Ошибка сервиса: {data.get('request')}")

        captcha_id = data["request"]
        logger.info(f"Задача отправлена: ID {captcha_id}")

        # 2. Ожидание результата (используем res.php + POST, как в вашем примере)
        deadline = time.monotonic() + timeout
        attempt = 0
        
        while time.monotonic() < deadline:
            attempt += 1
            time.sleep(5)

            try:
                # Используем POST на res.php
                resp = self.session.post(
                    f"{self.BASE_URL}/res.php",
                    data={"key": self.api_key, "action": "get", "id": captcha_id, "json": 1},
                    timeout=30
                )
            except Exception as e:
                logger.warning(f"Ошибка сети при проверке {attempt}: {e}")
                continue

            if resp.status_code != 200:
                logger.warning(f"Статус {resp.status_code} при проверке (попытка {attempt})")
                continue
            
            if not resp.text:
                continue

            try:
                data = resp.json()
            except Exception:
                logger.warning(f"Ошибка JSON {attempt}: {resp.text[:50]}")
                continue
            
            code = data.get("status")
            text_request = data.get("request")
            
            if code == 1:
                logger.info(f"Решение получено за {attempt} попыток. Ответ: {text_request[:10]}...")
                return text_request
            if code == 0:
                logger.info(f"Капча решается... (попытка {attempt})")
                continue
            if code == 2:
                raise TwoCaptchaError("Некорректный request ID")
            if code == 3:
                raise TwoCaptchaError("Некорректный IP или лимит")
            raise TwoCaptchaError(f"Код ошибки {code}: {text_request}")

        raise TwoCaptchaError("Время ожидания истекло")
