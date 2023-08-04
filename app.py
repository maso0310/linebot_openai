from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import *
import os

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

@handler.add(MessageEvent, message=TextMessage)#這個函式要處理 MessageEvent 事件且訊息型別為 TextMessage
def handle_message(event):#event 是 LINE Bot 收到的事件資料，其中包含了使用者傳來的訊息內容等資訊。
    message = TextSendMessage(text=event.message.text)#使用者傳來的文字訊息內容（event.message.text），將其包裝成TextSendMessage 物件，用於 LINE Bot 回覆訊息的物件類型。
    #if message.startswith("梗圖支援 "):
    line_bot_api.reply_message(event.reply_token, "蛤")
    #else:
    #    line_bot_api.reply_message(event.reply_token, message)
    

import os
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)