import asyncio
import re
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright

import config
from two_captcha import TwoCaptcha, TwoCaptchaError

logger = logging.getLogger(__name__)
_captcha = TwoCaptcha(config.CAPTCHA_API_KEY)
_SEQUENCE_LOCK = asyncio.Lock()

_INJECT_TOKEN_JS = """
(token) => {
    const els = document.querySelectorAll('textarea[name="g-recaptcha-response"], textarea#g-recaptcha-response');
    els.forEach(e => { e.value = token; });
    return true;
}
"""

async def run_job(job: dict, runs_dir: Path):
    async with _SEQUENCE_LOCK:
        await _run_job_inner(job, runs_dir)

async def _run_job_inner(job: dict, runs_dir: Path):
    logger.info(f"Запуск задания {job['id']}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=config.HEADLESS)
        try:
            for item in job["items"]:
                if item["status"] != "pending":
                    continue
                logger.info(f"Строка {item['index']}: {item['target']} - Старт")
                item["status"] = "in_progress"
                await _submit_item(browser, job, item, runs_dir / job["id"])
        finally:
            await browser.close()
    job["finished"] = True
    logger.info(f"Задание {job['id']} завершено")

async def _submit_item(browser, job: dict, item: dict, job_dir: Path):
    params = job["params"]
    page = await browser.new_page()
    try:
        await _open_form(page)
        await _set_submission_type(page, params["submission_type"])
        await page.wait_for_timeout(1500) 
        await _fill_target(page, params["submission_type"], item["target"])
        await _fill_threat_type(page, params["threat_type"])
        await page.wait_for_timeout(1000)
        await _fill_reason(page, params["reason"])
        if params.get("attachment"):
            await _upload_attachment(page, params["attachment"])
        
        # Ставим галочку до капчи (или после, но перед сабмитом)
        # Лучше ставить до, чтобы не пропустить
        await _accept_terms(page) 
        
        await _solve_captcha(page)
        
        code = await _submit_and_get_code(page)
        
        # Скриншот результата всегда
        job_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = job_dir / f"result_{item['index']}.png"
        await page.screenshot(path=str(screenshot_path))
        logger.info(f"Скриншот результата сохранен: {screenshot_path}")

        item["status"] = "done"
        item["code"] = code
        item["error"] = None
        
    except Exception as e:
        await _save_error(page, job_dir, item, e)
        item["status"] = "error"
        item["error"] = str(e)
    finally:
        await page.close()

async def _open_form(page):
    try:
        await page.goto(config.FORM_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_selector("select, #threat_type_dd", state="attached", timeout=15000)
    except Exception as e:
        raise Exception(f"Ошибка сети при открытии формы: {e}")

async def _set_submission_type(page, value: str):
    sel = page.locator("select").first
    if await sel.count() == 0:
        btn = page.locator("label:has-text('Submission Type')").locator("button")
        if await btn.count():
            sel = btn.first
    try:
        await sel.select_option(label=value)
    except:
        if value in "DOMAINIPURL":
            await sel.select_option(value)
        else:
             await sel.click()

async def _fill_target(page, type_val: str, val: str):
    lbl = page.locator(f"label:has-text('{type_val}')")
    if await lbl.count() > 0:
        fid = await lbl.first.get_attribute('for')
        if fid:
            inp = page.locator(f"[id='{fid}']")
        else:
             inp = lbl.locator("input")
        if await inp.count() > 0:
            await inp.first.fill(val)
            return
             
    inputs = page.locator("input[type='text'], input[type='url']")
    if await inputs.count() == 0:
        inputs = page.locator("input")
        
    if await inputs.count() > 0:
        inp = inputs.first
        await inp.wait_for(state="visible", timeout=5000)
        await inp.fill(val)
    else:
        raise Exception(f"Не найден инпут для ввода цели (Тип: {type_val})")

async def _fill_threat_type(page, value: str):
    trigger = page.locator("#threat_type_dd")
    if await trigger.count() == 0:
        trigger = page.locator("label:has-text('Threat')").locator("button, select")
    if await trigger.count() == 0:
        sels = page.locator("select")
        if await sels.count() > 1:
            trigger = sels.nth(1)
        else:
            trigger = page.locator("select").filter(has_text="threat")
            
    if await trigger.count() == 0:
         raise Exception("Элемент Threat Type не найден")

    node = await trigger.element_handle()
    tag = await node.evaluate("node => node.tagName.toLowerCase()")

    if tag == "select":
        await trigger.select_option(label=value)
    else:
        logger.info("Открываем кастомный список Threat Type...")
        await trigger.click()
        await page.wait_for_timeout(800)
        
        dropdown_list = page.locator("div.dd") 
        if await dropdown_list.count() > 0:
            option_to_click = dropdown_list.locator("div:has-text('" + value + "')")
            if await option_to_click.count() > 0:
                 await option_to_click.first.click()
                 await page.wait_for_timeout(1000)
                 return

        options_2 = page.locator("div, li").filter(has_text=re.compile(value, re.I)).filter(has_not_text=re.compile("Type|Reason|Domain", re.I))
        if await options_2.count() == 0:
             raise Exception(f"Опция '{value}' не найдена в меню")
        await options_2.first.click()
        logger.info(f"Выбрана опция: {value}")
        await page.wait_for_timeout(1000)

async def _fill_reason(page, text: str):
    lbl = page.locator(f"label:has-text('Reason')")
    inp = None
    if await lbl.count() > 0:
        fid = await lbl.first.get_attribute('for')
        if fid: inp = page.locator(f"[id='{fid}']")
    if not inp or await inp.count() == 0:
        inp = page.locator("textarea").first
        if await inp.count() == 0: raise Exception("Не найдено поле Reason")
    await inp.fill(text)

async def _upload_attachment(page, path: str):
    inp = page.locator("input[type='file']")
    if await inp.count() == 0: raise Exception("Не найден инпут файла")
    await inp.set_input_files(path)

async def _accept_terms(page):
    # Поиск чекбокса "I accept..."
    # Ищем все чекбоксы на странице
    chks = page.locator("input[type='checkbox']")
    count = await chks.count()
    
    if count == 0:
        logger.warning("На странице не найдено ни одного чекбокса!")
        return

    found_and_checked = False
    
    # Проходим по каждому чекбосу, ищем связанный лейбл
    for i in range(count):
        chk = chks.nth(i)
        if await chk.is_checked():
            logger.info(f"Чекбокс {i} уже отмечен! (ID: {await chk.get_attribute('id')})")
            # В редких случаях их несколько, и это не тот. Продолжаем поиск, но ставим флаг
            found_and_checked = True
            
        chk_id = await chk.get_attribute('id')
        target_label_text = None
        
        # 1. Пытаемся найти лейбл с attribute for=""
        if chk_id:
            lbl = page.locator(f"label[for='{chk_id}']")
            if await lbl.count() > 0:
                target_label_text = await lbl.first.inner_text()
        
        # 2. Если лейбла с ID не нашлось, ищем внутри родительского label или рядом
        if not target_label_text:
            lbl = page.locator(f"label:has(#{chk_id})") # иногда инпут внутри лейбла
            if await lbl.count() > 0:
                target_label_text = await lbl.first.inner_text()
            else:
                # Ищем просто близкий текст
                parent_lbl = chk.locator("xpath=ancestor::label[1]")
                if await parent_lbl.count() > 0:
                    target_label_text = await parent_lbl.first.inner_text()

        if target_label_text:
            txt_lower = target_label_text.lower()
            if "accept" in txt_lower or "terms" in txt_lower or "condition" in txt_lower:
                if not await chk.is_checked():
                    await chk.check()
                    logger.info(f"УСТАНОВЛЕНА ГАЛОЧКА: {target_label_text[:30]}...")
                    found_and_checked = True
                else:
                    logger.info(f"ГАЛОЧКА УЖЕ УСТАНОВЛЕНА: {target_label_text[:30]}...")
                    found_and_checked = True
                break
    
    # Fallback: Если тексты не нашлись (редко), просто ставим галочку на последний чекбокс (обычно это Terms)
    if not found_and_checked and count > 0:
        chk_last = chks.last
        if not await chk_last.is_checked():
            await chk_last.check()
            logger.warning("Uстановлена галочка на ПОСЛЕДНИЙ чекбокс (FALLBACK)")
            found_and_checked = True
            
    if found_and_checked:
        logger.info("Ожидание реакции интерфейса Terms...")
        await page.wait_for_timeout(500)
    else:
        logger.error("Чекбокс Terms НЕ УСТАНОВЛЕН!")

async def _solve_captcha(page):
    el = page.locator("[data-sitekey]")
    if await el.count():
        key = await el.get_attribute("data-sitekey")
        logger.info(f"Капча найдена: {key[:5]}...")
        try:
            token = await asyncio.get_event_loop().run_in_executor(
                None, _captcha.solve_recaptcha_v2, key, page.url
            )
            logger.info(f"Токен получен: {len(token)}")
            await page.evaluate(_INJECT_TOKEN_JS, token)
        except Exception as e:
             logger.error(f"Ошибка решения капчи: {e}")
             raise Exception(f"Ошибка капчи: {e}")

async def _submit_and_get_code(page) -> str:
    btn = page.locator("button[type='submit'], button:has-text('Submit')").last
    await btn.click()
    logger.info("Нажата кнопка Submit")
    
    # Ждем 5 сек, чтобы появилось сообщение
    await page.wait_for_timeout(5000)
    
    code = await _extract_code_from_text(page)
    if code: return code
    
    # Ждем еще 40 сек
    await page.wait_for_timeout(40000)
    
    code = await _extract_code_from_text(page)
    if code: return code

    snippet = (await page.inner_text("body"))[-500:]
    logger.warning(f"Код не найден. Фрагмент текста:\n{snippet}")
    return "SUCCESS-NO-CODE"

async def _extract_code_from_text(page) -> str:
    text = await page.inner_text("body")
    
    match_cf = re.search(r"CF-[A-Z0-9]{4,}", text)
    if match_cf:
        logger.info(f"Найден код CF: {match_cf.group(0)}")
        return match_cf.group(0)
        
    match_id = re.search(r"(?:Request|Submission|Order|Report)\s*(?:ID|Number|No)[:\s]+([A-Za-z0-9\-]{5,})", text, re.I)
    if match_id:
        logger.info(f"Найден Request ID: {match_id.group(1)}")
        return f"REQ-{match_id.group(1)}"
        
    match_plain = re.search(r"[\s:,](\d{6,})[\s:,\)\.]", text)
    if match_plain and len(match_plain.group(1)) >= 8:
         logger.info(f"Найден ID (число): {match_plain.group(1)}")
         return f"CODE-{match_plain.group(1)}"

    match_url = re.search(r"[?&](id|request|conf|id_report|submission_id)=([A-Za-z0-9\-]+)", page.url)
    if match_url:
        code = match_url.group(2)
        logger.info(f"Найден ID в URL: {code}")
        return f"URL-{code}"

    return None

async def _save_error(page, job_dir: Path, item: dict, exc):
    logger.error(f"Ошибка в строке {item['index']}: {exc}")
    job_dir.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(job_dir / f"err_{item['index']}.png"))
