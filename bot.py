"""
Telegram Gateway Bot — стабильная версия с админкой и поддержкой приватных каналов.
"""

import os
import sqlite3
import time
import logging
import traceback
from datetime import datetime, date
import telebot
from telebot import types

# ------------------- Конфигурация -------------------
BOT_TOKEN = "8350341011:AAEG2PflBtPVsPUbEaWVB_vmkplmpMprZFs"
ADMINS = [8366174417]
DB_PATH = 'gateway_bot.db'
LOG_FILE = 'bot_errors.log'

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')
waiting_for = {}  # state для админских действий

# ------------------- Логирование -------------------
logging.basicConfig(filename=LOG_FILE, level=logging.ERROR,
                    format='%(asctime)s %(levelname)s:%(message)s')

# ------------------- База данных -------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT UNIQUE, title TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS access_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    full_name TEXT,
                    granted INTEGER,
                    checked_at TEXT)''')
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('access_link', 'https://example.com')")
    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT value FROM settings WHERE key=?', (key,))
    r = cur.fetchone()
    conn.close()
    return r[0] if r else None

def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()

def add_channel(chan, title=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute('INSERT INTO channels (channel, title) VALUES (?, ?)', (chan, title or chan))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def remove_channel(chan):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('DELETE FROM channels WHERE channel=?', (chan,))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return changed > 0

def list_channels():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT channel, title FROM channels ORDER BY id')
    rows = cur.fetchall()
    conn.close()
    return rows

def log_access(user_id, username, full_name, granted):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT INTO access_logs (user_id, username, full_name, granted, checked_at) VALUES (?, ?, ?, ?, ?)',
                (user_id, username or '', full_name or '', 1 if granted else 0, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def query_logs(from_iso=None, to_iso=None, limit=1000):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    q = 'SELECT id, user_id, username, full_name, granted, checked_at FROM access_logs'
    params = []
    if from_iso or to_iso:
        conds = []
        if from_iso:
            conds.append('checked_at >= ?')
            params.append(from_iso)
        if to_iso:
            conds.append('checked_at <= ?')
            params.append(to_iso)
        q += ' WHERE ' + ' AND '.join(conds)
    q += ' ORDER BY checked_at DESC LIMIT ?'
    params.append(limit)
    cur.execute(q, params)
    rows = cur.fetchall()
    conn.close()
    return rows

def count_logs_since(day_iso):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM access_logs WHERE checked_at >= ?', (day_iso,))
    r = cur.fetchone()[0]
    conn.close()
    return r

def clear_logs():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('DELETE FROM access_logs')
    conn.commit()
    conn.close()

# ------------------- Утилиты -------------------
def is_admin(user_id):
    return user_id in ADMINS

def ensure_channels_exist():
    if len(list_channels()) == 0:
        add_channel('@example_channel1')
        add_channel('@example_channel2')

# ------------------- Проверка подписки -------------------
def check_subscriptions(user_id):
    details = []
    chans = list_channels()
    for ch, _ in chans:
        try:
            member = bot.get_chat_member(ch, user_id)
            status = member.status
        except Exception as e:
            status = f'error:{str(e)}'
        details.append((ch, status))
    ok = all(d[1] in ('creator', 'administrator', 'member') for d in details)
    return ok, details

# ------------------- Handlers -------------------
@bot.message_handler(commands=['start'])
def cmd_start(message):
    try:
        text = ('Привет! Я бот-переходник.\n'
                'Чтобы получить ссылку — подпишись на все указанные каналы и нажми кнопку "Проверить подписку".')
        kb = types.InlineKeyboardMarkup()
        for ch, title in list_channels():
            url = f'https://t.me/{ch.lstrip("@")}' if ch.startswith('@') else None
            kb.add(types.InlineKeyboardButton(text=f'{title}', url=url))
        kb.add(types.InlineKeyboardButton('Проверить подписку', callback_data='check_subs'))
        if is_admin(message.from_user.id):
            kb.add(types.InlineKeyboardButton('Админ-панель', callback_data='open_admin'))
        bot.send_message(message.chat.id, text, reply_markup=kb)
    except Exception:
        logging.error(traceback.format_exc())

@bot.callback_query_handler(func=lambda c: c.data == 'check_subs')
def cb_check_subs(call):
    try:
        user = call.from_user
        ok, details = check_subscriptions(user.id)
        lines = [f'{ch}: {st}' for ch, st in details]
        if ok:
            link = get_setting('access_link')
            bot.send_message(user.id, f'✅ Доступ разрешён. Ссылка: {link}')
            log_access(user.id, user.username, f'{user.first_name or ""} {user.last_name or ""}'.strip(), True)
            bot.answer_callback_query(call.id, 'Доступ выдан — ссылка отправлена')
        else:
            bot.send_message(user.id, '❌ Не все подписки выполнены. Статусы:\n' + '\n'.join(lines))
            log_access(user.id, user.username, f'{user.first_name or ""} {user.last_name or ""}'.strip(), False)
            bot.answer_callback_query(call.id, 'Нужно подписаться на все каналы и повторить проверку')
    except Exception:
        logging.error(traceback.format_exc())

@bot.callback_query_handler(func=lambda c: c.data == 'open_admin')
def cb_open_admin(call):
    try:
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, 'Доступ запрещён')
            return
        show_admin_menu(call.from_user.id)
        bot.answer_callback_query(call.id, '')
    except Exception:
        logging.error(traceback.format_exc())

@bot.message_handler(commands=['admin'])
def cmd_admin(message):
    try:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, 'Доступ запрещён.')
            return
        show_admin_menu(message.from_user.id)
    except Exception:
        logging.error(traceback.format_exc())

def show_admin_menu(uid):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton('🔗 Показать ссылку', callback_data='admin_view_link'))
    kb.add(types.InlineKeyboardButton('✏️ Изменить ссылку', callback_data='admin_set_link'))
    kb.add(types.InlineKeyboardButton('📢 Список каналов', callback_data='admin_list_channels'))
    kb.add(types.InlineKeyboardButton('➕ Добавить канал', callback_data='admin_add_channel'))
    kb.add(types.InlineKeyboardButton('❌ Удалить канал', callback_data='admin_del_channel'))
    kb.add(types.InlineKeyboardButton('📊 Статистика', callback_data='admin_stats'))
    kb.add(types.InlineKeyboardButton('🧹 Очистить статистику', callback_data='admin_clear_stats'))
    bot.send_message(uid, 'Админ-панель:', reply_markup=kb)

# ------------------- Admin callbacks -------------------
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith('admin_'))
def cb_admin_actions(call):
    try:
        uid = call.from_user.id
        if not is_admin(uid):
            bot.answer_callback_query(call.id, 'Доступ запрещён')
            return
        data = call.data
        if data == 'admin_view_link':
            link = get_setting('access_link')
            bot.send_message(uid, f'Текущая ссылка: {link}')
        elif data == 'admin_set_link':
            waiting_for[uid] = {'action': 'set_link'}
            bot.send_message(uid, 'Отправь новую ссылку (например https://example.com)')
        elif data == 'admin_list_channels':
            chs = list_channels()
            text = '\n'.join([f'{title} ({ch})' for ch, title in chs]) if chs else 'Список каналов пуст'
            bot.send_message(uid, text)
        elif data == 'admin_add_channel':
            waiting_for[uid] = {'action': 'add_channel'}
            bot.send_message(uid, 'Отправь имя канала и название через | например: @mychannel|Мой канал')
        elif data == 'admin_del_channel':
            waiting_for[uid] = {'action': 'del_channel'}
            bot.send_message(uid, 'Отправь имя канала для удаления, например @mychannel')
        elif data == 'admin_stats':
            send_stats(uid)
        elif data == 'admin_clear_stats':
            waiting_for[uid] = {'action': 'confirm_clear'}
            bot.send_message(uid, 'Подтверди очистку статистики — отправь "YES"')
        bot.answer_callback_query(call.id, '')
    except Exception:
        logging.error(traceback.format_exc())

# ------------------- Admin text inputs -------------------
@bot.message_handler(func=lambda m: m.from_user.id in ADMINS)
def admin_text_handler(message):
    try:
        uid = message.from_user.id
        if uid not in waiting_for:
            return
        state = waiting_for.pop(uid)
        action = state.get('action')
        text = message.text.strip()
        if action == 'set_link':
            set_setting('access_link', text)
            bot.send_message(uid, 'Ссылка обновлена.')
        elif action == 'add_channel':
            if '|' in text:
                chan, title = text.split('|', 1)
            else:
                chan, title = text, text
            ok = add_channel(chan.strip(), title.strip())
            bot.send_message(uid, f'Канал {"добавлен" if ok else "уже существует"}: {chan.strip()}')
        elif action == 'del_channel':
            ok = remove_channel(text)
            bot.send_message(uid, f'Канал {"удалён" if ok else "не найден"}: {text}')
        elif action == 'confirm_clear':
            if text.upper() == 'YES':
                clear_logs()
                bot.send_message(uid, 'Статистика очищена.')
            else:
                bot.send_message(uid, 'Операция отменена.')
    except Exception:
        logging.error(traceback.format_exc())

# ------------------- Stats -------------------
def send_stats(uid):
    try:
        today_iso = date.today().isoformat() + 'T00:00:00'
        today_count = count_logs_since(today_iso)
        total = len(query_logs(limit=1000000))
        last50 = query_logs(limit=50)
        lines = [f'Статистика:\nСегодня: {today_count}\nВсего записей: {total}\nПоследние 50:']
        for r in last50:
            lines.append(f"{r[0]} | {r[3]} (@{r[2]}) | {'YES' if r[4]==1 else 'NO'} | {r[5]}")
        bot.send_message(uid, '\n'.join(lines))
    except Exception:
        logging.error(traceback.format_exc())

# ------------------- Запуск с авто-перезапуском -------------------
def run_bot():
    while True:
        try:
            print("Bot started...")
            bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            logging.error("Ошибка в polling:\n" + traceback.format_exc())
            time.sleep(5)

if __name__ == '__main__':
    init_db()
    ensure_channels_exist()
    run_bot()
