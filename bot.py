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


RESTRICTED_AUTOMATION_HOST_KEYWORDS = (
    "coinpayu",
    "offerwall",
    "cpx",
    "bitlabs",
    "lootably",
)


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


def wait_and_fill_first(driver: webdriver.Firefox, selectors: Sequence[tuple[By, str]], value: str, timeout: int = 20) -> tuple[By, str]:
    last_error: Exception | None = None
    for by, selector in selectors:
        try:
            wait_and_fill(driver, by, selector, value, timeout=timeout)
            return by, selector
        except TimeoutException as exc:
            last_error = exc
            continue
    raise TimeoutException(f"Champ introuvable pour les sélecteurs: {selectors}") from last_error


def click_if_exists(driver: webdriver.Firefox, by: By, selector: str) -> bool:
    try:
        elem = driver.find_element(by, selector)
        elem.click()
        return True
    except NoSuchElementException:
        return False


def click_first_clickable(driver: webdriver.Firefox, selectors: Sequence[tuple[By, str]], timeout: int = 10) -> tuple[By, str] | None:
    for by, selector in selectors:
        try:
            button = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, selector)))
            button.click()
            return by, selector
        except TimeoutException:
            continue
    return None


def click_login_button_only(driver: webdriver.Firefox, timeout: int = 10) -> bool:
    """Clique uniquement sur le bouton de connexion classique (pas OAuth/social)."""
    oauth_keywords = (
        "google",
        "facebook",
        "apple",
        "github",
        "microsoft",
        "linkedin",
        "twitter",
        "x.com",
        "oauth",
        "sso",
    )
    login_keywords = (
        "sign in",
        "login",
        "log in",
        "connexion",
        "se connecter",
    )

    button_selectors = [
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, "input[type='submit']"),
        (By.XPATH, "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'sign in') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'log in') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'connexion') or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'se connecter')]"),
    ]

    candidates = []
    for by, selector in button_selectors:
        try:
            candidates.extend(WebDriverWait(driver, timeout).until(EC.presence_of_all_elements_located((by, selector))))
        except TimeoutException:
            continue

    for button in candidates:
        try:
            if not (button.is_displayed() and button.is_enabled()):
                continue
            label = normalize_text(
                " ".join(
                    filter(
                        None,
                        [
                            button.text,
                            button.get_attribute("value"),
                            button.get_attribute("aria-label"),
                            button.get_attribute("name"),
                            button.get_attribute("id"),
                            button.get_attribute("class"),
                        ],
                    )
                )
            )
            if not label:
                continue
            if any(k in label for k in oauth_keywords):
                continue
            if not any(k in label for k in login_keywords):
                continue

            button.click()
            return True
        except (StaleElementReferenceException, ElementClickInterceptedException):
            continue
    return False


def login_with_2fa(driver: webdriver.Firefox, login_url: str, email: str, password: str) -> None:
    driver.get(login_url)

    email_selectors = [
        (By.NAME, "email"),
        (By.NAME, "username"),
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[name*='user']"),
        (By.CSS_SELECTOR, "input[name*='login']"),
        (By.ID, "email"),
        (By.ID, "username"),
    ]
    password_selectors = [
        (By.NAME, "password"),
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.ID, "password"),
    ]
    try:
        wait_and_fill_first(driver, email_selectors, email)
        wait_and_fill_first(driver, password_selectors, password)

        if not click_login_button_only(driver):
            raise RuntimeError("Impossible de trouver le bouton de connexion.")
    except TimeoutException:
        print("[login] Connexion automatique indisponible. Passez en connexion manuelle.")
        wait_for_manual_step(
            "Connectez-vous manuellement, validez le 2FA si nécessaire puis tapez Y pour continuer: "
        )
        return

    two_fa_selectors = [
        "input[name='2fa']",
        "input[name='otp']",
        "input[name='code']",
        "input[name*='token']",
        "input[id*='otp']",
        "input[id*='2fa']",
        "input[type='tel']",
        "input[type='number']",
        "input[inputmode='numeric']",
    ]

    two_fa_field = None
    for selector in two_fa_selectors:
        try:
            candidate = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
            if candidate.is_displayed() and candidate.is_enabled():
                two_fa_field = candidate
                break
        except TimeoutException:
            continue

    if not two_fa_field:
        print("[2FA] Aucun champ OTP/2FA détecté: étape ignorée.")
        return

    code = (os.getenv("TWO_FA_CODE") or os.getenv("OTP_CODE") or "").strip() or input("Entrez le code 2FA/OTP: ").strip()
    if not code:
        print("[2FA] Aucun code fourni, saisie manuelle nécessaire.")
        wait_for_manual_step("Validez le 2FA manuellement puis tapez Y pour continuer: ")
        return

    two_fa_field.clear()
    two_fa_field.send_keys(code)
    if not click_login_button_only(driver, timeout=8):
        wait_for_manual_step("Finalisez la connexion manuellement puis tapez Y pour continuer: ")


