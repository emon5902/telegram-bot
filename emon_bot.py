import os
import logging
import sqlite3
import random
import re
import threading
import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
import gspread
from google.oauth2.service_account import Credentials

# Your NEW bot token for metaincome_bot
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Google Sheets Setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'service-account.json'
SPREADSHEET_ID = '1TyMdpPyAS6sMc9kZPAs9stC_uwZ-SqrkHALdc46aX78'

# Setup logging
logging.basicConfig(level=logging.INFO)

# Conversation states
PHONE, VERIFICATION, PASSWORD_SETUP, PASSWORD_LOGIN, ADMIN_LOGIN, WITHDRAW_ACCOUNT = range(6)

# Your bKash and Nagad numbers - UPDATE WITH YOUR NUMBERS
YOUR_BKASH = "01712345678"
YOUR_NAGAD = "01787654321"

# Admin password
ADMIN_PASSWORD = "admin123"

# Fixed recharge amounts
FIXED_AMOUNTS = [200, 500, 1000, 1500, 2000, 2500, 3000, 5000, 10000, 15000, 20000, 25000, 30000, 40000, 50000]

# Withdraw amounts
WITHDRAW_AMOUNTS = [200, 300, 500, 700, 1000, 1500, 2000, 2500, 3000, 5000, 7500, 10000, 15000, 20000]

# Bonus settings
REFERRAL_BONUS_PERCENT = 20
DELAYED_BONUS_PERCENT = 20

# Database setup
def init_database():
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            phone TEXT UNIQUE,
            password TEXT,
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
            login_attempts INTEGER DEFAULT 0,
            last_login_attempt TEXT
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
            created_date TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referee_id INTEGER,
            instant_bonus_paid INTEGER DEFAULT 0,
            created_date TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bonuses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            transaction_id INTEGER,
            bonus_type TEXT,
            status TEXT DEFAULT 'pending',
            created_date TEXT
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
            created_date TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ ডাটাবেস তৈরি করা হয়েছে")

# Generate unique random referral code
def generate_referral_code():
    characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    while True:
        code = "META" + ''.join(random.choices(characters, k=8))
        
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users WHERE referral_code = ?', (code,))
        exists = cursor.fetchone()[0]
        conn.close()
        
        if not exists:
            return code

# Password validation
def is_strong_password(password):
    if len(password) < 6:
        return False, "পাসওয়ার্ড কমপক্ষে ৬ ক্যারেক্টার হতে হবে"
    if not re.search(r"[A-Z]", password):
        return False, "পাসওয়ার্ডে কমপক্ষে ১টি বড় হাতের অক্ষর থাকতে হবে"
    if not re.search(r"[a-z]", password):
        return False, "পাসওয়ার্ডে কমপক্ষে ১টি ছোট হাতের অক্ষর থাকতে হবে"
    if not re.search(r"\d", password):
        return False, "পাসওয়ার্ডে কমপক্ষে ১টি সংখ্যা থাকতে হবে"
    return True, "পাসওয়ার্ড শক্তিশালী"

