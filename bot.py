import requests
import logging
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from contextlib import contextmanager
import time
import os

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger()

# Конфигурация VK API
VK_GROUP_ID = "your id"  # Замените на ID вашей группы ВКонтакте
VK_ACCESS_TOKEN = "your token"
VK_API_VERSION = "5.131"

# URL Wildberries
URL = "https://www.wildberries.ru/"

@contextmanager
def get_webdriver(service_path):
    """Контекстный менеджер для управления WebDriver."""
    service = Service(service_path)
    options = webdriver.EdgeOptions()
    options.add_argument("start-maximized")
    options.add_argument("high-dpi-support=1")
    options.add_argument("force-device-scale-factor=2.0")
    driver = webdriver.Edge(service=service, options=options)
    try:
        yield driver
    finally:
        driver.quit()

def fetch_url_with_retries(url, retries=3, delay=2):
    """Загрузка URL с повторными попытками в случае неудачи."""
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            logger.warning(f"Попытка {attempt + 1} из {retries} не удалась: {e}")
            time.sleep(delay)
    raise Exception(f"Не удалось получить данные с {url} после {retries} попыток.")

def save_element_screenshot(element, file_path):
    """Сохраняет скриншот указанного элемента."""
    try:
        element.screenshot(file_path)
        logger.info(f"Скриншот элемента сохранен: {file_path}")
        return file_path
    except Exception as e:
        logger.error(f"Ошибка сохранения скриншота элемента: {e}")
        return None

def scrape_wildberries(category_url=None, max_products=10, driver_path=None, post_interval=300):
    """
    Извлечение данных о товарах с Wildberries.
    :param category_url: URL категории Wildberries.
    :param max_products: Максимальное количество товаров для извлечения.
    :param driver_path: Путь к WebDriver.
    :param post_interval: Интервал между публикациями в секундах.
    :return: Список товаров.
    """
    products = []
    target_url = category_url if category_url else URL

    with get_webdriver(driver_path) as driver:
        driver.get(target_url)
        try:
            product_cards = WebDriverWait(driver, 20).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".product-card__link"))
            )

            for index, card in enumerate(product_cards[:max_products]):
                try:
                    logger.info(f"Обрабатываем товар {index + 1}/{max_products}...")
                    product_link = card.get_attribute('href')

                    if not product_link:
                        logger.warning(f"Ссылка для товара {index + 1} недоступна. Пропускаем.")
                        continue

                    driver.execute_script("window.open(arguments[0]);", product_link)
                    driver.switch_to.window(driver.window_handles[-1])

                    WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".product-page__title"))
                    )

                    title = driver.find_element(By.CSS_SELECTOR, ".product-page__title").text.strip()

                    try:
                        price_element = WebDriverWait(driver, 20).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, ".price-block__wallet-price.red-price"))
                        )
                        raw_price = price_element.get_attribute("innerText").strip()
                        price = raw_price.replace("\u00a0", "").replace("₽", "").strip()
                    except Exception:
                        price = "Цена не найдена"

                    image_element = driver.find_element(By.CSS_SELECTOR, ".slide__content img")
                    high_quality_image = image_element.get_attribute("src").replace('small', 'ultra')

                    image_screenshot_path = save_element_screenshot(image_element, f"image_screenshot_{index + 1}.png")

                    product = {
                        "title": title,
                        "price": price,
                        "link": product_link,
                        "image": high_quality_image,
                        "screenshot": image_screenshot_path
                    }
                    products.append(product)

                    post_to_vk(product)

                    if index < max_products - 1:
                        logger.info(f"Ожидание {post_interval} секунд перед публикацией следующего товара...")
                        time.sleep(post_interval)

                    driver.close()
                    driver.switch_to.window(driver.window_handles[0])

                except Exception as e:
                    logger.error(f"Ошибка обработки товара {index + 1}: {e}")

        except Exception as e:
            logger.error(f"Ошибка парсинга категории: {e}")

    return products

def post_to_vk(product):
    """Публикация информации о товаре в группе ВКонтакте."""
    try:
        upload_url = requests.post(
            f"https://api.vk.com/method/photos.getWallUploadServer",
            params={
                "group_id": VK_GROUP_ID,
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION
            }
        ).json()["response"]["upload_url"]

        headers = {"User-Agent": "Mozilla/5.0"}

        # Загрузка изображения товара
        image_data = fetch_url_with_retries(product["image"]).content
        upload_response = requests.post(upload_url, files={"photo": ("image.jpg", image_data)}).json()

        # Загрузка скриншота страницы
        screenshot_data = open(product["screenshot"], "rb")
        screenshot_response = requests.post(upload_url, files={"photo": ("screenshot.png", screenshot_data)}).json()

        save_response = requests.post(
            f"https://api.vk.com/method/photos.saveWallPhoto",
            params={
                "group_id": VK_GROUP_ID,
                "photo": upload_response["photo"],
                "server": upload_response["server"],
                "hash": upload_response["hash"],
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION
            }
        ).json()

        photo_id = save_response["response"][0]["id"]
        owner_id = save_response["response"][0]["owner_id"]

        message = (f"🛒 **{product['title']}**\n"
                   f"🏷️ Цена: {product['price']} ₽\n"
                   f"{product['link']}\n\n"
                   f"_Цены могут отличаться, проверяйте в вашем городе._")

        requests.post(
            f"https://api.vk.com/method/wall.post",
            params={
                "owner_id": f"-{VK_GROUP_ID}",
                "from_group": 1,
                "message": message,
                "attachments": f"photo{owner_id}_{photo_id}",
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION
            }
        )

        logger.info(f"Товар '{product['title']}' успешно опубликован в группе ВКонтакте.")

    except Exception as e:
        logger.error(f"Ошибка публикации в ВКонтакте: {e}")

if __name__ == "__main__":
    driver_path = r"C:\\Users\\kuris\\Documents\\wildberries_vk\\msedgedriver.exe"
    categories = [
        "https://www.wildberries.ru/catalog/0/search.aspx?search=%D0%B4%D0%BE%20300%20%D1%80%D1%83%D0%B1%D0%BB%D0%B5%D0%B9%20%D1%82%D0%BE%D0%B2%D0%B0%D1%80%D1%8B",
    ]
    for category in categories:
        scrape_wildberries(category_url=category, max_products=4, driver_path=driver_path, post_interval=300)
