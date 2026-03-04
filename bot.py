from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


@dataclass
class Counters:
    share: int = 0
    bad: int = 0


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


def run_surveys(driver: webdriver.Firefox, survey_url: str) -> None:
    counters = Counters()
    driver.get(survey_url)

    print("Mode sûr actif: réponses automatiques désactivées. Validation humaine requise.")

    while True:
        action = input("[n] nouveau sondage / [q] quitter: ").strip().lower()
        if action == "q":
            break
        if action != "n":
            continue

        opened = click_if_exists(driver, By.CSS_SELECTOR, "a[href*='survey'], button[data-action*='survey']")
        if not opened:
            print("Aucun sondage détecté automatiquement. Ouvrez-en un manuellement dans le navigateur.")

        result = input("Résultat du sondage ([s]=rémunéré, [b]=non rémunéré): ").strip().lower()
        if result == "s":
            counters.share += 1
            print(f"1 share | total share={counters.share} bad={counters.bad}")
        else:
            counters.bad += 1
            print(f"1 bad | total share={counters.share} bad={counters.bad}")



def main() -> None:
    load_dotenv()

    email = os.getenv("EMAIL", "")
    password = os.getenv("PASSWORD", "")
    login_url = os.getenv("LOGIN_URL", "")
    survey_url = os.getenv("SURVEY_URL", "")
    model_url = os.getenv("MODEL_URL", "")
    model_path = Path(os.getenv("MODEL_PATH", "models/model.gguf"))

    if not all([email, password, login_url, survey_url]):
        raise RuntimeError("Variables .env manquantes.")

    if model_url:
        download_model(model_url, model_path)

    options = Options()
    driver = webdriver.Firefox(options=options)

    try:
        login_with_2fa(driver, login_url, email, password)
        time.sleep(2)
        run_surveys(driver, survey_url)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