# Check login attempts
def check_login_attempts(user_id):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT login_attempts, last_login_attempt FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        return True
    
    attempts, last_attempt = result
    
    if last_attempt:
        last_attempt_time = datetime.strptime(last_attempt, '%Y-%m-%d %H:%M:%S')
        time_diff = datetime.now() - last_attempt_time
        
        # Reset attempts after 1 hour
        if time_diff.total_seconds() > 3600:
            cursor.execute('UPDATE users SET login_attempts = 0 WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            return True
    
    if attempts >= 5:
        conn.close()
        return False
    
    conn.close()
    return True

# Update login attempts
def update_login_attempts(user_id, success=False):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    if success:
        cursor.execute('UPDATE users SET login_attempts = 0, last_login_attempt = datetime("now") WHERE user_id = ?', (user_id,))
    else:
        cursor.execute('SELECT login_attempts FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        attempts = result[0] + 1 if result else 1
        
        cursor.execute('UPDATE users SET login_attempts = ?, last_login_attempt = datetime("now") WHERE user_id = ?', 
                      (attempts, user_id))
    
    conn.commit()
    conn.close()

# Auto bonus system
def check_and_add_bonus():
    while True:
        try:
            conn = sqlite3.connect('users.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT t.id, t.user_id, t.amount 
                FROM transactions t 
                WHERE t.status = 'approved' 
                AND t.type = 'deposit'
                AND datetime(t.created_date) <= datetime('now', '-24 hours')
                AND NOT EXISTS (
                    SELECT 1 FROM bonuses b 
                    WHERE b.transaction_id = t.id AND b.bonus_type = 'delayed'
                )
            ''')
            transactions = cursor.fetchall()
            
            for txn_id, user_id, amount in transactions:
                delayed_bonus_amount = (amount * DELAYED_BONUS_PERCENT) / 100
                
                cursor.execute('UPDATE users SET bonus_balance = bonus_balance + ? WHERE user_id = ?', 
                             (delayed_bonus_amount, user_id))
                
                cursor.execute('''
                    INSERT INTO bonuses (user_id, amount, transaction_id, bonus_type, status, created_date)
                    VALUES (?, ?, ?, 'delayed', 'completed', datetime("now"))
                ''', (user_id, delayed_bonus_amount, txn_id))
                
                try:
                    from telegram import Bot
                    bot = Bot(token=TOKEN)
                    bot.send_message(
                        chat_id=user_id,
                        text=f"🎁 **ডেইলি বোনাস পেয়েছেন!**\n\n"
                             f"💰 {amount} টাকা রিচার্জের {DELAYED_BONUS_PERCENT}% ডেইলি বোনাস: {delayed_bonus_amount} টাকা\n"
                             f"💳 আপনার বোনাস ব্যালেন্সে যোগ করা হয়েছে\n\n"
                             f"উইথড্র করতে /withdraw লিখুন"
                    )
                except:
                    pass
                
                print(f"Daily bonus added: User {user_id} got {delayed_bonus_amount} bonus")
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Bonus error: {e}")
        
        time.sleep(3600)

# Start bonus thread
def start_bonus_thread():
    bonus_thread = threading.Thread(target=check_and_add_bonus)
    bonus_thread.daemon = True
    bonus_thread.start()

# Check if user can withdraw (24 hours cooldown)
def can_user_withdraw(user_id):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT last_withdraw_date FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result or not result[0]:
        return True, None
    
    last_withdraw = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
    now = datetime.now()
    time_diff = now - last_withdraw
    
    if time_diff.total_seconds() >= 24 * 3600:
        return True, None
    else:
        next_withdraw = last_withdraw + timedelta(hours=24)
        remaining_time = next_withdraw - now
        hours = int(remaining_time.total_seconds() // 3600)
        minutes = int((remaining_time.total_seconds() % 3600) // 60)
        return False, f"{hours} ঘন্টা {minutes} মিনিট"

# /start command - UPDATED WITH PASSWORD SYSTEM
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    
    args = context.args
    referral_code = args[0] if args else None
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT phone, is_verified, is_active, password FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    if result:
        phone, is_verified, is_active, password = result
        
        if is_active == 0:
            await update.message.reply_text("❌ আপনার অ্যাকাউন্ট ব্লক করা হয়েছে!")
            conn.close()
            return ConversationHandler.END
        
        if is_verified == 1:
            # User exists and verified, check if password is set
            if password:
                # Password is set, ask for login
                context.user_data['phone'] = phone
                await update.message.reply_text(
                    "🔐 **লগইন প্রয়োজন**\n\n"
                    "আপনার পাসওয়ার্ড দিন:"
                )
                return PASSWORD_LOGIN
            else:
                # No password set, ask to set one
                context.user_data['phone'] = phone
                await update.message.reply_text(
                    "🔒 **পাসওয়ার্ড সেটআপ**\n\n"
                    "আপনার অ্যাকাউন্ট সুরক্ষিত করতে একটি শক্তিশালী পাসওয়ার্ড সেট করুন:\n\n"
                    "📋 **পাসওয়ার্ড রিকোয়ারমেন্ট:**\n"
                    "• কমপক্ষে ৬ ক্যারেক্টার\n"
                    "• ১টি বড় হাতের অক্ষর (A-Z)\n"
                    "• ১টি ছোট হাতের অক্ষর (a-z)\n"
                    "• ১টি সংখ্যা (0-9)\n\n"
                    "আপনার নতুন পাসওয়ার্ড দিন:"
                )
                return PASSWORD_SETUP
    
    # New user registration flow
    if referral_code:
        context.user_data['referral_code'] = referral_code
    
    conn.close()
    
    await update.message.reply_text(
        "🤖 **META Income Bot - অ্যাকাউন্ট ভেরিফিকেশন**\n\n"
        "আপনার ফোন নম্বর দিন (11 ডিজিট):\n"
        "উদাহরণ: 01712345678"
    )
    return PHONE

# Handle phone number input
async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_number = update.message.text.strip()
    
    if re.match(r'^01[3-9]\d{8}$', phone_number):
        user_id = update.message.from_user.id
        
        # Check if phone already exists
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users WHERE phone = ?', (phone_number,))
        existing_user = cursor.fetchone()
        conn.close()
        
        if existing_user:
            await update.message.reply_text(
                "❌ এই ফোন নম্বর ইতিমধ্যে রেজিস্টার্ড!\n\n"
                "আপনি ইতিমধ্যে রেজিস্টার্ড ইউজার। /start লিখে লগইন করুন।"
            )
            return ConversationHandler.END
        
        verification_code = str(random.randint(1000, 9999))
        
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO verification_codes (phone, code, created_time)
            VALUES (?, ?, datetime("now"))
        ''', (phone_number, verification_code))
        conn.commit()
        conn.close()
        
        context.user_data['phone'] = phone_number
        context.user_data['verification_code'] = verification_code
        
        await update.message.reply_text(
            f"✅ **ফোন নম্বর গ্রহণ করা হয়েছে!**\n\n"
            f"📱 ফোন: {phone_number}\n"
            f"🔐 আপনার ভেরিফিকেশন কোড: **{verification_code}**\n\n"
            "4 ডিজিটের কোডটি টাইপ করুন:"
        )
        
        return VERIFICATION
    else:
        await update.message.reply_text(
            "❌ ভুল ফোন নম্বর!\n\n"
            "সঠিক ফোন নম্বর দিন (11 ডিজিট):\n"
            "উদাহরণ: 01712345678\n\n"
            "আবার চেষ্টা করুন:"
        )
        return PHONE

# Handle verification code
async def handle_verification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    verification_code = context.user_data.get('verification_code')
    phone_number = context.user_data.get('phone')
    user_id = update.message.from_user.id
    referral_code = context.user_data.get('referral_code')
    
    if user_input == verification_code:
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        
        # Generate unique random referral code
        new_referral_code = generate_referral_code()
        
        referred_by = None
        if referral_code:
            cursor.execute('SELECT user_id FROM users WHERE referral_code = ?', (referral_code,))
            referrer = cursor.fetchone()
            if referrer:
                referred_by = referrer[0]
        
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, phone, referral_code, referred_by, joined_date, is_verified, is_active)
            VALUES (?, ?, ?, ?, datetime("now"), 1, 1)
        ''', (user_id, phone_number, new_referral_code, referred_by))
        
        conn.commit()
        conn.close()
        
        # Ask for password setup
        context.user_data['phone'] = phone_number
        await update.message.reply_text(
            "🎉 **ভেরিফিকেশন সফল!**\n\n"
            "🔒 **পাসওয়ার্ড সেটআপ**\n\n"
            "আপনার অ্যাকাউন্ট সুরক্ষিত করতে একটি শক্তিশালী পাসওয়ার্ড সেট করুন:\n\n"
            "📋 **পাসওয়ার্ড রিকোয়ারমেন্ট:**\n"
            "• কমপক্ষে ৬ ক্যারেক্টার\n"
            "• ১টি বড় হাতের অক্ষর (A-Z)\n"
            "• ১টি ছোট হাতের অক্ষর (a-z)\n"
            "• ১টি সংখ্যা (0-9)\n\n"
            "আপনার নতুন পাসওয়ার্ড দিন:"
        )
        return PASSWORD_SETUP
    else:
        await update.message.reply_text("❌ ভুল ভেরিফিকেশন কোড। আবার চেষ্টা করুন:")
        return VERIFICATION

# Handle password setup
async def handle_password_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    password = update.message.text.strip()
    phone = context.user_data.get('phone')
    
    # Validate password strength
    is_valid, message = is_strong_password(password)
    
    if not is_valid:
        await update.message.reply_text(
            f"❌ {message}\n\n"
            "দয়া করে শক্তিশালী পাসওয়ার্ড দিন:"
        )
        return PASSWORD_SETUP
    
    # Save password to database
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET password = ? WHERE user_id = ?', (password, user_id))
    conn.commit()
    
    # Get user data for welcome message
    cursor.execute('SELECT referral_code, balance, bonus_balance FROM users WHERE user_id = ?', (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    
    referral_code = user_data[0]
    bot_username = "metaincome_bot"
    referral_link = f"https://t.me/{bot_username}?start={referral_code}"
    
    welcome_message = (
        f"✅ **পাসওয়ার্ড সেটআপ সফল!**\n\n"
        f"🔐 আপনার অ্যাকাউন্ট এখন সুরক্ষিত\n"
        f"📱 ফোন: {phone}\n"
        f"🔗 আপনার রেফারেল কোড: `{referral_code}`\n"
        f"🔗 আপনার রেফারেল লিংক:\n{referral_link}\n\n"
        f"💰 ব্যালেন্স: 0 টাকা\n"
        f"🎁 বোনাস: 0 টাকা\n\n"
        f"💡 **পরবর্তী বার লগইন করতে /start লিখুন**\n\n"
        f"💳 রিচার্জ করতে /recharge লিখুন\n"
        f"🏧 উইথড্র করতে /withdraw লিখুন\n"
        f"🔗 রেফারেল দেখতে /referral লিখুন"
    )
    
    await update.message.reply_text(welcome_message)
    return ConversationHandler.END

# Handle password login
async def handle_password_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    password_input = update.message.text.strip()
    phone = context.user_data.get('phone')
    
    # Check login attempts
    if not check_login_attempts(user_id):
        await update.message.reply_text(
            "❌ **অনেকবার ভুল পাসওয়ার্ড দেওয়ার尝试!**\n\n"
            "আপনার অ্যাকাউন্ট ১ ঘন্টার জন্য লক করা হয়েছে।\n"
            "১ ঘন্টা পর আবার চেষ্টা করুন।"
        )
        return ConversationHandler.END
    
    # Verify password
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT password FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    if not result or result[0] != password_input:
        update_login_attempts(user_id, success=False)
        
        cursor.execute('SELECT login_attempts FROM users WHERE user_id = ?', (user_id,))
        attempts_result = cursor.fetchone()
        attempts = attempts_result[0] if attempts_result else 1
        
        remaining_attempts = 5 - attempts
        
        await update.message.reply_text(
            f"❌ **ভুল পাসওয়ার্ড!**\n\n"
            f"📊 অবশিষ্ট চেষ্টা: {remaining_attempts} বার\n\n"
            f"আবার পাসওয়ার্ড দিন:"
        )
        conn.close()
        return PASSWORD_LOGIN
    
    # Successful login
    update_login_attempts(user_id, success=True)
    
    cursor.execute('SELECT referral_code, balance, bonus_balance FROM users WHERE user_id = ?', (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    
    referral_code, balance, bonus_balance = user_data
    
    await update.message.reply_text(
        f"✅ **লগইন সফল!**\n\n"
        f"🤖 **META Income Bot**\n\n"
        f"📱 ফোন: {phone}\n"
        f"💰 ব্যালেন্স: {balance} টাকা\n"
        f"🎁 বোনাস: {bonus_balance} টাকা\n"
        f"🔗 রেফারেল কোড: `{referral_code}`\n\n"
        f"রিচার্জ করতে /recharge লিখুন\n"
        f"উইথড্র করতে /withdraw লিখুন\n"
        f"রেফারেল দেখতে /referral লিখুন"
    )
    return ConversationHandler.END

# Change password command
async def change_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT phone, password FROM users WHERE user_id = ? AND is_verified = 1', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text("❌ প্রথমে /start লিখে লগইন করুন")
        return
    
    phone, current_password = result
    
    if not current_password:
        await update.message.reply_text("❌ আপনার কোনো পাসওয়ার্ড সেট নেই। /start লিখে পাসওয়ার্ড সেট করুন।")
        return
    
    context.user_data['changing_password'] = True
    context.user_data['phone'] = phone
    
    await update.message.reply_text(
        "🔐 **পাসওয়ার্ড পরিবর্তন**\n\n"
        "প্রথমে আপনার বর্তমান পাসওয়ার্ড দিন:"
    )
    return PASSWORD_LOGIN

# Handle password change after verification
async def handle_password_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    password_input = update.message.text.strip()
    
    if not context.user_data.get('changing_password'):
        return await handle_password_login(update, context)
    
    # Verify current password
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT password FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    if not result or result[0] != password_input:
        await update.message.reply_text(
            "❌ **ভুল পাসওয়ার্ড!**\n\n"
            "আবার চেষ্টা করুন:"
        )
        return PASSWORD_LOGIN
    
    # Current password verified, now ask for new password
    context.user_data['current_password_verified'] = True
    conn.close()
    
    await update.message.reply_text(
        "✅ **বর্তমান পাসওয়ার্ড verified!**\n\n"
        "এখন আপনার নতুন পাসওয়ার্ড দিন:\n\n"
        "📋 **পাসওয়ার্ড রিকোয়ারমেন্ট:**\n"
        "• কমপক্ষে ৬ ক্যারেক্টার\n"
        "• ১টি বড় হাতের অক্ষর (A-Z)\n"
        "• ১টি ছোট হাতের অক্ষর (a-z)\n"
        "• ১টি সংখ্যা (0-9)"
    )
    return PASSWORD_SETUP

# Referral command - UPDATED WITH PASSWORD CHECK
async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT referral_code, phone, password FROM users WHERE user_id = ? AND is_verified = 1', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text("❌ প্রথমে /start লিখে লগইন করুন")
        return
    
    referral_code, phone, password = result
    
    if not password:
        await update.message.reply_text("❌ প্রথমে /start লিখে পাসওয়ার্ড সেটআপ সম্পন্ন করুন")
        return
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ?', (user_id,))
    total_referrals = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND instant_bonus_paid = 1', (user_id,))
    successful_referrals = cursor.fetchone()[0]
    conn.close()
    
    bot_username = "metaincome_bot"
    referral_link = f"https://t.me/{bot_username}?start={referral_code}"
    
    message = (
        f"🤖 **META Income - রেফারেল সিস্টেম**\n\n"
        f"📱 আপনার নম্বর: {phone}\n"
        f"🔐 রেফারেল কোড: `{referral_code}`\n"
        f"🔗 রেফারেল লিংক:\n{referral_link}\n\n"
        f"📊 **স্ট্যাটিস্টিক্স:**\n"
        f"👥 মোট রেফারেল: {total_referrals} জন\n"
        f"✅ সফল রেফারেল: {successful_referrals} জন\n\n"
        f"🎁 **বোনাস সিস্টেম:**\n"
        f"• আপনার রেফারেল রিচার্জ করলে\n"
        f"• আপনি পাবেন: {REFERRAL_BONUS_PERCENT}% ইন্সট্যান্ট বোনাস\n"
        f"• রেফারেল পাবে: {REFERRAL_BONUS_PERCENT}% ইন্সট্যান্ট + প্রতিদিন {DELAYED_BONUS_PERCENT}% বোনাস\n\n"
        f"💰 **উদাহরণ:**\n"
        f"রেফারেল 1000 টাকা রিচার্জ করলে:\n"
        f"• আপনি পাবেন: 200 টাকা ইন্সট্যান্ট\n"
        f"• রেফারেল পাবে: 200 টাকা ইন্সট্যান্ট + প্রতিদিন 200 টাকা বোনাস\n\n"
        f"🔗 লিংক শেয়ার করে টাকা উপার্জন করুন!"
    )
    
    await update.message.reply_text(message)

# Recharge command - UPDATED WITH PASSWORD CHECK
async def recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT is_verified, is_active, password FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result or result[0] != 1:
        await update.message.reply_text("❌ প্রথমে /start লিখে লগইন করুন")
        return
    if result[1] == 0:
        await update.message.reply_text("❌ আপনার অ্যাকাউন্ট ব্লক করা হয়েছে!")
        return
    if not result[2]:
        await update.message.reply_text("❌ প্রথমে /start লিখে পাসওয়ার্ড সেটআপ সম্পন্ন করুন")
        return
    
    keyboard = []
    for amount in FIXED_AMOUNTS:
        keyboard.append([InlineKeyboardButton(f"{amount} টাকা", callback_data=f"amount_{amount}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🤖 **META Income - রিচার্জ সিস্টেম**\n\n"
        "রিচার্জের জন্য নিচের বাটন থেকে অ্যামাউন্ট সিলেক্ট করুন:",
        reply_markup=reply_markup
    )

# Balance command - UPDATED WITH PASSWORD CHECK
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT balance, bonus_balance, password FROM users WHERE user_id = ? AND is_verified = 1', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text("❌ প্রথমে /start লিখে লগইন করুন")
        return
    
    balance, bonus_balance, password = result
    
    if not password:
        await update.message.reply_text("❌ প্রথমে /start লিখে পাসওয়ার্ড সেটআপ সম্পন্ন করুন")
        return
    
    can_withdraw, remaining_time = can_user_withdraw(user_id)
    
    message = (
        f"🤖 **META Income - ব্যালেন্স**\n\n"
        f"💰 মূল ব্যালেন্স: {balance} টাকা\n"
        f"🎁 বোনাস ব্যালেন্স: {bonus_balance} টাকা\n"
        f"💵 মোট: {balance + bonus_balance} টাকা\n\n"
    )
    
    if can_withdraw:
        message += f"✅ উইথড্র উপলব্ধ\n"
    else:
        message += f"⏳ উইথড্র কুলডাউন: {remaining_time}\n"
    
    message += f"\nরিচার্জ করতে /recharge লিখুন\nউইথড্র করতে /withdraw লিখুন"
    
    await update.message.reply_text(message)

# Withdraw command - UPDATED WITH PASSWORD CHECK
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT is_verified, is_active, password FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result or result[0] != 1:
        await update.message.reply_text("❌ প্রথমে /start লিখে লগইন করুন")
        return
    if result[1] == 0:
        await update.message.reply_text("❌ আপনার অ্যাকাউন্ট ব্লক করা হয়েছে!")
        return
    if not result[2]:
        await update.message.reply_text("❌ প্রথমে /start লিখে পাসওয়ার্ড সেটআপ সম্পন্ন করুন")
        return
    
    # Check if user can withdraw
    can_withdraw, remaining_time = can_user_withdraw(user_id)
    if not can_withdraw:
        await update.message.reply_text(
            f"⏳ **উইথড্র কুলডাউন**\n\n"
            f"আপনি ইতিমধ্যে আজ উইথড্র করেছেন!\n"
            f"⏰ আবার উইথড্র করতে পারবেন: {remaining_time} পর\n\n"
            f"💡 প্রতি 24 ঘন্টায় 1 বার উইথড্র করতে পারবেন"
        )
        return
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT bonus_balance FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text("❌ প্রথমে /start লিখে লগইন করুন")
        return
    
    bonus_balance = result[0]
    
    if bonus_balance < min(WITHDRAW_AMOUNTS):
        await update.message.reply_text(
            f"❌ পর্যাপ্ত বোনাস নেই!\n\n"
            f"🎁 আপনার বোনাস: {bonus_balance} টাকা\n"
            f"💰 সর্বনিম্ন উইথড্র: {min(WITHDRAW_AMOUNTS)} টাকা\n\n"
            f"রিচার্জ করে বোনাস সংগ্রহ করুন!"
        )
        return
    
    keyboard = []
    for amount in WITHDRAW_AMOUNTS:
        if amount <= bonus_balance:
            keyboard.append([InlineKeyboardButton(f"{amount} টাকা", callback_data=f"withdraw_{amount}")])
    
    if not keyboard:
        await update.message.reply_text("❌ পর্যাপ্ত বোনাস নেই!")
        return
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = (
        f"🤖 **META Income - উইথড্র সিস্টেম**\n\n"
        f"🎁 আপনার বোনাস: {bonus_balance} টাকা\n"
        f"⏰ প্রতি 24 ঘন্টায় 1 বার উইথড্র\n\n"
        f"উইথড্র অ্যামাউন্ট সিলেক্ট করুন:"
    )
    
    await update.message.reply_text(message, reply_markup=reply_markup)

# Handle amount selection
async def handle_amount_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith('amount_'):
        amount = int(data.split('_')[1])
        
        context.user_data['selected_amount'] = amount
        
        # Show payment method selection
        keyboard = [
            [InlineKeyboardButton("📱 বিকাশ", callback_data=f"recharge_bkash_{amount}")],
            [InlineKeyboardButton("📱 নগদ", callback_data=f"recharge_nagad_{amount}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"💰 **রিচার্জ অ্যামাউন্ট: {amount} টাকা**\n\n"
            f"পেমেন্ট মেথড সিলেক্ট করুন:",
            reply_markup=reply_markup
        )

# Handle payment method selection for recharge
async def handle_recharge_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith('recharge_bkash_') or data.startswith('recharge_nagad_'):
        parts = data.split('_')
        payment_method = parts[1]  # bkash or nagad
        amount = int(parts[2])
        
        context.user_data['selected_amount'] = amount
        context.user_data['payment_method'] = payment_method
        context.user_data['waiting_for_txn'] = True
        
        method_name = "বিকাশ" if payment_method == "bkash" else "নগদ"
        account_number = YOUR_BKASH if payment_method == "bkash" else YOUR_NAGAD
        
        instant_bonus = (amount * REFERRAL_BONUS_PERCENT) / 100
        
        await query.edit_message_text(
            f"🤖 **META Income - রিচার্জ**\n\n"
            f"💰 রিচার্জ অ্যামাউন্ট: {amount} টাকা\n"
            f"📱 আমাদের {method_name} নম্বর: `{account_number}`\n\n"
            f"✅ **{amount} টাকা** উপরের {method_name} নম্বরে সেন্ড করুন\n\n"
            f"🎁 **বোনাস ডিটেইলস:**\n"
            f"• ইন্সট্যান্ট {REFERRAL_BONUS_PERCENT}% বোনাস: {instant_bonus} টাকা\n"
            f"• প্রতিদিন {DELAYED_BONUS_PERCENT}% ডেইলি বোনাস\n\n"
            f"💰 টাকা সেন্ড করার পর:\n"
            f"1. ট্র্যানজেকশন আইডি নোট করুন\n"
            f"2. এই ফরম্যাটে মেসেজ দিন:\n\n"
            f"`{amount} TXN123ABC`\n\n"
            f"যেখানে:\n"
            f"• **{amount}** = টাকার পরিমাণ\n"
            f"• **TXN123ABC** = আপনার ট্র্যানজেকশন আইডি\n\n"
            f"টাকা সেন্ড করুন এবং Transaction ID দিন:"
        )

# Handle transaction ID input
async def handle_transaction_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    
    if not context.user_data.get('waiting_for_txn'):
        return
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT is_verified, is_active FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    if not result or result[0] != 1:
        await update.message.reply_text("❌ প্রথমে /start লিখে লগইন করুন")
        conn.close()
        return
    if result[1] == 0:
        await update.message.reply_text("❌ আপনার অ্যাকাউন্ট ব্লক করা হয়েছে!")
        conn.close()
        return
    
    parts = text.split()
    if len(parts) == 2 and parts[0].isdigit():
        amount = float(parts[0])
        transaction_id = parts[1]
        
        selected_amount = context.user_data.get('selected_amount')
        payment_method = context.user_data.get('payment_method')
        
        if amount != selected_amount:
            await update.message.reply_text(
                f"❌ অ্যামাউন্ট মিলেনি!\n\n"
                f"আপনি সিলেক্ট করেছিলেন: {selected_amount} টাকা\n"
                f"আপনি দিয়েছেন: {amount} টাকা\n\n"
                f"সঠিক অ্যামাউন্ট দিয়ে আবার চেষ্টা করুন।\n"
                f"রিচার্জ করতে /recharge লিখুন"
            )
            conn.close()
            return
        
        cursor.execute('SELECT referred_by FROM users WHERE user_id = ?', (user_id,))
        referrer_result = cursor.fetchone()
        referred_by = referrer_result[0] if referrer_result else None
        
        # Save transaction with payment method
        cursor.execute('''
            INSERT INTO transactions (user_id, amount, type, status, transaction_id, payment_method, created_date)
            VALUES (?, ?, 'deposit', 'pending', ?, ?, datetime("now"))
        ''', (user_id, amount, transaction_id, payment_method))
        
        if referred_by:
            cursor.execute('''
                INSERT OR REPLACE INTO referrals (referrer_id, referee_id, created_date)
                VALUES (?, ?, datetime("now"))
            ''', (referred_by, user_id))
        
        conn.commit()
        conn.close()
        
        context.user_data['waiting_for_txn'] = False
        
        method_name = "বিকাশ" if payment_method == "bkash" else "নগদ"
        instant_bonus = (amount * REFERRAL_BONUS_PERCENT) / 100
        
        message = (
            f"✅ **রিচার্জ রিকুয়েস্ট গ্রহণ করা হয়েছে!**\n\n"
            f"💰 পরিমাণ: {amount} টাকা\n"
            f"📊 পেমেন্ট: {method_name}\n"
            f"📋 ট্র্যানজেকশন আইডি: {transaction_id}\n\n"
        )
        
        if referred_by:
            message += f"🔗 **রেফারেল বোনাস:**\nApprove হলে আপনি এবং রেফারার প্রত্যেকে {instant_bonus} টাকা বোনাস পাবেন!\n\n"
        
        message += (
            f"🎁 **বোনাস ডিটেইলস:**\n"
            f"• ইন্সট্যান্ট {REFERRAL_BONUS_PERCENT}%: {instant_bonus} টাকা\n"
            f"• প্রতিদিন {DELAYED_BONUS_PERCENT}% ডেইলি বোনাস\n\n"
            f"⏳ অ্যাডমিন ভেরিফিকেশনের জন্য অপেক্ষা করুন\n"
            f"ব্যালেন্স চেক করতে /balance লিখুন"
        )
        
        await update.message.reply_text(message)
    else:
        await update.message.reply_text(
            "❌ ভুল ফরম্যাট!\n\n"
            "সঠিক ফরম্যাটে মেসেজ দিন:\n"
            f"`{context.user_data.get('selected_amount', '200')} TXN123ABC`\n\n"
            "যেখানে:\n"
            f"• {context.user_data.get('selected_amount', '200')} = টাকার পরিমাণ\n"
            "• TXN123ABC = ট্র্যানজেকশন আইডি\n\n"
            "আবার চেষ্টা করুন:"
        )
        conn.close()

# Handle withdraw amount selection
async def handle_withdraw_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    # Check if user can withdraw
    can_withdraw, remaining_time = can_user_withdraw(user_id)
    if not can_withdraw:
        await query.edit_message_text(
            f"⏳ **উইথড্র কুলডাউন**\n\n"
            f"আপনি ইতিমধ্যে আজ উইথড্র করেছেন!\n"
            f"⏰ আবার উইথড্র করতে পারবেন: {remaining_time} পর"
        )
        return
    
    if data.startswith('withdraw_'):
        amount = int(data.split('_')[1])
        
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute('SELECT bonus_balance FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        
        if not result or result[0] < amount:
            await query.edit_message_text("❌ পর্যাপ্ত বোনাস নেই!")
            conn.close()
            return
        
        conn.close()
        
        # Save amount to context
        context.user_data['withdraw_amount'] = amount
        
        # Ask for payment method
        keyboard = [
            [InlineKeyboardButton("📱 বিকাশ", callback_data="method_bkash")],
            [InlineKeyboardButton("📱 নগদ", callback_data="method_nagad")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"💰 **উইথড্র অ্যামাউন্ট: {amount} টাকা**\n\n"
            f"পেমেন্ট মেথড সিলেক্ট করুন:",
            reply_markup=reply_markup
        )

# Handle payment method selection for withdraw
async def handle_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith('method_'):
        payment_method = data.split('_')[1]
        context.user_data['payment_method'] = payment_method
        
        method_name = "বিকাশ" if payment_method == "bkash" else "নগদ"
        amount = context.user_data.get('withdraw_amount')
        
        await query.edit_message_text(
            f"📱 **{method_name} নম্বর প্রয়োজন**\n\n"
            f"💰 উইথড্র অ্যামাউন্ট: {amount} টাকা\n"
            f"📊 পেমেন্ট মেথড: {method_name}\n\n"
            f"আপনার {method_name} নম্বর দিন (11 ডিজিট):\n"
            f"উদাহরণ: 01712345678"
        )
        
        # Set flag to indicate we're waiting for account number
        context.user_data['waiting_for_account'] = True
        return WITHDRAW_ACCOUNT

# Handle account number input for withdraw
async def handle_withdraw_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    account_number = update.message.text.strip()
    withdraw_amount = context.user_data.get('withdraw_amount')
    payment_method = context.user_data.get('payment_method')
    
    # Check if we're actually waiting for account number
    if not context.user_data.get('waiting_for_account'):
        return ConversationHandler.END
    
    # Check if user can withdraw
    can_withdraw, remaining_time = can_user_withdraw(user_id)
    if not can_withdraw:
        await update.message.reply_text(
            f"⏳ **উইথড্র কুলডাউন**\n\n"
            f"আপনি ইতিমধ্যে আজ উইথড্র করেছেন!\n"
            f"⏰ আবার উইথড্র করতে পারবেন: {remaining_time} পর"
        )
        context.user_data['waiting_for_account'] = False
        return ConversationHandler.END
    
    if re.match(r'^01[3-9]\d{8}$', account_number) and withdraw_amount and payment_method:
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        
        try:
            # Check balance again
            cursor.execute('SELECT bonus_balance FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            
            if not result or result[0] < withdraw_amount:
                await update.message.reply_text("❌ পর্যাপ্ত বোনাস নেই!")
                context.user_data['waiting_for_account'] = False
                return ConversationHandler.END
            
            # Update user's account number
            if payment_method == "bkash":
                cursor.execute('UPDATE users SET bkash_number = ? WHERE user_id = ?', (account_number, user_id))
            else:
                cursor.execute('UPDATE users SET nagad_number = ? WHERE user_id = ?', (account_number, user_id))
            
            # Save withdraw request
            cursor.execute('''
                INSERT INTO withdrawals (user_id, amount, payment_method, account_number, status, created_date)
                VALUES (?, ?, ?, ?, 'pending', datetime("now"))
            ''', (user_id, withdraw_amount, payment_method, account_number))
            
            # Update last withdraw date
            cursor.execute('UPDATE users SET last_withdraw_date = datetime("now") WHERE user_id = ?', (user_id,))
            
            conn.commit()
            
            method_name = "বিকাশ" if payment_method == "bkash" else "নগদ"
            
            await update.message.reply_text(
                f"✅ **উইথড্র রিকুয়েস্ট পাঠানো হয়েছে!**\n\n"
                f"💰 পরিমাণ: {withdraw_amount} টাকা\n"
                f"📊 পেমেন্ট মেথড: {method_name}\n"
                f"📱 অ্যাকাউন্ট: {account_number}\n\n"
                f"⏳ **অ্যাডমিন অ্যাপ্রুভের জন্য অপেক্ষা করুন**\n"
                f"অ্যাডমিন রিকুয়েস্ট Approve করলে আপনার বোনাস ব্যালেন্স থেকে টাকা অটো কেটে নেওয়া হবে\n\n"
                f"✅ প্রতি 24 ঘন্টায় 1 বার উইথড্র করতে পারবেন\n"
                f"⏰ পরবর্তী উইথড্র: আগামীকাল"
            )
            
            # Clear the waiting flag
            context.user_data['waiting_for_account'] = False
            return ConversationHandler.END
            
        except Exception as e:
            logging.error(f"Withdraw error: {e}")
            await update.message.reply_text("❌ সিস্টেম এরর! আবার চেষ্টা করুন")
            context.user_data['waiting_for_account'] = False
            return ConversationHandler.END
        finally:
            conn.close()
    else:
        await update.message.reply_text(
            "❌ ভুল অ্যাকাউন্ট নম্বর!\n\n"
            "সঠিক অ্যাকাউন্ট নম্বর দিন (11 ডিজিট):\n"
            "উদাহরণ: 01712345678\n\n"
            "আবার চেষ্টা করুন:"
        )
        return WITHDRAW_ACCOUNT

# Admin login
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔐 **META Income - অ্যাডমিন লগইন**\n\nপাসওয়ার্ড দিন:")
    return ADMIN_LOGIN

# Handle admin password
async def handle_admin_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    
    if password == ADMIN_PASSWORD:
        context.user_data['is_admin'] = True
        await update.message.reply_text(
            "✅ **লগইন সফল!**\n\n"
            "📊 **অ্যাডমিন কমান্ডস:**\n"
            "/pending - পেন্ডিং রিচার্জ\n"
            "/withdrawals - পেন্ডিং উইথড্র\n"
            "/users - সব ইউজার\n"
            "/transactions - সব ট্র্যানজেকশন\n"
            "/stats - স্ট্যাটিস্টিক্স\n\n"
            "💡 **সুবিধা:**\n"
            "- ✅ Approve = ব্যালেন্স যোগ হবে\n"
            "- ❌ Reject = রিকুয়েস্ট ডিলিট হবে"
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ ভুল পাসওয়ার্ড! আবার চেষ্টা করুন:")
        return ADMIN_LOGIN

# Show pending recharge requests
async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('is_admin'):
        await update.message.reply_text("❌ অ্যাডমিন এক্সেস প্রয়োজন! /admin লিখুন")
        return
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT t.id, u.phone, t.amount, t.payment_method, t.transaction_id, t.created_date
        FROM transactions t 
        JOIN users u ON t.user_id = u.user_id 
        WHERE t.status = 'pending'
    ''')
    pending_requests = cursor.fetchall()
    conn.close()
    
    if not pending_requests:
        await update.message.reply_text("✅ কোনো পেন্ডিং রিচার্জ নেই")
        return
    
    for req in pending_requests:
        req_id, phone, amount, payment_method, txn_id, date = req
        
        method_name = "বিকাশ" if payment_method == "bkash" else "নগদ"
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{req_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{req_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = (
            f"⏳ **পেন্ডিং রিচার্জ:**\n\n"
            f"🆔 রিকুয়েস্ট ID: {req_id}\n"
            f"📱 ইউজার: {phone}\n"
            f"💰 অ্যামাউন্ট: {amount} টাকা\n"
            f"📊 পেমেন্ট: {method_name}\n"
            f"📋 TXN ID: {txn_id}\n"
            f"📅 তারিখ: {date}\n\n"
            f"নিচের বাটন ক্লিক করুন:"
        )
        
        await update.message.reply_text(message, reply_markup=reply_markup)

# Show pending withdrawals
async def withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('is_admin'):
        await update.message.reply_text("❌ অ্যাডমিন এক্সেস প্রয়োজন! /admin লিখুন")
        return
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT w.id, u.phone, w.amount, w.payment_method, w.account_number, w.created_date
        FROM withdrawals w 
        JOIN users u ON w.user_id = u.user_id 
        WHERE w.status = 'pending'
    ''')
    pending_withdrawals = cursor.fetchall()
    conn.close()
    
    if not pending_withdrawals:
        await update.message.reply_text("✅ কোনো পেন্ডিং উইথড্র নেই")
        return
    
    for withdraw in pending_withdrawals:
        w_id, phone, amount, method, account, date = withdraw
        
        method_name = "বিকাশ" if method == "bkash" else "নগদ"
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Pay", callback_data=f"pay_{w_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{w_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = (
            f"⏳ **পেন্ডিং উইথড্র:**\n\n"
            f"🆔 রিকুয়েস্ট ID: {w_id}\n"
            f"📱 ইউজার: {phone}\n"
            f"💰 অ্যামাউন্ট: {amount} টাকা\n"
            f"📊 পেমেন্ট: {method_name}\n"
            f"📱 অ্যাকাউন্ট: {account}\n"
            f"📅 তারিখ: {date}\n\n"
            f"নিচের বাটন ক্লিক করুন:"
        )
        
        await update.message.reply_text(message, reply_markup=reply_markup)

# Show all transactions
async def transactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('is_admin'):
        await update.message.reply_text("❌ অ্যাডমিন এক্সেস প্রয়োজন! /admin লিখুন")
        return
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM users WHERE is_verified = 1')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT t.id, u.phone, t.amount, t.type, t.status, t.payment_method, t.transaction_id, t.created_date
        FROM transactions t 
        JOIN users u ON t.user_id = u.user_id 
        ORDER BY t.id DESC LIMIT 50
    ''')
    all_transactions = cursor.fetchall()
    conn.close()
    
    if not all_transactions:
        await update.message.reply_text("❌ কোনো ট্র্যানজেকশন নেই")
        return
    
    message = f"📊 **META Income - ট্র্যানজেকশন হিস্ট্রি**\n\n👥 **মোট ইউজার:** {total_users} জন\n\n"
    
    for txn in all_transactions:
        if txn[4] == "approved":
            status_icon = "✅"
        elif txn[4] == "rejected":
            status_icon = "❌"
        else:
            status_icon = "⏳"
        
        payment_method = txn[5] if txn[5] else "N/A"
        method_name = "বিকাশ" if payment_method == "bkash" else "নগদ" if payment_method == "nagad" else payment_method
        
        message += f"{status_icon} **ID:** {txn[0]}\n"
        message += f"📱 **ইউজার:** {txn[1]}\n"
        message += f"💰 **টাকা:** {txn[2]}\n"
        message += f"📊 **টাইপ:** {txn[3]}\n"
        message += f"🔰 **স্ট্যাটাস:** {txn[4]}\n"
        message += f"💳 **পেমেন্ট:** {method_name}\n"
        if txn[6]:
            message += f"📋 **TXN ID:** {txn[6]}\n"
        message += f"📅 **তারিখ:** {txn[7]}\n"
        message += "─" * 30 + "\n"
    
    await update.message.reply_text(message)

# Show statistics
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('is_admin'):
        await update.message.reply_text("❌ অ্যাডমিন এক্সেস প্রয়োজন! /admin লিখুন")
        return
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM users WHERE is_verified = 1')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(balance), SUM(bonus_balance) FROM users WHERE is_verified = 1')
    balance_result = cursor.fetchone()
    total_balance = balance_result[0] or 0
    total_bonus = balance_result[1] or 0
    
    cursor.execute('SELECT SUM(amount) FROM transactions WHERE status = "approved" AND type = "deposit"')
    total_deposits = cursor.fetchone()[0] or 0
    
    cursor.execute('SELECT COUNT(*) FROM transactions WHERE status = "pending"')
    pending_requests = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM referrals')
    total_referrals = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM withdrawals WHERE status = "paid"')
    total_withdrawals = cursor.fetchone()[0]
    
    conn.close()
    
    message = (
        "🤖 **META Income - বট স্ট্যাটিস্টিক্স:**\n\n"
        f"👥 **মোট ইউজার:** {total_users} জন\n"
        f"💰 **মোট ব্যালেন্স:** {total_balance} টাকা\n"
        f"🎁 **মোট বোনাস:** {total_bonus} টাকা\n"
        f"💳 **মোট ডিপোজিট:** {total_deposits} টাকা\n"
        f"⏳ **পেন্ডিং রিকুয়েস্ট:** {pending_requests} টি\n"
        f"🔗 **মোট রেফারেল:** {total_referrals} জন\n"
        f"🏧 **মোট উইথড্র:** {total_withdrawals} টি\n"
        f"🎯 **বোনাস রেট:** {REFERRAL_BONUS_PERCENT}% ইন্সট্যান্ট + {DELAYED_BONUS_PERCENT}% ডেইলি\n"
        f"⏰ **উইথড্র লিমিট:** প্রতি 24 ঘন্টায় 1 বার"
    )
    
    await update.message.reply_text(message)

