import asyncio
import logging
import time
import random
from datetime import datetime
from typing import List, Tuple, Set, Optional
import re

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'channel_search_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

try:
    from seleniumbase import Driver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.keys import Keys
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from telethon import TelegramClient
    from telethon.errors import ChannelPrivateError, ChannelInvalidError
    from telethon.tl.functions.channels import GetFullChannelRequest
    import g4f
except ImportError as e:
    logger.error(f"Ошибка импорта библиотек: {e}")
    raise

# Глобальные переменные
search_active = False
found_channels: Set[str] = set()
driver = None
current_settings = {}
telethon_client = None
search_progress = {'current_keyword': '', 'current_topic': ''}

def setup_driver():
    """Настройка и инициализация веб-драйвера"""
    logger.info("Инициализация веб-драйвера...")
    
    try:
        # Создаем драйвер
        driver = Driver(uc=True, headless=False)
        driver.set_window_size(600, 1200)
        
        # Установка десктопного user-agent
        desktop_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {"userAgent": desktop_user_agent})
        
        # Проверка работоспособности драйвера
        driver.get("about:blank")
        logger.info("Веб-драйвер успешно инициализирован")
        
        return driver
    except Exception as e:
        logger.error(f"Ошибка инициализации драйвера: {e}")
        return None

def wait_and_find_element(driver, selectors, timeout=10):  # Уменьшили таймаут с 30 до 10
    """Поиск элемента по нескольким селекторам"""
    if isinstance(selectors, str):
        selectors = [selectors]
    
    for selector in selectors:
        try:
            if selector.startswith('//'):
                element = WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.XPATH, selector))
                )
            else:
                element = WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
            return element
        except TimeoutException:
            continue
        except Exception as e:
            logger.warning(f"Ошибка поиска элемента {selector}: {e}")
            continue
    
    return None

def wait_and_click_element(driver, selectors, timeout=10):  # Уменьшили таймаут с 30 до 10
    """Клик по элементу с ожиданием его доступности"""
    element = wait_and_find_element(driver, selectors, timeout)
    if element:
        try:
            # Ждем, пока элемент станет кликабельным
            if isinstance(selectors, str):
                selectors = [selectors]
            
            for selector in selectors:
                try:
                    if selector.startswith('//'):
                        clickable_element = WebDriverWait(driver, timeout).until(
                            EC.element_to_be_clickable((By.XPATH, selector))
                        )
                    else:
                        clickable_element = WebDriverWait(driver, timeout).until(
                            EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                        )
                    clickable_element.click()
                    return True
                except:
                    continue
        except Exception as e:
            logger.warning(f"Ошибка клика по элементу: {e}")
    
    return False

def navigate_to_channel_search(driver):
    """Навигация к странице поиска каналов"""
    try:
        logger.info("Переход на tgstat.ru...")
        driver.get("https://tgstat.ru/")
        time.sleep(2)  # Уменьшили с 3 до 2
        
        # Нажимаем на три полоски для открытия меню
        logger.info("Открываем меню...")
        menu_selectors = [
            'a.d-flex.d-lg-none.nav-user',
            '.nav-user',
            '[data-toggle="collapse"]',
            'i.uil-bars'
        ]
        
        if not wait_and_click_element(driver, menu_selectors, 5):  # Уменьшили с 10 до 5
            logger.warning("Не удалось найти кнопку меню, возможно меню уже открыто")
        
        time.sleep(1)  # Уменьшили с 2 до 1
        
        # Открываем выпадающее меню "Каталог"
        logger.info("Открываем каталог...")
        catalog_selectors = [
            '#topnav-catalog',
            'a[id="topnav-catalog"]',
            '.nav-link.dropdown-toggle',
            '//a[contains(text(), "Каталог")]'
        ]
        
        if not wait_and_click_element(driver, catalog_selectors, 5):  # Уменьшили с 10 до 5
            logger.error("Не удалось найти кнопку каталога")
            return False
        
        time.sleep(1)  # Уменьшили с 2 до 1
        
        # Нажимаем на "Поиск каналов"
        logger.info("Переходим к поиску каналов...")
        search_selectors = [
            'a[href="/channels/search"]',
            '//a[contains(text(), "Поиск каналов")]',
            '.dropdown-item[href="/channels/search"]'
        ]
        
        if not wait_and_click_element(driver, search_selectors, 5):  # Уменьшили с 10 до 5
            logger.error("Не удалось найти кнопку поиска каналов")
            return False
        
        time.sleep(2)  # Уменьшили с 3 до 2
        logger.info("Успешно перешли на страницу поиска каналов")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка навигации: {e}")
        return False