def wait_for_manual_step(prompt: str) -> None:
    while True:
        value = input(prompt).strip().lower()
        if value == "y":
            return
        print("Réponse attendue: Y")


def get_survey_candidates(driver: webdriver.Firefox) -> list:
    selectors = [
        "a[href*='survey']",
        "a[href*='sondage']",
        "a[href*='offerwall']",
        "a[href*='lootably']",
        "a[href*='bitlabs']",
        "a[href*='cpx']",
        "button[data-action*='survey']",
        "button[class*='survey']",
        "[data-testid*='survey']",
    ]
    found = []
    for selector in selectors:
        found.extend(driver.find_elements(By.CSS_SELECTOR, selector))
    return found


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


def run_surveys(driver: webdriver.Firefox, survey_url: str, memory: SurveyMemory, max_surveys: int = 20) -> None:
    counters = Counters()
    driver.get(survey_url)

    while True:
        if counters.completed >= max_surveys:
            print(f"[survey] limite atteinte ({max_surveys}). Arrêt de la boucle.")
            break

        survey_links = get_survey_candidates(driver)
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


def enforce_safe_usage(login_url: str, survey_url: str) -> None:
    """Bloque l'automatisation des plateformes rémunérées et similaires.

    Le script reste utilisable pour des tests internes, formulaires propriétaires ou QA,
    mais ne doit pas servir à contourner les règles de plateformes tierces.
    """

    combined = normalize_text(f"{login_url} {survey_url}")
    if any(keyword in combined for keyword in RESTRICTED_AUTOMATION_HOST_KEYWORDS):
        raise RuntimeError(
            "Automatisation bloquée pour ce domaine cible. "
            "Utilisez ce script uniquement pour des formulaires/tests autorisés."
        )


def main() -> None:
    load_dotenv()

    email = os.getenv("EMAIL", "")
    password = os.getenv("PASSWORD", "")
    login_url = os.getenv("LOGIN_URL", "")
    survey_url = os.getenv("SURVEY_URL", "")
    model_url = os.getenv("MODEL_URL", "")
    model_path = Path(os.getenv("MODEL_PATH", "models/model.gguf"))
    db_path = Path(os.getenv("DB_PATH", "database.db"))
    max_surveys = int(os.getenv("MAX_SURVEYS", "20"))

    if not all([email, password, login_url, survey_url]):
        raise RuntimeError("Variables .env manquantes.")

    enforce_safe_usage(login_url, survey_url)

    if model_url:
        download_model(model_url, model_path)

    options = Options()
    driver = webdriver.Firefox(options=options)
    memory = SurveyMemory(db_path)

    try:
        login_with_2fa(driver, login_url, email, password)
        time.sleep(2)
        run_surveys(driver, survey_url, memory, max_surveys=max_surveys)
    finally:
        memory.close()
        driver.quit()


if __name__ == "__main__":
    main()
