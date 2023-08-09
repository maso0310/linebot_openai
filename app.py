from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import *
import os
import request_4

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ['CHANNEL_ACCESS_TOKEN'])
handler = WebhookHandler(os.environ['CHANNEL_SECRET'])


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']#是用來驗證請求的有效性
    body = request.get_data(as_text=True)#獲取 HTTP 請求的資料主體（body） 以文字格式解析 後續處理方便
    app.logger.info("Request body: " + body)#請求的資料主體寫入 Flask 應用程式的日誌，方便後續查看
    try:
        handler.handle(body, signature)#資料主體和簽名傳handler處理 handler是WebhookHandler 物件 處理 LINE Bot 收到的事件。
    except InvalidSignatureError:#簽名驗證異常
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        if event.message.text.startswith("梗圖支援 "):
            reply = request_4.get_img_url(event.message.text[4:])
            app.logger.info("reply is :"+str(reply))
            message = ImageSendMessage(original_content_url=reply, preview_image_url=reply)
            line_bot_api.reply_message(event.reply_token, message)
    except Exception as e:
        app.logger.error("An error occurred: " + str(e))
        reply = "出了一些問題，請稍後再試"
        message = TextSendMessage(text=reply)
        line_bot_api.reply_message(event.reply_token, message)
    

import os
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)