def search_channels(driver, keyword: str, topic: str, first_search: bool = False):
    """Поиск каналов по ключевому слову и теме"""
    try:
        logger.info(f"Поиск каналов по ключевому слову: '{keyword}', тема: '{topic}'")
        
        # Вводим ключевое слово
        keyword_input = wait_and_find_element(driver, [
            '#q',
            'input[name="q"]',
            '.form-control[name="q"]'
        ])
        
        if keyword_input:
            # Очищаем поле и вводим новое ключевое слово
            keyword_input.clear()
            time.sleep(0.5)  # Уменьшили с 1 до 0.5
            keyword_input.send_keys(keyword)
            logger.info(f"Введено ключевое слово: {keyword}")
        else:
            logger.error("Не удалось найти поле ввода ключевого слова")
            return []
        
        time.sleep(0.5)  # Уменьшили с 1 до 0.5
        
        # Вводим тему
        topic_input = wait_and_find_element(driver, [
            '.select2-search__field',
            'input[role="searchbox"]',
            '.select2-search input'
        ])
        
        if topic_input:
            topic_input.clear()
            time.sleep(0.5)  # Уменьшили с 1 до 0.5
            topic_input.send_keys(topic)
            time.sleep(1)  # Уменьшили с 2 до 1
            topic_input.send_keys(Keys.ENTER)
            logger.info(f"Введена тема: {topic}")
        else:
            logger.error("Не удалось найти поле ввода темы")
            return []
        
        time.sleep(1)  # Уменьшили с 2 до 1
        
        # Если это первый поиск, нужно отметить дополнительные опции
        if first_search:
            # Отмечаем "также искать в описании"
            description_checkbox = wait_and_find_element(driver, [
                '#inabout',
                'input[name="inAbout"]',
                '.custom-control-input[name="inAbout"]'
            ])
            
            if description_checkbox and not description_checkbox.is_selected():
                driver.execute_script("arguments[0].click();", description_checkbox)
                logger.info("Отмечен поиск в описании")
            
            time.sleep(0.5)  # Уменьшили с 1 до 0.5
            
            # Выбираем тип канала "публичный"
            channel_type_select = wait_and_find_element(driver, [
                '#channeltype',
                'select[name="channelType"]',
                '.custom-select[name="channelType"]'
            ])
            
            if channel_type_select:
                driver.execute_script("arguments[0].value = 'public';", channel_type_select)
                logger.info("Выбран тип канала: публичный")
            
            time.sleep(0.5)  # Уменьшили с 1 до 0.5
        
        # Нажимаем кнопку "Искать"
        search_button = wait_and_find_element(driver, [
            '#search-form-submit-btn',
            'button[type="button"].btn-primary',
            '.btn.btn-primary.w-100'
        ])
        
        if search_button:
            driver.execute_script("arguments[0].click();", search_button)
            logger.info("Нажата кнопка поиска")
        else:
            logger.error("Не удалось найти кнопку поиска")
            return []
       
        # Ждем загрузки результатов (уменьшили время ожидания)
        time.sleep(3)  # Уменьшили с 5 до 3
        
        # Извлекаем результаты
        channels = extract_channel_usernames(driver)
        logger.info(f"Найдено каналов: {len(channels)}")
        
        return channels
        
    except Exception as e:
        logger.error(f"Ошибка поиска каналов: {e}")
        return []

