#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import asyncio
from telegram import (
    Update, 
    KeyboardButton, 
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

# ------------------------------------------------------------------------------------
# ۱) تنظیمات اولیه
# ------------------------------------------------------------------------------------
BOT_TOKEN = "7982278622:AAHD4Yh67vxZ0GlzOpg-ZPXoBK6oPw11C8A"
ADMIN_CHAT_ID = 179044957  # آی‌دی عددی ادمین؛ این را عدد صحیح بگذارید

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# در این متغیر شماره کارت فعلی نگه می‌داریم و با پنل ادمین قابل تغییر خواهد بود
CURRENT_BANK_ACCOUNT = "1234-5678-9012-3456"

# ------------------------------------------------------------------------------------
# ۲) تعریف استیت‌ها (مراحل مختلف ConversationHandler)
# ------------------------------------------------------------------------------------
(
    STATE_AUTH_NAME,
    STATE_AUTH_FAMILY,
    STATE_AUTH_PHONE,
    
    STATE_ASK_USER_ID,
    STATE_ASK_AMOUNT,
    STATE_CONFIRM_CHARGE,
    STATE_WAIT_RECEIPT,
    STATE_WAIT_ADMIN_APPROVAL,

    # استیت‌های مربوط به منوی ادمین
    STATE_ADMIN_MENU,
    STATE_CHANGE_CARD_REQUEST
) = range(10)

# ------------------------------------------------------------------------------------
# ۳) دیتابیس ساده در حافظه
# ------------------------------------------------------------------------------------
users_db = {}               # key: user_id, value: dict → {"name", "family", "phone", "is_verified"}
pending_verifications = {}  # کاربران در انتظار تأیید ادمین
pending_charges = {}        # درخواست شارژ کاربران { user_id: {...} }

# ------------------------------------------------------------------------------------
# ۴) توابع کمکی
# ------------------------------------------------------------------------------------
async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text="منوی اصلی"):
    """ارسال منوی اصلی ربات برای کاربر."""
    user_id = update.effective_user.id

    # اگر کاربر ادمین است، او را به منوی ادمین ارجاع می‌دهیم (یا پیام متفاوتی می‌دهیم)
    if user_id == ADMIN_CHAT_ID:
        await update.message.reply_text(
            "شما ادمین هستید. برای دسترسی به پنل ادمین دستور /admin را وارد کنید."
        )
        return

    if user_id not in users_db or not users_db[user_id].get("is_verified"):
        await update.message.reply_text("ابتدا باید احراز هویت شوید. لطفاً /start را بزنید.")
        return

    # ساخت منوی کاربری
    keyboard = [
        [KeyboardButton("ارتباط با ادمین ✉️")],
        [KeyboardButton("شارژ کردن حساب 💰")],
        [KeyboardButton("انصراف ❌")]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(text, reply_markup=markup)


async def cancel_and_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دکمهٔ انصراف یا لغو عملیات → بازگشت به منوی اصلی."""
    if update.message:
        await update.message.reply_text("انصراف داده شد. بازگشت به منوی اصلی...")
        await send_main_menu(update, context)
    else:
        # اگر از کال‌بک اینلاین آمده
        await update.callback_query.message.reply_text("انصراف داده شد. بازگشت به منوی اصلی...")
        await send_main_menu(update.callback_query, context)
    return ConversationHandler.END

# ------------------------------------------------------------------------------------
# ۵) استارت و احراز هویت
# ------------------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in users_db and users_db[user_id].get("is_verified"):
        # کاربر قبلاً احراز شده → منوی اصلی
        await send_main_menu(update, context, "شما از قبل احراز شده‌اید. از منوی زیر انتخاب کنید:")
        return ConversationHandler.END

    await update.message.reply_text("لطفاً نام خود را وارد کنید ")
    return STATE_AUTH_NAME

async def auth_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "انصراف":
        return await cancel_and_back_to_menu(update, context)
    
    user_id = update.effective_user.id
    users_db[user_id] = {
        "name": text,
        "is_verified": False
    }
    await update.message.reply_text("نام خانوادگی خود را وارد کنید :")
    return STATE_AUTH_FAMILY

async def auth_get_family(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "انصراف":
        return await cancel_and_back_to_menu(update, context)
    
    user_id = update.effective_user.id
    users_db[user_id]["family"] = text
    
    # درخواست شماره تلفن از طریق دکمه share contact
    button = KeyboardButton("ارسال شماره تماس", request_contact=True)
    markup = ReplyKeyboardMarkup([[button], ["انصراف"]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "لطفاً شماره تماس خود را ارسال کنید:",
        reply_markup=markup
    )
    return STATE_AUTH_PHONE

async def auth_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if update.message.contact:
        phone_number = update.message.contact.phone_number
    else:
        text = update.message.text.strip()
        if text.lower() == "انصراف":
            return await cancel_and_back_to_menu(update, context)
        phone_number = text
    
    users_db[user_id]["phone"] = phone_number
    
    # ارسال برای ادمین جهت تأیید
    pending_verifications[user_id] = True
    msg = (
        f"درخواست احراز هویت جدید:\n"
        f"UserID: {user_id}\n"
        f"نام: {users_db[user_id]['name']}\n"
        f"نام خانوادگی: {users_db[user_id]['family']}\n"
        f"شماره تماس: {phone_number}\n\n"
        f"تأیید یا رد کنید."
    )
    keyboard = [
        [
            InlineKeyboardButton("تأیید ✅", callback_data=f"verify_accept_{user_id}"),
            InlineKeyboardButton("رد ❌", callback_data=f"verify_reject_{user_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, reply_markup=reply_markup)
    
    await update.message.reply_text(
        "اطلاعات شما ثبت شد. منتظر تأیید ادمین باشید.\n"
        "پس از تأیید، مجدداً /start را بزنید."
    )
    return ConversationHandler.END

# ------------------------------------------------------------------------------------
# ۶) کال‌بک ادمین: تأیید/رد احراز هویت
# ------------------------------------------------------------------------------------
async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data  # مثلاً "verify_accept_12345"
    parts = data.split("_")  # ["verify", "accept"/"reject", user_id]
    action = parts[1]
    target_user_id = int(parts[2])
    
    if target_user_id in pending_verifications:
        if action == "accept":
            users_db[target_user_id]["is_verified"] = True
            await context.bot.send_message(
                chat_id=target_user_id, 
                text="تبریک! حساب شما تأیید شد. اکنون /start را بزنید. ✅"
            )
            await query.edit_message_text(f"کاربر {target_user_id} تأیید شد.")
        else:
            await context.bot.send_message(
                chat_id=target_user_id, 
                text="متأسفانه حساب شما رد شد. ❌"
            )
            await query.edit_message_text(f"کاربر {target_user_id} رد شد.")
        
        del pending_verifications[target_user_id]
    else:
        await query.edit_message_text("کاربری یافت نشد یا قبلاً تأیید/رد شده است.")

# ------------------------------------------------------------------------------------
# ۷) منوی اصلی ربات
# ------------------------------------------------------------------------------------
async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip().lower()

    # اگر کاربر ادمین است، مسیرش جداست
    if user_id == ADMIN_CHAT_ID:
        await update.message.reply_text("شما ادمین هستید؛ برای دسترسی به پنل مدیریت، دستور /admin را استفاده کنید.")
        return

    if user_id not in users_db or not users_db[user_id].get("is_verified"):
        await update.message.reply_text("ابتدا /start را بزنید و احراز هویت شوید.")
        return

    if text in ["ارتباط با ادمین", "ارتباط با ادمین ✉️"]:
        # فقط آی‌دی یا یوزرنیم ادمین را نشان می‌دهیم
        await update.message.reply_text(
            "برای ارتباط با ادمین کافیست به آی‌دی زیر پیام بدهید:\n"
            "➡️ @MyAdminUserName\n\nموفق باشید! ✅"
        )
        return
    
    elif text in ["شارژ کردن حساب", "شارژ کردن حساب 💰"]:
        rules_msg = (
            "قوانین شارژ:\n"
            "1) حساب شما در عرض 3 تا 15 دقیقه شارژ خواهد شد.\n"
            "2) اسکرین‌شات فیش (یا رسید) را ارسال کنید.\n"
            "3) با تأیید، مسئول صحت اطلاعات خود هستید.\n\n"
            
            
            
            
            "  لطفاً آی‌دی کاربری خود را وارد کنید :"
        )
        await update.message.reply_text(rules_msg)
        return STATE_ASK_USER_ID
    
    elif text in ["انصراف", "انصراف ❌"]:
        return await cancel_and_back_to_menu(update, context)
    
    else:
        await update.message.reply_text("دستور نامعتبر! یکی از گزینه‌های منو را انتخاب کنید.")

# ------------------------------------------------------------------------------------
# ۸) مراحل شارژ: دریافت آی‌دی کاربری
# ------------------------------------------------------------------------------------
async def charge_get_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() in ["انصراف", "انصراف ❌"]:
        return await cancel_and_back_to_menu(update, context)
    
    context.user_data["charge_user_id"] = text
    await update.message.reply_text(" مبلغ را به تومان وارد کنید :")
    return STATE_ASK_AMOUNT

# ------------------------------------------------------------------------------------
# ۹) دریافت مبلغ
# ------------------------------------------------------------------------------------
async def charge_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() in ["انصراف", "انصراف ❌"]:
        return await cancel_and_back_to_menu(update, context)
    
    try:
        amount = int(text)
    except ValueError:
        await update.message.reply_text(" مبلغ نامعتبر. یک عدد وارد کنید '.")
        return STATE_ASK_AMOUNT
    
    context.user_data["charge_amount"] = amount
    
    summary = (
        f"شما قصد شارژ {amount} تومان دارید.\n"
        f"آی‌دی کاربری: {context.user_data['charge_user_id']}\n\n"
        "آیا تأیید می‌کنید؟"
    )
    keyboard = [
        [
            InlineKeyboardButton("تأیید ✅", callback_data="charge_confirm"),
            InlineKeyboardButton("انصراف ❌", callback_data="charge_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(summary, reply_markup=reply_markup)
    
    return STATE_CONFIRM_CHARGE

# ------------------------------------------------------------------------------------
# ۱۰) کال‌بک تأیید/انصراف مبلغ
# ------------------------------------------------------------------------------------
async def charge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CURRENT_BANK_ACCOUNT
    
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    if data == "charge_confirm":
        # از متغیر CURRENT_BANK_ACCOUNT استفاده می‌کنیم
        bank_account = CURRENT_BANK_ACCOUNT
        await query.message.reply_text(
            f"لطفاً مبلغ را به کارت زیر واریز کرده و هر نوع فیش (عکس، فایل، متن) را حداکثر در ۲۰۰ ثانیه ارسال کنید.\n"
            f"شماره کارت: {bank_account}\n\n"
            "⏰ مهلت ارسال فیش: ۲۰۰ ثانیه"
        )
        
        # ثبت درخواست
        pending_charges[user_id] = {
            "charge_user_id": context.user_data["charge_user_id"],
            "amount": context.user_data["charge_amount"],
            "job": None
        }
        
        # ایجاد Job برای تایمر ۲۰۰ ثانیه‌ای
        if context.job_queue:
            job_name = f"charge_timeout_{user_id}"
            job = context.job_queue.run_once(timeout_charge, 200, chat_id=user_id, name=job_name)
            pending_charges[user_id]["job"] = job
        else:
            logging.warning("JobQueue is not available. Install with pip install 'python-telegram-bot[job-queue]'")
        
        await query.edit_message_reply_markup(reply_markup=None)
        
        return STATE_WAIT_RECEIPT
    
    else:  # "charge_cancel"
        await query.message.reply_text("درخواست شما لغو شد. بازگشت به منوی اصلی.")
        await send_main_menu(query, context)
        await query.edit_message_reply_markup(reply_markup=None)
        return ConversationHandler.END

# ------------------------------------------------------------------------------------
# ۱۱) تابع تایمر ۲۰۰ ثانیه‌ای (timeout)
# ------------------------------------------------------------------------------------
async def timeout_charge(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.chat_id
    
    if user_id in pending_charges:
        # هنوز فیشی دریافت نشده
        del pending_charges[user_id]
        await context.bot.send_message(
            chat_id=user_id,
            text="مهلت ۲۰۰ ثانیه‌ای تمام شد. درخواست شارژ لغو شد. ❌"
        )

# ------------------------------------------------------------------------------------
# ۱۲) دریافت فیش واریزی از کاربر (عکس، فایل، متن و ...)
# ------------------------------------------------------------------------------------
async def charge_get_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    text = (update.message.text or "").strip().lower()
    if text in ["انصراف", "انصراف ❌"]:
        if user_id in pending_charges:
            job = pending_charges[user_id].get("job")
            if job:
                job.schedule_removal()
            del pending_charges[user_id]
        return await cancel_and_back_to_menu(update, context)
    
    if user_id not in pending_charges:
        await update.message.reply_text("درخواستی یافت نشد یا مهلت ارسال فیش تمام شده. مجدداً اقدام کنید.")
        return ConversationHandler.END

    # فیش را فوروارد کنیم برای ادمین
    charge_info = pending_charges[user_id]
    name = users_db[user_id]["name"]
    family = users_db[user_id]["family"]
    phone = users_db[user_id]["phone"]
    
    caption_for_admin = (
        f"کاربر {user_id} یک فیش واریزی ارسال کرد:\n"
        f"نام: {name}\n"
        f"نام خانوادگی: {family}\n"
        f"شماره تماس: {phone}\n"
        f"آی‌دی کاربری: {charge_info['charge_user_id']}\n"
        f"مبلغ: {charge_info['amount']} تومان\n\n"
        "لطفاً تأیید یا رد کنید."
    )
    keyboard = [
        [
            InlineKeyboardButton("تأیید و واریز شد ✅", callback_data=f"charge_accept_{user_id}"),
            InlineKeyboardButton("رد درخواست ❌", callback_data=f"charge_reject_{user_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # اگر عکس
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=file_id,
            caption=caption_for_admin,
            reply_markup=reply_markup
        )
    # اگر سند
    elif update.message.document:
        file_id = update.message.document.file_id
        await context.bot.send_document(
            chat_id=ADMIN_CHAT_ID,
            document=file_id,
            caption=caption_for_admin,
            reply_markup=reply_markup
        )
    else:
        # متن یا هر چیز دیگر
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=caption_for_admin,
            reply_markup=reply_markup
        )
    
    # کنسل کردن تایمر
    job = charge_info.get("job")
    if job:
        job.schedule_removal()
    
    del pending_charges[user_id]  # از صف انتظار خارج می‌شود
    await update.message.reply_text(
        "فیش شما دریافت شد. لطفاً منتظر تأیید ادمین باشید.\n"
        "در صورت انصراف /cancel را بزنید."
    )
    return STATE_WAIT_ADMIN_APPROVAL

# ------------------------------------------------------------------------------------
# ۱۳) مرحله انتظار تأیید ادمین
# ------------------------------------------------------------------------------------
async def wait_admin_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text in ["انصراف", "انصراف ❌"]:
        return await cancel_and_back_to_menu(update, context)
    
    await update.message.reply_text("هنوز تأیید ادمین انجام نشده! صبور باشید یا /cancel بزنید.")
    return STATE_WAIT_ADMIN_APPROVAL

# ------------------------------------------------------------------------------------
# ۱۴) کال‌بک ادمین: تأیید یا رد شارژ
# ------------------------------------------------------------------------------------
async def admin_charge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data  # مثلا: "charge_accept_12345"
    parts = data.split("_")  # ["charge", "accept"/"reject", user_id]
    action = parts[1]
    target_user_id = int(parts[2])
    
    if action == "accept":
        await context.bot.send_message(
            chat_id=target_user_id,
            text="ادمین تأیید کرد. شارژ شما انجام شد. ✅"
        )
        await query.edit_message_text(f"درخواست شارژ کاربر {target_user_id} تأیید شد.")
    else:
        await context.bot.send_message(
            chat_id=target_user_id,
            text="ادمین رد کرد. شارژ انجام نشد. ❌"
        )
        await query.edit_message_text(f"درخواست شارژ کاربر {target_user_id} رد شد.")

# ------------------------------------------------------------------------------------
# ۱۵) ایجاد پنل ادمین (دستور /admin)
# ------------------------------------------------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش منوی ادمین با استفاده از اینلاین کیبورد."""
    user_id = update.effective_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("شما ادمین نیستید!")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("تغییر شماره کارت 💳", callback_data="admin_change_card")],
        # می‌توانید دکمه‌های دیگری برای بررسی لیست کاربران، درخواست‌های تایید نشده و ... اضافه کنید
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("پنل ادمین - یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=markup)
    return STATE_ADMIN_MENU

async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر کال‌بک‌های منوی ادمین."""
    query = update.callback_query
    data = query.data

    if data == "admin_change_card":
        await query.message.reply_text(
            "شماره کارت جدید را وارد کنید یا /cancel برای لغو."
        )
        await query.edit_message_reply_markup(reply_markup=None)
        return STATE_CHANGE_CARD_REQUEST
    
    # اگر گزینه دیگری اضافه کردید اینجا بررسی کنید

    # اگر چیز نامعتبری باشد
    await query.message.reply_text("گزینه نامعتبر در پنل ادمین!")
    return STATE_ADMIN_MENU

async def admin_change_card_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت شماره کارت جدید از ادمین."""
    global CURRENT_BANK_ACCOUNT
    
    text = update.message.text.strip()
    if text.lower() in ["انصراف", "انصراف ❌", "/cancel"]:
        await update.message.reply_text("انصراف از تغییر شماره کارت.")
        return ConversationHandler.END

    # ذخیره در متغیر سراسری
    CURRENT_BANK_ACCOUNT = text
    await update.message.reply_text(
        f"شماره کارت با موفقیت تغییر کرد:\n{CURRENT_BANK_ACCOUNT}"
    )
    return ConversationHandler.END

# ------------------------------------------------------------------------------------
# ۱۶) تابع main و تنظیم ConversationHandlers
# ------------------------------------------------------------------------------------
def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # ConversationHandler برای احراز هویت
    auth_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            STATE_AUTH_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, auth_get_name)],
            STATE_AUTH_FAMILY: [MessageHandler(filters.TEXT & ~filters.COMMAND, auth_get_family)],
            STATE_AUTH_PHONE: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), auth_get_phone)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^(انصراف|انصراف ❌)$"), cancel_and_back_to_menu),
        ],
        allow_reentry=True
    )
    
    # ConversationHandler برای شارژ
    charge_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^(شارژ کردن حساب|شارژ کردن حساب 💰)$"), main_menu_handler)
        ],
        states={
            STATE_ASK_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, charge_get_user_id)],
            STATE_ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, charge_get_amount)],
            STATE_CONFIRM_CHARGE: [CallbackQueryHandler(charge_callback, pattern="^charge_(confirm|cancel)$")],
            STATE_WAIT_RECEIPT: [MessageHandler(filters.ALL, charge_get_receipt)],
            STATE_WAIT_ADMIN_APPROVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, wait_admin_approval)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^(انصراف|انصراف ❌)$"), cancel_and_back_to_menu),
            CommandHandler("cancel", cancel_and_back_to_menu)
        ],
        allow_reentry=True
    )

    # ConversationHandler برای پنل ادمین
    admin_panel_handler = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_command)],
        states={
            STATE_ADMIN_MENU: [
                CallbackQueryHandler(admin_menu_callback, pattern="^admin_.*")
            ],
            STATE_CHANGE_CARD_REQUEST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_change_card_request)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_and_back_to_menu)],
        allow_reentry=True
    )
    
    # افزودن هندلرها به برنامه
    application.add_handler(auth_handler)
    application.add_handler(charge_handler)
    application.add_handler(admin_panel_handler)

    # هندلر منوی اصلی کاربر
    application.add_handler(MessageHandler(
        filters.Regex("^(ارتباط با ادمین|ارتباط با ادمین ✉️|شارژ کردن حساب|شارژ کردن حساب 💰|انصراف|انصراف ❌)$"),
        main_menu_handler
    ))

    # کال‌بک برای تأیید/رد احراز هویت
    application.add_handler(CallbackQueryHandler(verify_callback, pattern="^verify_"))

    # کال‌بک برای تأیید/رد شارژ
    application.add_handler(CallbackQueryHandler(admin_charge_callback, pattern="^charge_(accept|reject)_"))

    # اجرای ربات
    application.run_polling()

if __name__ == "__main__":
    main()
