import logging
import sqlite3
import random
import re
import threading
import time
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler

# Your NEW bot token for metaincome_bot
TOKEN = "YOUR_NEW_BOT_TOKEN_HERE"

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

# Database setup - RENDER COMPATIBLE
def init_database():
    # Render এ persistent storage এর জন্য /tmp folder ব্যবহার করুন
    db_path = '/tmp/users.db' if 'RENDER' in os.environ else 'users.db'
    
    conn = sqlite3.connect(db_path)
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

# Database connection function - RENDER COMPATIBLE
def get_db_connection():
    db_path = '/tmp/users.db' if 'RENDER' in os.environ else 'users.db'
    return sqlite3.connect(db_path)

# Generate unique random referral code
def generate_referral_code():
    characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    while True:
        code = "META" + ''.join(random.choices(characters, k=8))
        
        conn = get_db_connection()
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
    conn = get_db_connection()
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
    conn = get_db_connection()
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
            conn = get_db_connection()
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
    conn = get_db_connection()
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

# বাকি সব ফাংশন একদম আগের মতোই থাকবে...
# শুধু প্রতিটি database connection এ get_db_connection() ব্যবহার করুন

# Example: /start command - শুধু conn = sqlite3.connect('users.db') পরিবর্তন করুন
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    
    args = context.args
    referral_code = args[0] if args else None
    
    conn = get_db_connection()  # এখানে পরিবর্তন
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

# বাকি সব ফাংশনে একইভাবে database connection পরিবর্তন করুন...
# প্রতিটি conn = sqlite3.connect('users.db') কে conn = get_db_connection() দিয়ে replace করুন

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