def extract_channel_usernames(driver) -> List[str]:
    """Извлечение юзернеймов каналов из результатов поиска"""
    usernames = []
    
    try:
        # Ждем появления результатов (уменьшили время ожидания)
        WebDriverWait(driver, 15).until(  # Уменьшили с 30 до 15
            EC.presence_of_element_located((By.CSS_SELECTOR, '.card.peer-item-row, .peer-item-row'))
        )
        
        # Ищем все карточки каналов
        channel_cards = driver.find_elements(By.CSS_SELECTOR, '.card.peer-item-row, .peer-item-row')
        
        if not channel_cards:
            logger.warning("Не найдено карточек каналов")
            return usernames
        
        for card in channel_cards:
            try:
                # Ищем ссылку на канал
                link_elements = card.find_elements(By.CSS_SELECTOR, 'a[href*="/channel/@"]')
                
                for link in link_elements:
                    href = link.get_attribute('href')
                    if href and '/channel/@' in href:
                        # Извлекаем username из ссылки
                        # Формат: https://tgstat.ru/channel/@username/stat
                        match = re.search(r'/channel/(@[^/]+)', href)
                        if match:
                            username = match.group(1)
                            if username not in usernames:
                                usernames.append(username)
                                logger.info(f"Найден канал: {username}")
                                break
                
            except Exception as e:
                logger.warning(f"Ошибка обработки карточки канала: {e}")
                continue
        
        return usernames
        
    except TimeoutException:
        logger.warning("Результаты поиска не загрузились за отведенное время")
        return usernames
    except Exception as e:
        logger.error(f"Ошибка извлечения юзернеймов: {e}")
        return usernames

async def check_channel_comments_available(client: TelegramClient, username: str) -> bool:
    """Проверка доступности комментариев в канале с использованием GetFullChannelRequest"""
    try:
        if not username.startswith('@'):
            username = '@' + username
        
        # Получаем информацию о канале
        entity = await client.get_entity(username)
        
        # Получаем полную информацию о канале
        full_channel = await client(GetFullChannelRequest(entity))
        
        # Проверяем, есть ли linked_chat_id
        if hasattr(full_channel.full_chat, 'linked_chat_id') and full_channel.full_chat.linked_chat_id:
            logger.info(f"Канал {username} имеет группу обсуждений (linked_chat_id: {full_channel.full_chat.linked_chat_id})")
            return True
        else:
            logger.info(f"Канал {username} не имеет группы обсуждений")
            return False
        
    except (ChannelPrivateError, ChannelInvalidError):
        logger.warning(f"Канал {username} недоступен")
        return False
    except Exception as e:
        logger.warning(f"Ошибка проверки канала {username}: {e}")
        return False

async def check_post_comments_available(client: TelegramClient, message) -> bool:
    """Проверка доступности комментариев под конкретным постом"""
    try:
        # Получаем информацию о канале
        entity = await client.get_entity(message.peer_id)
        
        # Получаем полную информацию о канале
        full_channel = await client(GetFullChannelRequest(entity))
        
        # Проверяем, есть ли linked_chat_id
        if hasattr(full_channel.full_chat, 'linked_chat_id') and full_channel.full_chat.linked_chat_id:
            # Проверяем, включены ли комментарии для этого поста
            if hasattr(message, 'replies') and message.replies:
                logger.info(f"Пост {message.id} в канале {entity.username} поддерживает комментарии")
                return True
            else:
                logger.info(f"Пост {message.id} в канале {entity.username} не поддерживает комментарии")
                return False
        else:
            logger.info(f"Канал {entity.username} не имеет группы обсуждений")
            return False
        
    except Exception as e:
        logger.warning(f"Ошибка проверки комментариев поста: {e}")
        return False

