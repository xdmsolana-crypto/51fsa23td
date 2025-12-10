# bot.py
import telebot
from telebot import types
import sqlite3
import time
from datetime import datetime, timedelta

# ======================= КОНСТАНТЫ И ID =======================
# !!! ОБЯЗАТЕЛЬНО ЗАМЕНИТЕ ЭТОТ ТОКЕН НА СВОЙ !!!
API_TOKEN = "8406093250:AAEVg3uBA6YF89LkSt0Niv06HWXDLp2H_lE" 
ADMIN_ID = 8393627070  # ID Администратора для получения уведомлений

# ID ЧАТОВ ДЛЯ ЛОГОВ/МОДЕРАЦИИ (ДОЛЖНЫ БЫТЬ ВАШИ)
# Эти значения используются для инициализации настроек, но бот будет брать их из БД.
YT_LOG_CHAT_ID = -5066165769     # Ваш чат для проверки ссылок YouTube / Вывод средств
INSTA_LOG_CHAT_ID = -5093319533  # Ваш чат для проверки ссылок Instagram

# Настройки системы
CURRENCY = "₽"
REF_BONUS_DEFAULT = 15.00 # Рублей за привлеченного участника (Значение по умолчанию)
MIN_WITHDRAW_DEFAULT = 50.00 # Значение по умолчанию для минимального вывода

# Инициализация бота
bot = telebot.TeleBot(API_TOKEN)

# Глобальный словарь для временного хранения данных рассылки
broadcast_content = {} 

# ======================= БАЗА ДАННЫХ =======================
def db_connect():
    # Рекомендация: Для многопоточного Telegram-бота с sqlite необходимо check_same_thread=False
    # Но для большей надежности на продакшене лучше использовать PostgreSQL/MySQL
    return sqlite3.connect("database.db", check_same_thread=False)

db = db_connect()
cursor = db.cursor()

# 1. Создание таблицы users
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    firstname TEXT,
    balance REAL DEFAULT 0,
    hold REAL DEFAULT 0,
    status TEXT DEFAULT 'Траффер',
    is_banned INTEGER DEFAULT 0,
    ref_id TEXT,
    referred_by INTEGER,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP 
)
""")
db.commit()

# 2. Таблица для истории финансовых операций
cursor.execute("""
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT, -- PAYOUT, WITHDRAW, REF_BONUS, ADMIN_ADD, ADMIN_SUB, ADMIN_STATUS
    amount REAL,
    status TEXT, -- APPROVED, PENDING, REJECTED
    platform TEXT, -- YT, INSTA, N/A
    link TEXT, -- Для PAYOUT
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
db.commit()

