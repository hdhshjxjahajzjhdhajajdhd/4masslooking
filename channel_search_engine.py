import asyncio
import logging
import random
import time
from datetime import datetime
from typing import List, Optional, Set
import os

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'masslooker_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

try:
    from telethon import TelegramClient, events
    from telethon.errors import ChannelPrivateError, ChatWriteForbiddenError, FloodWaitError, UserNotParticipantError
    from telethon.tl.types import Channel, Chat, MessageMediaPhoto, MessageMediaDocument
    from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest, GetFullChannelRequest
    from telethon.tl.functions.messages import SendReactionRequest
    from telethon.tl.types import ReactionEmoji
    import g4f
except ImportError as e:
    logger.error(f"Ошибка импорта библиотек: {e}")
    raise

# Глобальные переменные
masslooking_active = False
client: Optional[TelegramClient] = None
settings = {}
channel_queue = asyncio.Queue()
processed_channels: Set[str] = set()
masslooking_progress = {'current_channel': '', 'processed_count': 0}
statistics = {
    'comments_sent': 0,
    'reactions_set': 0,
    'channels_processed': 0,
    'errors': 0
}

# Положительные реакции для Telegram
POSITIVE_REACTIONS = [
    '👍', '❤️', '🔥', '🥰', '👏', '😍', '🤩', '🤝', '💯', '⭐',
    '🎉', '🙏', '💪', '👌', '✨', '💝', '🌟', '🏆', '🚀', '💎'
]

def load_comment_prompt():
    """Загрузка промпта для генерации комментариев"""
    prompt_file = 'prompt_for_generating_comments.txt'
    
    try:
        if os.path.exists(prompt_file):
            with open(prompt_file, 'r', encoding='utf-8') as f:
                return f.read().strip()
        else:
            logger.warning(f"Файл {prompt_file} не найден, используется базовый промпт")
            return """Создай короткий, естественный комментарий к посту на русском языке. 

Текст поста: {text_of_the_post}

Тематика канала: {topics}

Требования к комментарию:
- Максимум 2-3 предложения
- Естественный стиль общения
- Положительная или нейтральная тональность
- Без спама и навязчивости
- Соответствует тематике поста
- Выглядит как реальный отзыв пользователя

Создай комментарий:"""
    except Exception as e:
        logger.error(f"Ошибка загрузки промпта: {e}")
        return "Создай короткий позитивный комментарий к посту: {text_of_the_post}"