async def analyze_channel(channel_id: int) -> Tuple[List[str], List[str]]:
    """Анализ канала для определения тематики и ключевых слов"""
    try:
        # Получаем клиент из bot_interface
        import bot_interface
        client = bot_interface.bot_data.get('telethon_client')
        
        if not client:
            logger.error("Telethon клиент не инициализирован в bot_interface")
            return [], []
        
        # Получаем информацию о канале
        entity = await client.get_entity(channel_id)
        
        # Собираем информацию для анализа
        channel_info = []
        
        # Название канала
        if hasattr(entity, 'title'):
            channel_info.append(f"Название: {entity.title}")
        
        # Описание канала
        if hasattr(entity, 'about') and entity.about:
            channel_info.append(f"Описание: {entity.about}")
        
        # Получаем последние 20 постов БЕЗ ограничений
        posts_text = []
        async for message in client.iter_messages(entity, limit=20):
            if message.text:
                posts_text.append(message.text)  # Убрали ограничение [:500]
        
        # Добавляем ВСЕ 20 постов (убрали ограничение [:5])
        if posts_text:
            channel_info.append(f"Примеры постов: {' | '.join(posts_text)}")
        
        # Объединяем всю информацию
        full_text = "\n".join(channel_info)
        
        # Анализируем с помощью GPT-4
        prompt = f"""Ты эксперт по анализу контента и тематической классификации. На основе предоставленных данных канала определи:

1. Сгенерируй список ключевых слов, которые могут использоваться в названиях похожих каналов.  
- Ключевые слова должны быть напрямую связаны с основной темой канала и отражать его суть.  
- Исключи слова, которые упоминаются лишь косвенно или в единичных случаях (например, если канал про спорт, не включай "технологии" только потому, что один раз упомянули смарт-часы).  
- Ключевые слова должны быть конкретными, релевантными и подходить для использования в названиях каналов.  

2. Определи основную тему или темы канала из списка: ["Бизнес и стартапы", "Блоги", "Букмекерство", "Видео и фильмы", "Даркнет", "Дизайн", "Для взрослых", "Еда и кулинария", "Здоровье и Фитнес", "Игры", "Инстаграм", "Интерьер и строительство", "Искусство", "Картинки и фото", "Карьера", "Книги", "Криптовалюты", "Курсы и гайды", "Лингвистика", "Маркетинг, PR, реклама", "Медицина", "Мода и красота", "Музыка", "Новости и СМИ", "Образование", "Познавательное", "Политика", "Право", "Природа", "Продажи", "Психология", "Путешествия", "Религия", "Рукоделие", "Семья и дети", "Софт и приложения", "Спорт", "Технологии", "Транспорт", "Цитаты", "Шок-контент", "Эзотерика", "Экономика", "Эроктика", "Юмор и развлечения", "Другое"].  
- Укажи только те темы, которые прямо относятся к содержимому канала. Исключи темы, которые упоминаются косвенно.  
- Если канал охватывает несколько тем, укажи их все, но только если они являются основными.  

Формат ответа:  
ТЕМЫ: тема1, тема2, тема3
КЛЮЧЕВЫЕ_СЛОВА: слово1, слово2, слово3, слово4, слово5

Входные данные:  
{full_text}"""
        
        try:
            response = g4f.ChatCompletion.create(
                model=g4f.models.gpt_4,
                messages=[{"role": "user", "content": prompt}],
                stream=False
            )
            
            # Парсим ответ
            topics = []
            keywords = []
            
            lines = response.split('\n')
            for line in lines:
                if line.startswith('ТЕМЫ:'):
                    topics_text = line.replace('ТЕМЫ:', '').strip()
                    topics = [topic.strip() for topic in topics_text.split(',') if topic.strip()]
                elif line.startswith('КЛЮЧЕВЫЕ_СЛОВА:'):
                    keywords_text = line.replace('КЛЮЧЕВЫЕ_СЛОВА:', '').strip()
                    keywords = [kw.strip() for kw in keywords_text.split(',') if kw.strip()]
            
            # Если не удалось распарсить, используем дефолтные значения
            if not topics:
                topics = ['Бизнес и стартапы', 'Маркетинг, PR, реклама']
            if not keywords:
                keywords = ['бизнес', 'маркетинг', 'продвижение', 'реклама', 'стратегия']
            
            logger.info(f"Анализ канала завершен. Темы: {topics}, Ключевые слова: {keywords}")
            return topics, keywords
            
        except Exception as e:
            logger.error(f"Ошибка анализа с GPT-4: {e}")
            # Возвращаем дефолтные значения
            return ['Бизнес и стартапы', 'Маркетинг, PR, реклама'], ['бизнес', 'маркетинг', 'продвижение']
    
    except Exception as e:
        logger.error(f"Ошибка анализа канала: {e}")
        return [], []

