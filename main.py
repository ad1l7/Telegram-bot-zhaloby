import telebot
import requests
import base64
import json
import os
from flask import Flask, request

TOKEN = os.getenv("TOKEN")
BITRIX_WEBHOOK = os.getenv("BITRIX_WEBHOOK")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)
processed_updates = set()
MAX_FILE_SIZE = 20 * 1024 * 1024
user_data = {}

# =========================
# 💾 СОХРАНЕНИЕ СВЯЗИ
# =========================
def save_mapping(chat_id, deal_id):
    try:
        with open("deals.json", "r") as f:
            data = json.load(f)
    except:
        data = {}

    data[str(deal_id)] = chat_id

    with open("deals.json", "w") as f:
        json.dump(data, f)


def get_chat_id(deal_id):
    try:
        with open("deals.json", "r") as f:
            data = json.load(f)
        return data.get(str(deal_id))
    except:
        return None


# =========================
# TELEGRAM WEBHOOK
# =========================
@app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    json_str = request.get_data().decode("UTF-8")
    update = telebot.types.Update.de_json(json_str)

    # ❗ защита от дублей
    if update.update_id in processed_updates:
        return "OK"
    processed_updates.add(update.update_id)

    bot.process_new_updates([update])
    return "OK"


# =========================
# START
# =========================
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    user_data[chat_id] = {"files": []}

    msg = bot.send_message(chat_id, "Введите комментарий по заявке")
    bot.register_next_step_handler(msg, question1)


def question1(message):
    chat_id = message.chat.id
    user_data[chat_id]["q1"] = message.text

    msg = bot.send_message(chat_id, "Введите адрес")
    bot.register_next_step_handler(msg, question2)


def question2(message):
    chat_id = message.chat.id
    user_data[chat_id]["q2"] = message.text

    bot.send_message(chat_id,
        "Отправьте файлы.\nКогда закончите — напишите *готово*",
        parse_mode="Markdown"
    )


# =========================
# 📎 ПРИЕМ ФАЙЛОВ
# =========================
@bot.message_handler(content_types=['document', 'photo', 'video', 'audio', 'voice'])
def handle_all_files(message):

    chat_id = message.chat.id

    # ❗ защита от дублей
    if message.message_id in processed_messages:
        return
    processed_messages.add(message.message_id)

    if chat_id not in user_data:
        return

    file_id = None
    filename = "file"

    if message.content_type == 'document':
        file_id = message.document.file_id
        filename = message.document.file_name
        file_size = message.document.file_size

    elif message.content_type == 'photo':
        file_id = message.photo[-1].file_id
        file_size = message.photo[-1].file_size
        filename = f"photo_{file_id}.jpg"

    elif message.content_type == 'video':
        file_id = message.video.file_id
        file_size = message.video.file_size
        filename = message.video.file_name or f"video_{file_id}.mp4"

    elif message.content_type == 'audio':
        file_id = message.audio.file_id
        file_size = message.audio.file_size
        filename = message.audio.file_name or f"audio_{file_id}.mp3"

    elif message.content_type == 'voice':
        file_id = message.voice.file_id
        file_size = message.voice.file_size
        filename = f"voice_{file_id}.oga"

    else:
        return

    if file_size > MAX_FILE_SIZE:
        bot.send_message(chat_id, f"❌ Файл {filename} слишком большой")
        return

    file_info = bot.get_file(file_id)
    file = bot.download_file(file_info.file_path)

    encoded = base64.b64encode(file).decode()

    user_data[chat_id]["files"].append({
        "name": filename,
        "content": encoded
    })

    bot.send_message(chat_id, f"Файл {filename} сохранен ✅")

# =========================
# 🚀 СОЗДАНИЕ СДЕЛКИ
# =========================
@bot.message_handler(func=lambda message: message.text and message.text.lower() == "готово")
def finish(message):

    chat_id = message.chat.id

    if chat_id not in user_data:
        bot.send_message(chat_id, "Сначала нажмите /start")
        return

    data = user_data[chat_id]

    deal = requests.post(
        BITRIX_WEBHOOK + "crm.deal.add.json",
        json={
            "fields": {
                "TITLE": "Сделка из Telegram",
                "UF_CRM_1712739320936": data["q1"],
                "UF_CRM_1713972050594": data["q2"]
            }
        }
    ).json()

    deal_id = deal.get("result")

    if not deal_id:
        bot.send_message(chat_id, "❌ Ошибка при создании сделки")
        return

    save_mapping(chat_id, deal_id)

    files = []

    for f in data["files"]:
        files.append({
            "NAME": f["name"],
            "CONTENT": f["content"]
        })

    requests.post(
        BITRIX_WEBHOOK + "crm.timeline.comment.add.json",
        json={
            "fields": {
                "ENTITY_ID": deal_id,
                "ENTITY_TYPE": "deal",
                "COMMENT": "Файлы из Telegram",
                "FILES": files
            }
        }
    )

    bot.send_message(chat_id, f"Сделка создана ✅\nНомер заявки: {deal_id}")

    user_data.pop(chat_id)


# =========================
# 🔔 WEBHOOK ИЗ BITRIX
# =========================
@app.route('/bitrix_webhook', methods=['POST'])
def bitrix_webhook():

    data = request.form.to_dict()
    deal_id = data.get('data[FIELDS][ID]')

    deal = requests.post(
        BITRIX_WEBHOOK + "crm.deal.get.json",
        json={"id": deal_id}
    ).json()

    deal_data = deal.get("result")
    stage = deal_data.get("STAGE_ID")

    if stage == "WON":
        answer = deal_data.get("UF_CRM_1773918858225") or "Ответ пока не заполнен"
        chat_id = get_chat_id(deal_id)

        if chat_id:
            bot.send_message(chat_id, f"📩 Ответ по заявке №{deal_id}:\n\n{answer}")

    return "OK"


# =========================
# 🚀 ЗАПУСК
# =========================
if __name__ == "__main__":
    bot.remove_webhook()

    url = os.getenv("RENDER_EXTERNAL_URL")
    bot.set_webhook(url=f"{url}/{TOKEN}")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