async def generate_comment(post_text: str, topics: List[str]) -> str:
    """Генерация комментария с помощью GPT-4"""
    try:
        prompt_template = load_comment_prompt()
        
        # Подготавливаем промпт
        topics_text = ', '.join(topics) if topics else 'общая тематика'
        
        # Проверяем наличие плейсхолдеров в промпте
        if '{text_of_the_post}' in prompt_template:
            prompt = prompt_template.replace('{text_of_the_post}', post_text[:1000])
        else:
            prompt = prompt_template + f"\n\nТекст поста: {post_text[:1000]}"
        
        if '{topics}' in prompt:
            prompt = prompt.replace('{topics}', topics_text)
        
        # Генерируем комментарий
        response = g4f.ChatCompletion.create(
            model=g4f.models.gpt_4,
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        
        # Очищаем ответ
        comment = response.strip()
        
        # Ограничиваем длину комментария
        if len(comment) > 200:
            comment = comment[:200] + '...'
        
        # Удаляем кавычки в начале и конце если есть
        if comment.startswith('"') and comment.endswith('"'):
            comment = comment[1:-1]
        
        if comment.startswith("'") and comment.endswith("'"):
            comment = comment[1:-1]
        
        logger.info(f"Сгенерирован комментарий: {comment[:50]}...")
        return comment
        
    except Exception as e:
        logger.error(f"Ошибка генерации комментария: {e}")
        # Возвращаем простой комментарий в случае ошибки
        fallback_comments = [
            "Интересно, спасибо за пост!",
            "Полезная информация",
            "Актуальная тема",
            "Хороший материал",
            "Согласен с автором"
        ]
        return random.choice(fallback_comments)

async def add_reaction_to_post(message, max_retries=3):
    """Добавление реакции к посту"""
    for attempt in range(max_retries):
        try:
            # Выбираем случайную положительную реакцию
            reaction = random.choice(POSITIVE_REACTIONS)
            
            # Отправляем реакцию
            await client.send_reaction(
                entity=message.peer_id,
                message=message.id,
                reaction=ReactionEmoji(emoticon=reaction)
            )
            
            logger.info(f"Поставлена реакция {reaction} к посту {message.id}")
            statistics['reactions_set'] += 1
            
            # Обновляем статистику в bot_interface
            try:
                import bot_interface
                bot_interface.update_statistics(reactions=1)
            except:
                pass
            
            return True
            
        except FloodWaitError as e:
            wait_time = e.seconds
            logger.warning(f"FloodWait при добавлении реакции: {wait_time} секунд")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait_time + 1)
                continue
            else:
                return False
        except Exception as e:
            logger.error(f"Ошибка добавления реакции (попытка {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(random.uniform(5, 10))
                continue
            else:
                return False
    
    return False

async def check_post_comments_available(message) -> bool:
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
                logger.info(f"Пост {message.id} поддерживает комментарии")
                return True
            else:
                logger.info(f"Пост {message.id} не поддерживает комментарии")
                return False
        else:
            logger.info(f"Канал не имеет группы обсуждений")
            return False
        
    except Exception as e:
        logger.warning(f"Ошибка проверки комментариев поста: {e}")
        return False

async def send_comment_to_post(message, comment_text: str, max_retries=3):
    """Отправка комментария к посту"""
    for attempt in range(max_retries):
        try:
            # Проверяем доступность комментариев под постом
            if not await check_post_comments_available(message):
                logger.info(f"Комментарии недоступны для поста {message.id}")
                return False
            
            # Проверяем, есть ли группа для обсуждений
            if hasattr(message.peer_id, 'channel_id'):
                entity = await client.get_entity(message.peer_id)
                
                # Получаем полную информацию о канале
                full_channel = await client(GetFullChannelRequest(entity))
                
                if hasattr(full_channel.full_chat, 'linked_chat_id') and full_channel.full_chat.linked_chat_id:
                    # Получаем группу обсуждений
                    discussion_group = await client.get_entity(full_channel.full_chat.linked_chat_id)
                    
                    # Проверяем, состоим ли мы в группе
                    try:
                        await client.get_participants(discussion_group, limit=1)
                        logger.info(f"Уже состоим в группе обсуждений {discussion_group.title}")
                    except UserNotParticipantError:
                        # Нужно вступить в группу
                        try:
                            await client(JoinChannelRequest(discussion_group))
                            logger.info(f"Вступили в группу обсуждений {discussion_group.title}")
                            await asyncio.sleep(2)
                        except Exception as e:
                            logger.error(f"Не удалось вступить в группу обсуждений: {e}")
                            return False
                    except Exception as e:
                        logger.warning(f"Ошибка проверки участия в группе: {e}")
                        # Попытаемся вступить в группу
                        try:
                            await client(JoinChannelRequest(discussion_group))
                            logger.info(f"Вступили в группу обсуждений {discussion_group.title}")
                            await asyncio.sleep(2)
                        except Exception as e2:
                            logger.error(f"Не удалось вступить в группу обсуждений: {e2}")
                            return False
                    
                    # Отправляем комментарий в группу обсуждений
                    await client.send_message(
                        discussion_group,
                        comment_text,
                        reply_to=message.id
                    )
                    
                    logger.info(f"Отправлен комментарий в группу обсуждений: {comment_text[:50]}...")
                    
                else:
                    # Пытаемся отправить комментарий напрямую
                    await client.send_message(
                        message.peer_id,
                        comment_text,
                        reply_to=message.id
                    )
                    
                    logger.info(f"Отправлен комментарий: {comment_text[:50]}...")
            
            statistics['comments_sent'] += 1
            
            # Обновляем статистику в bot_interface
            try:
                import bot_interface
                bot_interface.update_statistics(comments=1)
            except:
                pass
            
            return True
            
        except FloodWaitError as e:
            wait_time = e.seconds
            logger.warning(f"FloodWait при отправке комментария: {wait_time} секунд")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait_time + 1)
                continue
            else:
                return False
        except ChatWriteForbiddenError:
            logger.warning("Запрещена отправка сообщений в этот канал")
            return False
        except Exception as e:
            logger.error(f"Ошибка отправки комментария (попытка {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(random.uniform(5, 10))
                continue
            else:
                return False
    
    return False

async def save_masslooking_progress():
    """Сохранение прогресса масслукинга в базу данных"""
    try:
        from database import db
        await db.save_bot_state('masslooking_progress', masslooking_progress)
        await db.save_bot_state('processed_channels', list(processed_channels))
    except Exception as e:
        logger.error(f"Ошибка сохранения прогресса масслукинга: {e}")

async def load_masslooking_progress():
    """Загрузка прогресса масслукинга из базы данных"""
    global masslooking_progress, processed_channels
    try:
        from database import db
        
        # Загружаем прогресс масслукинга
        saved_progress = await db.load_bot_state('masslooking_progress', {})
        if saved_progress:
            masslooking_progress.update(saved_progress)
        
        # Загружаем обработанные каналы
        saved_channels = await db.load_bot_state('processed_channels', [])
        if saved_channels:
            processed_channels.update(saved_channels)
        
        logger.info(f"Загружен прогресс масслукинга: {masslooking_progress}")
        logger.info(f"Загружено обработанных каналов: {len(processed_channels)}")
    except Exception as e:
        logger.error(f"Ошибка загрузки прогресса масслукинга: {e}")

async def process_channel(username: str):
    """Обработка канала: подписка, комментарии, реакции, отписка"""
    try:
        logger.info(f"Начинаем обработку канала {username}")
        
        if not username.startswith('@'):
            username = '@' + username
        
        # Обновляем прогресс
        masslooking_progress['current_channel'] = username
        await save_masslooking_progress()
        
        # Получаем информацию о канале
        try:
            entity = await client.get_entity(username)
            logger.info(f"Получена информация о канале {username}: {entity.title}")
        except Exception as e:
            logger.error(f"Не удалось получить информацию о канале {username}: {e}")
            return False
        
        # Подписываемся на канал
        try:
            if hasattr(entity, 'left') and entity.left:
                await client(JoinChannelRequest(entity))
                logger.info(f"Подписались на канал {username}")
                await asyncio.sleep(random.uniform(2, 5))
            else:
                logger.info(f"Уже подписаны на канал {username}")
        except Exception as e:
            logger.warning(f"Ошибка подписки на канал {username}: {e}")
        
        # Определяем количество постов для обработки
        posts_range = settings.get('posts_range', (1, 5))
        posts_count = random.randint(posts_range[0], posts_range[1])
        
        logger.info(f"Обрабатываем {posts_count} последних постов в {username}")
        
        # Получаем последние посты
        processed_posts = 0
        topics = settings.get('topics', [])
        
        try:
            async for message in client.iter_messages(entity, limit=posts_count * 3):
                if not masslooking_active:
                    logger.info("Масслукинг остановлен, прерываем обработку канала")
                    break
                
                if processed_posts >= posts_count:
                    break
                
                # Пропускаем сообщения без текста
                if not message.text or len(message.text.strip()) < 10:
                    logger.debug(f"Пропускаем пост {message.id} без текста или с коротким текстом")
                    continue
                
                logger.info(f"Обрабатываем пост {message.id} в канале {username}")
                
                try:
                    # Проверяем доступность комментариев под постом
                    post_supports_comments = await check_post_comments_available(message)
                    
                    if post_supports_comments:
                        # Генерируем и отправляем комментарий
                        logger.info(f"Генерируем комментарий для поста {message.id}")
                        comment = await generate_comment(message.text, topics)
                        
                        logger.info(f"Отправляем комментарий для поста {message.id}: {comment[:50]}...")
                        comment_sent = await send_comment_to_post(message, comment)
                        
                        if comment_sent:
                            logger.info(f"Комментарий успешно отправлен для поста {message.id}")
                            # Добавляем задержку между комментарием и реакцией
                            delay = random.uniform(2, 8)
                            await asyncio.sleep(delay)
                        else:
                            logger.warning(f"Не удалось отправить комментарий для поста {message.id}")
                    else:
                        logger.info(f"Пост {message.id} не поддерживает комментарии, пропускаем комментирование")
                    
                    # Ставим реакцию (всегда пытаемся поставить реакцию)
                    logger.info(f"Ставим реакцию на пост {message.id}")
                    reaction_result = await add_reaction_to_post(message)
                    if reaction_result:
                        logger.info(f"Реакция успешно поставлена на пост {message.id}")
                    else:
                        logger.warning(f"Не удалось поставить реакцию на пост {message.id}")
                    
                    processed_posts += 1
                    logger.info(f"Обработан пост {processed_posts}/{posts_count} в канале {username}")
                    
                    # Задержка между обработкой постов
                    delay_range = settings.get('delay_range', (20, 1000))
                    if delay_range != (0, 0):
                        delay = random.uniform(delay_range[0], delay_range[1])
                        logger.info(f"Задержка {delay:.1f} секунд перед следующим действием")
                        await asyncio.sleep(delay)
                    
                except Exception as e:
                    logger.error(f"Ошибка обработки поста {message.id}: {e}")
                    statistics['errors'] += 1
                    continue
            
            logger.info(f"Завершена обработка постов в канале {username}. Обработано: {processed_posts}")
            
        except Exception as e:
            logger.error(f"Ошибка получения сообщений из канала {username}: {e}")
            statistics['errors'] += 1
        
        # Отписываемся от канала
        try:
            await client(LeaveChannelRequest(entity))
            logger.info(f"Отписались от канала {username}")
        except Exception as e:
            logger.warning(f"Ошибка отписки от канала {username}: {e}")
        
        statistics['channels_processed'] += 1
        masslooking_progress['processed_count'] += 1
        
        # Обновляем статистику в bot_interface
        try:
            import bot_interface
            bot_interface.update_statistics(channels=1)
        except:
            pass
        
        # Сохраняем прогресс
        await save_masslooking_progress()
        
        logger.info(f"Обработка канала {username} завершена успешно")
        return True
        
    except Exception as e:
        logger.error(f"Критическая ошибка обработки канала {username}: {e}")
        statistics['errors'] += 1
        return False

async def masslooking_worker():
    """Рабочий процесс масслукинга"""
    global masslooking_active
    
    # Загружаем прогресс при запуске
    await load_masslooking_progress()
    logger.info("Рабочий процесс масслукинга запущен")
    
    while masslooking_active:
        try:
            # Получаем канал из очереди с таймаутом
            try:
                username = await asyncio.wait_for(channel_queue.get(), timeout=10.0)
                logger.info(f"Получен канал из очереди: {username}")
            except asyncio.TimeoutError:
                continue
            
            if not masslooking_active:
                logger.info("Масслукинг остановлен, прерываем обработку")
                break
            
            # Проверяем лимит каналов
            max_channels = settings.get('max_channels', 150)
            if max_channels != float('inf') and len(processed_channels) >= max_channels:
                logger.info(f"Достигнут лимит каналов: {max_channels}")
                await asyncio.sleep(60)
                continue
            
            # Пропускаем уже обработанные каналы
            if username in processed_channels:
                logger.info(f"Канал {username} уже был обработан, пропускаем")
                continue
            
            # Обрабатываем канал
            logger.info(f"Начинаем обработку канала {username}")
            success = await process_channel(username)
            
            if success:
                processed_channels.add(username)
                logger.info(f"Канал {username} успешно обработан и добавлен в список обработанных")
                # Сохраняем информацию об обработанном канале в БД
                try:
                    from database import db
                    await db.add_processed_channel(username)
                except Exception as e:
                    logger.error(f"Ошибка сохранения обработанного канала в БД: {e}")
            else:
                logger.warning(f"Обработка канала {username} завершилась с ошибкой")
            
            # Задержка между каналами
            delay_range = settings.get('delay_range', (20, 1000))
            if delay_range != (0, 0):
                channel_delay = random.uniform(delay_range[0], delay_range[1])
                logger.info(f"Задержка {channel_delay:.1f} секунд перед следующим каналом")
                await asyncio.sleep(channel_delay)
            
        except Exception as e:
            logger.error(f"Ошибка в рабочем процессе масслукинга: {e}")
            await asyncio.sleep(30)
    
    logger.info("Рабочий процесс масслукинга завершен")

async def add_channel_to_queue(username: str):
    """Добавление канала в очередь обработки"""
    if username not in processed_channels:
        await channel_queue.put(username)
        logger.info(f"Канал {username} добавлен в очередь обработки")
    else:
        logger.info(f"Канал {username} уже был обработан, не добавляем в очередь")

async def start_masslooking(telegram_client: TelegramClient, masslooking_settings: dict):
    """Запуск масслукинга"""
    global masslooking_active, client, settings
    
    if masslooking_active:
        logger.warning("Масслукинг уже запущен")
        return
    
    logger.info("Запуск масслукинга...")
    
    client = telegram_client
    settings = masslooking_settings.copy()
    masslooking_active = True
    
    logger.info(f"Настройки масслукинга: {settings}")
    
    # Запускаем рабочий процесс
    asyncio.create_task(masslooking_worker())
    
    logger.info("Масслукинг запущен")

async def stop_masslooking():
    """Остановка масслукинга"""
    global masslooking_active
    
    logger.info("Остановка масслукинга...")
    masslooking_active = False
    
    # Сохраняем прогресс перед остановкой
    await save_masslooking_progress()
    
    # Очищаем очередь
    queue_size = channel_queue.qsize()
    while not channel_queue.empty():
        try:
            channel_queue.get_nowait()
        except:
            break
    
    logger.info(f"Масслукинг остановлен, очищено {queue_size} каналов из очереди")

def get_statistics():
    """Получение статистики масслукинга"""
    return {
        **statistics,
        'progress': masslooking_progress.copy(),
        'queue_size': channel_queue.qsize()
    }

def reset_statistics():
    """Сброс статистики"""
    global statistics, masslooking_progress
    statistics = {
        'comments_sent': 0,
        'reactions_set': 0,
        'channels_processed': 0,
        'errors': 0
    }
    masslooking_progress = {'current_channel': '', 'processed_count': 0}

# Основная функция для тестирования
async def main():
    """Тестирование модуля"""
    # Этот код предназначен только для тестирования
    print("Модуль masslooker готов к работе")
    print("Для запуска используйте функции start_masslooking() и add_channel_to_queue()")

if __name__ == "__main__":
    asyncio.run(main())