# 3. Таблица для хранения одобренных ссылок (для защиты от дублей)
cursor.execute("""
CREATE TABLE IF NOT EXISTS links (
    url TEXT PRIMARY KEY,
    user_id INTEGER,
    platform TEXT,
    payout REAL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
db.commit()

# 4. Таблица настроек
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")
db.commit()

# Заглушки для настроек
DEFAULT_SUPPORT_USERNAME = "@Telepat_CEO"
DEFAULT_INFO_TEXT = "ℹ️ Информация о проекте TelepatYT появится позже."
DEFAULT_INSTRUCTION_TEXT = "✨ <b>Инструкция пока не настроена администратором.</b>"
DEFAULT_UPLOAD_VIDEO_TEXT = "✨ <b>Видео для залива пока не настроено администратором.</b>"
DEFAULT_INSTA_INSTRUCTION_TEXT = "✨ <b>Инструкция по Instagram пока не настроена.</b>"
DEFAULT_INSTA_UPLOAD_TEXT = "✨ <b>Видео/материалы для Instagram пока не настроены.</b>"

def init_settings():
    """Инициализация настроек по умолчанию, если их нет."""
    settings = {
        # Глобальные настройки
        'min_withdraw': f'{MIN_WITHDRAW_DEFAULT:.2f}', 
        'support_username': DEFAULT_SUPPORT_USERNAME, 
        'info_text': DEFAULT_INFO_TEXT, 
        'ref_bonus': f'{REF_BONUS_DEFAULT:.2f}', 
        
        # Настройки YouTube
        'yt_video_file_id': '', 
        'yt_instruction_text': DEFAULT_INSTRUCTION_TEXT,
        'yt_payout': '70.00', 
        'yt_upload_video_file_id': '', 
        'yt_upload_video_text': DEFAULT_UPLOAD_VIDEO_TEXT,
        'yt_log_chat_id': str(YT_LOG_CHAT_ID),

        # Настройки Instagram
        'insta_payout': '0.50', 
        'insta_log_chat_id': str(INSTA_LOG_CHAT_ID),
        'insta_video_file_id': '',
        'insta_instruction_text': DEFAULT_INSTA_INSTRUCTION_TEXT,
        'insta_upload_video_file_id': '',
        'insta_upload_video_text': DEFAULT_INSTA_UPLOAD_TEXT,
    }
    for key, value in settings.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()

init_settings()

def get_setting(key):
    """Получение настройки из БД."""
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    result = cursor.fetchone()
    return result[0] if result else None

def set_setting(key, value):
    """Установка/обновление настройки в БД."""
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    db.commit()

def get_float_setting(key, default):
    """Получение настройки как float."""
    try:
        return float(get_setting(key))
    except (TypeError, ValueError):
        return default

def get_min_withdraw():
    """Получение минимальной суммы вывода."""
    return get_float_setting('min_withdraw', MIN_WITHDRAW_DEFAULT)

def get_ref_bonus():
    """Получение реферального бонуса."""
    return get_float_setting('ref_bonus', REF_BONUS_DEFAULT)

def log_transaction(user_id, type, amount, status, platform="N/A", link=None):
    """Запись транзакции в историю и возврат ID транзакции."""
    cursor.execute(
        "INSERT INTO history (user_id, type, amount, status, platform, link) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, type, amount, status, platform, link)
    )
    db.commit()
    return cursor.lastrowid # Возвращаем ID только что вставленной строки

# ======================= ФУНКЦИИ ПОЛЬЗОВАТЕЛЕЙ =======================

def update_last_active(user_id):
    """Обновление времени последней активности пользователя."""
    cursor.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?", (user_id,))
    db.commit()

def add_user(user_id, firstname, referrer_id=None):
    ref_bonus = get_ref_bonus()
    cursor.execute("SELECT user_id, ref_id FROM users WHERE user_id = ?", (user_id,))
    existing_user = cursor.fetchone()

    if existing_user is None:
        ref_id = str(user_id) 
        
        cursor.execute(
            "INSERT INTO users (user_id, firstname, ref_id, referred_by) VALUES (?, ?, ?, ?)",
            (user_id, firstname, ref_id, referrer_id)
        )
        db.commit()

        if referrer_id:
            try:
                referrer_id = int(referrer_id)
                if referrer_id != user_id: 
                    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (ref_bonus, referrer_id))
                    db.commit()
                    log_transaction(referrer_id, 'REF_BONUS', ref_bonus, 'APPROVED')
                    
                    try:
                        # ИСПРАВЛЕНО: parse_mode="HTML" вместо reply_mode
                        bot.send_message(
                            referrer_id, 
                            f"🎉 <b>+ {ref_bonus:.2f} {CURRENCY}!</b>\n\nПо вашей ссылке пришел новый участник.", 
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        print(f"Failed to notify referrer {referrer_id}: {e}")
            except ValueError:
                pass 
    
    update_last_active(user_id)

def get_user(user_id):
    cursor.execute("SELECT balance, hold, status, ref_id, is_banned FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()

def get_referral_count(user_id):
    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
    return cursor.fetchone()[0]

def check_ban(user_id):
    """Проверяет, забанен ли пользователь."""
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    return result[0] == 1 if result else False

def set_ban_status(user_id, status, caller_id=None):
    """Устанавливает статус бана (1 - забанен, 0 - активен)."""
    cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (status, user_id))
    db.commit()
    
    status_text = "ЗАБАНЕН" if status == 1 else "РАЗБАНЕН"
    if caller_id and caller_id != user_id:
        log_transaction(user_id, 'ADMIN_STATUS', 0, status_text)


# ======================= ФУНКЦИИ СТАТИСТИКИ (ДЛЯ АДМИНА) =======================

def get_stats():
    """Собирает все метрики для админ-панели."""
    stats = {}
    
    # Общее число пользователей
    cursor.execute("SELECT COUNT(user_id) FROM users")
    stats['total_users'] = cursor.fetchone()[0]

    # Активные за 24 часа
    time_24h_ago = datetime.now() - timedelta(hours=24)
    cursor.execute("SELECT COUNT(user_id) FROM users WHERE last_active >= ?", (time_24h_ago,))
    stats['active_24h'] = cursor.fetchone()[0]
    
    # Общая сумма выплат (APPROVED WITHDRAW)
    cursor.execute("SELECT SUM(amount) FROM history WHERE type = 'WITHDRAW' AND status = 'APPROVED'")
    stats['total_paid'] = cursor.fetchone()[0] or 0.0

    # Общая сумма на рассмотрении (PENDING WITHDRAW + PENDING PAYOUT)
    # Сумма PAYOUT теперь 0.0 до одобрения, поэтому учитывает только WITHDRAW
    cursor.execute("SELECT SUM(amount) FROM history WHERE status = 'PENDING'")
    stats['total_pending'] = cursor.fetchone()[0] or 0.0
    
    # Ссылки на проверке (PENDING PAYOUT)
    cursor.execute("SELECT COUNT(*) FROM history WHERE type = 'PAYOUT' AND status = 'PENDING'")
    stats['pending_links'] = cursor.fetchone()[0]

    # Одобренные/Отклоненные ссылки
    cursor.execute("SELECT COUNT(*) FROM history WHERE type = 'PAYOUT' AND status = 'APPROVED'")
    stats['approved_links'] = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM history WHERE type = 'PAYOUT' AND status = 'REJECTED'")
    stats['rejected_links'] = cursor.fetchone()[0]
    
    return stats

def get_top_users(limit=10):
    """Получает топ пользователей по заработанному балансу."""
    cursor.execute("""
        SELECT user_id, balance 
        FROM users 
        ORDER BY balance DESC 
        LIMIT ?
    """, (limit,))
    return cursor.fetchall()


# ======================= ТЕКСТ ИНФО-БЛОКА =======================
WORK_INFO = f"""
💸 <b>Работа с YouTube и Instagram</b>
Выберите площадку для работы:
"""

def get_main_menu_text(user_id, firstname):
    """Генерирует текст главного меню."""
    balance, hold, status, _, _ = get_user(user_id)
    return (
        f"⚡ {firstname}, добро пожаловать в проект TelepatYT!\n\n"
        f"🆔 ID: <code>{user_id}</code>\n\n"
        f"💰 Баланс: <b>{balance:.2f} {CURRENCY}</b>\n"
        f"👀 На рассмотрении: <b>{hold:.2f} {CURRENCY}</b>\n\n"
        f"🟦 Статус: <b>{status}</b>"
    )

# ======================= КНОПКИ =======================

def main_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(f"💸 Запросить вывод", callback_data="withdraw"),
    )
    kb.add(
        types.InlineKeyboardButton("🚀 Начать работу", callback_data="start_work"),
    )
    kb.add(
        types.InlineKeyboardButton("👥 Реферальная система", callback_data="ref"),
        types.InlineKeyboardButton("📜 История", callback_data="history_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("ℹ️ Информация", callback_data="info"),
        types.InlineKeyboardButton("💬 Поддержка", callback_data="support")
    )
    return kb

def history_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔗 Начисления за ссылки", callback_data="history_payout"),
        types.InlineKeyboardButton("💰 Заявки на вывод", callback_data="history_withdraw")
    )
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_main"))
    return kb


def work_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📥 Загрузка YouTube", callback_data="yt"),
        types.InlineKeyboardButton("📥 Загрузка Instagram", callback_data="insta_menu")
    )
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_main"))
    return kb

def youtube_upload_menu():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📑 Инструкция", callback_data="yt_instruction"),
        types.InlineKeyboardButton("🎥 Видео под залив", callback_data="yt_get_upload_video"),
        types.InlineKeyboardButton("🔗 Отправить ссылку на проверку", callback_data="yt_send_link"),
        types.InlineKeyboardButton("⬅️ Назад к выбору площадки", callback_data="start_work")
    )
    return kb

# Клавиатура для возврата в меню YouTube
def back_to_yt_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("⬅️ Назад в меню YouTube", callback_data="yt")
    )
    return kb

def insta_upload_menu():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📑 Инструкция", callback_data="insta_instruction"),
        types.InlineKeyboardButton("🎥 Видео под залив", callback_data="insta_get_upload_video"),
        types.InlineKeyboardButton("🔗 Отправить ссылку на проверку", callback_data="insta_send_link"),
        types.InlineKeyboardButton("⬅️ Назад к выбору площадки", callback_data="start_work")
    )
    return kb

# Клавиатура для возврата в меню Instagram
def back_to_insta_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("⬅️ Назад в меню Instagram", callback_data="insta_menu")
    )
    return kb


def admin_menu():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📈 Статистика (Dashboard)", callback_data="admin_dashboard"),
        types.InlineKeyboardButton("📢 Массовая рассылка", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("💰 Установить лимит вывода", callback_data="admin_set_min_withdraw"),
        types.InlineKeyboardButton("👥 Настройка реф. бонуса", callback_data="admin_set_ref_bonus"),
        types.InlineKeyboardButton("✍️ Настройки текста", callback_data="admin_text_settings"), 
        types.InlineKeyboardButton("⚙️ Настройки YouTube", callback_data="admin_yt_settings"),
        types.InlineKeyboardButton("⚙️ Настройки Instagram", callback_data="admin_insta_settings"),
    )
    return kb

def admin_text_settings_menu():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("ℹ️ Установить текст 'Информация'", callback_data="admin_set_info_text"),
        types.InlineKeyboardButton("💬 Установить логин поддержки", callback_data="admin_set_support_username"),
        types.InlineKeyboardButton("⬅️ Назад в Админ-панель", callback_data="admin_menu_back"),
    )
    return kb

def admin_yt_settings_menu():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(f"💰 Установить выплату YT ({CURRENCY}) (Игнор)", callback_data="admin_set_payout_yt"),
        types.InlineKeyboardButton("📑 Установить текст инструкции YT", callback_data="admin_set_text_yt"),
        types.InlineKeyboardButton("🎥 Установить видео-инструкцию YT", callback_data="admin_set_video_yt"),
        types.InlineKeyboardButton("📥 Установить видео под залив YT", callback_data="admin_set_upload_video_yt"),
        types.InlineKeyboardButton("⬅️ Назад в Админ-панель", callback_data="admin_menu_back"),
    )
    return kb

def admin_insta_settings_menu():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(f"💰 Установить выплату Insta ({CURRENCY}) (Игнор)", callback_data="admin_set_payout_insta"),
        types.InlineKeyboardButton("📑 Установить текст инструкции Insta", callback_data="admin_set_text_insta"),
        types.InlineKeyboardButton("🎥 Установить видео-инструкцию Insta", callback_data="admin_set_video_insta"),
        types.InlineKeyboardButton("📥 Установить видео под залив Insta", callback_data="admin_set_upload_video_insta"),
        types.InlineKeyboardButton("⬅️ Назад в Админ-панель", callback_data="admin_menu_back"),
    )
    return kb

def cancel_input_kb():
    """Клавиатура для отмены ввода ссылки / суммы / рассылки"""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("❌ Отмена / Назад", callback_data="cancel_input")
    )
    return kb

def broadcast_confirm_kb():
    """Клавиатура для подтверждения рассылки"""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("✅ Начать рассылку", callback_data="broadcast_start"),
        types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_input")
    )
    return kb

# ======================= ОБЩИЕ ОБРАБОТЧИКИ =======================

@bot.message_handler(commands=['start'])
def send_welcome(message):
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].isdigit():
        referrer_id = int(args[1])
        
    firstname = message.from_user.first_name
    add_user(message.chat.id, firstname, referrer_id)
    
    text = get_main_menu_text(message.chat.id, firstname)
    
    bot.send_message(
        message.chat.id,
        text,
        parse_mode="HTML",
        reply_markup=main_menu()
    )
    
    # Уведомление админа о новом пользователе
    if referrer_id is None:
        try:
            bot.send_message(
                ADMIN_ID, 
                f"🔔 <b>Новый пользователь!</b>\n\nID: <code>{message.chat.id}</code>\nИмя: {firstname}",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Failed to notify admin: {e}")

# ======================= АДМИН-ПАНЕЛЬ /admin =======================

@bot.message_handler(commands=['admin', 'unban', 'user', 'add_balance', 'set_status', 'send'])
def admin_commands(msg):
    if msg.from_user.id != ADMIN_ID:
        return 

    if msg.text.startswith('/admin'):
        current_payout_yt = get_setting('yt_payout')
        current_payout_insta = get_setting('insta_payout')
        current_min_withdraw = get_min_withdraw() 
        current_support_username = get_setting('support_username')
        current_ref_bonus = get_ref_bonus()
        
        text = (
            "⚙️ <b>Панель Администратора</b>\n\n"
            f"Текущий лимит вывода: <b>{current_min_withdraw:.2f} {CURRENCY}</b>\n" 
            f"Реферальный бонус: <b>{current_ref_bonus:.2f} {CURRENCY}</b>\n"
            f"Текущий логин поддержки: <b>{current_support_username}</b>\n"
            f"Текущая выплата за YouTube: <b>{current_payout_yt} {CURRENCY}</b> (Вручную)\n"
            f"Текущая выплата за Instagram: <b>{current_payout_insta} {CURRENCY}</b> (Вручную)"
        )
        bot.send_message(
            msg.chat.id,
            text,
            parse_mode="HTML",
            reply_markup=admin_menu()
        )
    
    # --- КОМАНДА: /unban <user_id> ---
    elif msg.text.startswith('/unban'):
        parts = msg.text.split()
        if len(parts) == 2 and parts[1].isdigit():
            user_id_to_unban = int(parts[1])
            set_ban_status(user_id_to_unban, 0, msg.from_user.id)
            bot.send_message(msg.chat.id, f"✅ Пользователь с ID <code>{user_id_to_unban}</code> разбанен.", parse_mode="HTML")
            try:
                bot.send_message(user_id_to_unban, "🎉 Вы были разблокированы администратором и можете продолжить работу!")
            except:
                pass
        else:
            bot.send_message(msg.chat.id, "❌ Неверный формат. Используйте: `/unban <user_id>`", parse_mode="Markdown")

    # --- КОМАНДА: /user <user_id> ---
    elif msg.text.startswith('/user'):
        parts = msg.text.split()
        if len(parts) == 2 and parts[1].isdigit():
            target_id = int(parts[1])
            user_data = cursor.execute("SELECT firstname, balance, hold, status, is_banned, referred_by FROM users WHERE user_id = ?", (target_id,)).fetchone()
            
            if user_data:
                firstname, balance, hold, status, is_banned, referred_by = user_data
                ref_count = get_referral_count(target_id)
                ban_status = "ДА 🚫" if is_banned else "НЕТ ✅"
                
                text = (
                    f"👤 <b>Профиль пользователя: {firstname}</b>\n"
                    f"ID: <code>{target_id}</code>\n"
                    f"Статус: <b>{status}</b>\n"
                    f"Бан: <b>{ban_status}</b>\n\n"
                    f"💰 Баланс: <b>{balance:.2f} {CURRENCY}</b>\n"
                    f"👀 На рассмотрении: <b>{hold:.2f} {CURRENCY}</b>\n"
                    f"👥 Рефералы: <b>{ref_count}</b>\n"
                    f"Пригласил: <code>{referred_by or 'Нет'}</code>"
                )
                bot.send_message(msg.chat.id, text, parse_mode="HTML")
            else:
                bot.send_message(msg.chat.id, "❌ Пользователь с таким ID не найден.")
        else:
            bot.send_message(msg.chat.id, "❌ Неверный формат. Используйте: `/user <user_id>`", parse_mode="Markdown")

    # --- КОМАНДА: /add_balance <user_id> <amount> ---
    elif msg.text.startswith('/add_balance'):
        parts = msg.text.split()
        if len(parts) == 3 and parts[1].isdigit():
            try:
                target_id = int(parts[1])
                amount = float(parts[2].replace(',', '.'))
                
                cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
                db.commit()
                
                log_transaction(target_id, 'ADMIN_ADD', amount, 'APPROVED')
                
                bot.send_message(msg.chat.id, f"✅ Баланс пользователя <code>{target_id}</code> пополнен на <b>{amount:.2f} {CURRENCY}</b>.", parse_mode="HTML")
                try:
                    bot.send_message(target_id, f"🎉 Ваш баланс был пополнен администратором на <b>{amount:.2f} {CURRENCY}</b>.", parse_mode="HTML")
                except: pass
            except ValueError:
                bot.send_message(msg.chat.id, "❌ Неверный формат суммы. Используйте: `/add_balance <user_id> <сумма>`", parse_mode="Markdown")
        else:
            bot.send_message(msg.chat.id, "❌ Неверный формат. Используйте: `/add_balance <user_id> <сумма>`", parse_mode="Markdown")

    # --- КОМАНДА: /set_status <user_id> <new_status> ---
    elif msg.text.startswith('/set_status'):
        parts = msg.text.split()
        if len(parts) >= 3 and parts[1].isdigit():
            target_id = int(parts[1])
            new_status = " ".join(parts[2:]).strip()
            
            cursor.execute("UPDATE users SET status = ? WHERE user_id = ?", (new_status, target_id))
            db.commit()
            
            bot.send_message(msg.chat.id, f"✅ Статус пользователя <code>{target_id}</code> обновлен: <b>{new_status}</b>.", parse_mode="HTML")
            try:
                bot.send_message(target_id, f"⭐ Ваш статус в системе обновлен на: <b>{new_status}</b>.", parse_mode="HTML")
            except: pass
        else:
            bot.send_message(msg.chat.id, "❌ Неверный формат. Используйте: `/set_status <user_id> <новый_статус>`", parse_mode="Markdown")

    # --- КОМАНДА: /send (Запуск рассылки) ---
    elif msg.text.startswith('/send'):
        msg = bot.send_message(msg.chat.id, "📢 **Подготовка рассылки**\n\nОтправьте сообщение (текст/фото/видео) для рассылки всем пользователям.", parse_mode="Markdown", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, handle_broadcast_content)


# --- Admin Step: Обработка контента для рассылки ---
def handle_broadcast_content(message):
    if message.from_user.id != ADMIN_ID: return
    
    # Отмена ввода
    if message.text and message.text.lower() in ["❌ отмена / назад", "/cancel"]:
        bot.send_message(message.chat.id, "❌ Рассылка отменена.", reply_markup=admin_menu())
        return

    # Сохраняем контент (текст, фото, видео)
    if message.text:
        broadcast_content['text'] = message.text
        broadcast_content['type'] = 'text'
    elif message.photo:
        broadcast_content['file_id'] = message.photo[-1].file_id # Берем самое большое фото
        broadcast_content['caption'] = message.caption
        broadcast_content['type'] = 'photo'
    elif message.video:
        broadcast_content['file_id'] = message.video.file_id
        broadcast_content['caption'] = message.caption
        broadcast_content['type'] = 'video'
    else:
        msg = bot.send_message(message.chat.id, "❌ Неподдерживаемый тип контента. Пожалуйста, отправьте только текст, фото или видео.")
        bot.register_next_step_handler(msg, handle_broadcast_content)
        return

    # Запрос подтверждения
    text = f"✅ **Контент для рассылки получен ({broadcast_content['type']}).**\n\nВы уверены, что хотите начать рассылку всем пользователям?"
    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=broadcast_confirm_kb())


# --- Admin Callback: Запуск рассылки ---
@bot.callback_query_handler(func=lambda call: call.data == 'broadcast_start')
def start_broadcast_callback(call):
    if call.from_user.id != ADMIN_ID: return

    bot.answer_callback_query(call.id, "Начинаем рассылку...")
    chat_id = call.message.chat.id
    
    cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
    users = cursor.fetchall()
    total_users = len(users)
    success_count = 0
    
    bot.edit_message_text(f"🚀 **Рассылка запущена...**\nОтправляем сообщения {total_users} пользователям.", chat_id, call.message.message_id, parse_mode="Markdown")

    for user_tuple in users:
        user_id = user_tuple[0]
        try:
            if broadcast_content['type'] == 'text':
                bot.send_message(user_id, broadcast_content['text'], parse_mode="HTML")
            elif broadcast_content['type'] == 'photo':
                bot.send_photo(user_id, broadcast_content['file_id'], caption=broadcast_content['caption'], parse_mode="HTML")
            elif broadcast_content['type'] == 'video':
                bot.send_video(user_id, broadcast_content['file_id'], caption=broadcast_content['caption'], parse_mode="HTML")
            
            success_count += 1
            
        except telebot.apihelper.ApiTelegramException as e:
            # 403 Forbidden: Бот заблокирован пользователем
            if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                # Пропускаем заблокированных/удаленных пользователей
                pass
            else: 
                print(f"Error sending message to {user_id}: {e}")
        except Exception as e:
            print(f"Unknown error sending message to {user_id}: {e}")
            
        time.sleep(0.1) # Задержка для обхода лимитов Telegram

    broadcast_content.clear()
    final_text = (
        f"✅ **Рассылка завершена!**\n\n"
        f"Успешно доставлено: **{success_count}** из **{total_users}**."
    )
    bot.send_message(chat_id, final_text, parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Назад в Админ-панель", callback_data="admin_menu_back")))


# --- Admin Callback: Обработка Админ-Меню (Пропущена большая часть кода, восстанавливаю структуру) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callbacks(call):
    if call.from_user.id != ADMIN_ID: return

    chat_id = call.message.chat.id
    message_id = call.message.message_id
    bot.answer_callback_query(call.id)
    
    if call.data == "admin_menu_back":
        admin_commands(call.message) 
        try: bot.delete_message(chat_id, message_id)
        except: pass
        
    # --- Dashboard ---
    elif call.data == "admin_dashboard":
        stats = get_stats()
        top_users = get_top_users(10)
        
        top_users_text = "\n".join([f"  {i+1}. ID `{uid}`: {bal:.2f} {CURRENCY}" for i, (uid, bal) in enumerate(top_users)])
        
        text = (
            "📈 **Статистика проекта (Dashboard)**\n\n"
            "--- Общие метрики ---\n"
            f"👤 Всего пользователей: **{stats['total_users']}**\n"
            f"🟢 Активных за 24ч: **{stats['active_24h']}**\n"
            f"💸 Общая сумма выплат: **{stats['total_paid']:.2f} {CURRENCY}**\n"
            f"👀 На рассмотрении (Total Pending): **{stats['total_pending']:.2f} {CURRENCY}**\n\n"
            "--- Аналитика ссылок ---\n"
            f"⏳ Ссылок на проверке: **{stats['pending_links']}**\n"
            f"✅ Одобренных ссылок: **{stats['approved_links']}**\n"
            f"❌ Отклоненных ссылок: **{stats['rejected_links']}**\n\n"
            "--- Топ 10 Трафферов (по балансу) ---\n"
            f"{top_users_text}"
        )
        bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_menu_back")))

    # --- Broadcast ---
    elif call.data == "admin_broadcast":
        msg = bot.send_message(chat_id, "📢 **Подготовка рассылки**\n\nОтправьте сообщение (текст/фото/видео) для рассылки всем пользователям.", parse_mode="Markdown", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, handle_broadcast_content)

    # --- Настройки YT ---
    elif call.data == "admin_yt_settings":
        bot.edit_message_text(
            "⚙️ **Настройки YouTube**\n\nВыберите, что хотите изменить:",
            chat_id, message_id, parse_mode="Markdown", reply_markup=admin_yt_settings_menu()
        )

    # --- Настройки Insta ---
    elif call.data == "admin_insta_settings":
        bot.edit_message_text(
            "⚙️ **Настройки Instagram**\n\nВыберите, что хотите изменить:",
            chat_id, message_id, parse_mode="Markdown", reply_markup=admin_insta_settings_menu()
        )

    # --- Настройки Текста ---
    elif call.data == "admin_text_settings":
        bot.edit_message_text(
            "✍️ **Настройки текстов**\n\nВыберите, что хотите изменить:",
            chat_id, message_id, parse_mode="Markdown", reply_markup=admin_text_settings_menu()
        )

    # --- Установка лимита вывода ---
    elif call.data == "admin_set_min_withdraw":
        msg = bot.send_message(chat_id, "💰 Введите новый минимальный лимит вывода (например, 50.00):", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, admin_set_min_withdraw_step)

    # --- Установка реферального бонуса ---
    elif call.data == "admin_set_ref_bonus":
        msg = bot.send_message(chat_id, "👥 Введите новый реферальный бонус (например, 15.00):", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, admin_set_ref_bonus_step)

    # --- Текстовые настройки ---
    elif call.data == "admin_set_info_text":
        msg = bot.send_message(chat_id, "ℹ️ Отправьте новый текст для раздела 'Информация'. (Поддерживается HTML разметка)", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, admin_set_info_text_step)
        
    elif call.data == "admin_set_support_username":
        msg = bot.send_message(chat_id, "💬 Отправьте новый логин поддержки (@username):", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, admin_set_support_username_step)

    # --- Настройки YT ---
    elif call.data == "admin_set_payout_yt":
        msg = bot.send_message(chat_id, "💰 Введите сумму выплаты YT (например, 70.00):", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, admin_set_payout_yt_step)
        
    elif call.data == "admin_set_text_yt":
        msg = bot.send_message(chat_id, "📑 Отправьте новый текст инструкции YT. (Поддерживается HTML разметка)", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, admin_set_text_yt_step)
        
    elif call.data == "admin_set_video_yt":
        msg = bot.send_message(chat_id, "🎥 Отправьте видеофайл для инструкции YT:")
        bot.register_next_step_handler(msg, admin_set_video_yt_step_next)
        
    elif call.data == "admin_set_upload_video_yt":
        msg = bot.send_message(chat_id, "📥 Отправьте видеофайл под залив YouTube с описанием (caption):")
        bot.register_next_step_handler(msg, admin_set_upload_video_yt_step_next)

    # --- Настройки Insta ---
    elif call.data == "admin_set_payout_insta":
        msg = bot.send_message(chat_id, "💰 Введите сумму выплаты Insta (например, 0.50):", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, admin_set_payout_insta_step)
        
    elif call.data == "admin_set_text_insta":
        msg = bot.send_message(chat_id, "📑 Отправьте новый текст инструкции Insta. (Поддерживается HTML разметка)", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, admin_set_text_insta_step)
        
    elif call.data == "admin_set_video_insta":
        msg = bot.send_message(chat_id, "🎥 Отправьте видеофайл для инструкции Insta:")
        bot.register_next_step_handler(msg, admin_set_video_insta_step_next)
        
    elif call.data == "admin_set_upload_video_insta":
        msg = bot.send_message(chat_id, "📥 Отправьте видеофайл под залив Instagram с описанием (caption):")
        bot.register_next_step_handler(msg, admin_set_upload_video_insta_step_next)


# --- Admin Step: Установка лимита вывода ---
def admin_set_min_withdraw_step(message):
    if message.from_user.id != ADMIN_ID: return
    # Проверка на отмену
    if message.text and message.text.lower() in ["❌ отмена / назад", "/cancel"]:
        bot.send_message(message.chat.id, "❌ Отменено.", reply_markup=admin_menu())
        return
        
    try:
        new_limit = float(message.text.replace(',', '.').strip())
        if new_limit < 0: raise ValueError
        set_setting('min_withdraw', f"{new_limit:.2f}")
        bot.send_message(message.chat.id, f"✅ Новый лимит вывода установлен: <b>{new_limit:.2f} {CURRENCY}</b>", parse_mode="HTML")
        admin_commands(message)
    except ValueError:
        msg = bot.send_message(message.chat.id, f"❌ Неверный формат. Пожалуйста, введите положительное число или 0 (например, 50.00).")
        bot.register_next_step_handler(msg, admin_set_min_withdraw_step)

# --- Admin Step: Установка реферального бонуса ---
def admin_set_ref_bonus_step(message):
    if message.from_user.id != ADMIN_ID: return
    # Проверка на отмену
    if message.text and message.text.lower() in ["❌ отмена / назад", "/cancel"]:
        bot.send_message(message.chat.id, "❌ Отменено.", reply_markup=admin_menu())
        return
        
    try:
        new_bonus = float(message.text.replace(',', '.').strip())
        if new_bonus <= 0: raise ValueError
        set_setting('ref_bonus', f"{new_bonus:.2f}")
        bot.send_message(message.chat.id, f"✅ Новый реферальный бонус установлен: <b>{new_bonus:.2f} {CURRENCY}</b>", parse_mode="HTML")
        admin_commands(message)
    except ValueError:
        msg = bot.send_message(message.chat.id, f"❌ Неверный формат. Пожалуйста, введите положительное число (например, 15.00).")
        bot.register_next_step_handler(msg, admin_set_ref_bonus_step)

# --- Admin Steps: Настройка текста ---
def admin_set_info_text_step(message):
    if message.from_user.id != ADMIN_ID: return
    if message.text and message.text.lower() in ["❌ отмена / назад", "/cancel"]:
        bot.send_message(message.chat.id, "❌ Отменено.", reply_markup=admin_menu())
        return

    new_text = message.text
    if not new_text: new_text = DEFAULT_INFO_TEXT
    set_setting('info_text', new_text)
    bot.send_message(message.chat.id, "✅ Текст 'Информация' обновлен.")
    admin_commands(message)

def admin_set_support_username_step(message):
    if message.from_user.id != ADMIN_ID: return
    if message.text and message.text.lower() in ["❌ отмена / назад", "/cancel"]:
        bot.send_message(message.chat.id, "❌ Отменено.", reply_markup=admin_menu())
        return
        
    new_username = message.text.strip()
    if new_username and new_username.startswith('@'):
        set_setting('support_username', new_username)
        bot.send_message(message.chat.id, "✅ Логин поддержки обновлен.")
    else:
        bot.send_message(message.chat.id, "❌ Неверный формат логина. Используйте формат `@username`.")
    admin_commands(message)


# --- Admin Steps: Настройка YouTube ---
def admin_set_payout_yt_step(message):
    if message.from_user.id != ADMIN_ID: return
    if message.text and message.text.lower() in ["❌ отмена / назад", "/cancel"]:
        bot.send_message(message.chat.id, "❌ Отменено.", reply_markup=admin_menu())
        return
        
    try:
        new_payout = float(message.text.replace(',', '.').strip())
        if new_payout <= 0: raise ValueError
        set_setting('yt_payout', f"{new_payout:.2f}")
        bot.send_message(message.chat.id, f"✅ Новая сумма выплаты YT установлена: <b>{new_payout:.2f} {CURRENCY}</b>", parse_mode="HTML")
        admin_commands(message)
    except ValueError:
        msg = bot.send_message(message.chat.id, f"❌ Неверный формат. Пожалуйста, введите положительное число (например, 70.00).")
        bot.register_next_step_handler(msg, admin_set_payout_yt_step)

def admin_set_text_yt_step(message):
    if message.from_user.id != ADMIN_ID: return
    if message.text and message.text.lower() in ["❌ отмена / назад", "/cancel"]:
        bot.send_message(message.chat.id, "❌ Отменено.", reply_markup=admin_menu())
        return
        
    new_text = message.text
    set_setting('yt_instruction_text', new_text)
    bot.send_message(message.chat.id, "✅ Текст инструкции YT обновлен.")
    admin_commands(message)

def admin_set_video_yt_step_next(message):
    if message.from_user.id != ADMIN_ID: return
    
    if message.video:
        file_id = message.video.file_id
        set_setting('yt_video_file_id', file_id)
        bot.send_message(message.chat.id, "✅ Видео-инструкция YT успешно сохранена.")
        admin_commands(message)
    else:
        msg = bot.send_message(message.chat.id, "❌ Это не видеофайл. Пожалуйста, отправьте именно видеофайл.")
        bot.register_next_step_handler(msg, admin_set_video_yt_step_next)

def admin_set_upload_video_yt_step_next(message):
    if message.from_user.id != ADMIN_ID: return
    
    if message.video:
        file_id = message.video.file_id
        caption = message.caption if message.caption else DEFAULT_UPLOAD_VIDEO_TEXT
        set_setting('yt_upload_video_file_id', file_id)
        set_setting('yt_upload_video_text', caption)
        bot.send_message(message.chat.id, "✅ Видео под залив и его описание YT успешно сохранены.")
        admin_commands(message)
    else:
        msg = bot.send_message(message.chat.id, "❌ Это не видеофайл. Пожалуйста, отправьте именно видеофайл.")
        bot.register_next_step_handler(msg, admin_set_upload_video_yt_step_next)


# --- Admin Steps: Настройка Instagram ---
def admin_set_payout_insta_step(message):
    if message.from_user.id != ADMIN_ID: return
    if message.text and message.text.lower() in ["❌ отмена / назад", "/cancel"]:
        bot.send_message(message.chat.id, "❌ Отменено.", reply_markup=admin_menu())
        return
        
    try:
        new_payout = float(message.text.replace(',', '.').strip())
        if new_payout <= 0: raise ValueError
        set_setting('insta_payout', f"{new_payout:.2f}")
        bot.send_message(message.chat.id, f"✅ Новая сумма выплаты Insta установлена: <b>{new_payout:.2f} {CURRENCY}</b>", parse_mode="HTML")
        admin_commands(message)
    except ValueError:
        msg = bot.send_message(message.chat.id, f"❌ Неверный формат. Пожалуйста, введите положительное число (например, 0.50).")
        bot.register_next_step_handler(msg, admin_set_payout_insta_step)

def admin_set_text_insta_step(message):
    if message.from_user.id != ADMIN_ID: return
    if message.text and message.text.lower() in ["❌ отмена / назад", "/cancel"]:
        bot.send_message(message.chat.id, "❌ Отменено.", reply_markup=admin_menu())
        return
        
    new_text = message.text
    set_setting('insta_instruction_text', new_text)
    bot.send_message(message.chat.id, "✅ Текст инструкции Insta обновлен.")
    admin_commands(message)

def admin_set_video_insta_step_next(message):
    if message.from_user.id != ADMIN_ID: return
    
    if message.video:
        file_id = message.video.file_id
        set_setting('insta_video_file_id', file_id)
        bot.send_message(message.chat.id, "✅ Видео-инструкция Insta успешно сохранена.")
        admin_commands(message)
    else:
        msg = bot.send_message(message.chat.id, "❌ Это не видеофайл. Пожалуйста, отправьте именно видеофайл.")
        bot.register_next_step_handler(msg, admin_set_video_insta_step_next)

def admin_set_upload_video_insta_step_next(message):
    if message.from_user.id != ADMIN_ID: return
    
    if message.video:
        file_id = message.video.file_id
        caption = message.caption if message.caption else DEFAULT_INSTA_UPLOAD_TEXT
        set_setting('insta_upload_video_file_id', file_id)
        set_setting('insta_upload_video_text', caption)
        bot.send_message(message.chat.id, "✅ Видео под залив и его описание Insta успешно сохранены.")
        admin_commands(message)
    else:
        msg = bot.send_message(message.chat.id, "❌ Это не видеофайл. Пожалуйста, отправьте именно видеофайл.")
        bot.register_next_step_handler(msg, admin_set_upload_video_insta_step_next)

# ======================= ОБРАБОТЧИКИ ССЫЛОК =======================

def is_link_approved(link):
    """Проверяет, была ли ссылка уже одобрена."""
    cursor.execute("SELECT url FROM links WHERE url = ?", (link,))
    return cursor.fetchone() is not None

# --- YouTube Link Handler (ИСПРАВЛЕНО: Передача ID транзакции в callback_data) ---
def handle_youtube_link(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = message.from_user.username if message.from_user.username else f"ID: {user_id}"
    link = message.text.strip()
    platform = 'YT'
    log_chat_id = get_setting('yt_log_chat_id')
    
    if link.startswith('/'): return

    try: bot.clear_step_handler_by_chat_id(chat_id)
    except: pass

    if is_link_approved(link):
        bot.send_message(chat_id, "❌ **Ошибка!** Эта ссылка уже была одобрена и оплачена ранее.", parse_mode="Markdown", reply_markup=youtube_upload_menu())
        return

    # Логируем как PENDING и получаем ID транзакции
    history_id = log_transaction(user_id, 'PAYOUT', 0.0, 'PENDING', platform=platform, link=link)

    # --- Создание кнопок 70/45/Отклонить для Админа ---
    # Формат: approve_<платформа>_<history_id>_<сумма>
    admin_kb = types.InlineKeyboardMarkup(row_width=3)
    admin_kb.row(
        types.InlineKeyboardButton("✅ 70₽", callback_data=f"approve_{platform.lower()}_{history_id}_70.00"),
        types.InlineKeyboardButton("✅ 45₽", callback_data=f"approve_{platform.lower()}_{history_id}_45.00")
    )
    admin_kb.row(
        # Формат: reject_<платформа>_<history_id>
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{platform.lower()}_{history_id}"),
        types.InlineKeyboardButton(f"🚫 Забанить {user_id}", callback_data=f"ban_link_{user_id}"),
    )

    admin_message = (
        f"🔔 <b>НОВАЯ ССЫЛКА ({platform}) НА ПРОВЕРКУ</b>\n\n"
        f"Пользователь: @{username}\n"
        f"ID: <code>{user_id}</code>\n"
        f"ID транзакции: <code>{history_id}</code>\n"
        f"Сумма к начислению: <b>Админ выберет (70/45)</b>\n\n"
        f"🔗 Ссылка: <a href='{link}'>{link}</a>"
    )
    
    try:
        # Отправляем сообщение в лог-чат
        bot.send_message(
            log_chat_id, 
            admin_message, 
            parse_mode="HTML", 
            reply_markup=admin_kb,
            disable_web_page_preview=True # Отключаем превью, чтобы не нагружать Telegram
        )
    except Exception as e:
        print(f"Error sending link log to YT chat: {e}")

    # --- Сообщение пользователю об успешной отправке ---
    bot.send_message(
        chat_id, 
        "✅ **Ссылка принята на проверку!**\n\nКак только модератор проверит ее, вы получите уведомление.",
        parse_mode="Markdown", 
        reply_markup=youtube_upload_menu()
    )


# --- Instagram Link Handler (ИСПРАВЛЕНО: Передача ID транзакции в callback_data) ---
def handle_instagram_link(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = message.from_user.username if message.from_user.username else f"ID: {user_id}"
    link = message.text.strip()
    platform = 'INSTA'
    log_chat_id = get_setting('insta_log_chat_id')
    
    if link.startswith('/'): return

    try: bot.clear_step_handler_by_chat_id(chat_id)
    except: pass

    if is_link_approved(link):
        bot.send_message(chat_id, "❌ **Ошибка!** Эта ссылка уже была одобрена и оплачена ранее.", parse_mode="Markdown", reply_markup=insta_upload_menu())
        return

    # Логируем как PENDING и получаем ID транзакции
    history_id = log_transaction(user_id, 'PAYOUT', 0.0, 'PENDING', platform=platform, link=link)

    # --- Создание кнопок для Админа ---
    # Формат: approve_<платформа>_<history_id>_<сумма>
    admin_kb = types.InlineKeyboardMarkup(row_width=3)
    admin_kb.row(
        types.InlineKeyboardButton("✅ 0.50₽", callback_data=f"approve_{platform.lower()}_{history_id}_0.50"),
        types.InlineKeyboardButton("✅ 0.25₽", callback_data=f"approve_{platform.lower()}_{history_id}_0.25")
    )
    admin_kb.row(
        # Формат: reject_<платформа>_<history_id>
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{platform.lower()}_{history_id}"),
        types.InlineKeyboardButton(f"🚫 Забанить {user_id}", callback_data=f"ban_link_{user_id}"),
    )

    admin_message = (
        f"🔔 <b>НОВАЯ ССЫЛКА ({platform}) НА ПРОВЕРКУ</b>\n\n"
        f"Пользователь: @{username}\n"
        f"ID: <code>{user_id}</code>\n"
        f"ID транзакции: <code>{history_id}</code>\n"
        f"Сумма к начислению: <b>Админ выберет (0.50/0.25)</b>\n\n"
        f"🔗 Ссылка: <a href='{link}'>{link}</a>"
    )
    
    try:
        # Отправляем сообщение в лог-чат
        bot.send_message(
            log_chat_id, 
            admin_message, 
            parse_mode="HTML", 
            reply_markup=admin_kb,
            disable_web_page_preview=True 
        )
    except Exception as e:
        print(f"Error sending link log to INSTA chat: {e}")

    # --- Сообщение пользователю об успешной отправке ---
    bot.send_message(
        chat_id, 
        "✅ **Ссылка принята на проверку!**\n\nКак только модератор проверит ее, вы получите уведомление.",
        parse_mode="Markdown", 
        reply_markup=insta_upload_menu()
    )


# --- Обработка команд approve/reject/ban (Админ-панель) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_', 'reject_', 'ban_link_')))
def admin_actions(call):
    if call.from_user.id != ADMIN_ID: return

    parts = call.data.split('_')
    action = parts[0] # approve, reject, ban_link
    
    if action == 'ban_link':
        # Отдельная логика для бана
        user_id = int(parts[2])
        set_ban_status(user_id, 1, call.from_user.id)
        bot.answer_callback_query(call.id, f"Пользователь {user_id} забанен!", show_alert=True)
        
        # Обновляем сообщение в логе
        try:
            bot.edit_message_text(
                f"{call.message.text}\n\n—---\n🚫 <b>ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН</b> Администратором.", 
                chat_id=call.message.chat.id, 
                message_id=call.message.message_id, 
                parse_mode="HTML", 
                reply_markup=None
            )
        except:
             pass 
        
        # Также ищем PENDING транзакцию и отклоняем ее (если она связана с этим пользователем)
        cursor.execute("""
            UPDATE history SET status = 'REJECTED' 
            WHERE user_id = ? AND type = 'PAYOUT' AND status = 'PENDING'
        """, (user_id,))
        # !!! ОСТОРОЖНО: При бане через кнопку ссылки, мы не знаем ID транзакции, поэтому ищем все pending Payouts этого юзера.
        db.commit()
        
        try:
            bot.send_message(user_id, "🚫 **Ваш аккаунт был заблокирован администратором.**", parse_mode="Markdown")
        except: pass
        return

    # Логика для approve и reject
    platform = parts[1] # yt, insta
    
    if len(parts) < 3 or not parts[2].isdigit():
        bot.answer_callback_query(call.id, "❌ Неверный формат callback-данных (старое/поврежденное сообщение?).")
        return
        
    history_id = int(parts[2]) # ID транзакции
    amount = 0.0
    is_approved = False
    
    if action == 'approve':
        amount = float(parts[3]) 
        is_approved = True
        
    # 1. Получаем полную информацию о транзакции по history_id
    cursor.execute(
        "SELECT user_id, link, status FROM history WHERE id = ? AND type = 'PAYOUT' LIMIT 1", 
        (history_id,)
    )
    history_entry = cursor.fetchone()
    
    if not history_entry or history_entry[2] != 'PENDING':
        bot.answer_callback_query(call.id, "Действие уже выполнено или транзакция не найдена/неактивна.")
        return

    user_id = history_entry[0]
    full_link = history_entry[1]
    
    # 2. Обработка
    if is_approved:
        if is_link_approved(full_link): # Проверка полной ссылки на дубль
            bot.answer_callback_query(call.id, "❌ Ссылка уже была оплачена.")
            return

        # Начисляем средства
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        # Сохраняем полную ссылку в таблицу links
        cursor.execute("INSERT INTO links (url, user_id, platform, payout) VALUES (?, ?, ?, ?)", (full_link, user_id, platform.upper(), amount))
        # Обновляем статус в history (и сумму, которая была 0.0 при PENDING)
        cursor.execute("UPDATE history SET status = 'APPROVED', amount = ? WHERE id = ?", (amount, history_id))
        db.commit()

        try:
            bot.send_message(
                user_id, 
                f"🎉 <b>Ваша ссылка {platform.upper()} одобрена!</b>\n\nНа ваш баланс начислено <b>{amount:.2f} {CURRENCY}</b>.", 
                parse_mode="HTML"
            )
        except: pass
        edit_status = f"✅ <b>ОДОБРЕНО ({amount:.2f} {CURRENCY})</b>"
    else:
        # Обновляем статус в history на REJECTED
        cursor.execute("UPDATE history SET status = 'REJECTED' WHERE id = ?", (history_id,))
        db.commit()
        
        try:
            bot.send_message(
                user_id, 
                f"❌ <b>К сожалению, ваша ссылка {platform.upper()} отклонена.</b>\n\n"
                "Проверьте, соответствует ли контент всем требованиям.", 
                parse_mode="HTML"
            )
        except: pass
        edit_status = "❌ <b>ОТКЛОНЕНО</b>"

    # 3. Обновляем сообщение в лог-чате
    # Обновляем оригинальный текст сообщения, чтобы добавить статус.
    original_text_lines = call.message.text.split('\n')
    # Убираем старую строку "Сумма к начислению" для чистоты лога
    original_text_lines = [line for line in original_text_lines if "Сумма к начислению:" not in line] 
    
    new_text = "\n".join(original_text_lines) + f"\n\n—---\n{edit_status} Администратором."
    
    try:
        bot.edit_message_text(
            new_text, 
            chat_id=call.message.chat.id, 
            message_id=call.message.message_id, 
            parse_mode="HTML", 
            reply_markup=None
        )
    except:
        pass 
        
    bot.answer_callback_query(call.id, "Статус ссылки обновлен.")


# ======================= ВЫВОД СРЕДСТВ =======================

def handle_withdraw_amount(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = message.from_user.username if message.from_user.username else f"ID: {user_id}"
    
    balance_info = get_user(user_id)
    if not balance_info:
        # Отправляем главное меню в случае сбоя
        bot.clear_step_handler_by_chat_id(chat_id) 
        text = get_main_menu_text(user_id, message.from_user.first_name)
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=main_menu())
        return 
        
    balance = balance_info[0]
    MIN_WITHDRAW = get_min_withdraw()
    
    if message.text and message.text.startswith('/'):
        # Отправляем главное меню, если пользователь ввел команду
        bot.clear_step_handler_by_chat_id(chat_id) 
        text = get_main_menu_text(user_id, message.from_user.first_name)
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=main_menu())
        return 

    try:
        amount = float(message.text.replace(',', '.').strip())
        
        if amount <= 0:
            msg = bot.send_message(chat_id, f"❌ Сумма вывода должна быть положительной. Введите другую сумму.", reply_markup=cancel_input_kb())
            bot.register_next_step_handler(msg, handle_withdraw_amount)
            return
            
        if amount < MIN_WITHDRAW:
            msg = bot.send_message(chat_id, f"❌ Минимальная сумма для вывода: **{MIN_WITHDRAW:.2f} {CURRENCY}**. Введите другую сумму.", parse_mode="Markdown", reply_markup=cancel_input_kb())
            bot.register_next_step_handler(msg, handle_withdraw_amount)
            return
            
        if amount > balance:
            msg = bot.send_message(chat_id, f"❌ Недостаточно средств на балансе. Ваш баланс: **{balance:.2f} {CURRENCY}**. Введите другую сумму.", parse_mode="Markdown", reply_markup=cancel_input_kb())
            bot.register_next_step_handler(msg, handle_withdraw_amount)
            return
            
        # Запрашиваем реквизиты
        bot.clear_step_handler_by_chat_id(chat_id)
        msg = bot.send_message(chat_id, f"✅ Выбрана сумма **{amount:.2f} {CURRENCY}**.\n\nВведите ваши реквизиты для вывода (например, номер карты, Qiwi, Payeer):", parse_mode="Markdown", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, handle_withdraw_details, amount)

    except ValueError:
        msg = bot.send_message(chat_id, f"❌ Неверный формат суммы. Пожалуйста, введите число (например, 50.00).", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, handle_withdraw_amount)


def handle_withdraw_details(message, amount):
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = message.from_user.username if message.from_user.username else f"ID: {user_id}"
    details = message.text.strip()

    if details.startswith('/'):
        bot.clear_step_handler_by_chat_id(chat_id)
        text = get_main_menu_text(user_id, message.from_user.first_name)
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=main_menu())
        return

    # 1. Уменьшаем основной баланс и увеличиваем HOLD
    cursor.execute("UPDATE users SET balance = balance - ?, hold = hold + ? WHERE user_id = ?", (amount, amount, user_id))
    db.commit()
    
    # 2. Логируем PENDING транзакцию и получаем ID
    history_id = log_transaction(user_id, 'WITHDRAW', amount, 'PENDING', link=details)

    # 3. Уведомление пользователя
    bot.send_message(
        chat_id, 
        f"✅ **Заявка на вывод {amount:.2f} {CURRENCY} принята!**\n\nОжидайте выплату в течение 24 часов. Средства переведены в раздел 'На рассмотрении'.",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

    # 4. Уведомление админа
    log_chat_id = get_setting('yt_log_chat_id') # Используем YT чат для вывода
    admin_kb = types.InlineKeyboardMarkup(row_width=2)
    # Формат: confirm_<history_id>
    admin_kb.add(
        types.InlineKeyboardButton(f"✅ Выплачено ({amount:.2f}₽)", callback_data=f"confirm_{history_id}"),
        types.InlineKeyboardButton(f"❌ Отклонить", callback_data=f"decline_{history_id}"),
    )
    
    admin_message = (
        f"💸 <b>НОВАЯ ЗАЯВКА НА ВЫВОД</b>\n\n"
        f"Пользователь: @{username}\n"
        f"ID: <code>{user_id}</code>\n"
        f"ID транзакции: <code>{history_id}</code>\n"
        f"Сумма: <b>{amount:.2f} {CURRENCY}</b>\n\n"
        f"💳 Реквизиты:\n<code>{details}</code>"
    )
    
    try:
        bot.send_message(log_chat_id, admin_message, parse_mode="HTML", reply_markup=admin_kb)
    except Exception as e:
        print(f"Error sending withdraw log to admin chat: {e}")


# --- Обработка команд confirm/decline (Админ-панель) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith(('confirm_', 'decline_')))
def withdraw_admin_actions(call):
    if call.from_user.id != ADMIN_ID: return

    parts = call.data.split('_')
    action = parts[0] # confirm, decline
    
    if len(parts) < 2 or not parts[1].isdigit():
        bot.answer_callback_query(call.id, "❌ Неверный формат callback-данных (старое/поврежденное сообщение?).")
        return
        
    history_id = int(parts[1]) # ID транзакции
    
    # Получаем всю информацию по ID
    cursor.execute("SELECT user_id, amount, status FROM history WHERE id = ? AND type = 'WITHDRAW' LIMIT 1", (history_id,))
    history_entry = cursor.fetchone()
    
    if not history_entry or history_entry[2] != 'PENDING':
        bot.answer_callback_query(call.id, "Действие уже выполнено или заявка не найдена/неактивна.")
        return

    user_id = history_entry[0]
    amount = history_entry[1]
    
    # 1. Обновляем статус в history
    new_status = 'APPROVED' if action == 'confirm' else 'REJECTED'
    cursor.execute("UPDATE history SET status = ? WHERE id = ?", (new_status, history_id))
    
    # 2. Обновляем баланс пользователя
    cursor.execute("UPDATE users SET hold = hold - ? WHERE user_id = ?", (amount, user_id))
    
    edit_status = ""
    if action == 'confirm':
        # При подтверждении основной баланс уже уменьшен, HOLD уменьшается. Все корректно.
        edit_status = f"✅ <b>ВЫПЛАЧЕНО ({amount:.2f} {CURRENCY})</b>"
        try:
            bot.send_message(user_id, f"🎉 **Ваша заявка на вывод {amount:.2f} {CURRENCY} подтверждена!**\n\nСредства отправлены на указанные реквизиты.", parse_mode="Markdown")
        except: pass
    else:
        # При отклонении возвращаем HOLD на основной баланс
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        edit_status = "❌ <b>ОТКЛОНЕНО</b>"
        try:
            bot.send_message(user_id, f"❌ **К сожалению, ваша заявка на вывод {amount:.2f} {CURRENCY} отклонена.**\n\nСредства возвращены на ваш основной баланс. Проверьте правильность реквизитов.", parse_mode="Markdown")
        except: pass
        
    db.commit()
    
    # 3. Обновляем сообщение в лог-чате
    original_text_lines = call.message.text.split('\n')
    # Убираем старую строку "ID транзакции" для чистоты лога, если нужно, но лучше оставить
    new_text = "\n".join(original_text_lines) + f"\n\n—---\n{edit_status} Администратором."
    
    try:
        bot.edit_message_text(
            new_text, 
            chat_id=call.message.chat.id, 
            message_id=call.message.message_id, 
            parse_mode="HTML", 
            reply_markup=None
        )
    except:
        pass 
        
    bot.answer_callback_query(call.id, "Статус заявки на вывод обновлен.")


# ======================= ОБРАБОТЧИК КНОПОК =======================

@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    if check_ban(user_id):
        bot.answer_callback_query(call.id, "🚫 Ваш аккаунт заблокирован.", show_alert=True)
        return
        
    # Обработка админ-действий (approve/reject/ban/confirm/decline)
    if call.data.startswith(('approve_', 'reject_', 'ban_', 'confirm_', 'decline_')):
        # Эти действия обрабатываются в отдельных функциях (@bot.callback_query_handler),
        # поэтому здесь мы их игнорируем, но отвечаем на колбэк, чтобы не было ошибки "часиков"
        bot.answer_callback_query(call.id)
        return
        
    bot.answer_callback_query(call.id)

    # --- Общее меню ---
    if call.data == "start_work":
        bot.edit_message_text(WORK_INFO, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=work_menu())
        
    elif call.data == "withdraw":
        balance, _, _, _, _ = get_user(user_id)
        MIN_WITHDRAW = get_min_withdraw()
        
        if balance < MIN_WITHDRAW:
            bot.send_message(chat_id, f"❌ Недостаточно средств для вывода. Мин. сумма: **{MIN_WITHDRAW:.2f} {CURRENCY}**.", parse_mode="Markdown")
            return
            
        # Удаляем предыдущее сообщение с меню перед запросом ввода
        try: bot.delete_message(chat_id, message_id)
        except: pass
        
        msg = bot.send_message(chat_id, f"💰 Ваш текущий баланс: **{balance:.2f} {CURRENCY}**.\n\nВведите сумму, которую хотите вывести:", parse_mode="Markdown", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, handle_withdraw_amount)

    elif call.data == "ref":
        ref_bonus = get_ref_bonus()
        _, _, _, ref_id, _ = get_user(user_id)
        ref_link = f"https://t.me/{bot.get_me().username}?start={ref_id}"
        ref_count = get_referral_count(user_id)
        
        ref_text = (
            "👥 **Реферальная система**\n\n"
            f"За каждого активного реферала, начавшего работу, вы получаете **{ref_bonus:.2f} {CURRENCY}**.\n\n"
            f"🔗 Ваша реферальная ссылка:\n`{ref_link}`\n\n"
            f"Привлечено участников: **{ref_count}**"
        )
        bot.edit_message_text(ref_text, chat_id, message_id, parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_main")))

    elif call.data == "info":
        info_text = get_setting('info_text')
        bot.edit_message_text(info_text, chat_id, message_id, parse_mode="HTML", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_main")))

    elif call.data == "support":
        support_username = get_setting('support_username')
        text = f"💬 **Поддержка**\n\nПо всем вопросам обращайтесь к: {support_username}"
        bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back_main")))
        
    # --- История ---
    elif call.data == "history_menu":
        bot.edit_message_text("📜 **История операций**\n\nВыберите, какую историю показать:", chat_id, message_id, parse_mode="Markdown", reply_markup=history_menu())

    elif call.data == "history_payout":
        cursor.execute("SELECT amount, status, timestamp, platform FROM history WHERE user_id = ? AND type = 'PAYOUT' ORDER BY timestamp DESC LIMIT 20", (user_id,))
        records = cursor.fetchall()
        title = "🔗 **История начислений за ссылки (Последние 20)**"
        history_list = []
        if records:
            for amount, status, timestamp, platform in records:
                status_emoji = {"APPROVED": "✅", "REJECTED": "❌", "PENDING": "⏳"}.get(status, "❓")
                try:
                    time_formatted = datetime.strptime(timestamp.split('.')[0], '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M')
                except ValueError:
                    time_formatted = timestamp.split('.')[0]
                history_list.append(f"{status_emoji} {time_formatted} ({platform}): **{amount:.2f} {CURRENCY}** (Статус: {status})")
            text = f"{title}\n\n" + "\n".join(history_list)
        else:
            text = f"{title}\n\nИстория начислений пуста."
        bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=history_menu())

    elif call.data == "history_withdraw":
        cursor.execute("SELECT amount, status, timestamp FROM history WHERE user_id = ? AND type = 'WITHDRAW' ORDER BY timestamp DESC LIMIT 20", (user_id,))
        records = cursor.fetchall()
        title = "💰 **История заявок на вывод (Последние 20)**"
        history_list = []
        if records:
            for amount, status, timestamp in records:
                status_emoji = {"APPROVED": "✅", "REJECTED": "❌", "PENDING": "⏳"}.get(status, "❓")
                try:
                    time_formatted = datetime.strptime(timestamp.split('.')[0], '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M')
                except ValueError:
                    time_formatted = timestamp.split('.')[0]
                history_list.append(f"{status_emoji} {time_formatted}: **{amount:.2f} {CURRENCY}** (Статус: {status})")
            text = f"{title}\n\n" + "\n".join(history_list)
        else:
            text = f"{title}\n\nИстория выводов пуста."
        bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown", reply_markup=history_menu())

    # --- Отмена ввода / Назад ---
    elif call.data == "cancel_input":
        bot.clear_step_handler_by_chat_id(chat_id=chat_id)
        # Отправляем главное меню
        firstname = call.from_user.first_name
        text = get_main_menu_text(user_id, firstname)
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=main_menu())
        try: bot.delete_message(chat_id, message_id)
        except: pass
        bot.answer_callback_query(call.id, "Ввод отменен.")

    # --- Меню YouTube / Instagram ---
    elif call.data == "yt":
        try:
            bot.edit_message_text("📁 Выберите действие для работы с YouTube:", chat_id, message_id, parse_mode="Markdown", reply_markup=youtube_upload_menu())
        except Exception:
            # Если не удалось отредактировать (старое сообщение), просто отправляем новое
            bot.send_message(chat_id, "📁 Выберите действие для работы с YouTube:", parse_mode="Markdown", reply_markup=youtube_upload_menu())

    elif call.data == "insta_menu":
        try:
            bot.edit_message_text("📁 Выберите действие для работы с Instagram:", chat_id, message_id, parse_mode="Markdown", reply_markup=insta_upload_menu())
        except Exception:
            bot.send_message(chat_id, "📁 Выберите действие для работы с Instagram:", parse_mode="Markdown", reply_markup=insta_upload_menu())
            
    # --- Инструкции YouTube ---
    elif call.data == "yt_instruction":
        yt_instruction_file_id = get_setting('yt_video_file_id')
        yt_instruction_text = get_setting('yt_instruction_text')
        try: bot.delete_message(chat_id, message_id)
        except: pass
        
        if yt_instruction_file_id:
            # Отправляем видео с описанием и кнопкой "назад"
            bot.send_video(chat_id, yt_instruction_file_id, caption=yt_instruction_text, parse_mode="HTML", reply_markup=back_to_yt_menu_kb())
        else: 
            bot.send_message(chat_id, yt_instruction_text, parse_mode="HTML", reply_markup=back_to_yt_menu_kb())
            
    elif call.data == "yt_get_upload_video":
        yt_upload_file_id = get_setting('yt_upload_video_file_id')
        yt_upload_text = get_setting('yt_upload_video_text')
        try: bot.delete_message(chat_id, message_id)
        except: pass
        
        # Сначала отправляем текст (как заголовок)
        bot.send_message(chat_id, "🎥 **Видео под залив YouTube**\n\n" + yt_upload_text, parse_mode="HTML")
        # Затем отправляем видео (отдельно, без подписи/кнопок)
        if yt_upload_file_id: 
            bot.send_video(chat_id, yt_upload_file_id)
        
        # Отправляем меню для дальнейшей работы
        bot.send_message(chat_id, "Выберите следующее действие:", reply_markup=back_to_yt_menu_kb())

    # --- Инструкции Instagram ---
    elif call.data == "insta_instruction":
        insta_instruction_file_id = get_setting('insta_video_file_id')
        insta_instruction_text = get_setting('insta_instruction_text')
        try: bot.delete_message(chat_id, message_id)
        except: pass
        
        if insta_instruction_file_id:
            bot.send_video(chat_id, insta_instruction_file_id, caption=insta_instruction_text, parse_mode="HTML", reply_markup=back_to_insta_menu_kb())
        else: 
            bot.send_message(chat_id, insta_instruction_text, parse_mode="HTML", reply_markup=back_to_insta_menu_kb())
            
    elif call.data == "insta_get_upload_video":
        insta_upload_file_id = get_setting('insta_upload_video_file_id')
        insta_upload_text = get_setting('insta_upload_video_text')
        try: bot.delete_message(chat_id, message_id)
        except: pass

        # Сначала отправляем текст (как заголовок)
        bot.send_message(chat_id, "🎥 **Видео под залив Instagram**\n\n" + insta_upload_text, parse_mode="HTML")
        # Затем отправляем видео (отдельно, без подписи/кнопок)
        if insta_upload_file_id: 
            bot.send_video(chat_id, insta_upload_file_id)
            
        # Отправляем меню для дальнейшей работы
        bot.send_message(chat_id, "Выберите следующее действие:", reply_markup=back_to_insta_menu_kb())
        

    # --- Отправка ссылки ---
    elif call.data == "yt_send_link":
        # Удаляем предыдущее сообщение с меню перед запросом ввода
        try: bot.delete_message(chat_id, message_id)
        except: pass
        msg = bot.send_message(chat_id, "🔗 Отправьте мне **полную ссылку** на ваше загруженное YouTube видео для проверки.", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, handle_youtube_link)
        
    elif call.data == "insta_send_link":
        # Удаляем предыдущее сообщение с меню перед запросом ввода
        try: bot.delete_message(chat_id, message_id)
        except: pass
        msg = bot.send_message(chat_id, "🔗 Отправьте мне **полную ссылку** на ваш загруженный Instagram Reel/Post для проверки.", reply_markup=cancel_input_kb())
        bot.register_next_step_handler(msg, handle_instagram_link)


    # --- Возврат в Главное меню (ИСПРАВЛЕНО) ---
    elif call.data == "back_main":
        firstname = call.from_user.first_name
        text = get_main_menu_text(user_id, firstname)
        try:
            # Пытаемся отредактировать сообщение (если оно не слишком старое)
            bot.edit_message_text(text, chat_id, message_id, parse_mode="HTML", reply_markup=main_menu())
        except Exception:
            # Если не удалось, отправляем новое
            bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=main_menu())
            try: bot.delete_message(chat_id, message_id) # Удаляем старое, если возможно
            except: pass
            

# ======================= ОСНОВНОЙ ЦИКЛ БОТА ДЛЯ 24/7 РАБОТЫ =======================
if __name__ == '__main__':
    print("Bot started...")
    while True:
        try:
            # *** ИСПРАВЛЕНИЕ ОШИБКИ: non_stop=True удален, чтобы избежать TypeError. ***
            bot.infinity_polling(interval=0, timeout=20) 
        except Exception as e:
            # Логирование критической ошибки и пауза перед перезапуском
            print(f"CRITICAL ERROR IN POLLING: {e}")
            # Попытка закрыть соединение с БД перед перезапуском
            try:
                db.close()
            except:
                pass
            
            # Переподключение к БД
            try:
                db = db_connect()
                cursor = db.cursor()
            except Exception as db_e:
                print(f"Database reconnection failed: {db_e}")

            time.sleep(5) # Ждем 5 секунд перед перезапуском
