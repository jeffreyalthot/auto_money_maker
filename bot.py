from __future__ import annotations

import os
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

import requests
from difflib import SequenceMatcher
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait


@dataclass
class Counters:
    completed: int = 0
    failed: int = 0


class SurveyMemory:
    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qa_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_original TEXT NOT NULL,
                question_norm TEXT NOT NULL,
                response_text TEXT NOT NULL,
                response_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def remember(self, question: str, response: str, response_type: str) -> None:
        question = (question or "").strip()
        response = (response or "").strip()
        if not question or not response:
            return

        self.conn.execute(
            """
            INSERT INTO qa_logs(question_original, question_norm, response_text, response_type, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                question,
                normalize_text(question),
                response,
                response_type,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()

    def find_similar(self, question: str, response_type: str, threshold: float = 0.86) -> str | None:
        question_norm = normalize_text(question)
        if not question_norm:
            return None

        rows = self.conn.execute(
            """
            SELECT question_original, question_norm, response_text
            FROM qa_logs
            WHERE response_type = ?
            ORDER BY id DESC
            LIMIT 500
            """,
            (response_type,),
        ).fetchall()

        for row in rows:
            if row["question_norm"] == question_norm:
                return row["response_text"]

        best_score = 0.0
        best_answer = None
        for row in rows:
            score = SequenceMatcher(None, question_norm, row["question_norm"]).ratio()
            if score > best_score and score >= threshold:
                best_score = score
                best_answer = row["response_text"]
        return best_answer

    def close(self) -> None:
        self.conn.close()


def normalize_text(text: str) -> str:
    txt = unicodedata.normalize("NFKD", (text or "").lower())
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def download_model(model_url: str, model_path: Path) -> None:
    model_path.parent.mkdir(parents=True, exist_ok=True)
    if model_path.exists():
        print(f"[model] déjà présent: {model_path}")
        return

    print(f"[model] téléchargement: {model_url}")
    with requests.get(model_url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with model_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    print(f"[model] téléchargé: {model_path}")


def wait_and_fill(driver: webdriver.Firefox, by: By, selector: str, value: str, timeout: int = 20) -> None:
    field = WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, selector)))
    field.clear()
    field.send_keys(value)


def click_if_exists(driver: webdriver.Firefox, by: By, selector: str) -> bool:
    try:
        elem = driver.find_element(by, selector)
        elem.click()
        return True
    except NoSuchElementException:
        return False


def login_with_2fa(driver: webdriver.Firefox, login_url: str, email: str, password: str) -> None:
    driver.get(login_url)

    wait_and_fill(driver, By.NAME, "email", email)
    wait_and_fill(driver, By.NAME, "password", password)

    if not click_if_exists(driver, By.CSS_SELECTOR, "button[type='submit']"):
        raise RuntimeError("Impossible de trouver le bouton de connexion.")

    code = input("Entrez le code 2FA: ").strip()
    if code:
        filled = False
        for selector in ["input[name='2fa']", "input[name='otp']", "input[type='tel']", "input[type='number']"]:
            try:
                wait_and_fill(driver, By.CSS_SELECTOR, selector, code, timeout=10)
                filled = True
                break
            except TimeoutException:
                continue

        if not filled:
            print("[2FA] champ non détecté automatiquement, saisie manuelle nécessaire.")
        else:
            click_if_exists(driver, By.CSS_SELECTOR, "button[type='submit']")


def _safe_text(elem) -> str:
    for getter in ("text",):
        try:
            value = getattr(elem, getter)
            if value:
                return value.strip()
        except StaleElementReferenceException:
            return ""
    return ""


def _question_for_element(driver: webdriver.Firefox, elem) -> str:
    label = ""
    elem_id = elem.get_attribute("id")
    if elem_id:
        labels = driver.find_elements(By.CSS_SELECTOR, f"label[for='{elem_id}']")
        for lb in labels:
            label = _safe_text(lb)
            if label:
                return label

    try:
        parent = elem.find_element(By.XPATH, "./ancestor::*[self::label or self::fieldset or self::div][1]")
        if parent.tag_name.lower() == "fieldset":
            legends = parent.find_elements(By.TAG_NAME, "legend")
            if legends:
                txt = _safe_text(legends[0])
                if txt:
                    return txt

        label = _safe_text(parent)
        if label:
            return label
    except NoSuchElementException:
        pass

    for attr in ["aria-label", "placeholder", "name"]:
        value = (elem.get_attribute(attr) or "").strip()
        if value:
            return value
    return "question inconnue"


def _first_clickable(elements: Iterable) -> object | None:
    for elem in elements:
        try:
            if elem.is_displayed() and elem.is_enabled():
                return elem
        except StaleElementReferenceException:
            continue
    return None


def _text_answer_for_question(question: str, memory: SurveyMemory) -> str:
    remembered = memory.find_similar(question, "text")
    if remembered:
        return remembered

    q = normalize_text(question)
    if any(k in q for k in ["age", "quel age", "votre age"]):
        return "30"
    if any(k in q for k in ["ville", "residez", "region", "pays"]):
        return "Je vis en France."
    if any(k in q for k in ["travail", "profession", "emploi", "metier"]):
        return "Je travaille dans le secteur des services."
    if any(k in q for k in ["pourquoi", "commentaire", "avis", "opinion"]):
        return "Je trouve l'expérience globalement positive et cohérente avec mes besoins."
    return "Réponse cohérente avec la question." 


def _select_matching_option(select_elem, remembered: str) -> bool:
    options = [o for o in Select(select_elem).options if o.get_attribute("value")]
    if not options:
        return False

    if remembered:
        for opt in options:
            if normalize_text(opt.text) == normalize_text(remembered):
                opt.click()
                return True
        for opt in options:
            if normalize_text(remembered) in normalize_text(opt.text):
                opt.click()
                return True

    options[0].click()
    return True


def answer_current_survey(driver: webdriver.Firefox, memory: SurveyMemory) -> bool:
    answered = False

    radios = driver.find_elements(By.CSS_SELECTOR, "input[type='radio']")
    grouped: dict[str, list] = {}
    for radio in radios:
        name = radio.get_attribute("name") or f"__radio_{len(grouped)}"
        grouped.setdefault(name, []).append(radio)

    for group in grouped.values():
        candidate = _first_clickable(group)
        if not candidate:
            continue
        question = _question_for_element(driver, candidate)
        remembered = memory.find_similar(question, "radio")

        chosen = None
        if remembered:
            for opt in group:
                opt_label = _question_for_element(driver, opt)
                if normalize_text(opt_label) == normalize_text(remembered):
                    chosen = opt
                    break
        if chosen is None:
            chosen = candidate

        try:
            chosen.click()
            answer_text = _question_for_element(driver, chosen)
            memory.remember(question, answer_text, "radio")
            answered = True
        except ElementClickInterceptedException:
            pass

    for checkbox in driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox']"):
        try:
            if not checkbox.is_displayed() or not checkbox.is_enabled():
                continue
            question = _question_for_element(driver, checkbox)
            remembered = memory.find_similar(question, "checkbox")
            should_check = remembered.lower() == "true" if remembered else True
            if checkbox.is_selected() != should_check:
                checkbox.click()
            memory.remember(question, str(should_check), "checkbox")
            answered = True
        except StaleElementReferenceException:
            continue
        except ElementClickInterceptedException:
            continue

    for text_input in driver.find_elements(By.CSS_SELECTOR, "textarea, input[type='text'], input[type='email'], input[type='number']"):
        try:
            if not text_input.is_displayed() or not text_input.is_enabled():
                continue
            question = _question_for_element(driver, text_input)
            answer = _text_answer_for_question(question, memory)
            text_input.clear()
            text_input.send_keys(answer)
            memory.remember(question, answer, "text")
            answered = True
        except StaleElementReferenceException:
            continue

    for select_elem in driver.find_elements(By.TAG_NAME, "select"):
        try:
            if not select_elem.is_displayed() or not select_elem.is_enabled():
                continue
            question = _question_for_element(driver, select_elem)
            remembered = memory.find_similar(question, "select")
            if _select_matching_option(select_elem, remembered):
                selected = Select(select_elem).first_selected_option.text.strip()
                memory.remember(question, selected, "select")
                answered = True
        except StaleElementReferenceException:
            continue

    submit_buttons = driver.find_elements(
        By.XPATH,
        "//button[@type='submit' or contains(translate(., 'SUIVANTCONTINUERTERMINERNEXTSUBMIT', 'suivantcontinuerterminernextsubmit'), 'suivant') or contains(translate(., 'SUIVANTCONTINUERTERMINERNEXTSUBMIT', 'suivantcontinuerterminernextsubmit'), 'continuer') or contains(translate(., 'SUIVANTCONTINUERTERMINERNEXTSUBMIT', 'suivantcontinuerterminernextsubmit'), 'next') or contains(translate(., 'SUIVANTCONTINUERTERMINERNEXTSUBMIT', 'suivantcontinuerterminernextsubmit'), 'submit') or contains(translate(., 'SUIVANTCONTINUERTERMINERNEXTSUBMIT', 'suivantcontinuerterminernextsubmit'), 'terminer')]|//input[@type='submit']",
    )
    submit = _first_clickable(submit_buttons)
    if submit:
        try:
            submit.click()
            answered = True
            time.sleep(1.2)
        except ElementClickInterceptedException:
            pass

    return answered


def run_surveys(driver: webdriver.Firefox, survey_url: str, memory: SurveyMemory) -> None:
    counters = Counters()
    driver.get(survey_url)

    while True:
        survey_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='survey'], button[data-action*='survey']")
        target = _first_clickable(survey_links)
        if target:
            target.click()
            time.sleep(1.2)

        answered = answer_current_survey(driver, memory)
        if answered:
            counters.completed += 1
            print(f"[survey] rempli | total completed={counters.completed} failed={counters.failed}")
        else:
            counters.failed += 1
            print(f"[survey] aucun champ détecté | total completed={counters.completed} failed={counters.failed}")

        if not target:
            break

        driver.get(survey_url)
        time.sleep(1)


def main() -> None:
    load_dotenv()

    email = os.getenv("EMAIL", "")
    password = os.getenv("PASSWORD", "")
    login_url = os.getenv("LOGIN_URL", "")
    survey_url = os.getenv("SURVEY_URL", "")
    model_url = os.getenv("MODEL_URL", "")
    model_path = Path(os.getenv("MODEL_PATH", "models/model.gguf"))
    db_path = Path(os.getenv("DB_PATH", "database.db"))

    if not all([email, password, login_url, survey_url]):
        raise RuntimeError("Variables .env manquantes.")

    if model_url:
        download_model(model_url, model_path)

    options = Options()
    driver = webdriver.Firefox(options=options)
    memory = SurveyMemory(db_path)

    try:
        login_with_2fa(driver, login_url, email, password)
        time.sleep(2)
        run_surveys(driver, survey_url, memory)
    finally:
        memory.close()
        driver.quit()


if __name__ == "__main__":
    main()