# Show all users
async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('is_admin'):
        await update.message.reply_text("❌ অ্যাডমিন এক্সেস প্রয়োজন! /admin লিখুন")
        return
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, phone, balance, bonus_balance, joined_date, is_active FROM users WHERE is_verified = 1')
    all_users = cursor.fetchall()
    conn.close()
    
    if not all_users:
        await update.message.reply_text("❌ কোনো ইউজার নেই")
        return
    
    message = f"🤖 **META Income - সব ইউজার** - মোট: {len(all_users)} জন\n\n"
    
    for user in all_users:
        status = "✅" if user[5] == 1 else "❌"
        message += f"{status} **ID:** {user[0]}\n"
        message += f"📱 **ফোন:** {user[1]}\n"
        message += f"💰 **ব্যালেন্স:** {user[2]} টাকা\n"
        message += f"🎁 **বোনাস:** {user[3]} টাকা\n"
        message += f"📅 **যোগদান:** {user[4]}\n"
        message += "─" * 30 + "\n"
    
    await update.message.reply_text(message)

# Handle admin buttons
async def handle_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if not context.user_data.get('is_admin'):
        await query.edit_message_text("❌ অ্যাডমিন এক্সেস প্রয়োজন!")
        return
    
    if data.startswith('approve_'):
        req_id = data.split('_')[1]
        await approve_recharge(query, context, req_id)
    
    elif data.startswith('reject_'):
        req_id = data.split('_')[1]
        await reject_recharge(query, context, req_id)
    
    elif data.startswith('pay_'):
        w_id = data.split('_')[1]
        await approve_withdraw(query, context, w_id)
    
    elif data.startswith('cancel_'):
        w_id = data.split('_')[1]
        await reject_withdraw(query, context, w_id)

