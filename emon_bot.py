import logging
import sqlite3
import random
import re
import threading
import time
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, CallbackQueryHandler, ContextTypes, filters
)

# ========= CONFIG =========
TOKEN = os.environ.get("BOT_TOKEN") or "YOUR_FALLBACK_TOKEN_HERE"

PHONE, VERIFICATION, ADMIN_LOGIN, WITHDRAW_ACCOUNT = range(4)

YOUR_BKASH = "01331732308"
YOUR_NAGAD = "01331732308"
ADMIN_PASSWORD = "@md@emon@talukder@063"

FIXED_AMOUNTS = [200, 500, 1000, 1500, 2000, 2500, 3000, 5000, 10000, 15000, 20000, 25000, 30000, 40000, 50000]
WITHDRAW_AMOUNTS = [200, 300, 500, 700, 1000, 1500, 2000, 2500, 3000, 5000, 7500, 10000, 15000, 20000]

REFERRAL_BONUS_PERCENT = 20
DELAYED_BONUS_PERCENT = 20

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)


# ========= DATABASE SETUP =========
def init_database():
    conn = sqlite3.connect('users.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            phone TEXT UNIQUE,
            balance REAL DEFAULT 0,
            bonus_balance REAL DEFAULT 0,
            bkash_number TEXT,
            nagad_number TEXT,
            referral_code TEXT UNIQUE,
            referred_by INTEGER,
            joined_date TEXT,
            is_verified INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            last_withdraw_date TEXT,
            total_earned REAL DEFAULT 0,
            total_withdrawn REAL DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS verification_codes (
            phone TEXT PRIMARY KEY,
            code TEXT,
            created_time TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            type TEXT,
            status TEXT,
            transaction_id TEXT,
            payment_method TEXT,
            created_date TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            payment_method TEXT,
            account_number TEXT,
            status TEXT DEFAULT 'pending',
            created_date TEXT,
            processed_date TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    conn.commit()
    conn.close()
    logging.info("✅ ডাটাবেস তৈরি করা হয়েছে")


def get_db_connection():
    conn = sqlite3.connect('users.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ========= BONUS THREAD =========
def check_and_add_bonus():
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT t.id, t.user_id, t.amount 
                FROM transactions t 
                WHERE t.status = 'approved' 
                AND t.type = 'deposit'
                AND datetime(t.created_date) <= datetime('now', '-24 hours')
            ''')
            transactions = cursor.fetchall()

            for txn_id, user_id, amount in transactions:
                bonus_amount = (amount * DELAYED_BONUS_PERCENT) / 100
                cursor.execute('UPDATE users SET bonus_balance = bonus_balance + ? WHERE user_id = ?', 
                               (bonus_amount, user_id))
                conn.commit()
            conn.close()
        except Exception as e:
            logging.error(f"Bonus Thread Error: {e}")
        time.sleep(3600)


def start_bonus_thread():
    t = threading.Thread(target=check_and_add_bonus)
    t.daemon = True
    t.start()


# ========= HANDLERS =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT phone, is_verified FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()

    if result and result['is_verified'] == 1:
        await update.message.reply_text("✅ আপনি ইতিমধ্যে ভেরিফাইড ইউজার!")
        return ConversationHandler.END

    await update.message.reply_text("📱 আপনার ফোন নাম্বার দিন (১১ ডিজিট): উদাহরণ: 01712345678")
    return PHONE


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not re.match(r'^01[3-9]\d{8}$', phone):
        await update.message.reply_text("❌ ভুল নাম্বার! সঠিক ফরম্যাটে দিন।")
        return PHONE

    code = str(random.randint(1000, 9999))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO verification_codes (phone, code, created_time) VALUES (?, ?, datetime('now'))", (phone, code))
    conn.commit()
    conn.close()

    context.user_data["phone"] = phone
    context.user_data["code"] = code

    await update.message.reply_text(f"✅ আপনার ভেরিফিকেশন কোড: {code}\n\n৪ ডিজিট কোডটি পাঠান:")
    return VERIFICATION


async def handle_verification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    code = context.user_data.get("code")
    phone = context.user_data.get("phone")
    user_id = update.effective_user.id

    if user_input != code:
        await update.message.reply_text("❌ ভুল কোড! আবার চেষ্টা করুন।")
        return VERIFICATION

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, phone, joined_date, is_verified, is_active)
        VALUES (?, ?, datetime('now'), 1, 1)
    """, (user_id, phone))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🎉 ভেরিফিকেশন সফল!\n\n📱 ফোন: {phone}\n💰 ব্যালেন্স: 0 টাকা\n🎁 বোনাস: 0 টাকা\n\n/recharge লিখে রিচার্জ করুন।")
    return ConversationHandler.END


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT balance, bonus_balance FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    conn.close()

    if not data:
        await update.message.reply_text("❌ প্রথমে /start লিখে ভেরিফিকেশন করুন।")
        return
    await update.message.reply_text(f"💰 ব্যালেন্স: {data['balance']} টাকা\n🎁 বোনাস: {data['bonus_balance']} টাকা")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ বাতিল করা হয়েছে।")
    return ConversationHandler.END


# ========= MAIN =========
def main():
    init_database()
    start_bonus_thread()

    app = ApplicationBuilder().token(TOKEN).build()

    user_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            VERIFICATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_verification)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(user_conv)
    app.add_handler(CommandHandler("balance", balance))

    logging.info("🤖 META Income Bot started successfully!")
    app.run_polling()


if __name__ == "__main__":
    main()
