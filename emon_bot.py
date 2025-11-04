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

# Your bot token
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Google Sheets Disabled
def save_user_to_sheets(user_id, phone, balance=0, bonus_balance=0, referral_code=""):
    print(f"📊 User {user_id} saved to SQLite")
    return True

def save_transaction_to_sheets(user_id, amount, transaction_type, status, txn_id=""):
    print(f"💰 Transaction {txn_id} saved to SQLite")
    return True

# Setup logging
logging.basicConfig(level=logging.INFO)

# Conversation states
PHONE, VERIFICATION, PASSWORD_SETUP, PASSWORD_LOGIN, ADMIN_LOGIN, WITHDRAW_ACCOUNT = range(6)

# Your bKash and Nagad numbers - UPDATE THESE!
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
    print("✅ Database initialized")

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
        return False, "Password must be at least 6 characters"
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least 1 uppercase letter"
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least 1 lowercase letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least 1 number"
    return True, "Strong password"

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

# Check if user can withdraw
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
        return False, f"{hours} hours {minutes} minutes"

# /start command - FIXED VERSION
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
            await update.message.reply_text("❌ Your account is blocked!")
            conn.close()
            return
        
        if is_verified == 1:
            if password:
                context.user_data['phone'] = phone
                await update.message.reply_text(
                    "🔐 **Login Required**\n\n"
                    "Enter your password:"
                )
                return PASSWORD_LOGIN
            else:
                context.user_data['phone'] = phone
                await update.message.reply_text(
                    "🔒 **Password Setup**\n\n"
                    "Set a strong password for your account:\n\n"
                    "📋 **Requirements:**\n"
                    "• At least 6 characters\n"
                    "• 1 uppercase letter (A-Z)\n"
                    "• 1 lowercase letter (a-z)\n"
                    "• 1 number (0-9)\n\n"
                    "Enter your new password:"
                )
                return PASSWORD_SETUP
    
    if referral_code:
        context.user_data['referral_code'] = referral_code
    
    conn.close()
    
    await update.message.reply_text(
        "🤖 **META Income Bot - Account Verification**\n\n"
        "Enter your phone number (11 digits):\n"
        "Example: 01712345678"
    )
    return PHONE

# Handle phone number input
async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_number = update.message.text.strip()
    
    if re.match(r'^01[3-9]\d{8}$', phone_number):
        user_id = update.message.from_user.id
        
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users WHERE phone = ?', (phone_number,))
        existing_user = cursor.fetchone()
        conn.close()
        
        if existing_user:
            await update.message.reply_text(
                "❌ This phone number is already registered!\n\n"
                "You are already registered. Use /start to login."
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
            f"✅ **Phone number accepted!**\n\n"
            f"📱 Phone: {phone_number}\n"
            f"🔐 Verification code: **{verification_code}**\n\n"
            "Enter the 4-digit code:"
        )
        
        return VERIFICATION
    else:
        await update.message.reply_text(
            "❌ Invalid phone number!\n\n"
            "Enter correct phone number (11 digits):\n"
            "Example: 01712345678\n\n"
            "Try again:"
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
        
        save_user_to_sheets(user_id, phone_number, 0, 0, new_referral_code)
        
        conn.commit()
        conn.close()
        
        context.user_data['phone'] = phone_number
        await update.message.reply_text(
            "🎉 **Verification successful!**\n\n"
            "🔒 **Password Setup**\n\n"
            "Set a strong password for your account:\n\n"
            "📋 **Requirements:**\n"
            "• At least 6 characters\n"
            "• 1 uppercase letter (A-Z)\n"
            "• 1 lowercase letter (a-z)\n"
            "• 1 number (0-9)\n\n"
            "Enter your new password:"
        )
        return PASSWORD_SETUP
    else:
        await update.message.reply_text("❌ Wrong verification code. Try again:")
        return VERIFICATION

# Handle password setup
async def handle_password_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    password = update.message.text.strip()
    phone = context.user_data.get('phone')
    
    is_valid, message = is_strong_password(password)
    
    if not is_valid:
        await update.message.reply_text(
            f"❌ {message}\n\n"
            "Please enter a strong password:"
        )
        return PASSWORD_SETUP
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET password = ? WHERE user_id = ?', (password, user_id))
    conn.commit()
    
    cursor.execute('SELECT referral_code, balance, bonus_balance FROM users WHERE user_id = ?', (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    
    referral_code = user_data[0]
    bot_username = "metaincome_bot"
    referral_link = f"https://t.me/{bot_username}?start={referral_code}"
    
    welcome_message = (
        f"✅ **Password setup successful!**\n\n"
        f"🔐 Your account is now secure\n"
        f"📱 Phone: {phone}\n"
        f"🔗 Referral code: `{referral_code}`\n"
        f"🔗 Referral link:\n{referral_link}\n\n"
        f"💰 Balance: 0 Tk\n"
        f"🎁 Bonus: 0 Tk\n\n"
        f"💡 **Use /start to login next time**\n\n"
        f"💳 Use /recharge to deposit\n"
        f"🏧 Use /withdraw to withdraw\n"
        f"🔗 Use /referral for referral info"
    )
    
    await update.message.reply_text(welcome_message)
    return ConversationHandler.END

# Handle password login
async def handle_password_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    password_input = update.message.text.strip()
    phone = context.user_data.get('phone')
    
    if not check_login_attempts(user_id):
        await update.message.reply_text(
            "❌ **Too many failed attempts!**\n\n"
            "Your account is locked for 1 hour.\n"
            "Try again after 1 hour."
        )
        return ConversationHandler.END
    
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
            f"❌ **Wrong password!**\n\n"
            f"📊 Remaining attempts: {remaining_attempts}\n\n"
            f"Enter password again:"
        )
        conn.close()
        return PASSWORD_LOGIN
    
    update_login_attempts(user_id, success=True)
    
    cursor.execute('SELECT referral_code, balance, bonus_balance FROM users WHERE user_id = ?', (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    
    referral_code, balance, bonus_balance = user_data
    
    await update.message.reply_text(
        f"✅ **Login successful!**\n\n"
        f"🤖 **META Income Bot**\n\n"
        f"📱 Phone: {phone}\n"
        f"💰 Balance: {balance} Tk\n"
        f"🎁 Bonus: {bonus_balance} Tk\n"
        f"🔗 Referral code: `{referral_code}`\n\n"
        f"Use /recharge to deposit\n"
        f"Use /withdraw to withdraw\n"
        f"Use /referral for referral info"
    )
    return ConversationHandler.END

# Cancel conversation - FIXED
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

# Basic commands without conversation
async def recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await update.message.reply_text("💳 Use /start first to login")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await update.message.reply_text("💰 Use /start first to login")

async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await update.message.reply_text("🔗 Use /start first to login")

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await update.message.reply_text("🏧 Use /start first to login")

# Main function - SIMPLIFIED
def main():
    init_database()
    start_bonus_thread()
    
    application = Application.builder().token(TOKEN).build()
    
    # SIMPLIFIED ConversationHandler - FIXED
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
    
    # Add basic command handlers
    application.add_handler(CommandHandler("recharge", recharge))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("referral", referral))
    application.add_handler(CommandHandler("withdraw", withdraw))
    
    # Add conversation handler LAST
    application.add_handler(user_conv_handler)
    
    print("🤖 META Income Bot started...")
    print("🔐 Password system active")
    application.run_polling()

if __name__ == "__main__":
    main()