# Approve recharge
async def approve_recharge(query, context, req_id):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT t.user_id, t.amount, u.referred_by, t.payment_method
        FROM transactions t 
        JOIN users u ON t.user_id = u.user_id 
        WHERE t.id = ? AND t.status = "pending"
    ''', (req_id,))
    transaction = cursor.fetchone()
    
    if not transaction:
        await query.edit_message_text("❌ ট্র্যানজেকশন পাওয়া যায়নি")
        conn.close()
        return
    
    user_id, amount, referred_by, payment_method = transaction
    
    cursor.execute('UPDATE transactions SET status = "approved" WHERE id = ?', (req_id,))
    
    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    
    user_instant_bonus = (amount * REFERRAL_BONUS_PERCENT) / 100
    cursor.execute('UPDATE users SET bonus_balance = bonus_balance + ? WHERE user_id = ?', 
                 (user_instant_bonus, user_id))
    
    if referred_by:
        cursor.execute('SELECT instant_bonus_paid FROM referrals WHERE referrer_id = ? AND referee_id = ?', 
                     (referred_by, user_id))
        referral_result = cursor.fetchone()
        
        if not referral_result or referral_result[0] == 0:
            referrer_instant_bonus = (amount * REFERRAL_BONUS_PERCENT) / 100
            cursor.execute('UPDATE users SET bonus_balance = bonus_balance + ? WHERE user_id = ?', 
                         (referrer_instant_bonus, referred_by))
            
            cursor.execute('UPDATE referrals SET instant_bonus_paid = 1 WHERE referrer_id = ? AND referee_id = ?', 
                         (referred_by, user_id))
        else:
            referrer_instant_bonus = 0
    
    conn.commit()
    
    cursor.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,))
    user_phone = cursor.fetchone()[0]
    
    referrer_phone = None
    if referred_by:
        cursor.execute('SELECT phone FROM users WHERE user_id = ?', (referred_by,))
        referrer_result = cursor.fetchone()
        referrer_phone = referrer_result[0] if referrer_result else None
    
    conn.close()
    
    method_name = "বিকাশ" if payment_method == "bkash" else "নগদ"
    
    try:
        user_message = (
            f"🎉 **META Income - রিচার্জ Approved!**\n\n"
            f"💰 {amount} টাকা আপনার অ্যাকাউন্টে যোগ করা হয়েছে!\n"
            f"🎁 ইন্সট্যান্ট {REFERRAL_BONUS_PERCENT}% বোনাস: {user_instant_bonus} টাকা পেয়েছেন!\n"
            f"📊 পেমেন্ট: {method_name}\n\n"
            f"⏰ প্রতিদিন {DELAYED_BONUS_PERCENT}% ডেইলি বোনাস পাবেন!"
        )
        await context.bot.send_message(chat_id=user_id, text=user_message)
    except:
        pass
    
    if referred_by and referrer_instant_bonus > 0:
        try:
            referrer_message = (
                f"🎉 **META Income - রেফারেল বোনাস!**\n\n"
                f"👤 আপনার রেফারেল: {user_phone}\n"
                f"💰 রিচার্জ করেছে: {amount} টাকা\n"
                f"🎁 আপনি পেয়েছেন: {referrer_instant_bonus} টাকা ইন্সট্যান্ট বোনাস!\n\n"
                f"💡 এটি একবারের বোনাস, পরবর্তী রিচার্জে আর বোনাস পাবেন না\n"
                f"💳 নতুন ব্যালেন্স চেক করতে /balance লিখুন"
            )
            await context.bot.send_message(chat_id=referred_by, text=referrer_message)
        except:
            pass
    
    admin_message = (
        f"✅ **রিচার্জ Approved!**\n\n"
        f"👤 ইউজার: {user_phone}\n"
        f"💰 টাকা: {amount} টাকা\n"
        f"📊 পেমেন্ট: {method_name}\n"
        f"🎁 ইউজার বোনাস: {user_instant_bonus} টাকা\n"
        f"🆔 রিকুয়েস্ট ID: {req_id}"
    )
    
    if referred_by and referrer_phone and referrer_instant_bonus > 0:
        admin_message += f"\n👥 রেফারার: {referrer_phone}\n🎁 রেফারার বোনাস: {referrer_instant_bonus} টাকা (১ বার)"
    elif referred_by:
        admin_message += f"\n👥 রেফারার: {referrer_phone}\n🎁 রেফারার বোনাস: ইতিমধ্যে দেওয়া হয়েছে"
    
    admin_message += f"\n\n⏰ ইউজার প্রতিদিন {DELAYED_BONUS_PERCENT}% ডেইলি বোনাস পাবে"
    
    await query.edit_message_text(admin_message)

# Reject recharge
async def reject_recharge(query, context, req_id):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT user_id, amount, payment_method, transaction_id FROM transactions WHERE id = ? AND status = "pending"', (req_id,))
    transaction = cursor.fetchone()
    
    if not transaction:
        await query.edit_message_text("❌ ট্র্যানজেকশন পাওয়া যায়নি")
        conn.close()
        return
    
    user_id, amount, payment_method, txn_id = transaction
    
    cursor.execute('DELETE FROM transactions WHERE id = ?', (req_id,))
    conn.commit()
    
    cursor.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,))
    user_phone = cursor.fetchone()[0]
    conn.close()
    
    method_name = "বিকাশ" if payment_method == "bkash" else "নগদ"
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ **META Income - রিচার্জ Rejected!**\n\n"
                 f"💰 পরিমাণ: {amount} টাকা\n"
                 f"📊 পেমেন্ট: {method_name}\n"
                 f"📋 TXN ID: {txn_id}\n"
                 f"📝 কারণ: Invalid transaction\n\n"
                 f"সঠিক তথ্য দিয়ে আবার চেষ্টা করুন\n"
                 f"রিচার্জ করতে /recharge লিখুন"
        )
    except:
        pass
    
    await query.edit_message_text(
        f"❌ **রিচার্জ Rejected!**\n\n"
        f"👤 ইউজার: {user_phone}\n"
        f"💰 টাকা: {amount} টাকা\n"
        f"📊 পেমেন্ট: {method_name}\n"
        f"📋 TXN ID: {txn_id}\n\n"
        f"ইউজারকে নোটিফাই করা হয়েছে"
    )

# Approve withdraw - AUTO DEDUCT FROM BONUS BALANCE
async def approve_withdraw(query, context, w_id):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT user_id, amount, payment_method, account_number FROM withdrawals WHERE id = ? AND status = "pending"', (w_id,))
    withdrawal = cursor.fetchone()
    
    if not withdrawal:
        await query.edit_message_text("❌ উইথড্র রিকুয়েস্ট পাওয়া যায়নি")
        conn.close()
        return
    
    user_id, amount, payment_method, account_number = withdrawal
    
    # Check user's bonus balance
    cursor.execute('SELECT bonus_balance FROM users WHERE user_id = ?', (user_id,))
    user_data = cursor.fetchone()
    
    if not user_data or user_data[0] < amount:
        await query.edit_message_text("❌ ইউজারের পর্যাপ্ত বোনাস নেই!")
        conn.close()
        return
    
    try:
        # Update withdrawal status to paid
        cursor.execute('UPDATE withdrawals SET status = "paid" WHERE id = ?', (w_id,))
        
        # AUTO DEDUCT from user's bonus balance
        cursor.execute('UPDATE users SET bonus_balance = bonus_balance - ? WHERE user_id = ?', (amount, user_id))
        
        conn.commit()
        
        cursor.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,))
        user_phone = cursor.fetchone()[0]
        
        method_name = "বিকাশ" if payment_method == "bkash" else "নগদ"
        
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🎉 **META Income - উইথড্র Approved!**\n\n"
                     f"💰 পরিমাণ: {amount} টাকা\n"
                     f"📊 পেমেন্ট: {method_name}\n"
                     f"📱 অ্যাকাউন্ট: {account_number}\n\n"
                     f"✅ টাকা 24 ঘন্টার মধ্যে পাঠানো হবে\n"
                     f"💳 আপনার বোনাস ব্যালেন্স থেকে {amount} টাকা কেটে নেওয়া হয়েছে\n"
                     f"⏰ পরবর্তী উইথড্র: 24 ঘন্টা পর\n\n"
                     f"ব্যালেন্স চেক করতে /balance লিখুন"
            )
        except Exception as e:
            logging.error(f"Error notifying user: {e}")
        
        await query.edit_message_text(
            f"✅ **উইথড্র Approved!**\n\n"
            f"👤 ইউজার: {user_phone}\n"
            f"💰 টাকা: {amount} টাকা\n"
            f"📊 পেমেন্ট: {method_name}\n"
            f"📱 অ্যাকাউন্ট: {account_number}\n\n"
            f"✅ ইউজারের বোনাস ব্যালেন্স থেকে {amount} টাকা অটো কেটে নেওয়া হয়েছে\n"
            f"💳 টাকা পাঠান: {account_number}\n\n"
            f"💰 নতুন বোনাস ব্যালেন্স: {user_data[0] - amount} টাকা"
        )
        
    except Exception as e:
        logging.error(f"Error in approve_withdraw: {e}")
        await query.edit_message_text("❌ ডাটাবেস এরর! আবার চেষ্টা করুন")
    finally:
        conn.close()

# Reject withdraw
async def reject_withdraw(query, context, w_id):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT user_id, amount, payment_method, account_number FROM withdrawals WHERE id = ? AND status = "pending"', (w_id,))
    withdrawal = cursor.fetchone()
    
    if not withdrawal:
        await query.edit_message_text("❌ উইথড্র রিকুয়েস্ট পাওয়া যায়নি")
        conn.close()
        return
    
    user_id, amount, payment_method, account_number = withdrawal
    
    cursor.execute('DELETE FROM withdrawals WHERE id = ?', (w_id,))
    conn.commit()
    
    cursor.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,))
    user_phone = cursor.fetchone()[0]
    conn.close()
    
    method_name = "বিকাশ" if payment_method == "bkash" else "নগদ"
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ **META Income - উইথড্র Rejected!**\n\n"
                 f"💰 পরিমাণ: {amount} টাকা\n"
                 f"📊 পেমেন্ট: {method_name}\n"
                 f"📱 অ্যাকাউন্ট: {account_number}\n"
                 f"📝 কারণ: Invalid request\n\n"
                 f"আবার চেষ্টা করুন\n"
                 f"উইথড্র করতে /withdraw লিখুন"
        )
    except:
        pass
    
    await query.edit_message_text(
        f"❌ **উইথড্র Rejected!**\n\n"
        f"👤 ইউজার: {user_phone}\n"
        f"💰 টাকা: {amount} টাকা\n"
        f"📊 পেমেন্ট: {method_name}\n"
        f"📱 অ্যাকাউন্ট: {account_number}\n\n"
        f"ইউজারকে নোটিফাই করা হয়েছে"
    )

# Cancel conversation
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ বাতিল করা হয়েছে।")
    return ConversationHandler.END

# Main function
def main():
    init_database()
    start_bonus_thread()
    
    application = Application.builder().token(TOKEN).build()
    
    # Conversation handlers
    user_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            VERIFICATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_verification)],
            PASSWORD_SETUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password_setup)],
            PASSWORD_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password_login)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    admin_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('admin', admin)],
        states={
            ADMIN_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_login)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    # withdraw conversation handler
    withdraw_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_payment_method, pattern="^method_")],
        states={
            WITHDRAW_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_withdraw_account)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    # Add handlers
    application.add_handler(user_conv_handler)
    application.add_handler(admin_conv_handler)
    application.add_handler(withdraw_conv_handler)
    
    # Command handlers
    application.add_handler(CommandHandler("recharge", recharge))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("referral", referral))
    application.add_handler(CommandHandler("withdraw", withdraw))
    application.add_handler(CommandHandler("changepassword", change_password))
    
    application.add_handler(CommandHandler("pending", pending))
    application.add_handler(CommandHandler("withdrawals", withdrawals))
    application.add_handler(CommandHandler("transactions", transactions))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("users", users))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(handle_amount_selection, pattern="^amount_"))
    application.add_handler(CallbackQueryHandler(handle_recharge_payment_method, pattern="^recharge_(bkash|nagad)_"))
    application.add_handler(CallbackQueryHandler(handle_withdraw_selection, pattern="^withdraw_"))
    application.add_handler(CallbackQueryHandler(handle_admin_buttons, pattern="^(approve_|reject_|pay_|cancel_)"))
    
    # Transaction ID handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_transaction_id))
    
    print("🤖 META Income Bot শুরু হয়েছে...")
    print("🔐 পাসওয়ার্ড সিস্টেম সক্রিয়")
    print("🔗 প্রতিটি ইউজারের জন্য র‍্যান্ডম রেফারেল লিংক তৈরি হবে")
    print("🎁 রেফারেল রিচার্জে 20% ইন্সট্যান্ট বোনাস")
    application.run_polling()

if __name__ == "__main__":

    main()