async def process_found_channels(channels: List[str]):
    """Обработка найденных каналов"""
    # Получаем клиент из bot_interface
    import bot_interface
    client = bot_interface.bot_data.get('telethon_client')
    
    if not client:
        logger.error("Telethon клиент не инициализирован в bot_interface")
        return
    
    for username in channels:
        if username in found_channels:
            continue  # Канал уже был обработан
        
        if not search_active:
            break
        
        try:
            # Проверяем доступность комментариев с использованием GetFullChannelRequest
            if await check_channel_comments_available(client, username):
                logger.info(f"Канал {username} доступен для комментариев")
                found_channels.add(username)
                
                # Передаем канал в masslooker
                try:
                    import masslooker
                    await masslooker.add_channel_to_queue(username)
                    logger.info(f"Канал {username} добавлен в очередь масслукера")
                except Exception as e:
                    logger.error(f"Ошибка добавления канала в очередь: {e}")
            else:
                logger.info(f"Канал {username} недоступен для комментариев")
        
        except Exception as e:
            logger.error(f"Ошибка обработки канала {username}: {e}")
        
        # Небольшая задержка между проверками
        await asyncio.sleep(random.uniform(0.5, 1.5))  # Уменьшили с (1, 3) до (0.5, 1.5)

async def save_search_progress():
    """Сохранение прогресса поиска в базу данных"""
    try:
        from database import db
        await db.save_bot_state('search_progress', search_progress)
        await db.save_bot_state('found_channels', list(found_channels))
    except Exception as e:
        logger.error(f"Ошибка сохранения прогресса поиска: {e}")

async def load_search_progress():
    """Загрузка прогресса поиска из базы данных"""
    global search_progress, found_channels
    try:
        from database import db
        
        # Загружаем прогресс поиска
        saved_progress = await db.load_bot_state('search_progress', {})
        if saved_progress:
            search_progress.update(saved_progress)
        
        # Загружаем найденные каналы
        saved_channels = await db.load_bot_state('found_channels', [])
        if saved_channels:
            found_channels.update(saved_channels)
        
        logger.info(f"Загружен прогресс поиска: {search_progress}")
        logger.info(f"Загружено найденных каналов: {len(found_channels)}")
    except Exception as e:
        logger.error(f"Ошибка загрузки прогресса поиска: {e}")

async def search_loop():
    """Основной цикл поиска каналов"""
    global driver, search_active
    
    # Загружаем прогресс поиска при запуске
    await load_search_progress()
    
    while search_active:
        try:
            if not driver:
                driver = setup_driver()
                if not driver:
                    logger.error("Не удалось инициализировать драйвер")
                    await asyncio.sleep(60)
                    continue
            
            # Навигация к поиску каналов
            if not navigate_to_channel_search(driver):
                logger.error("Не удалось перейти к поиску каналов")
                await asyncio.sleep(60)
                continue
            
            keywords = current_settings.get('keywords', [])
            topics = current_settings.get('topics', [])
            
            if not keywords or not topics:
                logger.warning("Ключевые слова или темы не настроены")
                await asyncio.sleep(300)  # Ждем 5 минут
                continue
            
            first_search = True
            
            # Определяем с какого места продолжить поиск
            start_keyword_index = 0
            start_topic_index = 0
            
            if search_progress.get('current_keyword'):
                try:
                    start_keyword_index = keywords.index(search_progress['current_keyword'])
                except ValueError:
                    start_keyword_index = 0
            
            if search_progress.get('current_topic'):
                try:
                    start_topic_index = topics.index(search_progress['current_topic'])
                except ValueError:
                    start_topic_index = 0
            
            # Перебираем все комбинации тем и ключевых слов
            for topic_idx, topic in enumerate(topics[start_topic_index:], start_topic_index):
                if not search_active:
                    break
                
                keyword_start = start_keyword_index if topic_idx == start_topic_index else 0
                
                for keyword_idx, keyword in enumerate(keywords[keyword_start:], keyword_start):
                    if not search_active:
                        break
                    
                    try:
                        # Сохраняем текущий прогресс
                        search_progress['current_keyword'] = keyword
                        search_progress['current_topic'] = topic
                        await save_search_progress()
                        
                        # Выполняем поиск
                        channels = search_channels(driver, keyword, topic, first_search)
                        first_search = False
                        
                        if channels:
                            # Обрабатываем найденные каналы
                            await process_found_channels(channels)
                        
                        # Задержка между поисками (уменьшили)
                        await asyncio.sleep(random.uniform(5, 10))  # Уменьшили с (10, 20) до (5, 10)
                        
                    except Exception as e:
                        logger.error(f"Ошибка поиска по '{keyword}' и '{topic}': {e}")
                        await asyncio.sleep(30)
                        continue
            
            # Сбрасываем прогресс после завершения полного цикла
            search_progress['current_keyword'] = ''
            search_progress['current_topic'] = ''
            await save_search_progress()
            
            # Ждем 30 минут перед следующим циклом поиска
            logger.info("Цикл поиска завершен, ожидание 30 минут...")
            for _ in range(1800):  # 30 минут = 1800 секунд
                if not search_active:
                    break
                await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"Критическая ошибка в цикле поиска: {e}")
            if driver:
                try:
                    driver.quit()
                except:
                    pass
                driver = None
            await asyncio.sleep(60)

async def start_search(settings: dict):
    """Запуск поиска каналов"""
    global search_active, current_settings
    
    if search_active:
        logger.warning("Поиск уже запущен")
        return
    
    logger.info("Запуск поиска каналов...")
    search_active = True
    current_settings = settings.copy()
    
    # Проверяем наличие клиента в bot_interface
    try:
        import bot_interface
        client = bot_interface.bot_data.get('telethon_client')
        if not client:
            logger.error("Telethon клиент не найден в bot_interface")
            search_active = False
            return
        logger.info("Telethon клиент найден в bot_interface")
    except Exception as e:
        logger.error(f"Ошибка получения Telethon клиента: {e}")
        search_active = False
        return
    
    # Запускаем поиск в отдельной задаче
    asyncio.create_task(search_loop())
    logger.info("Поиск каналов запущен")

async def stop_search():
    """Остановка поиска каналов"""
    global search_active, driver
    
    logger.info("Остановка поиска каналов...")
    search_active = False
    
    # Сохраняем прогресс перед остановкой
    await save_search_progress()
    
    if driver:
        try:
            driver.quit()
            logger.info("Драйвер закрыт")
        except Exception as e:
            logger.error(f"Ошибка закрытия драйвера: {e}")
        driver = None
    
    logger.info("Поиск каналов остановлен")

def get_statistics():
    """Получение статистики поиска"""
    return {
        'found_channels': len(found_channels),
        'search_active': search_active,
        'current_progress': search_progress.copy()
    }

# Основная функция для тестирования
async def main():
    """Тестирование модуля"""
    test_settings = {
        'keywords': ['тест', 'пример'],
        'topics': ['Технологии', 'Образование']
    }
    
    await start_search(test_settings)
    
    # Тест в течение 2 минут
    await asyncio.sleep(120)
    
    await stop_search()

if __name__ == "__main__":
    asyncio.run(main())