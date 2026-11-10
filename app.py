from flask import Flask, request, abort
import os
import json
import traceback
import math
import re
import uuid
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs

import requests
from Health_Adapter.Google_Health_Adapter.google_health import register_google_health, HealthError
from Health_Adapter.Apple_Health_Adapter.apple_health import register_apple_health
from openai import OpenAI
from medication_analyzer import analyze_medication_image
from master_roles import MasterRoles

from Richmenu.richmenu_manager import get_home_rich_menu_id
from Richmenu.richmenu_paths import project_root, static_root

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    PostbackAction,
    DatetimePickerAction,
    CameraAction,
    CameraRollAction,
    URIAction,
    PushMessageRequest,
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent,
    PostbackEvent,
    FollowEvent,
    JoinEvent,
)


# =========================================================
# Flask 與環境變數
# =========================================================

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")


required_env = {
    "CHANNEL_ACCESS_TOKEN": CHANNEL_ACCESS_TOKEN,
    "CHANNEL_SECRET": CHANNEL_SECRET,
    "OPENAI_API_KEY": OPENAI_API_KEY,
}

missing_env = [name for name, value in required_env.items() if not value]

if missing_env:
    raise ValueError(
        "缺少必要環境變數：" + ", ".join(missing_env)
    )


# =========================================================
# 路徑與身份設定
# =========================================================

BASE_DIR = str(project_root(__file__))
TMP_DIR = str(static_root(__file__) / "tmp")

ROLE_CONFIG = {
    "family": {
        "name": "家屬",
        "env_name": "FAMILY_RICH_MENU_ID",
    },
    "caregiver": {
        "name": "看護",
        "env_name": "CAREGIVER_RICH_MENU_ID",
    },
    "elderly": {
        "name": "長者",
        "env_name": "ELDERLY_RICH_MENU_ID",
    },
}

# 群組中呼叫的 Rich Menu 為暫時性選單。
# 使用者每次操作選單都會重新計算 10 分鐘。
TEMP_RICH_MENU_MINUTES = 10
TEMP_RICH_MENU_CLEANUP_SECONDS = 30

# 群組聊天室不會顯示底部 Rich Menu，因此群組改用可保留在對話中的
# Flex Message。第一層按鈕開啟第二層功能，第二層沿用既有 postback action。
GROUP_FLEX_MENUS = {
    "family": {
        "title": "家屬功能選單",
        "home": [
            ("監控中心", "family_monitoring"), ("家庭管理", "family_management"),
            ("藥物管理", "family_medication"), ("行事曆", "family_calendar"),
            ("報表", "family_report"), ("設定", "family_settings"),
        ],
        "pages": {
            "family_monitoring": [("今日狀態", "family_monitor_today_status"), ("漏服通知", "family_monitor_missed"), ("緊急通知", "family_monitor_emergency"), ("不舒服紀錄", "family_monitor_discomfort"), ("服藥率統計", "family_monitor_adherence")],
            "family_management": [("新增長者", "family_add_elder"), ("修改長者", "family_manage_elder"), ("新增看護", "family_add_caregiver"), ("指派看護", "family_assign_caregiver"), ("家庭群組 ID", "family_bind_group")],
            "family_medication": [("查看藥物", "family_medication_list"), ("修正藥物", "family_medication_correct"), ("剩餘藥量", "family_medication_remaining"), ("快用完", "family_medication_low"), ("藥袋紀錄", "family_medication_bag_records")],
            "family_calendar": [("查看行事曆", "family_calendar_view"), ("新增行程", "family_calendar_add"), ("修改行程", "family_calendar_edit"), ("刪除行程", "family_calendar_delete"), ("回診提醒", "family_calendar_reminder")],
            "family_report": [("今日報表", "family_report_today"), ("近 7 天", "family_report_7days"), ("近 30 天", "family_report_30days"), ("異常統計", "family_report_abnormal"), ("輸出摘要", "family_report_summary")],
            "family_settings": [],
        },
    },
    "elderly": {
        "title": "長者功能選單",
        "home": [("今日用藥", "elder_today"), ("我的藥物", "elder_medicine"), ("用藥回報", "elder_taken"), ("不舒服回報", "elder_discomfort"), ("行事曆", "elder_calendar"), ("SOS", "elder_sos")],
        "pages": {
            "elder_today": [("早餐", "elder_today_breakfast"), ("午餐", "elder_today_lunch"), ("晚餐", "elder_today_dinner"), ("睡前", "elder_today_bedtime"), ("今日全部", "elder_today_all")],
            "elder_medicine": [("藥物清單", "elder_medicine_list"), ("藥物資訊", "elder_medicine_info"), ("剩餘藥量", "elder_medicine_remaining"), ("拍攝藥袋", "elder_medicine_capture"), ("停用藥物", "elder_medicine_stop")],
            "elder_taken": [("早餐已服", "elder_taken_breakfast"), ("午餐已服", "elder_taken_lunch"), ("晚餐已服", "elder_taken_dinner"), ("睡前已服", "elder_taken_bedtime"), ("今日紀錄", "elder_taken_today")],
            "elder_discomfort": [("頭暈", "elder_discomfort_dizziness"), ("頭痛", "elder_discomfort_headache"), ("想吐", "elder_discomfort_nausea"), ("睡不好", "elder_discomfort_sleep"), ("其他問題", "elder_discomfort_other")],
            "elder_calendar": [("查看行事曆", "elder_calendar_view"), ("新增行程", "elder_calendar_add"), ("修改行程", "elder_calendar_edit"), ("刪除行程", "elder_calendar_delete"), ("回診提醒", "elder_calendar_reminder")],
            "elder_sos": [("緊急聯絡人 1", "elder_sos_contact1"), ("緊急聯絡人 2", "elder_sos_contact2"), ("通知全部", "elder_sos_notify_all")],
        },
    },
    "caregiver": {
        "title": "看護功能選單",
        "home": [("選擇患者", "caregiver_select_patient"), ("今日任務", "caregiver_tasks"), ("用藥紀錄", "caregiver_medication_records"), ("異常回報", "caregiver_report_issue"), ("行事曆", "caregiver_calendar"), ("SOS", "caregiver_emergency")],
        "pages": {},
    },
}


# =========================================================
# LINE 與 OpenAI 初始化
# =========================================================

configuration = Configuration(
    access_token=CHANNEL_ACCESS_TOKEN
)

handler = WebhookHandler(CHANNEL_SECRET)
openai_client = OpenAI(api_key=OPENAI_API_KEY)


def get_messaging_api():
    api_client = ApiClient(configuration)
    return api_client, MessagingApi(api_client)


def get_blob_api():
    api_client = ApiClient(configuration)
    return api_client, MessagingApiBlob(api_client)


# =========================================================
# 共用函式
# =========================================================

def safe_text(text, limit=5000):
    text = str(text or "")

    if len(text) <= limit:
        return text

    return text[: limit - 3] + "..."


def get_user_id(event):
    source = getattr(event, "source", None)
    return getattr(source, "user_id", None) if source else None


def reply_text(reply_token, text):
    message_text = safe_text(text).strip()
    if not message_text:
        message_text = "目前沒有可顯示的資料。"

    api_client, messaging_api = get_messaging_api()

    try:
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(text=message_text)
                ],
            )
        )
    finally:
        api_client.close()


# =========================================================
# PostgreSQL 資料庫
# =========================================================

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError(
            "缺少 DATABASE_URL，無法連線 PostgreSQL"
        )

    try:
        import psycopg2
        from psycopg2.extras import register_uuid
    except ImportError as error:
        raise RuntimeError(
            "使用 PostgreSQL 時需安裝 psycopg2-binary"
        ) from error

    # 讓 psycopg2 能直接處理 PostgreSQL UUID 欄位。
    # 否則從資料庫 SELECT 出來的 uuid.UUID 物件再次作為參數寫入時，
    # 會出現：can't adapt type 'UUID'。
    register_uuid()

    return psycopg2.connect(DATABASE_URL)


def init_database():
    """
    驗證新版 PostgreSQL 架構是否已建立。
    不再建立舊的 line_users 資料表。
    """
    required_tables = {
        "roles",
        "languages",
        "app_users",
        "rich_menus",
        "user_rich_menu_bindings",
        "operation_logs",
    }

    connection = get_db_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )

        existing_tables = {
            row[0]
            for row in cursor.fetchall()
        }

        missing_tables = sorted(
            required_tables - existing_tables
        )

        if missing_tables:
            raise RuntimeError(
                "新版 PostgreSQL 架構尚未完成，缺少資料表："
                + ", ".join(missing_tables)
            )

        # Persist the patient currently selected by each caregiver.  This is
        # intentionally independent of temporary conversation state.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS caregiver_selected_patients (
                caregiver_user_id UUID PRIMARY KEY REFERENCES app_users(id),
                patient_id UUID NOT NULL REFERENCES patients(id),
                selected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS temporary_rich_menu_sessions (
                line_user_id VARCHAR(64) PRIMARY KEY,
                role VARCHAR(30) NOT NULL,
                line_rich_menu_id VARCHAR(255) NOT NULL,
                source_type VARCHAR(20) NOT NULL,
                conversation_id VARCHAR(255),
                expires_at TIMESTAMPTZ NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_temporary_rich_menu_expiry
            ON temporary_rich_menu_sessions (status, expires_at)
            """
        )
        connection.commit()

    finally:
        connection.close()


def get_default_language_code(role, profile_language=None):
    """
    優先使用 LINE Profile 回傳語言。
    若資料庫未支援該語言，save_user() 會回退到身份預設語言。
    """
    if profile_language:
        return profile_language

    if role == "caregiver":
        return "en"

    return "zh-TW"


def get_user(user_id):
    if not user_id:
        return None

    connection = get_db_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                u.id,
                u.line_user_id AS user_id,
                u.display_name,
                r.code AS role,
                u.current_rich_menu_id AS rich_menu_id,
                u.picture_url,
                l.code AS language,
                u.created_at,
                u.updated_at,
                u.last_seen_at
            FROM app_users u
            JOIN roles r
                ON r.id = u.role_id
            LEFT JOIN languages l
                ON l.id = u.language_id
            WHERE u.line_user_id = %s
              AND u.is_active = TRUE
            """,
            (user_id,),
        )

        row = cursor.fetchone()

        if not row:
            return None

        columns = [
            column[0]
            for column in cursor.description
        ]
        return dict(zip(columns, row))

    finally:
        connection.close()


def get_role_rich_menu_id_from_database(role):
    """
    從 rich_menus 取得該身份啟用中的首頁 Rich Menu。
    caregiver 預設使用英文；其他身份預設繁體中文。
    """
    language_code = (
        "en"
        if role == "caregiver"
        else "zh-TW"
    )

    connection = get_db_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT rm.line_rich_menu_id
            FROM rich_menus rm
            JOIN roles r
                ON r.id = rm.role_id
            JOIN languages l
                ON l.id = rm.language_id
            WHERE r.code = %s
              AND l.code = %s
              AND rm.is_home = TRUE
              AND rm.is_active = TRUE
              AND rm.line_rich_menu_id IS NOT NULL
            ORDER BY rm.updated_at DESC
            LIMIT 1
            """,
            (role, language_code),
        )

        row = cursor.fetchone()
        return row[0] if row else None

    finally:
        connection.close()


def get_role_rich_menu_id(role):
    """
    取得身份對應首頁 Rich Menu ID。

    優先順序：
    1. PostgreSQL rich_menus
    2. Render 環境變數
    3. richmenu_ids.json
    """
    database_value = get_role_rich_menu_id_from_database(
        role
    )

    if database_value:
        return database_value

    role_setting = ROLE_CONFIG.get(role)

    if not role_setting:
        return None

    env_name = role_setting.get("env_name")
    env_value = os.getenv(env_name) if env_name else None

    if env_value:
        return env_value.strip()

    return get_home_rich_menu_id(role)


def save_user(
    user_id,
    display_name,
    role,
    rich_menu_id=None,
    picture_url=None,
    language=None,
):
    """
    儲存或更新 LINE 使用者、身份、語言及目前 Rich Menu。
    """
    connection = get_db_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id
            FROM roles
            WHERE code = %s
              AND is_active = TRUE
            """,
            (role,),
        )
        role_row = cursor.fetchone()

        if not role_row:
            raise RuntimeError(
                f"資料庫找不到身份代碼：{role}"
            )

        role_id = role_row[0]
        requested_language = get_default_language_code(
            role,
            language,
        )

        cursor.execute(
            """
            SELECT id
            FROM languages
            WHERE code = %s
              AND is_active = TRUE
            """,
            (requested_language,),
        )
        language_row = cursor.fetchone()

        if not language_row:
            fallback_language = (
                "en"
                if role == "caregiver"
                else "zh-TW"
            )

            cursor.execute(
                """
                SELECT id
                FROM languages
                WHERE code = %s
                  AND is_active = TRUE
                """,
                (fallback_language,),
            )
            language_row = cursor.fetchone()

        language_id = (
            language_row[0]
            if language_row
            else None
        )

        cursor.execute(
            """
            INSERT INTO app_users (
                line_user_id,
                display_name,
                picture_url,
                role_id,
                language_id,
                current_rich_menu_id,
                is_active,
                last_seen_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, TRUE,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (line_user_id)
            DO UPDATE SET
                display_name = EXCLUDED.display_name,
                picture_url = EXCLUDED.picture_url,
                role_id = EXCLUDED.role_id,
                language_id = EXCLUDED.language_id,
                current_rich_menu_id =
                    EXCLUDED.current_rich_menu_id,
                is_active = TRUE,
                last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (
                user_id,
                display_name,
                picture_url,
                role_id,
                language_id,
                rich_menu_id,
            ),
        )

        app_user_id = cursor.fetchone()[0]
        connection.commit()
        return app_user_id

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def record_rich_menu_binding(
    line_user_id,
    role,
    line_rich_menu_id,
    success=True,
    error_message=None,
):
    """
    記錄使用者目前綁定的 Rich Menu。
    成功時會將前一筆 is_current 改為 FALSE。
    """
    connection = get_db_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id
            FROM app_users
            WHERE line_user_id = %s
            """,
            (line_user_id,),
        )
        user_row = cursor.fetchone()

        if not user_row:
            raise RuntimeError(
                "記錄 Rich Menu 綁定時找不到使用者"
            )

        app_user_id = user_row[0]

        cursor.execute(
            """
            SELECT rm.id
            FROM rich_menus rm
            JOIN roles r
                ON r.id = rm.role_id
            WHERE r.code = %s
              AND rm.line_rich_menu_id = %s
              AND rm.is_active = TRUE
            LIMIT 1
            """,
            (role, line_rich_menu_id),
        )
        menu_row = cursor.fetchone()

        if success and menu_row:
            rich_menu_uuid = menu_row[0]

            cursor.execute(
                """
                UPDATE user_rich_menu_bindings
                SET
                    is_current = FALSE,
                    unbound_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
                  AND is_current = TRUE
                """,
                (app_user_id,),
            )

            cursor.execute(
                """
                INSERT INTO user_rich_menu_bindings (
                    user_id,
                    rich_menu_id,
                    line_rich_menu_id,
                    is_current,
                    error_message
                )
                VALUES (%s, %s, %s, TRUE, NULL)
                """,
                (
                    app_user_id,
                    rich_menu_uuid,
                    line_rich_menu_id,
                ),
            )

            cursor.execute(
                """
                UPDATE app_users
                SET
                    current_rich_menu_id = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    line_rich_menu_id,
                    app_user_id,
                ),
            )

        cursor.execute(
            """
            INSERT INTO operation_logs (
                user_id,
                action_type,
                entity_type,
                entity_id,
                details,
                success,
                error_message
            )
            VALUES (
                %s,
                %s,
                'rich_menu',
                %s,
                %s::jsonb,
                %s,
                %s
            )
            """,
            (
                app_user_id,
                (
                    "rich_menu_bound"
                    if success
                    else "rich_menu_bind_failed"
                ),
                (
                    menu_row[0]
                    if menu_row
                    else None
                ),
                json.dumps(
                    {
                        "role": role,
                        "line_rich_menu_id": (
                            line_rich_menu_id
                        ),
                    },
                    ensure_ascii=False,
                ),
                success,
                error_message,
            ),
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def record_role_selection(
    line_user_id,
    role,
):
    connection = get_db_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id
            FROM app_users
            WHERE line_user_id = %s
            """,
            (line_user_id,),
        )
        row = cursor.fetchone()

        if not row:
            return

        cursor.execute(
            """
            INSERT INTO operation_logs (
                user_id,
                action_type,
                entity_type,
                details,
                success
            )
            VALUES (
                %s,
                'role_selected',
                'role',
                %s::jsonb,
                TRUE
            )
            """,
            (
                row[0],
                json.dumps(
                    {"role": role},
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


# =========================================================
# 身份選擇
# =========================================================

def create_role_selection_message():
    return TextMessage(
        text=(
            "歡迎使用長照用藥 Bot！\n\n"
            "請先選擇您的身份類別。"
        ),
        quick_reply=QuickReply(
            items=[
                QuickReplyItem(
                    action=PostbackAction(
                        label="家屬",
                        data="action=select_role&role=family",
                        display_text="我是家屬",
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label="長者",
                        data="action=select_role&role=elderly",
                        display_text="我是長者",
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label="看護",
                        data="action=select_role&role=caregiver",
                        display_text="我是看護",
                    )
                ),
            ]
        ),
    )


def reply_role_selection(reply_token):
    api_client, messaging_api = get_messaging_api()

    try:
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    create_role_selection_message()
                ],
            )
        )
    finally:
        api_client.close()


# =========================================================
# LINE 使用者資料與 Rich Menu
# =========================================================

def get_line_profile(user_id):
    api_client, messaging_api = get_messaging_api()

    try:
        profile = messaging_api.get_profile(
            user_id=user_id
        )

        return {
            "display_name": getattr(
                profile,
                "display_name",
                "使用者",
            ),
            "picture_url": getattr(
                profile,
                "picture_url",
                None,
            ),
            "language": getattr(
                profile,
                "language",
                None,
            ),
        }

    finally:
        api_client.close()


def link_rich_menu(user_id, rich_menu_id):
    if not user_id:
        raise RuntimeError("無法取得 LINE User ID")

    if not rich_menu_id:
        raise RuntimeError(
            "找不到對應的 Rich Menu ID。"
            "請確認 richmenu_ids.json 已建立，"
            "或 Render 環境變數已設定。"
        )

    url = (
        "https://api.line.me/v2/bot/user/"
        f"{user_id}/richmenu/{rich_menu_id}"
    )

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
        },
        timeout=20,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Rich Menu 綁定失敗："
            f"HTTP {response.status_code} "
            f"{response.text}"
        )

    app.logger.info(
        "Rich Menu 綁定成功：user_id=%s, rich_menu_id=%s",
        user_id,
        rich_menu_id,
    )

    return True


def unlink_rich_menu(user_id):
    """解除指定使用者目前綁定的 per-user Rich Menu。"""
    if not user_id:
        raise RuntimeError("無法取得 LINE User ID")

    url = (
        "https://api.line.me/v2/bot/user/"
        f"{user_id}/richmenu"
    )
    response = requests.delete(
        url,
        headers={
            "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
        },
        timeout=20,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Rich Menu 解除失敗："
            f"HTTP {response.status_code} {response.text}"
        )

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE user_rich_menu_bindings b
            SET is_current = FALSE,
                unbound_at = CURRENT_TIMESTAMP
            FROM app_users u
            WHERE b.user_id = u.id
              AND u.line_user_id = %s
              AND b.is_current = TRUE
            """,
            (user_id,),
        )
        cursor.execute(
            """
            UPDATE app_users
            SET current_rich_menu_id = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE line_user_id = %s
            """,
            (user_id,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    app.logger.info("Rich Menu 已解除：user_id=%s", user_id)
    return True


def open_temporary_rich_menu_session(event, user_id, role, rich_menu_id):
    """記錄群組／多人聊天室中的暫時 Rich Menu，10 分鐘後自動解除。"""
    source = getattr(event, "source", None)
    source_type = getattr(source, "type", None) or "unknown"
    conversation_id = (
        getattr(source, "group_id", None)
        or getattr(source, "room_id", None)
    )

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO temporary_rich_menu_sessions (
                line_user_id, role, line_rich_menu_id,
                source_type, conversation_id, expires_at,
                status, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s,
                CURRENT_TIMESTAMP + (%s * INTERVAL '1 minute'),
                'active', CURRENT_TIMESTAMP
            )
            ON CONFLICT (line_user_id)
            DO UPDATE SET
                role = EXCLUDED.role,
                line_rich_menu_id = EXCLUDED.line_rich_menu_id,
                source_type = EXCLUDED.source_type,
                conversation_id = EXCLUDED.conversation_id,
                expires_at = EXCLUDED.expires_at,
                status = 'active',
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                role,
                rich_menu_id,
                source_type,
                conversation_id,
                TEMP_RICH_MENU_MINUTES,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def refresh_temporary_rich_menu_session(user_id):
    """使用者操作暫時選單時，延長其有效時間。"""
    if not user_id:
        return False

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE temporary_rich_menu_sessions
            SET expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 minute'),
                updated_at = CURRENT_TIMESTAMP
            WHERE line_user_id = %s
              AND status = 'active'
            """,
            (TEMP_RICH_MENU_MINUTES, user_id),
        )
        changed = cursor.rowcount > 0
        connection.commit()
        return changed
    except Exception:
        connection.rollback()
        app.logger.error("延長暫時 Rich Menu 時發生錯誤")
        app.logger.error(traceback.format_exc())
        return False
    finally:
        connection.close()


def has_active_group_menu_session(user_id):
    """只有仍在 10 分鐘期限內的群組 Flex 選單才可操作。"""
    if not user_id:
        return False
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT 1
            FROM temporary_rich_menu_sessions
            WHERE line_user_id = %s
              AND status = 'active'
              AND expires_at > CURRENT_TIMESTAMP
            LIMIT 1
            """,
            (user_id,),
        )
        return cursor.fetchone() is not None
    finally:
        connection.close()


def close_temporary_rich_menu_session(user_id):
    """只關閉臨時 Quick Reply 工作階段，絕不解除私人 Rich Menu。"""
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM temporary_rich_menu_sessions WHERE line_user_id = %s",
            (user_id,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def delete_temporary_group_menu_session(user_id):
    """關閉群組 Quick Reply 工作階段，不影響私人聊天室的 Rich Menu。"""
    close_temporary_rich_menu_session(user_id)


def cleanup_expired_rich_menu_sessions():
    """刪除逾時 Quick Reply 工作階段；絕不解除私人 Rich Menu。"""
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            WITH expired AS (
                SELECT line_user_id
                FROM temporary_rich_menu_sessions
                WHERE status = 'active'
                  AND expires_at <= CURRENT_TIMESTAMP
                ORDER BY expires_at
                FOR UPDATE SKIP LOCKED
                LIMIT 20
            )
            UPDATE temporary_rich_menu_sessions s
            SET status = 'closing',
                updated_at = CURRENT_TIMESTAMP
            FROM expired
            WHERE s.line_user_id = expired.line_user_id
            RETURNING s.line_user_id
            """
        )
        expired_user_ids = [row[0] for row in cursor.fetchall()]
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    for expired_user_id in expired_user_ids:
        try:
            connection = get_db_connection()
            try:
                cursor = connection.cursor()
                cursor.execute(
                    "DELETE FROM temporary_rich_menu_sessions WHERE line_user_id = %s",
                    (expired_user_id,),
                )
                connection.commit()
            finally:
                connection.close()
        except Exception:
            app.logger.error(
                "清除逾時 Quick Reply 工作階段失敗：user_id=%s",
                expired_user_id,
            )
            app.logger.error(traceback.format_exc())
            connection = get_db_connection()
            try:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    UPDATE temporary_rich_menu_sessions
                    SET status = 'active',
                        expires_at = CURRENT_TIMESTAMP + INTERVAL '1 minute',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE line_user_id = %s
                    """,
                    (expired_user_id,),
                )
                connection.commit()
            finally:
                connection.close()


def temporary_rich_menu_cleanup_worker():
    while True:
        try:
            cleanup_expired_rich_menu_sessions()
        except Exception:
            app.logger.error("清理暫時 Rich Menu 時發生錯誤")
            app.logger.error(traceback.format_exc())
        time.sleep(TEMP_RICH_MENU_CLEANUP_SECONDS)


def bind_role_rich_menu(user_id, role):
    # 看護首頁依目前有效指派數量動態選擇。
    rich_menu_id = None
    if role != "caregiver":
        rich_menu_id = get_role_rich_menu_id(role)

    if role != "caregiver" and not rich_menu_id:
        raise RuntimeError(
            f"身份 {role} 尚未取得首頁 Rich Menu ID"
        )

    try:
        if role == "caregiver":
            rich_menu_id = link_caregiver_home_menu(user_id)
        else:
            link_rich_menu(user_id, rich_menu_id)

        record_rich_menu_binding(
            line_user_id=user_id,
            role=role,
            line_rich_menu_id=rich_menu_id,
            success=True,
        )

        return rich_menu_id

    except Exception as error:
        try:
            record_rich_menu_binding(
                line_user_id=user_id,
                role=role,
                line_rich_menu_id=rich_menu_id,
                success=False,
                error_message=str(error),
            )
        except Exception:
            app.logger.error(
                "記錄 Rich Menu 綁定失敗時發生錯誤"
            )
            app.logger.error(traceback.format_exc())

        raise





ELDER_MEDICATION_ACTIONS = {
    "elder_today_breakfast",
    "elder_today_lunch",
    "elder_today_dinner",
    "elder_today_bedtime",
    "elder_today_all",
    "elder_taken_breakfast",
    "elder_taken_lunch",
    "elder_taken_dinner",
    "elder_taken_bedtime",
    "elder_taken_today",
    "elder_confirm_taken_meal",
    "elder_cancel",
    "elder_medicine_list",
    "elder_medicine_info",
    "elder_medicine_remaining",
    "elder_medicine_capture",
    "elder_medicine_stop",
    "elder_medicine_select",
    "elder_medicine_stop_reason",
    "elder_medicine_confirm_stop",
    "elder_discomfort_dizziness",
    "elder_discomfort_headache",
    "elder_discomfort_nausea",
    "elder_discomfort_sleep",
    "elder_discomfort_other",
    "elder_discomfort_confirm",
    "elder_calendar_view",
    "elder_calendar_add",
    "elder_calendar_edit",
    "elder_calendar_delete",
    "elder_calendar_reminder",
    "elder_calendar_select_event",
    "elder_calendar_save_datetime",
    "elder_calendar_confirm_delete",
    "elder_sos_contact1",
    "elder_sos_contact2",
    "elder_sos_notify_all",
}



# =========================================================
# 長者：今日用藥與我已服藥
# =========================================================

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

ELDER_MEAL_LABELS = {
    "breakfast": "早餐藥物",
    "lunch": "午餐藥物",
    "dinner": "晚餐藥物",
    "bedtime": "睡前藥物",
}

ELDER_TODAY_ACTION_MEALS = {
    "elder_today_breakfast": "breakfast",
    "elder_today_lunch": "lunch",
    "elder_today_dinner": "dinner",
    "elder_today_bedtime": "bedtime",
}

ELDER_TAKEN_ACTION_MEALS = {
    "elder_taken_breakfast": "breakfast",
    "elder_taken_lunch": "lunch",
    "elder_taken_dinner": "dinner",
    "elder_taken_bedtime": "bedtime",
}


def taipei_now():
    return datetime.now(TAIPEI_TZ)



def get_elder_patient_by_line_user_id(line_user_id):
    """
    取得長者使用者與 patient。
    若使用者已是長者身份，但 patients 尚未建立，會自動建立基本長者資料。
    """
    test_patient = master_roles.test_patient(line_user_id, "elderly")
    if test_patient:
        return test_patient
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                u.id,
                COALESCE(NULLIF(u.display_name, ''), '長者'),
                LOWER(r.code),
                p.id,
                COALESCE(NULLIF(p.full_name, ''), NULLIF(u.display_name, ''), '長者')
            FROM app_users u
            JOIN roles r ON r.id = u.role_id
            LEFT JOIN patients p
              ON p.linked_user_id = u.id
             AND p.is_active = TRUE
            WHERE u.line_user_id = %s
              AND u.is_active = TRUE
            ORDER BY p.updated_at DESC NULLS LAST
            LIMIT 1
            """,
            (line_user_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise RuntimeError("找不到目前使用者資料，請重新加入 Bot 並設定身份")

        role_code = row[2]
        if role_code not in {"elderly", "elder", "patient"}:
            raise RuntimeError(
                f"目前身份為「{role_code}」，請先在身份設定中切換為長者"
            )

        patient_id = row[3]
        patient_name = row[4]

        if not patient_id:
            cursor.execute(
                """
                INSERT INTO patients (
                    linked_user_id,
                    full_name,
                    notes,
                    is_active
                )
                VALUES (%s,%s,'長者首次使用功能時自動建立',TRUE)
                RETURNING id
                """,
                (row[0], patient_name),
            )
            patient_id = cursor.fetchone()[0]
            connection.commit()

        return {
            "user_id": row[0],
            "display_name": row[1],
            "role": role_code,
            "patient_id": patient_id,
            "patient_name": patient_name,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def normalize_meal_slot(meal_slot, schedule_time=None):
    if meal_slot in ELDER_MEAL_LABELS:
        return meal_slot

    if schedule_time is None:
        return None

    hour = schedule_time.hour
    if 4 <= hour < 11:
        return "breakfast"
    if 11 <= hour < 16:
        return "lunch"
    if 16 <= hour < 21:
        return "dinner"
    return "bedtime"


def list_elder_today_medications(patient_id, meal_slot=None):
    """
    取得今天有效的用藥排程。
    meal_slot 優先使用資料庫欄位；舊資料沒有 meal_slot 時，以 schedule_time 推算。
    """
    today = taipei_now().date()
    weekday = today.isoweekday()

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                ms.id,
                ms.medication_id,
                ms.schedule_time,
                ms.dose_amount,
                ms.before_or_after_meal,
                ms.meal_slot,
                m.medication_name,
                m.generic_name,
                m.dosage,
                m.dosage_form,
                m.instructions,
                ml.id,
                ml.status::text,
                ml.taken_at,
                ml.note
            FROM medication_schedules ms
            JOIN medications m
              ON m.id = ms.medication_id
            LEFT JOIN medication_logs ml
              ON ml.schedule_id = ms.id
             AND ml.patient_id = m.patient_id
             AND (ml.scheduled_at AT TIME ZONE 'Asia/Taipei')::date = %s
            WHERE m.patient_id = %s
              AND m.is_active = TRUE
              AND ms.is_active = TRUE
              AND (ms.starts_on IS NULL OR ms.starts_on <= %s)
              AND (ms.ends_on IS NULL OR ms.ends_on >= %s)
              AND %s = ANY(ms.weekdays)
            ORDER BY ms.schedule_time, m.medication_name
            """,
            (today, patient_id, today, today, weekday),
        )
        result = []
        for row in cursor.fetchall():
            slot = normalize_meal_slot(row[5], row[2])
            if meal_slot and slot != meal_slot:
                continue
            result.append({
                "schedule_id": row[0],
                "medication_id": row[1],
                "schedule_time": row[2],
                "dose_amount": row[3],
                "meal_relation": row[4],
                "meal_slot": slot,
                "medication_name": row[6] or "未命名藥物",
                "generic_name": row[7],
                "dosage": row[8],
                "dosage_form": row[9],
                "instructions": row[10],
                "log_id": row[11],
                "status": row[12],
                "taken_at": row[13],
                "log_note": row[14],
            })
        return result
    finally:
        connection.close()


def medication_status_text(status):
    return {
        None: "尚未回報",
        "scheduled": "尚未回報",
        "taken": "已服藥",
        "missed": "漏服",
        "skipped": "略過",
        "late": "延遲服藥",
    }.get(status, status or "尚未回報")


def elder_medication_list_text(patient, meal_slot=None):
    medications = list_elder_today_medications(
        patient["patient_id"],
        meal_slot=meal_slot,
    )
    title = (
        ELDER_MEAL_LABELS[meal_slot]
        if meal_slot
        else "今日全部藥物"
    )

    if not medications:
        return f"{patient['patient_name']}的{title}：\n目前沒有需要使用的藥物。"

    grouped = {}
    for medication in medications:
        slot = medication["meal_slot"] or "other"
        grouped.setdefault(slot, []).append(medication)

    lines = [
        f"{patient['patient_name']}的{title}：",
        f"日期：{taipei_now().strftime('%Y-%m-%d')}",
    ]

    display_order = ["breakfast", "lunch", "dinner", "bedtime", "other"]
    for slot in display_order:
        items = grouped.get(slot, [])
        if not items:
            continue
        lines.extend([
            "",
            f"【{ELDER_MEAL_LABELS.get(slot, '其他時段')}】",
        ])
        for index, item in enumerate(items, 1):
            time_text = (
                item["schedule_time"].strftime("%H:%M")
                if item["schedule_time"]
                else "未設定"
            )
            dose_text = item["dose_amount"] or item["dosage"] or "未標示"
            lines.extend([
                f"{index}. {item['medication_name']}",
                f"   時間：{time_text}",
                f"   每次量：{dose_text}",
                f"   飯前／飯後：{item['meal_relation'] or '未標示'}",
                f"   狀態：{medication_status_text(item['status'])}",
            ])
            if item.get("instructions"):
                lines.append(f"   用法：{item['instructions']}")

    return "\n".join(lines)


def ensure_today_scheduled_log(patient_id, medication):
    now = taipei_now()
    scheduled_local = datetime.combine(
        now.date(),
        medication["schedule_time"],
        tzinfo=TAIPEI_TZ,
    )

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        # 舊資料庫未必已建立三欄唯一約束，因此不要依賴 ON CONFLICT。
        cursor.execute(
            """
            UPDATE medication_logs
            SET medication_id=%s, scheduled_at=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE schedule_id=%s AND patient_id=%s
              AND scheduled_local_date=%s
            RETURNING id, status::text
            """,
            (
                medication["medication_id"], scheduled_local,
                medication["schedule_id"], patient_id, now.date(),
            ),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                """
                INSERT INTO medication_logs (
                    schedule_id, medication_id, patient_id, scheduled_at,
                    status, created_at, updated_at
                ) VALUES (
                    %s,%s,%s,%s,'scheduled',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
                )
                RETURNING id, status::text
                """,
                (
                    medication["schedule_id"], medication["medication_id"],
                    patient_id, scheduled_local,
                ),
            )
            row = cursor.fetchone()
        connection.commit()
        return row[0], row[1]
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def mark_elder_meal_taken(patient, meal_slot, reported_by):
    medications = list_elder_today_medications(
        patient["patient_id"],
        meal_slot=meal_slot,
    )
    if not medications:
        raise RuntimeError(
            f"今天沒有設定{ELDER_MEAL_LABELS[meal_slot]}，無法建立服藥紀錄"
        )

    now = taipei_now()
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        updated = []
        already_taken = []

        for medication in medications:
            scheduled_local = datetime.combine(
                now.date(),
                medication["schedule_time"],
                tzinfo=TAIPEI_TZ,
            )
            note = f"長者確認已服用{ELDER_MEAL_LABELS[meal_slot]}"
            cursor.execute(
                """
                UPDATE medication_logs
                SET medication_id=%s, scheduled_at=%s,
                    taken_at=CASE
                        WHEN status::text='taken' THEN taken_at ELSE %s
                    END,
                    status='taken', reported_by=%s, note=%s,
                    updated_at=CURRENT_TIMESTAMP
                WHERE schedule_id=%s AND patient_id=%s
                  AND scheduled_local_date=%s
                RETURNING status::text, taken_at
                """,
                (
                    medication["medication_id"], scheduled_local, now,
                    reported_by, note, medication["schedule_id"],
                    patient["patient_id"], now.date(),
                ),
            )
            result = cursor.fetchone()
            if not result:
                cursor.execute(
                    """
                    INSERT INTO medication_logs (
                        schedule_id, medication_id, patient_id, scheduled_at,
                        taken_at, status, reported_by, note,
                        created_at, updated_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,'taken',%s,%s,
                        CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
                    )
                    RETURNING status::text, taken_at
                    """,
                    (
                        medication["schedule_id"], medication["medication_id"],
                        patient["patient_id"], scheduled_local, now,
                        reported_by, note,
                    ),
                )
                result = cursor.fetchone()
            if medication.get("status") == "taken":
                already_taken.append(medication["medication_name"])
            else:
                updated.append(medication["medication_name"])

        connection.commit()
        return {
            "updated": updated,
            "already_taken": already_taken,
            "taken_at": now,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def elder_today_taken_record_text(patient):
    medications = list_elder_today_medications(patient["patient_id"])
    taken_medications = [
        item for item in medications
        if item.get("status") == "taken"
    ]
    if not taken_medications:
        return (
            f"{patient['patient_name']}的今日服藥紀錄：\n"
            "今天尚未登記任何已服用藥物。"
        )

    lines = [
        f"{patient['patient_name']}的今日服藥紀錄：",
        f"日期：{taipei_now().strftime('%Y-%m-%d')}",
    ]

    grouped = {}
    for item in taken_medications:
        grouped.setdefault(item["meal_slot"] or "other", []).append(item)

    for slot in ["breakfast", "lunch", "dinner", "bedtime", "other"]:
        items = grouped.get(slot, [])
        if not items:
            continue
        lines.extend([
            "",
            f"【{ELDER_MEAL_LABELS.get(slot, '其他時段')}】",
        ])
        for item in items:
            taken_time = (
                item["taken_at"].astimezone(TAIPEI_TZ).strftime("%H:%M")
                if item.get("taken_at")
                else None
            )
            time_suffix = f"（{taken_time}）" if taken_time else ""
            lines.append(f"・{item['medication_name']}：已服藥{time_suffix}")

    return "\n".join(lines)


def handle_elder_medication_postback(event, action, params):
    if action not in {
        "elder_today_breakfast",
        "elder_today_lunch",
        "elder_today_dinner",
        "elder_today_bedtime",
        "elder_today_all",
        "elder_taken_breakfast",
        "elder_taken_lunch",
        "elder_taken_dinner",
        "elder_taken_bedtime",
        "elder_taken_today",
        "elder_confirm_taken_meal",
        "elder_cancel",
    }:
        return handle_elder_extended_postback(event, action, params)

    line_user_id = get_user_id(event)
    if not line_user_id:
        reply_text(event.reply_token, "無法取得您的 LINE User ID。")
        return True

    patient = get_elder_patient_by_line_user_id(line_user_id)

    if action in ELDER_TODAY_ACTION_MEALS:
        meal_slot = ELDER_TODAY_ACTION_MEALS[action]
        reply_text(
            event.reply_token,
            elder_medication_list_text(patient, meal_slot),
        )
        return True

    if action == "elder_today_all":
        reply_text(
            event.reply_token,
            elder_medication_list_text(patient, meal_slot=None),
        )
        return True

    if action in ELDER_TAKEN_ACTION_MEALS:
        meal_slot = ELDER_TAKEN_ACTION_MEALS[action]
        medications = list_elder_today_medications(
            patient["patient_id"],
            meal_slot=meal_slot,
        )
        if not medications:
            reply_text(
                event.reply_token,
                f"今天沒有設定{ELDER_MEAL_LABELS[meal_slot]}。",
            )
            return True

        not_taken = [
            item for item in medications
            if item.get("status") != "taken"
        ]
        if not not_taken:
            reply_text(
                event.reply_token,
                (
                    f"{ELDER_MEAL_LABELS[meal_slot]}已經完成回報。\n\n"
                    + elder_today_taken_record_text(patient)
                ),
            )
            return True

        medication_names = "\n".join(
            f"・{item['medication_name']}（{item['dose_amount'] or item['dosage'] or '未標示'}）"
            for item in medications
        )
        reply_message(
            event.reply_token,
            make_quick_reply_message(
                (
                    f"確認已吃完{ELDER_MEAL_LABELS[meal_slot]}的所有藥物？\n\n"
                    f"{medication_names}"
                ),
                [
                    postback_item(
                        "確認已服藥",
                        (
                            "action=elder_confirm_taken_meal"
                            f"&meal_slot={meal_slot}"
                        ),
                    ),
                    postback_item("取消", "action=elder_cancel"),
                ],
            ),
        )
        return True

    if action == "elder_confirm_taken_meal":
        meal_slot = params.get("meal_slot", [None])[0]
        if meal_slot not in ELDER_MEAL_LABELS:
            raise RuntimeError("服藥時段資料不正確")

        result = mark_elder_meal_taken(
            patient,
            meal_slot,
            patient["user_id"],
        )
        lines = [
            f"{ELDER_MEAL_LABELS[meal_slot]}服藥紀錄已完成。",
            f"紀錄時間：{result['taken_at'].strftime('%Y-%m-%d %H:%M')}",
        ]
        if result["updated"]:
            lines.extend([
                "",
                "本次已記錄：",
                *[f"・{name}" for name in result["updated"]],
            ])
        if result["already_taken"]:
            lines.extend([
                "",
                "先前已記錄：",
                *[f"・{name}" for name in result["already_taken"]],
            ])
        reply_text(event.reply_token, "\n".join(lines))
        return True

    if action == "elder_taken_today":
        reply_text(
            event.reply_token,
            elder_today_taken_record_text(patient),
        )
        return True

    if action == "elder_cancel":
        clear_operation_state(line_user_id)
        reply_text(event.reply_token, "已取消本次操作。")
        return True

    return False



# =========================================================
# 長者：我的藥物、身體不適、行事曆、SOS
# =========================================================

def camera_quick_reply_message():
    return TextMessage(
        text="請選擇拍攝藥單的方式：",
        quick_reply=QuickReply(
            items=[
                QuickReplyItem(
                    action=CameraAction(label="開啟相機")
                ),
                QuickReplyItem(
                    action=CameraRollAction(label="從圖庫選擇")
                ),
                postback_item("取消", "action=elder_cancel"),
            ]
        ),
    )


def list_elder_active_medications(patient_id):
    return list_patient_medications(patient_id, active_only=True)


def elder_medicine_list_text(patient):
    medications = list_patient_medications(
        patient["patient_id"],
        active_only=False,
    )
    if not medications:
        return f"{patient['patient_name']}目前沒有開藥紀錄。"

    lines = [f"{patient['patient_name']}的全部開藥紀錄："]
    for index, medication in enumerate(medications, 1):
        inv = _medication_inventory_values(medication)
        status = "使用中" if medication.get("is_active") else "已停用／療程結束"
        lines.extend([
            "",
            f"{index}. {medication['medication_name']}",
            f"狀態：{status}",
            f"含量：{medication.get('dosage') or '未標示'}",
            f"用法：{medication.get('instructions') or '未標示'}",
            f"開藥日期：{inv['dispense_date'] or '未標示'}",
            f"原始總量：{_format_quantity(inv['total_quantity'])} {medication['quantity_unit']}",
            f"剩餘：{_format_quantity(inv['remaining'])} {medication['quantity_unit']}",
        ])
    return "\n".join(lines)


def save_elder_discomfort(patient, reported_by, symptom, description=None):
    severity_map = {
        "頭暈": "moderate",
        "頭痛": "moderate",
        "想吐": "moderate",
        "睡不好": "mild",
        "其他問題": "normal",
    }
    occurred_at = taipei_now()
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO abnormal_reports (
                patient_id, reported_by, report_type, severity,
                description, occurred_at, is_active
            )
            VALUES (%s,%s,%s,%s,%s,%s,TRUE)
            """,
            (
                patient["patient_id"],
                reported_by,
                symptom,
                severity_map.get(symptom, "normal"),
                description or symptom,
                occurred_at,
            ),
        )
        connection.commit()
        return occurred_at
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_elder_contacts(patient_id):
    """
    優先讀取 emergency_contacts。
    若家屬端尚未建立專用緊急聯絡人，就以同家庭家屬成員作為備援。
    """
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                contact_name,
                relationship,
                phone_number,
                line_user_id,
                priority_order
            FROM emergency_contacts
            WHERE patient_id=%s AND is_active=TRUE
            ORDER BY priority_order, created_at
            """,
            (patient_id,),
        )
        rows = cursor.fetchall()
        contacts = [{
            "name": r[0],
            "relationship": r[1] or "家屬",
            "phone": r[2],
            "line_user_id": r[3],
            "priority": r[4],
        } for r in rows]

        if contacts:
            return contacts

        cursor.execute(
            """
            SELECT DISTINCT
                COALESCE(NULLIF(fu.display_name,''),'家屬'),
                fu.line_user_id
            FROM patients p
            JOIN family_members elder_member
              ON elder_member.user_id = p.linked_user_id
             AND elder_member.member_role = 'elderly'
             AND elder_member.is_active = TRUE
            JOIN family_members family_member
              ON family_member.family_id = elder_member.family_id
             AND family_member.member_role = 'family'
             AND family_member.is_active = TRUE
            JOIN app_users fu
              ON fu.id = family_member.user_id
             AND fu.is_active = TRUE
            WHERE p.id=%s
            ORDER BY 1
            """,
            (patient_id,),
        )
        return [{
            "name": r[0],
            "relationship": "家屬",
            "phone": None,
            "line_user_id": r[1],
            "priority": index,
        } for index, r in enumerate(cursor.fetchall(), 1)]
    finally:
        connection.close()


def notify_elder_family(patient, message):
    contacts = list_elder_contacts(patient["patient_id"])
    line_ids = [c["line_user_id"] for c in contacts if c.get("line_user_id")]

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT DISTINCT target.line_id
            FROM (
                SELECT flg.line_group_id AS line_id
                FROM patients p
                JOIN family_members em
                  ON em.user_id=p.linked_user_id
                 AND em.member_role='elderly'
                 AND em.is_active=TRUE
                JOIN family_line_groups flg
                  ON flg.family_id=em.family_id
                 AND flg.is_active=TRUE
                WHERE p.id=%s

                UNION

                SELECT fu.line_user_id AS line_id
                FROM patients p
                JOIN family_members em
                  ON em.user_id=p.linked_user_id
                 AND em.member_role='elderly'
                 AND em.is_active=TRUE
                JOIN family_members fm
                  ON fm.family_id=em.family_id
                 AND fm.member_role='family'
                 AND fm.is_active=TRUE
                JOIN app_users fu
                  ON fu.id=fm.user_id AND fu.is_active=TRUE
                WHERE p.id=%s

                UNION

                SELECT cu.line_user_id AS line_id
                FROM patients p
                JOIN caregiver_patient_assignments cpa
                  ON cpa.elder_user_id=p.linked_user_id
                 AND cpa.is_active=TRUE
                JOIN app_users cu
                  ON cu.id=cpa.caregiver_user_id AND cu.is_active=TRUE
                WHERE p.id=%s
            ) target
            WHERE target.line_id IS NOT NULL
            """,
            (
                patient["patient_id"],
                patient["patient_id"],
                patient["patient_id"],
            ),
        )
        line_ids.extend(row[0] for row in cursor.fetchall() if row[0])
    finally:
        connection.close()

    sent = 0
    failed = 0
    for target_id in dict.fromkeys(line_ids):
        api_client, messaging_api = get_messaging_api()
        try:
            messaging_api.push_message(
                PushMessageRequest(
                    to=target_id,
                    messages=[TextMessage(text=safe_text(message))],
                )
            )
            sent += 1
        except Exception:
            failed += 1
            app.logger.error(traceback.format_exc())
        finally:
            api_client.close()
    return sent, failed


def elder_calendar_text(patient):
    events = list_patient_calendar_events(
        patient["patient_id"],
        upcoming_only=False,
        limit=30,
    )
    return calendar_event_text(
        {
            "display_name": patient["patient_name"],
            "patient_id": patient["patient_id"],
        },
        events,
    )


def elder_followup_reminder_text(patient):
    events = [
        item for item in list_patient_calendar_events(
            patient["patient_id"],
            upcoming_only=True,
            limit=50,
        )
        if item.get("event_type") == "follow_up"
    ]
    if not events:
        return (
            f"{patient['patient_name']}目前沒有已由回診單建立的待回診提醒。\n"
            "請先拍攝回診單並確認回診日期。"
        )

    now = taipei_now()
    lines = [f"{patient['patient_name']}的待回診提醒："]
    for index, item in enumerate(events, 1):
        starts_at = item["starts_at"]
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=TAIPEI_TZ)
        else:
            starts_at = starts_at.astimezone(TAIPEI_TZ)
        days_left = max((starts_at.date() - now.date()).days, 0)
        lines.extend([
            "",
            f"{index}. {item['title']}",
            f"時間：{starts_at.strftime('%Y-%m-%d %H:%M')}",
            f"距離回診：{days_left} 天",
            f"地點：{item.get('location') or '未填寫'}",
        ])
        if item.get("description"):
            lines.append(f"備註：{item['description']}")
    return "\n".join(lines)


def handle_elder_extended_postback(event, action, params):
    line_user_id = get_user_id(event)
    patient = get_elder_patient_by_line_user_id(line_user_id)

    if action == "elder_medicine_list":
        reply_text(event.reply_token, elder_medicine_list_text(patient))
        return True

    if action == "elder_medicine_info":
        medications = list_elder_active_medications(patient["patient_id"])
        if not medications:
            reply_text(event.reply_token, "目前沒有可查看說明的藥物。")
            return True
        items = [
            postback_item(
                medication["medication_name"][:20],
                f"action=elder_medicine_select&mode=info&medication_id={medication['id']}",
            )
            for medication in medications[:12]
        ]
        reply_message(
            event.reply_token,
            make_quick_reply_message("請選擇要查看說明的藥物：", items),
        )
        return True

    if action == "elder_medicine_remaining":
        medications = list_elder_active_medications(patient["patient_id"])
        reply_text(
            event.reply_token,
            remaining_summary_text(
                {
                    "display_name": patient["patient_name"],
                    "patient_id": patient["patient_id"],
                },
                medications,
                low_only=False,
            ),
        )
        return True

    if action == "elder_medicine_capture":
        set_operation_state(
            line_user_id,
            action,
            "waiting_elder_prescription_image",
            {"patient_id": str(patient["patient_id"])},
        )
        reply_message(event.reply_token, camera_quick_reply_message())
        return True

    if action == "elder_medicine_stop":
        medications = list_elder_active_medications(patient["patient_id"])
        if not medications:
            reply_text(event.reply_token, "目前沒有可停用的藥物。")
            return True
        items = [
            postback_item(
                medication["medication_name"][:20],
                f"action=elder_medicine_select&mode=stop&medication_id={medication['id']}",
            )
            for medication in medications[:12]
        ]
        reply_message(
            event.reply_token,
            make_quick_reply_message(
                "請選擇要提出停用的藥物。停藥前仍應先詢問醫師：",
                items,
            ),
        )
        return True

    if action == "elder_medicine_select":
        medication_id = params.get("medication_id", [None])[0]
        mode = params.get("mode", [None])[0]
        medication = get_patient_medication(patient["patient_id"], medication_id)
        if not medication:
            raise RuntimeError("找不到這筆藥物資料")

        if mode == "info":
            inv = _medication_inventory_values(medication)
            reply_text(
                event.reply_token,
                (
                    f"藥物：{medication['medication_name']}\n"
                    f"學名：{medication.get('generic_name') or '未標示'}\n"
                    f"含量：{medication.get('dosage') or '未標示'}\n"
                    f"用法：{medication.get('instructions') or '未標示'}\n"
                    f"開藥日期：{inv['dispense_date'] or '未標示'}\n"
                    f"療程天數：{inv['course_days'] or '未標示'}\n"
                    f"原始總量：{_format_quantity(inv['total_quantity'])} "
                    f"{medication['quantity_unit']}\n"
                    f"已服用：{_format_quantity(inv['consumed_quantity'])} "
                    f"{medication['quantity_unit']}\n"
                    f"目前剩餘：{_format_quantity(inv['remaining'])} "
                    f"{medication['quantity_unit']}"
                ),
            )
            return True

        if mode == "stop":
            set_operation_state(
                line_user_id,
                "elder_medicine_stop",
                "select_stop_reason",
                {
                    "patient_id": str(patient["patient_id"]),
                    "medication_id": str(medication["id"]),
                    "medication_name": medication["medication_name"],
                },
            )
            reply_message(
                event.reply_token,
                make_quick_reply_message(
                    (
                        f"要停用「{medication['medication_name']}」的原因是什麼？\n"
                        "停藥前仍建議先詢問醫師或藥師。"
                    ),
                    [
                        postback_item(
                            "服用後不舒服",
                            "action=elder_medicine_stop_reason&reason=服用後不舒服",
                        ),
                        postback_item(
                            "醫師指示停用",
                            "action=elder_medicine_stop_reason&reason=醫師指示停用",
                        ),
                        postback_item(
                            "暫時沒有服用",
                            "action=elder_medicine_stop_reason&reason=暫時沒有服用",
                        ),
                        postback_item("取消", "action=elder_cancel"),
                    ],
                ),
            )
            return True

    if action == "elder_medicine_stop_reason":
        state = get_operation_state(line_user_id)
        payload = state.get("payload", {}) if state else {}
        if not payload.get("medication_id"):
            raise RuntimeError("停用資料已逾時")
        reason = params.get("reason", [None])[0]
        if not reason:
            raise RuntimeError("沒有取得停用原因")
        payload["stop_reason"] = reason[:500]
        set_operation_state(
            line_user_id,
            "elder_medicine_stop",
            "confirm_stop",
            payload,
        )
        reply_message(
            event.reply_token,
            make_quick_reply_message(
                (
                    f"藥物：{payload['medication_name']}\n"
                    f"停用原因：{payload['stop_reason']}\n\n"
                    "確定停止後續排程？原始藥單與服藥紀錄會保留。"
                ),
                [
                    postback_item("確認停用", "action=elder_medicine_confirm_stop"),
                    postback_item("取消", "action=elder_cancel"),
                ],
            ),
        )
        return True

    if action == "elder_medicine_confirm_stop":
        state = get_operation_state(line_user_id)
        payload = state.get("payload", {}) if state else {}
        medication_id = payload.get("medication_id")
        if not medication_id:
            raise RuntimeError("停用資料已逾時")
        connection = get_db_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE medications
                SET is_active=FALSE, end_date=COALESCE(end_date,CURRENT_DATE),
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=%s AND patient_id=%s
                """,
                (medication_id, patient["patient_id"]),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        clear_operation_state(line_user_id)
        notification = (
            "【長者停用藥物通知】\n"
            f"長者：{patient['patient_name']}\n"
            f"藥物：{payload.get('medication_name','未命名藥物')}\n"
            f"原因：{payload.get('stop_reason','未填寫')}\n"
            f"時間：{taipei_now().strftime('%Y-%m-%d %H:%M')}\n"
            "請家屬或看護確認，必要時聯絡醫師或藥師。"
        )
        sent, failed = notify_elder_family(patient, notification)
        reply_text(
            event.reply_token,
            (
                f"已將「{payload.get('medication_name','該藥物')}」標記為停用。\n"
                f"原因：{payload.get('stop_reason','未填寫')}\n"
                f"通知成功：{sent}；失敗：{failed}\n"
                "若身體持續不舒服，請儘快聯絡醫師或藥師。"
            ),
        )
        return True

    symptom_actions = {
        "elder_discomfort_dizziness": "頭暈",
        "elder_discomfort_headache": "頭痛",
        "elder_discomfort_nausea": "想吐",
        "elder_discomfort_sleep": "睡不好",
    }
    if action in symptom_actions:
        symptom = symptom_actions[action]
        occurred_at = save_elder_discomfort(
            patient,
            patient["user_id"],
            symptom,
            symptom,
        )
        reply_text(
            event.reply_token,
            (
                f"已記錄不舒服狀況：{symptom}\n"
                f"時間：{occurred_at.strftime('%Y-%m-%d %H:%M')}\n"
                "家屬端可在「不舒服紀錄」與「異常統計」中查看。"
            ),
        )
        return True

    if action == "elder_discomfort_other":
        set_operation_state(
            line_user_id,
            action,
            "waiting_elder_discomfort_text",
            {"patient_id": str(patient["patient_id"])},
        )
        reply_text(
            event.reply_token,
            "請描述目前不舒服的情況，例如：胸悶、皮膚癢。\n輸入「取消」可結束。",
        )
        return True

    if action == "elder_calendar_view":
        reply_text(event.reply_token, elder_calendar_text(patient))
        return True

    if action == "elder_calendar_add":
        set_operation_state(
            line_user_id,
            action,
            "waiting_elder_calendar_title",
            {"patient_id": str(patient["patient_id"]), "patient_name": patient["patient_name"]},
        )
        reply_text(event.reply_token, "請輸入行程名稱，例如：回診、領藥。")
        return True

    if action in {"elder_calendar_edit", "elder_calendar_delete"}:
        events = list_patient_calendar_events(patient["patient_id"], upcoming_only=False)
        if not events:
            reply_text(event.reply_token, "目前沒有可操作的行程。")
            return True
        mode = "edit" if action == "elder_calendar_edit" else "delete"
        items = [
            postback_item(
                f"{item['starts_at'].strftime('%m/%d')} {item['title']}"[:20],
                f"action=elder_calendar_select_event&mode={mode}&event_id={item['id']}",
            )
            for item in events[:12]
        ]
        reply_message(
            event.reply_token,
            make_quick_reply_message(
                "請選擇行程：",
                items,
            ),
        )
        return True

    if action == "elder_calendar_select_event":
        event_id = params.get("event_id", [None])[0]
        mode = params.get("mode", [None])[0]
        calendar_item = get_patient_calendar_event(patient["patient_id"], event_id)
        if not calendar_item:
            raise RuntimeError("找不到行程")

        if mode == "delete":
            set_operation_state(
                line_user_id,
                "elder_calendar_delete",
                "confirm_elder_calendar_delete",
                {"event_id": str(event_id), "event_title": calendar_item["title"]},
            )
            reply_message(
                event.reply_token,
                make_quick_reply_message(
                    f"確定刪除「{calendar_item['title']}」？",
                    [
                        postback_item(
                            "確認刪除",
                            "action=elder_calendar_confirm_delete",
                        ),
                        postback_item("取消", "action=elder_cancel"),
                    ],
                ),
            )
            return True

        set_operation_state(
            line_user_id,
            "elder_calendar_edit",
            "waiting_elder_calendar_datetime",
            {"event_id": str(event_id), "event_title": calendar_item["title"]},
        )
        reply_message(
            event.reply_token,
            make_quick_reply_message(
                "請選擇新的日期與時間：",
                [
                    datetime_item(
                        "選擇日期時間",
                        "action=elder_calendar_save_datetime&mode=edit",
                        mode="datetime",
                        minimum=taipei_now().strftime("%Y-%m-%dT%H:%M"),
                    ),
                    postback_item("取消", "action=elder_cancel"),
                ],
            ),
        )
        return True

    if action == "elder_calendar_save_datetime":
        state = get_operation_state(line_user_id)
        payload = state.get("payload", {}) if state else {}
        dt_value = get_postback_datetime(event)
        if not dt_value:
            raise RuntimeError("沒有取得日期時間")
        selected_dt = datetime.fromisoformat(dt_value).replace(tzinfo=TAIPEI_TZ)
        mode = params.get("mode", [None])[0]

        if mode == "add":
            create_patient_calendar_event(
                patient["patient_id"],
                payload["calendar_title"],
                payload.get("calendar_description"),
                payload.get("calendar_location"),
                selected_dt,
                patient["user_id"],
            )
            clear_operation_state(line_user_id)
            reply_text(event.reply_token, "行程新增完成。")
            return True

        update_patient_calendar_event(
            payload["event_id"],
            patient["patient_id"],
            "starts_at",
            selected_dt,
        )
        clear_operation_state(line_user_id)
        reply_text(event.reply_token, "行程日期時間修改完成。")
        return True

    if action == "elder_calendar_confirm_delete":
        state = get_operation_state(line_user_id)
        payload = state.get("payload", {}) if state else {}
        delete_patient_calendar_event(
            payload["event_id"],
            patient["patient_id"],
        )
        clear_operation_state(line_user_id)
        reply_text(
            event.reply_token,
            f"已刪除行程：{payload.get('event_title','未命名行程')}",
        )
        return True

    if action == "elder_calendar_reminder":
        reply_text(
            event.reply_token,
            elder_followup_reminder_text(patient),
        )
        return True

    if action in {"elder_sos_contact1", "elder_sos_contact2"}:
        index = 0 if action.endswith("contact1") else 1
        contacts = list_elder_contacts(patient["patient_id"])
        if len(contacts) <= index:
            reply_text(event.reply_token, f"目前尚未設定緊急聯絡人 {index + 1}。")
            return True
        contact = contacts[index]
        notification_sent = False
        if contact.get("line_user_id"):
            api_client, messaging_api = get_messaging_api()
            try:
                messaging_api.push_message(
                    PushMessageRequest(
                        to=contact["line_user_id"],
                        messages=[TextMessage(text=safe_text(
                            "【長者緊急通知】\n"
                            f"{patient['patient_name']}指定通知您。\n"
                            f"時間：{taipei_now().strftime('%Y-%m-%d %H:%M')}\n"
                            "請儘快確認長者狀況。"
                        ))],
                    )
                )
                notification_sent = True
            except Exception:
                app.logger.error(traceback.format_exc())
            finally:
                api_client.close()
        items = []
        if contact.get("phone"):
            items.append(
                QuickReplyItem(
                    action=URIAction(
                        label="立即撥打",
                        uri=f"tel:{contact['phone']}",
                    )
                )
            )
        reply_message(
            event.reply_token,
            make_quick_reply_message(
                (
                    f"緊急聯絡人 {index + 1}\n"
                    f"姓名：{contact['name']}\n"
                    f"關係：{contact['relationship']}\n"
                    f"電話：{contact.get('phone') or '未設定'}\n"
                    f"LINE 通知：{'已送出' if notification_sent else '未送出或傳送失敗'}"
                ),
                items or [postback_item("返回", "action=elder_cancel")],
            ),
        )
        return True

    if action == "elder_sos_notify_all":
        message = (
            f"【長者緊急通知】\n"
            f"{patient['patient_name']}按下了緊急通知。\n"
            f"時間：{taipei_now().strftime('%Y-%m-%d %H:%M')}\n"
            "請儘快確認長者狀況。"
        )
        sent, failed = notify_elder_family(patient, message)
        reply_text(
            event.reply_token,
            f"緊急通知已送出。\n成功：{sent} 位／群組\n失敗：{failed}",
        )
        return True

    return False

# =========================================================
# 看護功能：選擇長者、任務、用藥、行事曆、異常與 SOS
# =========================================================

CAREGIVER_ACTIONS = {
    "caregiver_select_patient", "caregiver_back_selector",
    "caregiver_emergency", "caregiver_tasks",
    "caregiver_medication_schedule", "caregiver_medication_plan",
    "caregiver_medication_records", "caregiver_medication_summary",
    "caregiver_calendar", "caregiver_prescription_details",
    "caregiver_recognition_result", "caregiver_medication_warnings",
    "caregiver_report_issue", "caregiver_sos_contact",
    "caregiver_sos_notify_all",
}

CAREGIVER_SLOT_LABELS = {
    "breakfast": "Morning", "lunch": "Noon/Afternoon",
    "dinner": "Evening", "bedtime": "Bedtime", "prn": "PRN",
}


def _caregiver_user(line_user_id):
    user = get_app_user_by_line_id(line_user_id)
    if not user or user.get("role") != "caregiver":
        raise RuntimeError("This function is available to caregivers only.")
    return user


def list_caregiver_patients(line_user_id):
    test_patient = master_roles.test_patient(line_user_id, "caregiver")
    if test_patient:
        return [test_patient]
    caregiver = _caregiver_user(line_user_id)
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT p.id, COALESCE(NULLIF(p.full_name,''), NULLIF(e.display_name,''), 'Patient'),
                   p.linked_user_id, e.line_user_id
            FROM caregiver_patient_assignments cpa
            JOIN app_users e ON e.id=cpa.elder_user_id AND e.is_active=TRUE
            JOIN patients p ON p.linked_user_id=e.id AND p.is_active=TRUE
            WHERE cpa.caregiver_user_id=%s AND cpa.is_active=TRUE
            ORDER BY cpa.created_at, p.created_at
            """,
            (caregiver["id"],),
        )
        return [{"patient_id": r[0], "patient_name": r[1],
                 "user_id": r[2], "line_user_id": r[3]} for r in cursor.fetchall()]
    finally:
        connection.close()


def caregiver_home_alias(line_user_id):
    """一位長者顯示雙格首頁；兩位以上顯示長者選擇首頁。"""
    return (
        "caregiver_single_main"
        if len(list_caregiver_patients(line_user_id)) == 1
        else "caregiver_main"
    )


def link_caregiver_home_menu(line_user_id):
    return link_rich_menu_alias(
        line_user_id,
        caregiver_home_alias(line_user_id),
    )


def select_caregiver_patient(line_user_id, slot):
    test_patient = master_roles.test_patient(line_user_id, "caregiver")
    if test_patient:
        if str(slot) != "1":
            raise RuntimeError("測試模式只有一位測試患者")
        return test_patient
    caregiver = _caregiver_user(line_user_id)
    patients = list_caregiver_patients(line_user_id)
    try:
        index = int(slot) - 1
    except (TypeError, ValueError):
        index = -1
    if index < 0 or index >= len(patients):
        raise RuntimeError(
            f"Patient {slot} has not been assigned to you. "
            "Please ask the family account to assign this patient first."
        )
    patient = patients[index]
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO caregiver_selected_patients (caregiver_user_id,patient_id)
            VALUES (%s,%s)
            ON CONFLICT (caregiver_user_id) DO UPDATE SET
                patient_id=EXCLUDED.patient_id,
                selected_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            """,
            (caregiver["id"], patient["patient_id"]),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return patient


def get_selected_caregiver_patient(line_user_id, required=True):
    test_patient = master_roles.test_patient(line_user_id, "caregiver")
    if test_patient:
        return test_patient
    caregiver = _caregiver_user(line_user_id)
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT p.id, COALESCE(NULLIF(p.full_name,''),NULLIF(e.display_name,''),'Patient'),
                   p.linked_user_id, e.line_user_id
            FROM caregiver_selected_patients selected
            JOIN patients p ON p.id=selected.patient_id AND p.is_active=TRUE
            JOIN app_users e ON e.id=p.linked_user_id AND e.is_active=TRUE
            JOIN caregiver_patient_assignments cpa
              ON cpa.caregiver_user_id=selected.caregiver_user_id
             AND cpa.elder_user_id=p.linked_user_id AND cpa.is_active=TRUE
            WHERE selected.caregiver_user_id=%s
            """,
            (caregiver["id"],),
        )
        row = cursor.fetchone()
        if row:
            return {"patient_id": row[0], "patient_name": row[1],
                    "user_id": row[2], "line_user_id": row[3]}
    finally:
        connection.close()
    if required:
        raise RuntimeError("Please select a patient first.")
    return None


def link_rich_menu_alias(user_id, alias_id):
    response = requests.get(
        f"https://api.line.me/v2/bot/richmenu/alias/{alias_id}",
        headers={"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"}, timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Cannot load Rich Menu alias {alias_id}: HTTP {response.status_code}")
    rich_menu_id = response.json().get("richMenuId")
    link_rich_menu(user_id, rich_menu_id)
    return rich_menu_id


def caregiver_plan_text(patient, slot=None):
    medications = list_elder_today_medications(patient["patient_id"], None if slot == "prn" else slot)
    if slot == "prn":
        medications = [m for m in medications if m.get("meal_slot") == "prn" or
                       re.search(r"\b(PRN|需要時|必要時)\b", m.get("instructions") or "", re.I)]
    if not medications:
        return f"{patient['patient_name']}\nNo {CAREGIVER_SLOT_LABELS.get(slot, 'scheduled')} medication found."
    lines = [f"{patient['patient_name']} – {CAREGIVER_SLOT_LABELS.get(slot, 'Medication Schedule')}"]
    for index, item in enumerate(medications, 1):
        when = item.get("schedule_time")
        when_text = when.strftime("%H:%M") if hasattr(when, "strftime") else str(when or "Time not set")
        dose = item.get("dose_amount") or item.get("dosage") or "Not specified"
        lines.extend(["", f"{index}. {item['medication_name']}",
                      f"Time: {when_text}", f"Dose: {dose}",
                      f"Directions: {item.get('instructions') or item.get('meal_relation') or 'Not specified'}"])
    return "\n".join(lines)


def caregiver_records_text(patient, period):
    conditions = {
        "today": "(ml.scheduled_at AT TIME ZONE 'Asia/Taipei')::date=CURRENT_DATE",
        "7days": "ml.scheduled_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'",
        "late": "ml.status::text='taken' AND ml.taken_at > ml.scheduled_at + INTERVAL '30 minutes'",
        "missed": "ml.status::text='missed' OR (ml.status::text='scheduled' AND ml.scheduled_at < CURRENT_TIMESTAMP - INTERVAL '30 minutes')",
    }
    condition = conditions.get(period, conditions["today"])
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            SELECT m.medication_name,ml.status::text,ml.scheduled_at,ml.taken_at,
                   COALESCE(NULLIF(ms.dose_amount,''),m.dose_per_time::text),
                   m.quantity_unit
            FROM medication_logs ml
            JOIN medications m ON m.id=ml.medication_id
            LEFT JOIN medication_schedules ms ON ms.id=ml.schedule_id
            WHERE ml.patient_id=%s AND ({condition})
            ORDER BY ml.scheduled_at DESC LIMIT 50
            """,
            (patient["patient_id"],),
        )
        rows = cursor.fetchall()
    finally:
        connection.close()
    if not rows:
        return f"{patient['patient_name']}\nNo matching medication records."
    labels = {"today": "Today's Records", "7days": "Last 7 Days", "late": "Late Records", "missed": "Missed Records"}
    lines = [f"{patient['patient_name']} – {labels.get(period, 'Medication Records')}"]
    for name, status, scheduled, taken, dose, unit in rows:
        local_time = scheduled.astimezone(TAIPEI_TZ).strftime("%m-%d %H:%M") if scheduled else "Time not set"
        lines.append(f"• {local_time} | {name} | {dose or '-'} {unit or ''} | {status}")
    return "\n".join(lines)


def caregiver_calendar_text(patient, event_type):
    events = list_patient_calendar_events(patient["patient_id"], upcoming_only=True)
    if event_type == "reminders":
        limit_date = taipei_now() + timedelta(days=3)
        events = [e for e in events if e.get("starts_at") and e["starts_at"] <= limit_date]
    elif event_type == "temporary":
        events = [e for e in events if e.get("event_type") in {"temporary", "temporary_appointment"}]
    elif event_type != "other":
        events = [e for e in events if e.get("event_type") == event_type]
    else:
        events = [e for e in events if e.get("event_type") not in {"hospital_visit", "medication_pickup", "temporary", "temporary_appointment"}]
    if not events:
        return f"{patient['patient_name']}\nNo matching upcoming calendar events."
    lines = [f"{patient['patient_name']} – Upcoming Calendar"]
    for item in events:
        starts = item["starts_at"].astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
        lines.extend(["", f"• {item['title']}", f"  {starts}", f"  {item.get('location') or 'Location not set'}"])
    return "\n".join(lines)


def caregiver_prescription_text(patient, mode):
    records = list_medication_bag_records(patient["patient_id"], limit=1)
    if not records:
        return f"{patient['patient_name']} has no prescription scan records."
    record = records[0]
    if mode == "recognition":
        content = record.get("parsed_result") or record.get("original_text")
        if isinstance(content, dict) and content.get("display_text"):
            content = content["display_text"]
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        return f"{patient['patient_name']} – Recognition Result\n\n{content or 'Recognition is not complete yet.'}"
    created = record["created_at"].astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
    return (f"{patient['patient_name']} – Latest Prescription\n"
            f"Uploaded: {created}\nUploader: {record['uploader_name']} ({record['uploader_role']})\n"
            f"Status: {record['status'] or 'unknown'}")


def save_caregiver_issue(patient, reported_by, issue_type, description=None):
    labels = {"refuse_service": "Refuse Service", "body_discomfort": "Body Discomfort",
              "vomiting": "Vomiting", "missing_medication": "Missing Medication", "other": "Other Issue"}
    severity = "urgent" if issue_type == "vomiting" else "moderate"
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """INSERT INTO abnormal_reports
               (patient_id,reported_by,report_type,severity,description,occurred_at,is_active)
               VALUES (%s,%s,%s,%s,%s,%s,TRUE)""",
            (patient["patient_id"], reported_by, labels.get(issue_type, issue_type),
             severity, description or labels.get(issue_type, issue_type), taipei_now()),
        )
        connection.commit()
    except Exception:
        connection.rollback(); raise
    finally:
        connection.close()
    return labels.get(issue_type, issue_type)


def handle_caregiver_postback(event, action, params):
    user_id = get_user_id(event)
    caregiver = _caregiver_user(user_id)
    if action == "caregiver_select_patient":
        slot = params.get("slot", [None])[0]
        patient = select_caregiver_patient(user_id, slot)
        link_rich_menu_alias(user_id, f"cg_p{int(slot)}_main")
        reply_text(event.reply_token, f"Selected: {patient['patient_name']}\nThe patient menu is now ready.")
        return True

    if action == "caregiver_back_selector":
        link_caregiver_home_menu(user_id)
        return True

    patient = get_selected_caregiver_patient(user_id, required=False)
    if action == "caregiver_emergency" and not patient:
        patients = list_caregiver_patients(user_id)
        if len(patients) == 1:
            patient = select_caregiver_patient(user_id, 1)
        else:
            reply_text(event.reply_token, "Please select a patient before using Emergency.")
            return True
    if not patient:
        reply_text(event.reply_token, "Please select a patient first.")
        return True
    if action == "caregiver_emergency":
        patients = list_caregiver_patients(user_id)
        selected_slot = next(
            (
                index
                for index, item in enumerate(patients, 1)
                if item["patient_id"] == patient["patient_id"]
            ),
            1,
        )
        link_rich_menu_alias(user_id, f"cg_p{selected_slot}_sos")
        reply_text(event.reply_token, f"Emergency contacts for {patient['patient_name']} are ready.")
    elif action in {"caregiver_tasks", "caregiver_medication_plan"}:
        reply_text(event.reply_token, caregiver_plan_text(patient, params.get("slot", [None])[0]))
    elif action == "caregiver_medication_schedule":
        reply_text(event.reply_token, caregiver_plan_text(patient))
    elif action == "caregiver_medication_records":
        reply_text(event.reply_token, caregiver_records_text(patient, params.get("period", ["today"])[0]))
    elif action == "caregiver_medication_summary":
        summary_patient = {"display_name": patient["patient_name"]}
        reply_text(event.reply_token, medication_summary_text(summary_patient, list_patient_medications(patient["patient_id"], True)))
    elif action == "caregiver_calendar":
        reply_text(event.reply_token, caregiver_calendar_text(patient, params.get("type", ["other"])[0]))
    elif action == "caregiver_prescription_details":
        reply_text(event.reply_token, caregiver_prescription_text(patient, "details"))
    elif action == "caregiver_recognition_result":
        reply_text(event.reply_token, caregiver_prescription_text(patient, "recognition"))
    elif action == "caregiver_medication_warnings":
        medications = list_patient_medications(patient["patient_id"], True)
        lines = [f"{patient['patient_name']} – Medication Warnings"]
        for medication in medications:
            lines.append(f"• {medication['medication_name']}: {medication.get('instructions') or 'No warning recorded'}")
        reply_text(event.reply_token, "\n".join(lines) if medications else f"{patient['patient_name']} has no active medications.")
    elif action == "caregiver_report_issue":
        issue_type = params.get("type", ["other"])[0]
        if issue_type in {"body_discomfort", "other"}:
            set_operation_state(user_id, "caregiver_report_issue", "waiting_caregiver_issue_text",
                                {"patient_id": str(patient["patient_id"]), "issue_type": issue_type})
            reply_text(event.reply_token, "Please describe the issue. The current time will be recorded automatically.\nType Cancel to stop.")
        else:
            label = save_caregiver_issue(patient, caregiver["id"], issue_type)
            reply_text(event.reply_token, f"Recorded: {label}\nTime: {taipei_now().strftime('%Y-%m-%d %H:%M')}")
    elif action == "caregiver_sos_contact":
        contacts = list_elder_contacts(patient["patient_id"])
        number = int(params.get("contact", ["1"])[0])
        if len(contacts) < number:
            reply_text(event.reply_token, f"Emergency Contact {number} has not been set by the family.")
        else:
            contact = contacts[number - 1]
            message = (f"EMERGENCY: {patient['patient_name']} needs assistance.\n"
                       f"Reported by caregiver at {taipei_now().strftime('%Y-%m-%d %H:%M')}.")
            sent = failed = 0
            if contact.get("line_user_id"):
                api_client, messaging_api = get_messaging_api()
                try:
                    messaging_api.push_message(
                        PushMessageRequest(
                            to=contact["line_user_id"],
                            messages=[TextMessage(text=safe_text(message))],
                        )
                    )
                    sent = 1
                except Exception:
                    failed = 1
                    app.logger.error(traceback.format_exc())
                finally:
                    api_client.close()
            reply_text(event.reply_token, f"Contact {number}: {contact['name']} ({contact['relationship']})\nPhone: {contact.get('phone') or 'Not set'}\nNotification sent: {sent}; failed: {failed}")
    elif action == "caregiver_sos_notify_all":
        message = (f"EMERGENCY: {patient['patient_name']} needs immediate assistance.\n"
                   f"Caregiver notification time: {taipei_now().strftime('%Y-%m-%d %H:%M')}.")
        sent, failed = notify_elder_family(patient, message)
        reply_text(event.reply_token, f"Emergency notification complete.\nSent: {sent}; failed: {failed}")
    return True


# =========================================================
# 家庭管理功能
# =========================================================

FAMILY_ACTIONS = {
    "family_add_elder",
    "family_manage_elder",
    "family_add_caregiver",
    "family_assign_caregiver",
    "family_bind_group",
    "family_confirm_add_elder",
    "family_confirm_add_caregiver",
    "family_remove_elder",
    "family_confirm_remove_elder",
    "family_select_caregiver",
    "family_select_elder_for_caregiver",
    "family_confirm_assignment",
    "family_medication_list",
    "family_medication_correct",
    "family_medication_remaining",
    "family_medication_low",
    "family_medication_bag_records",
    "family_medication_select_patient",
    "family_medication_select_item",
    "family_medication_select_bag",
    "family_medication_confirm_quantity",
    "family_calendar_view",
    "family_calendar_add",
    "family_calendar_edit",
    "family_calendar_delete",
    "family_calendar_reminder",
    "family_calendar_select_patient",
    "family_calendar_select_event",
    "family_calendar_select_edit_field",
    "family_calendar_save_datetime",
    "family_calendar_confirm_delete",
    "family_calendar_enable_reminder",
    "family_calendar_disable_reminder",
    "family_report_today",
    "family_report_7days",
    "family_report_30days",
    "family_report_abnormal",
    "family_report_summary",
    "family_report_select_patient",
    "family_monitor_today_status",
    "family_monitor_missed",
    "family_monitor_emergency",
    "family_monitor_discomfort",
    "family_monitor_adherence",
    "family_monitor_select_patient",
    "family_monitor_adherence_7",
    "family_monitor_adherence_30",
    "family_monitor_contact_select",
    "family_cancel",
}


def reply_message(reply_token, message):
    api_client, messaging_api = get_messaging_api()
    try:
        messaging_api.reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=[message])
        )
    finally:
        api_client.close()


def create_group_quick_menu(role, page=None):
    config = GROUP_FLEX_MENUS.get(role)
    if not config:
        raise RuntimeError(f"尚未設定身份 {role} 的群組選單")

    pages = config.get("pages", {})
    is_submenu = bool(page and page in pages)
    entries = pages.get(page, []) if is_submenu else config["home"]
    title = config["title"] if not is_submenu else next(
        (label for label, key in config["home"] if key == page),
        config["title"],
    )

    items = []
    for label, key in entries:
        if not is_submenu and key in pages:
            data = f"action=group_quick_menu&menu={key}"
            # 分頁／分類按鈕只切換下一層，不在聊天室顯示點擊文字。
            items.append(QuickReplyItem(
                action=PostbackAction(label=label[:20], data=data)
            ))
        else:
            data = f"action={key}"
            # 真正執行功能時才顯示使用者點擊的項目，接著由 Bot 回傳結果。
            items.append(postback_item(label, data, display_text=label))

    if is_submenu:
        items.append(QuickReplyItem(action=PostbackAction(
            label="返回主選單", data="action=group_quick_menu"
        )))
    items.append(QuickReplyItem(action=PostbackAction(
        label="關閉選單", data="action=close_group_quick_menu"
    )))

    prompt = f"【{title}】\n請點擊下方按鈕。選單 10 分鐘有效。"
    if not entries:
        prompt = f"【{title}】\n這個分類目前尚未設定功能。"

    return TextMessage(
        text=prompt,
        quick_reply=QuickReply(items=items),
    )


def make_quick_reply_message(text, items):
    return TextMessage(
        text=safe_text(text),
        quick_reply=QuickReply(items=items),
    )


def postback_item(label, data, display_text=None):
    return QuickReplyItem(
        action=PostbackAction(
            label=label[:20],
            data=data,
            display_text=(display_text or label)[:300],
        )
    )



def datetime_item(label, data, mode="datetime", initial=None, minimum=None, maximum=None):
    kwargs = {"label": label[:20], "data": data, "mode": mode}
    if initial:
        kwargs["initial"] = initial
    if minimum:
        kwargs["min"] = minimum
    if maximum:
        kwargs["max"] = maximum
    return QuickReplyItem(action=DatetimePickerAction(**kwargs))


def get_postback_datetime(event):
    """兼容 LINE SDK v3 的 dict 與 model 兩種 datetime params。"""
    postback_params = getattr(getattr(event, "postback", None), "params", None)
    if isinstance(postback_params, dict):
        return postback_params.get("datetime")
    if postback_params is None:
        return None
    value = getattr(postback_params, "datetime", None)
    if value:
        return value
    if hasattr(postback_params, "to_dict"):
        return postback_params.to_dict().get("datetime")
    return None


def get_app_user_by_line_id(line_user_id):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT u.id, u.line_user_id, u.display_name, r.code AS role
            FROM app_users u
            JOIN roles r ON r.id = u.role_id
            WHERE u.line_user_id = %s AND u.is_active = TRUE
            """,
            (line_user_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {"id": row[0], "line_user_id": row[1], "display_name": row[2], "role": row[3]}
    finally:
        connection.close()


def get_or_create_family_for_admin(line_user_id):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT id, display_name FROM app_users WHERE line_user_id=%s AND is_active=TRUE", (line_user_id,))
        user = cursor.fetchone()
        if not user:
            raise RuntimeError("找不到目前家屬的使用者資料")
        user_id, display_name = user
        cursor.execute(
            """
            SELECT f.id, f.family_name
            FROM families f
            JOIN family_members fm ON fm.family_id=f.id
            WHERE fm.user_id=%s AND fm.member_role='family' AND fm.is_active=TRUE AND f.is_active=TRUE
            ORDER BY fm.created_at LIMIT 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        if row:
            return {"id": row[0], "family_name": row[1], "admin_user_id": user_id}
        family_name = f"{display_name or '家屬'}的家庭"
        cursor.execute("INSERT INTO families (family_name, created_by) VALUES (%s,%s) RETURNING id", (family_name, user_id))
        family_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO family_members (family_id,user_id,member_role,is_admin,is_active,added_by)
            VALUES (%s,%s,'family',TRUE,TRUE,%s)
            """,
            (family_id, user_id, user_id),
        )
        connection.commit()
        return {"id": family_id, "family_name": family_name, "admin_user_id": user_id}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_operation_state(line_user_id, action, step, payload=None):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT id FROM app_users WHERE line_user_id=%s", (line_user_id,))
        row = cursor.fetchone()
        if not row:
            raise RuntimeError("找不到使用者資料")
        cursor.execute(
            """
            INSERT INTO user_operation_states (user_id,action,step,payload,updated_at)
            VALUES (%s,%s,%s,%s::jsonb,CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET
              action=EXCLUDED.action, step=EXCLUDED.step, payload=EXCLUDED.payload,
              updated_at=CURRENT_TIMESTAMP
            """,
            (row[0], action, step, json.dumps(payload or {}, ensure_ascii=False)),
        )
        connection.commit()
    except Exception:
        connection.rollback(); raise
    finally:
        connection.close()


def get_operation_state(line_user_id):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT s.action,s.step,s.payload
            FROM user_operation_states s JOIN app_users u ON u.id=s.user_id
            WHERE u.line_user_id=%s
            """, (line_user_id,)
        )
        row=cursor.fetchone()
        return {"action":row[0],"step":row[1],"payload":row[2] or {}} if row else None
    finally:
        connection.close()


def clear_operation_state(line_user_id):
    connection=get_db_connection()
    try:
        cursor=connection.cursor()
        cursor.execute("DELETE FROM user_operation_states USING app_users u WHERE user_operation_states.user_id=u.id AND u.line_user_id=%s", (line_user_id,))
        connection.commit()
    finally:
        connection.close()


def ensure_family_admin(line_user_id):
    user=get_app_user_by_line_id(line_user_id)
    if not user or user["role"] != "family":
        raise RuntimeError("只有家屬身份可以使用家庭管理功能")
    return get_or_create_family_for_admin(line_user_id)


def list_family_members(family_id, member_role):
    connection=get_db_connection()
    try:
        cursor=connection.cursor()
        cursor.execute(
            """
            SELECT u.id,u.line_user_id,u.display_name
            FROM family_members fm JOIN app_users u ON u.id=fm.user_id
            WHERE fm.family_id=%s AND fm.member_role=%s AND fm.is_active=TRUE AND u.is_active=TRUE
            ORDER BY u.display_name,u.created_at
            """, (family_id,member_role)
        )
        return [{"id":r[0],"line_user_id":r[1],"display_name":r[2] or "未命名使用者"} for r in cursor.fetchall()]
    finally:
        connection.close()


def bind_member_to_family(family_id, target_user_id, member_role, added_by):
    connection=get_db_connection()
    try:
        cursor=connection.cursor()
        cursor.execute(
            """
            INSERT INTO family_members (family_id,user_id,member_role,is_admin,is_active,added_by,removed_at)
            VALUES (%s,%s,%s,FALSE,TRUE,%s,NULL)
            ON CONFLICT (family_id,user_id) DO UPDATE SET member_role=EXCLUDED.member_role,is_active=TRUE,added_by=EXCLUDED.added_by,removed_at=NULL,updated_at=CURRENT_TIMESTAMP
            """, (family_id,target_user_id,member_role,added_by)
        )
        connection.commit()
    except Exception:
        connection.rollback(); raise
    finally:
        connection.close()


def remove_elder_from_family(family_id, elder_user_id, removed_by):
    connection=get_db_connection()
    try:
        cursor=connection.cursor()
        cursor.execute("UPDATE family_members SET is_active=FALSE,removed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE family_id=%s AND user_id=%s AND member_role='elderly' AND is_active=TRUE", (family_id,elder_user_id))
        if cursor.rowcount == 0:
            raise RuntimeError("找不到這位長者的有效家庭綁定")
        cursor.execute("UPDATE caregiver_patient_assignments SET is_active=FALSE,ended_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE family_id=%s AND elder_user_id=%s AND is_active=TRUE", (family_id,elder_user_id))
        connection.commit()
    except Exception:
        connection.rollback(); raise
    finally:
        connection.close()


def assign_caregiver(family_id, caregiver_user_id, elder_user_id, assigned_by):
    connection=get_db_connection()
    try:
        cursor=connection.cursor()
        cursor.execute(
            """
            INSERT INTO caregiver_patient_assignments (family_id,caregiver_user_id,elder_user_id,assigned_by,is_active,ended_at)
            VALUES (%s,%s,%s,%s,TRUE,NULL)
            ON CONFLICT (family_id,caregiver_user_id,elder_user_id) DO UPDATE SET is_active=TRUE,assigned_by=EXCLUDED.assigned_by,ended_at=NULL,updated_at=CURRENT_TIMESTAMP
            """, (family_id,caregiver_user_id,elder_user_id,assigned_by)
        )
        connection.commit()
    except Exception:
        connection.rollback(); raise
    finally:
        connection.close()


def bind_group_to_family(family_id, group_id, bound_by):
    if not group_id or not group_id.startswith("C") or len(group_id) < 20:
        raise RuntimeError("群組 ID 格式不正確，LINE 群組 ID 通常以 C 開頭")
    connection=get_db_connection()
    try:
        cursor=connection.cursor()
        cursor.execute(
            """
            INSERT INTO family_line_groups (family_id,line_group_id,bound_by,is_active)
            VALUES (%s,%s,%s,TRUE)
            ON CONFLICT (line_group_id) DO UPDATE SET family_id=EXCLUDED.family_id,bound_by=EXCLUDED.bound_by,is_active=TRUE,updated_at=CURRENT_TIMESTAMP
            """, (family_id,group_id,bound_by)
        )
        connection.commit()
    except Exception:
        connection.rollback(); raise
    finally:
        connection.close()


def handle_family_text_input(event, user_text, user_id):
    state=get_operation_state(user_id)
    if not state:
        return False
    family=ensure_family_admin(user_id)
    admin_id=family["admin_user_id"]
    if state["step"] == "waiting_elder_id":
        target=get_app_user_by_line_id(user_text)
        if not target:
            reply_text(event.reply_token,"找不到這個 LINE User ID。請確認對方已加入 Bot 並完成身份設定。\n輸入「取消」可結束操作。")
            return True
        if target["role"] != "elderly":
            reply_text(event.reply_token,f"此使用者身份是「{ROLE_CONFIG.get(target['role'],{}).get('name',target['role'])}」，不是長者。")
            return True
        set_operation_state(user_id,"family_add_elder","confirm",{"target_id":str(target["id"]),"line_user_id":target["line_user_id"],"display_name":target["display_name"]})
        reply_message(event.reply_token, make_quick_reply_message(f"找到長者：{target['display_name'] or '未命名'}\nLINE User ID：{target['line_user_id']}\n\n確定加入您的家庭？", [postback_item("確認新增","action=family_confirm_add_elder"),postback_item("取消","action=family_cancel")]))
        return True
    if state["step"] == "waiting_caregiver_id":
        target=get_app_user_by_line_id(user_text)
        if not target:
            reply_text(event.reply_token,"找不到這個 LINE User ID。請確認對方已加入 Bot 並完成身份設定。")
            return True
        if target["role"] != "caregiver":
            reply_text(event.reply_token,"這個帳號不是看護身份，無法加入。")
            return True
        set_operation_state(user_id,"family_add_caregiver","confirm",{"target_id":str(target["id"]),"line_user_id":target["line_user_id"],"display_name":target["display_name"]})
        reply_message(event.reply_token, make_quick_reply_message(f"找到看護：{target['display_name'] or '未命名'}\nLINE User ID：{target['line_user_id']}\n\n確定加入您的家庭？", [postback_item("確認新增","action=family_confirm_add_caregiver"),postback_item("取消","action=family_cancel")]))
        return True
    if state["step"] == "waiting_group_id":
        bind_group_to_family(family["id"],user_text,admin_id)
        clear_operation_state(user_id)
        reply_text(event.reply_token,f"家庭群組綁定完成！\nGroup ID：{user_text}")
        return True
    if state["step"] == "waiting_actual_quantity":
        payload = state.get("payload", {})
        actual_quantity = _parse_numeric_quantity(user_text)
        payload["actual_quantity"] = str(actual_quantity)
        set_operation_state(
            user_id,
            "family_medication_correct",
            "confirm_actual_quantity",
            payload,
        )
        reply_message(
            event.reply_token,
            make_quick_reply_message(
                (
                    f"長者：{payload['patient_name']}\n"
                    f"藥物：{payload['medication_name']}\n"
                    f"系統計算：{_format_quantity(payload['calculated_quantity'])} "
                    f"{payload['quantity_unit']}\n"
                    f"實際剩餘：{_format_quantity(actual_quantity)} "
                    f"{payload['quantity_unit']}\n\n"
                    "確定儲存這次修正？"
                ),
                [
                    postback_item(
                        "確認修正",
                        "action=family_medication_confirm_quantity",
                    ),
                    postback_item("取消", "action=family_cancel"),
                ],
            ),
        )
        return True

    if state["step"] == "waiting_calendar_title":
        payload = state.get("payload", {})
        payload["calendar_title"] = user_text[:255]
        set_operation_state(user_id,"family_calendar_add","waiting_calendar_location",payload)
        reply_text(event.reply_token,
            "請輸入醫院或地點，例如：埔里基督教醫院\n若沒有地點請輸入「未填寫」。")
        return True

    if state["step"] == "waiting_calendar_location":
        payload = state.get("payload", {})
        payload["calendar_location"] = None if user_text in {"未填寫","無","沒有"} else user_text[:500]
        set_operation_state(user_id,"family_calendar_add","calendar_waiting_datetime",payload)
        reply_message(event.reply_token,make_quick_reply_message(
            "請點選預計前往醫院的日期與時間：",
            [
                datetime_item("選擇日期時間",
                    "action=family_calendar_save_datetime&mode=add",
                    mode="datetime",
                    minimum=taipei_now().strftime("%Y-%m-%dT%H:%M")),
                postback_item("取消","action=family_cancel"),
            ]))
        return True

    if state["step"] == "waiting_calendar_edit_title":
        payload = state.get("payload", {})
        update_patient_calendar_event(
            payload["event_id"],payload["patient_id"],"title",user_text[:255])
        clear_operation_state(user_id)
        reply_text(event.reply_token,f"行程名稱已修改為：{user_text[:255]}")
        return True

    if state["step"] == "waiting_calendar_edit_location":
        payload = state.get("payload", {})
        new_location = None if user_text in {"未填寫","無","沒有"} else user_text[:500]
        update_patient_calendar_event(
            payload["event_id"],payload["patient_id"],"location",new_location)
        clear_operation_state(user_id)
        reply_text(event.reply_token,f"行程地點已修改為：{new_location or '未填寫'}")
        return True


    if state["step"] == "waiting_monitor_contact_name":
        payload = state.get("payload", {})
        payload["contact_name"] = user_text[:255]
        set_operation_state(
            user_id,"family_monitor_emergency",
            "waiting_monitor_contact_relationship",payload
        )
        reply_text(
            event.reply_token,
            "請輸入與長者的關係，例如：女兒、兒子、配偶。"
        )
        return True

    if state["step"] == "waiting_monitor_contact_relationship":
        payload = state.get("payload", {})
        payload["relationship"] = user_text[:100]
        set_operation_state(
            user_id,"family_monitor_emergency",
            "waiting_monitor_contact_phone",payload
        )
        reply_text(event.reply_token,"請輸入聯絡電話，例如：0912345678。")
        return True

    if state["step"] == "waiting_monitor_contact_phone":
        payload = state.get("payload", {})
        phone = re.sub(r"[^0-9+]", "", user_text)
        if len(phone) < 8:
            raise RuntimeError("電話格式不正確，請重新輸入")
        payload["phone_number"] = phone
        set_operation_state(
            user_id,"family_monitor_emergency",
            "waiting_monitor_contact_line",payload
        )
        reply_text(
            event.reply_token,
            "請輸入此聯絡人的 LINE User ID；若沒有請輸入「略過」。"
        )
        return True

    if state["step"] == "waiting_monitor_contact_line":
        payload = state.get("payload", {})
        line_user_id = None if user_text in {"略過","無","沒有"} else user_text
        upsert_family_emergency_contact(
            payload["patient_id"],
            payload["priority"],
            payload["contact_name"],
            payload["relationship"],
            payload["phone_number"],
            line_user_id,
        )
        clear_operation_state(user_id)
        reply_text(
            event.reply_token,
            (
                "緊急聯絡人設定完成！\n"
                f"長者：{payload['patient_name']}\n"
                f"順位：{payload['priority']}\n"
                f"姓名：{payload['contact_name']}\n"
                f"關係：{payload['relationship']}\n"
                f"電話：{payload['phone_number']}"
            ),
        )
        return True

    return False


def handle_family_postback(event, action, params):
    if action.startswith("family_monitor_"):
        return handle_family_monitor_postback(event, action, params)

    if action.startswith("family_report_"):
        return handle_family_report_postback(event, action, params)

    if action.startswith("family_calendar_"):
        return handle_family_calendar_postback(event, action, params)

    if action.startswith("family_medication_"):
        return handle_family_medication_postback(event, action, params)

    user_id=get_user_id(event)
    if not user_id:
        reply_text(event.reply_token,"無法取得您的 LINE User ID。")
        return True
    if action == "family_cancel":
        clear_operation_state(user_id); reply_text(event.reply_token,"已取消本次家庭管理操作。")
        return True
    family=ensure_family_admin(user_id); family_id=family["id"]; admin_id=family["admin_user_id"]
    if action == "family_add_elder":
        set_operation_state(user_id,action,"waiting_elder_id")
        reply_text(event.reply_token,"請輸入長者的 LINE User ID。\n\n對方必須先加入 Bot，並將身份設定為「長者」。\n輸入「取消」可結束操作。")
    elif action == "family_confirm_add_elder":
        state=get_operation_state(user_id)
        if not state or not state.get("payload",{}).get("target_id"):
            raise RuntimeError("新增資料已逾時，請重新操作")
        import uuid
        elder_uuid = uuid.UUID(state["payload"]["target_id"])
        bind_member_to_family(family_id,elder_uuid,"elderly",admin_id)
        ensure_patient_for_elder_user(elder_uuid)
        clear_operation_state(user_id)
        reply_text(event.reply_token,f"已成功新增長者：{state['payload'].get('display_name') or '未命名'}")
    elif action == "family_manage_elder":
        elders=list_family_members(family_id,"elderly")
        if not elders:
            reply_text(event.reply_token,"目前家庭尚未綁定任何長者。")
        else:
            items=[postback_item(e["display_name"],f"action=family_remove_elder&elder_id={e['id']}",f"管理 {e['display_name']}") for e in elders[:12]]
            reply_message(event.reply_token,make_quick_reply_message("請選擇要從家庭中移除的長者：",items))
    elif action == "family_remove_elder":
        elder_id=params.get("elder_id",[None])[0]
        elders={str(e["id"]):e for e in list_family_members(family_id,"elderly")}
        if elder_id not in elders: raise RuntimeError("找不到這位長者")
        set_operation_state(user_id,action,"confirm_remove",{"elder_id":elder_id,"display_name":elders[elder_id]["display_name"]})
        reply_message(event.reply_token,make_quick_reply_message(f"確定要將「{elders[elder_id]['display_name']}」移出家庭嗎？\n相關看護指派也會解除。",[postback_item("確認移除","action=family_confirm_remove_elder"),postback_item("取消","action=family_cancel")]))
    elif action == "family_confirm_remove_elder":
        state=get_operation_state(user_id); import uuid
        if not state or not state.get("payload",{}).get("elder_id"): raise RuntimeError("移除資料已逾時")
        remove_elder_from_family(family_id,uuid.UUID(state["payload"]["elder_id"]),admin_id)
        name=state["payload"].get("display_name"); clear_operation_state(user_id)
        reply_text(event.reply_token,f"已將「{name}」移出家庭。")
    elif action == "family_add_caregiver":
        set_operation_state(user_id,action,"waiting_caregiver_id")
        reply_text(event.reply_token,"請輸入看護的 LINE User ID。\n\n對方必須先加入 Bot，並將身份設定為「看護」。\n輸入「取消」可結束操作。")
    elif action == "family_confirm_add_caregiver":
        state=get_operation_state(user_id); import uuid
        if not state or not state.get("payload",{}).get("target_id"): raise RuntimeError("新增資料已逾時")
        bind_member_to_family(family_id,uuid.UUID(state["payload"]["target_id"]),"caregiver",admin_id)
        clear_operation_state(user_id)
        reply_text(event.reply_token,f"已成功新增看護：{state['payload'].get('display_name') or '未命名'}")
    elif action == "family_assign_caregiver":
        caregivers=list_family_members(family_id,"caregiver")
        if not caregivers: reply_text(event.reply_token,"目前家庭尚未新增任何看護。")
        else:
            items=[postback_item(c["display_name"],f"action=family_select_caregiver&caregiver_id={c['id']}") for c in caregivers[:12]]
            reply_message(event.reply_token,make_quick_reply_message("請選擇要指派的看護：",items))
    elif action == "family_select_caregiver":
        caregiver_id=params.get("caregiver_id",[None])[0]
        caregivers={str(c["id"]):c for c in list_family_members(family_id,"caregiver")}
        if caregiver_id not in caregivers: raise RuntimeError("找不到這位看護")
        elders=list_family_members(family_id,"elderly")
        if not elders: reply_text(event.reply_token,"目前家庭尚未新增任何長者。")
        else:
            set_operation_state(user_id,"family_assign_caregiver","select_elder",{"caregiver_id":caregiver_id,"caregiver_name":caregivers[caregiver_id]["display_name"]})
            items=[postback_item(e["display_name"],f"action=family_select_elder_for_caregiver&elder_id={e['id']}") for e in elders[:12]]
            reply_message(event.reply_token,make_quick_reply_message(f"已選擇看護：{caregivers[caregiver_id]['display_name']}\n請選擇要照顧的長者：",items))
    elif action == "family_select_elder_for_caregiver":
        state=get_operation_state(user_id); elder_id=params.get("elder_id",[None])[0]
        elders={str(e["id"]):e for e in list_family_members(family_id,"elderly")}
        if not state or elder_id not in elders: raise RuntimeError("指派資料已逾時或長者不存在")
        payload=state["payload"]; payload.update({"elder_id":elder_id,"elder_name":elders[elder_id]["display_name"]})
        set_operation_state(user_id,"family_assign_caregiver","confirm_assignment",payload)
        reply_message(event.reply_token,make_quick_reply_message(f"看護：{payload['caregiver_name']}\n長者：{payload['elder_name']}\n\n確定建立照護指派？",[postback_item("確認指派","action=family_confirm_assignment"),postback_item("取消","action=family_cancel")]))
    elif action == "family_confirm_assignment":
        state=get_operation_state(user_id); import uuid
        payload=state.get("payload",{}) if state else {}
        if not payload.get("caregiver_id") or not payload.get("elder_id"): raise RuntimeError("指派資料已逾時")
        assign_caregiver(family_id,uuid.UUID(payload["caregiver_id"]),uuid.UUID(payload["elder_id"]),admin_id)
        clear_operation_state(user_id)
        reply_text(event.reply_token,f"指派完成！\n看護：{payload['caregiver_name']}\n長者：{payload['elder_name']}")
    elif action == "family_bind_group":
        set_operation_state(user_id,action,"waiting_group_id")
        reply_text(event.reply_token,"請貼上 LINE 家庭群組 ID。\n\n先將 Bot 加入家庭群組，Bot 會在群組內顯示 Group ID。")
    else:
        return False
    return True


# =========================================================
# 家屬藥物管理
# =========================================================

MEDICATION_ACTION_LABELS = {
    "family_medication_list": "查看藥物",
    "family_medication_correct": "修正藥物",
    "family_medication_remaining": "藥量剩餘",
    "family_medication_low": "藥快用完",
    "family_medication_bag_records": "藥袋紀錄",
}


def _to_decimal(value, default=Decimal("0")):
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _format_quantity(value):
    number = _to_decimal(value)
    if number == number.to_integral():
        return str(int(number))
    return format(number.normalize(), "f")


def _parse_numeric_quantity(text_value):
    match = re.search(r"-?\d+(?:\.\d+)?", str(text_value or ""))
    if not match:
        raise RuntimeError("請輸入數字，例如：27")
    value = Decimal(match.group())
    if value < 0:
        raise RuntimeError("藥物數量不能小於 0")
    return value


def ensure_patient_for_elder_user(elder_user_id):
    """取得長者的 patients.id；家庭新增長者後若尚未建檔就自動建立。"""
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT p.id, p.full_name
            FROM patients p
            WHERE p.linked_user_id = %s
            ORDER BY p.is_active DESC, p.updated_at DESC
            LIMIT 1
            """,
            (elder_user_id,),
        )
        row = cursor.fetchone()
        if row:
            if not row[1]:
                cursor.execute(
                    """
                    UPDATE patients p
                    SET full_name = COALESCE(u.display_name, '未命名長者'),
                        is_active = TRUE,
                        updated_at = CURRENT_TIMESTAMP
                    FROM app_users u
                    WHERE p.id = %s AND u.id = %s
                    """,
                    (row[0], elder_user_id),
                )
                connection.commit()
            return row[0]

        cursor.execute(
            "SELECT COALESCE(NULLIF(display_name,''), '未命名長者') FROM app_users WHERE id=%s",
            (elder_user_id,),
        )
        user_row = cursor.fetchone()
        if not user_row:
            raise RuntimeError("找不到長者使用者資料")

        cursor.execute(
            """
            INSERT INTO patients (linked_user_id, full_name, notes, is_active)
            VALUES (%s, %s, '由家庭管理功能自動建立', TRUE)
            RETURNING id
            """,
            (elder_user_id, user_row[0]),
        )
        patient_id = cursor.fetchone()[0]
        connection.commit()
        return patient_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_family_patients(family_id):
    """列出家庭中所有有效長者，以及其 patient_id。"""
    elders = list_family_members(family_id, "elderly")
    result = []
    for elder in elders:
        patient_id = ensure_patient_for_elder_user(elder["id"])
        item = dict(elder)
        item["patient_id"] = patient_id
        result.append(item)
    return result


def get_family_patient(family_id, patient_id):
    for patient in list_family_patients(family_id):
        if str(patient["patient_id"]) == str(patient_id):
            return patient
    return None


def _medication_inventory_values(row, today=None):
    today = today or taipei_now().date()
    dispense_date = row.get("dispense_date") or row.get("start_date")
    course_days = row.get("course_days")
    total_quantity = _to_decimal(row.get("total_quantity"))
    dose_per_time = _to_decimal(row.get("dose_per_time"), Decimal("1"))
    times_per_day = _to_decimal(row.get("times_per_day"), Decimal("1"))
    daily_quantity = max(dose_per_time * times_per_day, Decimal("0"))

    if dispense_date and isinstance(dispense_date, datetime):
        dispense_date = dispense_date.date()

    elapsed_days = max((today - dispense_date).days, 0) if dispense_date else 0
    consumed_quantity = _to_decimal(row.get("consumed_quantity"))
    consumed_since_adjustment = _to_decimal(
        row.get("consumed_since_adjustment")
    )
    calculated = max(total_quantity - consumed_quantity, Decimal("0"))

    adjusted_quantity = row.get("adjusted_quantity")
    adjusted_at = row.get("adjusted_at")
    if adjusted_quantity is not None and adjusted_at:
        adjusted_date = adjusted_at.date() if isinstance(adjusted_at, datetime) else adjusted_at
        remaining = max(
            _to_decimal(adjusted_quantity) - consumed_since_adjustment,
            Decimal("0"),
        )
        basis = f"人工修正（{adjusted_date}）後扣除實際服藥量"
    else:
        remaining = calculated
        basis = "原始開藥總量扣除已登記服用量"

    if course_days and dispense_date:
        expected_end_date = dispense_date + timedelta(days=max(int(course_days) - 1, 0))
    elif row.get("end_date"):
        expected_end_date = row["end_date"]
    elif daily_quantity > 0 and total_quantity > 0 and dispense_date:
        expected_end_date = dispense_date + timedelta(
            days=max(math.ceil(float(total_quantity / daily_quantity)) - 1, 0)
        )
    else:
        expected_end_date = None

    warning_date = expected_end_date - timedelta(days=3) if expected_end_date else None
    days_remaining = (
        math.ceil(float(remaining / daily_quantity))
        if daily_quantity > 0
        else None
    )

    return {
        "dispense_date": dispense_date,
        "course_days": course_days,
        "total_quantity": total_quantity,
        "daily_quantity": daily_quantity,
        "elapsed_days": elapsed_days,
        "consumed_quantity": consumed_quantity,
        "remaining": remaining,
        "basis": basis,
        "expected_end_date": expected_end_date,
        "warning_date": warning_date,
        "days_remaining": days_remaining,
    }


def list_patient_medications(patient_id, active_only=True):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                m.id,
                m.medication_name,
                m.generic_name,
                m.dosage,
                m.instructions,
                m.start_date,
                m.end_date,
                m.is_active,
                m.dispense_date,
                m.course_days,
                m.total_quantity,
                m.dose_per_time,
                m.times_per_day,
                m.quantity_unit,
                a.actual_quantity,
                a.created_at,
                used.total_consumed,
                used.consumed_since_adjustment
            FROM medications m
            LEFT JOIN LATERAL (
                SELECT actual_quantity, created_at
                FROM medication_inventory_adjustments mia
                WHERE mia.medication_id = m.id
                ORDER BY mia.created_at DESC
                LIMIT 1
            ) a ON TRUE
            LEFT JOIN LATERAL (
                SELECT
                    COALESCE(SUM(
                        CASE WHEN ml.status::text = 'taken'
                             THEN COALESCE(
                                 NULLIF(
                                     regexp_replace(
                                         COALESCE(ms.dose_amount,''),
                                         '[^0-9.]','','g'
                                     ),
                                     ''
                                 )::numeric,
                                 m.dose_per_time,
                                 0
                             )
                             ELSE 0 END
                    ), 0) AS total_consumed,
                    COALESCE(SUM(
                        CASE WHEN ml.status::text = 'taken'
                                  AND a.created_at IS NOT NULL
                                  AND ml.taken_at >= a.created_at
                             THEN COALESCE(
                                 NULLIF(
                                     regexp_replace(
                                         COALESCE(ms.dose_amount,''),
                                         '[^0-9.]','','g'
                                     ),
                                     ''
                                 )::numeric,
                                 m.dose_per_time,
                                 0
                             )
                             ELSE 0 END
                    ), 0) AS consumed_since_adjustment
                FROM medication_logs ml
                LEFT JOIN medication_schedules ms ON ms.id = ml.schedule_id
                WHERE ml.medication_id = m.id
            ) used ON TRUE
            WHERE m.patient_id = %s
              AND (%s = FALSE OR m.is_active = TRUE)
            ORDER BY COALESCE(m.dispense_date, m.start_date) DESC NULLS LAST,
                     m.created_at DESC
            """,
            (patient_id, active_only),
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row[0],
                "medication_name": row[1] or "未命名藥物",
                "generic_name": row[2],
                "dosage": row[3],
                "instructions": row[4],
                "start_date": row[5],
                "end_date": row[6],
                "is_active": row[7],
                "dispense_date": row[8],
                "course_days": row[9],
                "total_quantity": row[10],
                "dose_per_time": row[11],
                "times_per_day": row[12],
                "quantity_unit": row[13] or "份",
                "adjusted_quantity": row[14],
                "adjusted_at": row[15],
                "consumed_quantity": row[16],
                "consumed_since_adjustment": row[17],
            })
        return result
    finally:
        connection.close()


def get_patient_medication(patient_id, medication_id):
    for medication in list_patient_medications(patient_id, active_only=False):
        if str(medication["id"]) == str(medication_id):
            return medication
    return None


def save_inventory_adjustment(
    medication_id,
    patient_id,
    adjusted_by,
    calculated_quantity,
    actual_quantity,
    reason="家屬修正實際剩餘數量",
):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO medication_inventory_adjustments (
                medication_id,
                patient_id,
                adjusted_by,
                calculated_quantity,
                actual_quantity,
                quantity_difference,
                reason
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                medication_id,
                patient_id,
                adjusted_by,
                calculated_quantity,
                actual_quantity,
                actual_quantity - calculated_quantity,
                reason,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_medication_bag_records(patient_id, limit=12):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                s.id,
                s.created_at,
                s.processing_status,
                s.original_text,
                s.parsed_result,
                s.image_path,
                COALESCE(u.display_name, '未知使用者'),
                COALESCE(r.name_zh_tw, r.code, '未知身份')
            FROM ai_medication_scans s
            LEFT JOIN app_users u ON u.id = s.uploaded_by
            LEFT JOIN roles r ON r.id = u.role_id
            WHERE s.patient_id = %s
            ORDER BY s.created_at DESC
            LIMIT %s
            """,
            (patient_id, limit),
        )
        rows = cursor.fetchall()
        return [{
            "id": r[0],
            "created_at": r[1],
            "status": r[2],
            "original_text": r[3],
            "parsed_result": r[4],
            "image_path": r[5],
            "uploader_name": r[6],
            "uploader_role": r[7],
        } for r in rows]
    finally:
        connection.close()


def get_medication_bag_record(patient_id, scan_id):
    records = list_medication_bag_records(patient_id, limit=50)
    for record in records:
        if str(record["id"]) == str(scan_id):
            return record
    return None


def send_patient_selection(event, user_id, action, family_id):
    patients = list_family_patients(family_id)
    if not patients:
        reply_text(event.reply_token, "目前家庭尚未新增任何長者。請先到「家庭管理」新增長者。")
        return True

    set_operation_state(user_id, action, "select_patient", {})
    if len(patients) == 1:
        patient = patients[0]
        return handle_selected_patient(event, user_id, action, patient)

    items = [
        postback_item(
            p["display_name"],
            f"action=family_medication_select_patient&next_action={action}&patient_id={p['patient_id']}",
            f"選擇 {p['display_name']}",
        )
        for p in patients[:12]
    ]
    reply_message(
        event.reply_token,
        make_quick_reply_message(
            f"{MEDICATION_ACTION_LABELS.get(action, '藥物管理')}\n請選擇長者：",
            items,
        ),
    )
    return True


def medication_summary_text(patient, medications):
    if not medications:
        return f"{patient['display_name']}目前沒有使用中的藥物。"

    lines = [f"{patient['display_name']}目前使用中的藥物："]
    for index, medication in enumerate(medications, 1):
        inventory = _medication_inventory_values(medication)
        lines.extend([
            "",
            f"{index}. {medication['medication_name']}",
            f"含量：{medication.get('dosage') or '未標示'}",
            f"用法：{medication.get('instructions') or '未標示'}",
            f"調劑日期：{inventory['dispense_date'] or '未標示'}",
            f"處方天數：{inventory['course_days'] or '未標示'}",
            f"總量：{_format_quantity(inventory['total_quantity'])} {medication['quantity_unit']}",
        ])
    return "\n".join(lines)


def remaining_summary_text(patient, medications, low_only=False):
    lines = [
        (
            f"{patient['display_name']}三天內可能用完的藥物："
            if low_only
            else f"{patient['display_name']}的藥物剩餘："
        )
    ]
    matched = 0
    today = taipei_now().date()

    for medication in medications:
        inventory = _medication_inventory_values(medication, today=today)
        low = False
        if inventory["warning_date"] and inventory["expected_end_date"]:
            low = inventory["warning_date"] <= today <= inventory["expected_end_date"]
        if inventory["daily_quantity"] > 0:
            low = low or inventory["remaining"] <= inventory["daily_quantity"] * Decimal("3")

        if low_only and not low:
            continue

        matched += 1
        lines.extend([
            "",
            f"{matched}. {medication['medication_name']}",
            f"原始開藥：{_format_quantity(inventory['total_quantity'])} {medication['quantity_unit']}",
            f"已登記服用：{_format_quantity(inventory['consumed_quantity'])} {medication['quantity_unit']}",
            f"目前剩餘：{_format_quantity(inventory['remaining'])} {medication['quantity_unit']}",
            f"每日使用：{_format_quantity(inventory['daily_quantity'])} {medication['quantity_unit']}",
            f"預計用完：{inventory['expected_end_date'] or '無法計算'}",
            f"計算基準：{inventory['basis']}",
        ])
        if inventory["days_remaining"] is not None:
            lines.append(f"預估還可使用：{inventory['days_remaining']} 天")

    if matched == 0:
        return (
            f"{patient['display_name']}目前沒有三天內即將用完的藥物。"
            if low_only
            else f"{patient['display_name']}目前沒有可計算剩餘量的藥物。"
        )
    return "\n".join(lines)


def handle_selected_patient(event, user_id, action, patient):
    patient_id = patient["patient_id"]
    payload = {
        "patient_id": str(patient_id),
        "patient_name": patient["display_name"],
    }

    if action == "family_medication_list":
        clear_operation_state(user_id)
        reply_text(
            event.reply_token,
            medication_summary_text(
                patient,
                list_patient_medications(patient_id, active_only=True),
            ),
        )
        return True

    if action == "family_medication_remaining":
        clear_operation_state(user_id)
        reply_text(
            event.reply_token,
            remaining_summary_text(
                patient,
                list_patient_medications(patient_id, active_only=True),
                low_only=False,
            ),
        )
        return True

    if action == "family_medication_low":
        clear_operation_state(user_id)
        reply_text(
            event.reply_token,
            remaining_summary_text(
                patient,
                list_patient_medications(patient_id, active_only=True),
                low_only=True,
            ),
        )
        return True

    if action == "family_medication_correct":
        medications = list_patient_medications(patient_id, active_only=True)
        if not medications:
            clear_operation_state(user_id)
            reply_text(event.reply_token, f"{patient['display_name']}目前沒有可修正的藥物。")
            return True
        set_operation_state(user_id, action, "select_medication", payload)
        items = [
            postback_item(
                m["medication_name"][:20],
                f"action=family_medication_select_item&medication_id={m['id']}",
            )
            for m in medications[:12]
        ]
        reply_message(
            event.reply_token,
            make_quick_reply_message(
                f"長者：{patient['display_name']}\n請選擇要修正數量的藥物：",
                items,
            ),
        )
        return True

    if action == "family_medication_bag_records":
        records = list_medication_bag_records(patient_id)
        if not records:
            clear_operation_state(user_id)
            reply_text(event.reply_token, f"{patient['display_name']}目前沒有藥袋拍攝紀錄。")
            return True
        set_operation_state(user_id, action, "select_bag_record", payload)
        items = []
        for record in records[:12]:
            created = record["created_at"].strftime("%Y-%m-%d %H:%M")
            items.append(
                postback_item(
                    created[:20],
                    f"action=family_medication_select_bag&scan_id={record['id']}",
                    f"查看 {created}",
                )
            )
        reply_message(
            event.reply_token,
            make_quick_reply_message(
                f"{patient['display_name']}的藥袋紀錄\n請選擇一筆：",
                items,
            ),
        )
        return True

    return False


def handle_family_medication_postback(event, action, params):
    user_id = get_user_id(event)
    if not user_id:
        reply_text(event.reply_token, "無法取得您的 LINE User ID。")
        return True

    family = ensure_family_admin(user_id)
    family_id = family["id"]
    admin_id = family["admin_user_id"]

    if action in MEDICATION_ACTION_LABELS:
        return send_patient_selection(event, user_id, action, family_id)

    if action == "family_medication_select_patient":
        next_action = params.get("next_action", [None])[0]
        patient_id = params.get("patient_id", [None])[0]
        patient = get_family_patient(family_id, patient_id)
        if not patient:
            raise RuntimeError("找不到這位長者，或長者已不在此家庭")
        if next_action not in MEDICATION_ACTION_LABELS:
            raise RuntimeError("藥物功能資料已逾時")
        return handle_selected_patient(event, user_id, next_action, patient)

    if action == "family_medication_select_item":
        state = get_operation_state(user_id)
        medication_id = params.get("medication_id", [None])[0]
        payload = state.get("payload", {}) if state else {}
        patient_id = payload.get("patient_id")
        patient = get_family_patient(family_id, patient_id)
        medication = (
            get_patient_medication(patient_id, medication_id)
            if patient and patient_id
            else None
        )
        if not patient or not medication:
            raise RuntimeError("修正資料已逾時，請重新操作")

        inventory = _medication_inventory_values(medication)
        payload.update({
            "medication_id": str(medication["id"]),
            "medication_name": medication["medication_name"],
            "calculated_quantity": str(inventory["remaining"]),
            "quantity_unit": medication["quantity_unit"],
        })
        set_operation_state(
            user_id,
            "family_medication_correct",
            "waiting_actual_quantity",
            payload,
        )
        reply_text(
            event.reply_token,
            (
                f"長者：{patient['display_name']}\n"
                f"藥物：{medication['medication_name']}\n"
                f"系統計算剩餘：{_format_quantity(inventory['remaining'])} "
                f"{medication['quantity_unit']}\n\n"
                "請輸入實際剩餘數量，例如：27\n"
                "輸入「取消」可結束操作。"
            ),
        )
        return True

    if action == "family_medication_confirm_quantity":
        state = get_operation_state(user_id)
        payload = state.get("payload", {}) if state else {}
        required = {
            "patient_id",
            "medication_id",
            "actual_quantity",
            "calculated_quantity",
        }
        if not required.issubset(payload):
            raise RuntimeError("修正資料已逾時，請重新操作")

        patient = get_family_patient(family_id, payload["patient_id"])
        if not patient:
            raise RuntimeError("找不到這位長者")

        save_inventory_adjustment(
            medication_id=payload["medication_id"],
            patient_id=payload["patient_id"],
            adjusted_by=admin_id,
            calculated_quantity=_to_decimal(payload["calculated_quantity"]),
            actual_quantity=_to_decimal(payload["actual_quantity"]),
        )
        clear_operation_state(user_id)
        reply_text(
            event.reply_token,
            (
                "藥物數量修正完成！\n"
                f"長者：{payload['patient_name']}\n"
                f"藥物：{payload['medication_name']}\n"
                f"原計算：{_format_quantity(payload['calculated_quantity'])} "
                f"{payload['quantity_unit']}\n"
                f"修正後：{_format_quantity(payload['actual_quantity'])} "
                f"{payload['quantity_unit']}"
            ),
        )
        return True

    if action == "family_medication_select_bag":
        state = get_operation_state(user_id)
        payload = state.get("payload", {}) if state else {}
        patient_id = payload.get("patient_id")
        scan_id = params.get("scan_id", [None])[0]
        patient = get_family_patient(family_id, patient_id)
        record = get_medication_bag_record(patient_id, scan_id) if patient else None
        if not patient or not record:
            raise RuntimeError("藥袋紀錄已逾時，請重新操作")

        clear_operation_state(user_id)
        parsed = record.get("parsed_result")
        if isinstance(parsed, dict) and parsed.get("display_text"):
            details = parsed["display_text"]
        elif parsed:
            details = json.dumps(parsed, ensure_ascii=False, indent=2)
        else:
            details = record.get("original_text") or "未保存辨識內容"

        reply_text(
            event.reply_token,
            (
                f"長者：{patient['display_name']}\n"
                f"拍攝時間：{record['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
                f"上傳者：{record['uploader_name']}（{record['uploader_role']}）\n"
                f"辨識狀態：{record['status']}\n\n"
                f"{details}"
            ),
        )
        return True

    return False


# =========================================================
# 家屬行事曆管理
# =========================================================

CALENDAR_ACTION_LABELS = {
    "family_calendar_view": "查看行事曆",
    "family_calendar_add": "新增行程",
    "family_calendar_edit": "修改行程",
    "family_calendar_delete": "刪除行程",
    "family_calendar_reminder": "回診提醒",
}


def list_patient_calendar_events(patient_id, upcoming_only=True, limit=30):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id,title,description,location,starts_at,ends_at,
                   all_day,event_type,COALESCE(is_active,TRUE)
            FROM calendar_events
            WHERE patient_id=%s
              AND COALESCE(is_active,TRUE)=TRUE
              AND (%s=FALSE OR starts_at >= CURRENT_TIMESTAMP - INTERVAL '1 day')
            ORDER BY starts_at ASC
            LIMIT %s
            """,
            (patient_id, upcoming_only, limit),
        )
        return [{
            "id": r[0], "title": r[1], "description": r[2],
            "location": r[3], "starts_at": r[4], "ends_at": r[5],
            "all_day": r[6], "event_type": r[7], "is_active": r[8],
        } for r in cursor.fetchall()]
    finally:
        connection.close()


def get_patient_calendar_event(patient_id, event_id):
    for item in list_patient_calendar_events(patient_id, upcoming_only=False, limit=100):
        if str(item["id"]) == str(event_id):
            return item
    return None


def create_patient_calendar_event(patient_id,title,description,location,starts_at,created_by,event_type="hospital_visit"):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO calendar_events (
                patient_id,title,description,location,starts_at,ends_at,
                all_day,event_type,created_by,source_type,is_active
            )
            VALUES (%s,%s,%s,%s,%s,%s,FALSE,%s,%s,'manual',TRUE)
            RETURNING id
            """,
            (patient_id,title,description,location,starts_at,
             starts_at + timedelta(hours=1),event_type,created_by),
        )
        event_id = cursor.fetchone()[0]
        connection.commit()
        return event_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def update_patient_calendar_event(event_id,patient_id,field,value):
    allowed = {"title":"title","location":"location","starts_at":"starts_at"}
    column = allowed.get(field)
    if not column:
        raise RuntimeError("不支援的行事曆修改欄位")
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        if field == "starts_at":
            cursor.execute(
                """
                UPDATE calendar_events
                SET starts_at=%s, ends_at=%s + INTERVAL '1 hour',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=%s AND patient_id=%s AND COALESCE(is_active,TRUE)=TRUE
                """,
                (value,value,event_id,patient_id),
            )
        else:
            cursor.execute(
                f"""
                UPDATE calendar_events
                SET {column}=%s, updated_at=CURRENT_TIMESTAMP
                WHERE id=%s AND patient_id=%s AND COALESCE(is_active,TRUE)=TRUE
                """,
                (value,event_id,patient_id),
            )
        if cursor.rowcount == 0:
            raise RuntimeError("找不到要修改的行程")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def delete_patient_calendar_event(event_id,patient_id):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE calendar_events
            SET is_active=FALSE, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND patient_id=%s AND COALESCE(is_active,TRUE)=TRUE
            """,
            (event_id,patient_id),
        )
        if cursor.rowcount == 0:
            raise RuntimeError("找不到要刪除的行程")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_followup_reminder_setting(patient_id,family_user_id):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT is_enabled,days_before,reminder_time
            FROM followup_reminder_settings
            WHERE patient_id=%s AND family_user_id=%s
            """,
            (patient_id,family_user_id),
        )
        row = cursor.fetchone()
        if not row:
            return {"is_enabled":False,"days_before":3,"reminder_time":"09:00"}
        return {"is_enabled":row[0],"days_before":row[1],"reminder_time":str(row[2])[:5]}
    finally:
        connection.close()


def save_followup_reminder_setting(patient_id,family_user_id,is_enabled,days_before=3,reminder_time="09:00"):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO followup_reminder_settings (
                patient_id,family_user_id,is_enabled,days_before,reminder_time,updated_at
            )
            VALUES (%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
            ON CONFLICT (patient_id,family_user_id)
            DO UPDATE SET is_enabled=EXCLUDED.is_enabled,
                          days_before=EXCLUDED.days_before,
                          reminder_time=EXCLUDED.reminder_time,
                          updated_at=CURRENT_TIMESTAMP
            """,
            (patient_id,family_user_id,is_enabled,days_before,reminder_time),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def send_due_followup_reminders():
    """由 Render Cron Job 每天呼叫一次，推播回診前三天提醒。"""
    today = taipei_now().date()
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS followup_reminder_delivery_logs (
                id BIGSERIAL PRIMARY KEY,
                calendar_event_id TEXT NOT NULL,
                recipient_line_user_id TEXT NOT NULL,
                reminded_for_date DATE NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (calendar_event_id, recipient_line_user_id, reminded_for_date)
            )
            """
        )
        cursor.execute(
            """
            SELECT
                ce.id,
                ce.title,
                ce.location,
                ce.starts_at,
                p.full_name,
                elder.line_user_id,
                family.line_user_id,
                frs.days_before
            FROM followup_reminder_settings frs
            JOIN calendar_events ce
              ON ce.patient_id=frs.patient_id
             AND ce.event_type='follow_up'
             AND COALESCE(ce.is_active,TRUE)=TRUE
            JOIN patients p ON p.id=ce.patient_id AND p.is_active=TRUE
            LEFT JOIN app_users elder
              ON elder.id=p.linked_user_id AND elder.is_active=TRUE
            JOIN app_users family
              ON family.id=frs.family_user_id AND family.is_active=TRUE
            WHERE frs.is_enabled=TRUE
              AND (ce.starts_at AT TIME ZONE 'Asia/Taipei')::date
                    = %s + frs.days_before
            ORDER BY ce.starts_at
            """,
            (today,),
        )
        rows = cursor.fetchall()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    sent = 0
    skipped = 0
    failed = 0
    for row in rows:
        event_id, title, location, starts_at, patient_name = row[:5]
        recipients = [row[5], row[6]]
        days_before = row[7]
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=TAIPEI_TZ)
        else:
            starts_at = starts_at.astimezone(TAIPEI_TZ)
        message = (
            "【回診提醒】\n"
            f"長者：{patient_name or '未命名長者'}\n"
            f"行程：{title or '回診'}\n"
            f"時間：{starts_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"地點：{location or '未填寫'}\n"
            f"距離回診還有 {days_before} 天，請預先準備。"
        )
        for recipient in dict.fromkeys(r for r in recipients if r):
            log_connection = get_db_connection()
            try:
                log_cursor = log_connection.cursor()
                log_cursor.execute(
                    """
                    INSERT INTO followup_reminder_delivery_logs (
                        calendar_event_id,recipient_line_user_id,reminded_for_date
                    )
                    VALUES (%s,%s,%s)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    (str(event_id), recipient, today),
                )
                if not log_cursor.fetchone():
                    log_connection.rollback()
                    skipped += 1
                    continue
                api_client, messaging_api = get_messaging_api()
                try:
                    messaging_api.push_message(
                        PushMessageRequest(
                            to=recipient,
                            messages=[TextMessage(text=safe_text(message))],
                        )
                    )
                    log_connection.commit()
                    sent += 1
                finally:
                    api_client.close()
            except Exception:
                log_connection.rollback()
                failed += 1
                app.logger.error(traceback.format_exc())
            finally:
                log_connection.close()
    return {"sent": sent, "skipped": skipped, "failed": failed}


def calendar_event_text(patient,events):
    if not events:
        return f"{patient['display_name']}目前沒有行事曆行程。"
    lines = [f"{patient['display_name']}的行事曆："]
    for index,item in enumerate(events,1):
        event_name = "原訂回診" if item.get("event_type") == "follow_up" else "自行新增"
        lines.extend([
            "",
            f"{index}. {item['title']}",
            f"時間：{item['starts_at'].strftime('%Y-%m-%d %H:%M')}",
            f"類型：{event_name}",
            f"地點：{item.get('location') or '未填寫'}",
        ])
        if item.get("description"):
            lines.append(f"備註：{item['description']}")
    return "\n".join(lines)


def send_calendar_patient_selection(event,user_id,action,family_id):
    patients = list_family_patients(family_id)
    if not patients:
        reply_text(event.reply_token,"目前家庭尚未新增長者，請先到「家庭管理」新增長者。")
        return True
    if len(patients) == 1:
        return handle_calendar_selected_patient(event,user_id,action,patients[0])
    set_operation_state(user_id,action,"calendar_select_patient",{})
    items = [
        postback_item(
            p["display_name"],
            f"action=family_calendar_select_patient&next_action={action}&patient_id={p['patient_id']}",
            f"選擇 {p['display_name']}",
        )
        for p in patients[:12]
    ]
    reply_message(event.reply_token,make_quick_reply_message(
        f"{CALENDAR_ACTION_LABELS.get(action,'行事曆')}\n請選擇長者：",items))
    return True


def handle_calendar_selected_patient(event,user_id,action,patient):
    patient_id = patient["patient_id"]
    payload = {"patient_id":str(patient_id),"patient_name":patient["display_name"]}

    if action == "family_calendar_view":
        clear_operation_state(user_id)
        reply_text(event.reply_token,calendar_event_text(
            patient,list_patient_calendar_events(patient_id,upcoming_only=False)))
        return True

    if action == "family_calendar_add":
        set_operation_state(user_id,action,"waiting_calendar_title",payload)
        reply_text(event.reply_token,
            f"長者：{patient['display_name']}\n請輸入行程名稱，例如：\n用藥後不舒服回診\n\n輸入「取消」可結束。")
        return True

    if action in {"family_calendar_edit","family_calendar_delete"}:
        events = list_patient_calendar_events(patient_id,upcoming_only=False)
        if not events:
            clear_operation_state(user_id)
            reply_text(event.reply_token,f"{patient['display_name']}目前沒有可操作的行程。")
            return True
        payload["calendar_action"] = action
        set_operation_state(user_id,action,"calendar_select_event",payload)
        items = [
            postback_item(
                f"{item['starts_at'].strftime('%m/%d')} {item['title']}"[:20],
                f"action=family_calendar_select_event&calendar_action={action}&event_id={item['id']}",
            )
            for item in events[:12]
        ]
        verb = "修改" if action == "family_calendar_edit" else "刪除"
        reply_message(event.reply_token,make_quick_reply_message(
            f"長者：{patient['display_name']}\n請選擇要{verb}的行程：",items))
        return True

    if action == "family_calendar_reminder":
        family = ensure_family_admin(user_id)
        setting = get_followup_reminder_setting(patient_id,family["admin_user_id"])
        payload["is_enabled"] = setting["is_enabled"]
        set_operation_state(user_id,action,"calendar_reminder_setting",payload)
        status = "已開啟" if setting["is_enabled"] else "未開啟"
        reply_message(event.reply_token,make_quick_reply_message(
            f"長者：{patient['display_name']}\n回診提醒目前：{status}\n"
            f"預設於回診前 {setting['days_before']} 天 {setting['reminder_time']} 提醒。",
            [
                postback_item("開啟提醒","action=family_calendar_enable_reminder"),
                postback_item("關閉提醒","action=family_calendar_disable_reminder"),
                postback_item("取消","action=family_cancel"),
            ]))
        return True
    return False


def handle_family_calendar_postback(event,action,params):
    user_id = get_user_id(event)
    if not user_id:
        reply_text(event.reply_token,"無法取得您的 LINE User ID。")
        return True
    family = ensure_family_admin(user_id)
    family_id = family["id"]
    admin_id = family["admin_user_id"]

    if action in CALENDAR_ACTION_LABELS:
        return send_calendar_patient_selection(event,user_id,action,family_id)

    if action == "family_calendar_select_patient":
        next_action = params.get("next_action",[None])[0]
        patient_id = params.get("patient_id",[None])[0]
        patient = get_family_patient(family_id,patient_id)
        if not patient:
            raise RuntimeError("找不到這位長者，或長者已不在此家庭")
        if next_action not in CALENDAR_ACTION_LABELS:
            raise RuntimeError("行事曆功能資料已逾時")
        return handle_calendar_selected_patient(event,user_id,next_action,patient)

    if action == "family_calendar_select_event":
        state = get_operation_state(user_id)
        payload = state.get("payload",{}) if state else {}
        patient_id = payload.get("patient_id")
        calendar_action = params.get("calendar_action",[None])[0]
        event_id = params.get("event_id",[None])[0]
        patient = get_family_patient(family_id,patient_id)
        item = get_patient_calendar_event(patient_id,event_id) if patient else None
        if not patient or not item:
            raise RuntimeError("行程資料已逾時，請重新操作")
        payload.update({"event_id":str(item["id"]),"event_title":item["title"]})

        if calendar_action == "family_calendar_delete":
            set_operation_state(user_id,calendar_action,"calendar_confirm_delete",payload)
            reply_message(event.reply_token,make_quick_reply_message(
                f"確定刪除以下行程？\n\n長者：{patient['display_name']}\n"
                f"行程：{item['title']}\n時間：{item['starts_at'].strftime('%Y-%m-%d %H:%M')}",
                [
                    postback_item("確認刪除","action=family_calendar_confirm_delete"),
                    postback_item("取消","action=family_cancel"),
                ]))
            return True

        set_operation_state(user_id,"family_calendar_edit","calendar_select_edit_field",payload)
        reply_message(event.reply_token,make_quick_reply_message(
            f"行程：{item['title']}\n請選擇要修改的內容：",
            [
                postback_item("修改名稱","action=family_calendar_select_edit_field&field=title"),
                postback_item("修改日期時間","action=family_calendar_select_edit_field&field=starts_at"),
                postback_item("修改地點","action=family_calendar_select_edit_field&field=location"),
                postback_item("取消","action=family_cancel"),
            ]))
        return True

    if action == "family_calendar_select_edit_field":
        state = get_operation_state(user_id)
        payload = state.get("payload",{}) if state else {}
        field = params.get("field",[None])[0]
        if not payload.get("event_id"):
            raise RuntimeError("修改資料已逾時，請重新操作")
        if field == "starts_at":
            payload["edit_field"] = field
            set_operation_state(user_id,"family_calendar_edit","calendar_waiting_datetime",payload)
            reply_message(event.reply_token,make_quick_reply_message(
                "請點選新的日期與時間：",
                [
                    datetime_item("選擇日期時間",
                        "action=family_calendar_save_datetime&mode=edit",
                        mode="datetime",
                        minimum=taipei_now().strftime("%Y-%m-%dT%H:%M")),
                    postback_item("取消","action=family_cancel"),
                ]))
            return True
        if field not in {"title","location"}:
            raise RuntimeError("不支援的修改項目")
        payload["edit_field"] = field
        set_operation_state(user_id,"family_calendar_edit",f"waiting_calendar_edit_{field}",payload)
        reply_text(event.reply_token,
            ("請輸入新的行程名稱：" if field == "title" else "請輸入新的地點：")
            + "\n輸入「取消」可結束。")
        return True

    if action == "family_calendar_save_datetime":
        state = get_operation_state(user_id)
        payload = state.get("payload",{}) if state else {}
        dt_value = get_postback_datetime(event)
        if not dt_value:
            raise RuntimeError("沒有取得選擇的日期時間")
        selected_dt = datetime.fromisoformat(dt_value)
        mode = params.get("mode",[None])[0]

        if mode == "add":
            required = {"patient_id","calendar_title","calendar_location"}
            if not required.issubset(payload):
                raise RuntimeError("新增行程資料已逾時")
            create_patient_calendar_event(
                payload["patient_id"],payload["calendar_title"],
                payload.get("calendar_description"),payload.get("calendar_location"),
                selected_dt,admin_id)
            clear_operation_state(user_id)
            reply_text(event.reply_token,
                f"行程新增完成！\n長者：{payload['patient_name']}\n"
                f"行程：{payload['calendar_title']}\n"
                f"時間：{selected_dt.strftime('%Y-%m-%d %H:%M')}\n"
                f"地點：{payload.get('calendar_location') or '未填寫'}")
            return True

        if mode == "edit":
            if not payload.get("event_id") or not payload.get("patient_id"):
                raise RuntimeError("修改行程資料已逾時")
            update_patient_calendar_event(
                payload["event_id"],payload["patient_id"],"starts_at",selected_dt)
            clear_operation_state(user_id)
            reply_text(event.reply_token,
                f"行程日期時間修改完成！\n行程：{payload['event_title']}\n"
                f"新時間：{selected_dt.strftime('%Y-%m-%d %H:%M')}")
            return True
        raise RuntimeError("無法判斷日期時間操作")

    if action == "family_calendar_confirm_delete":
        state = get_operation_state(user_id)
        payload = state.get("payload",{}) if state else {}
        if not payload.get("event_id") or not payload.get("patient_id"):
            raise RuntimeError("刪除資料已逾時")
        delete_patient_calendar_event(payload["event_id"],payload["patient_id"])
        clear_operation_state(user_id)
        reply_text(event.reply_token,
            f"已刪除行程：{payload.get('event_title') or '未命名行程'}")
        return True

    if action in {"family_calendar_enable_reminder","family_calendar_disable_reminder"}:
        state = get_operation_state(user_id)
        payload = state.get("payload",{}) if state else {}
        patient_id = payload.get("patient_id")
        if not patient_id or not get_family_patient(family_id,patient_id):
            raise RuntimeError("提醒設定資料已逾時")
        enabled = action == "family_calendar_enable_reminder"
        save_followup_reminder_setting(
            patient_id,admin_id,enabled,days_before=3,reminder_time="09:00")
        clear_operation_state(user_id)
        reply_text(event.reply_token,
            f"{payload.get('patient_name','長者')}的回診提醒已{'開啟' if enabled else '關閉'}。"
            + ("\n系統將在回診日前 3 天上午 09:00 排入通知。" if enabled else ""))
        return True
    return False




# =========================================================
# 家屬監控中心
# =========================================================

MONITOR_ACTIONS = {
    "family_monitor_today_status": "今日狀態",
    "family_monitor_missed": "漏服通知",
    "family_monitor_emergency": "緊急通知",
    "family_monitor_discomfort": "不舒服紀錄",
    "family_monitor_adherence": "服藥率統計",
}


def get_patient_today_status(patient_id):
    today = taipei_now().date()
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT ml.scheduled_at,ml.taken_at,ml.status::text,
                   COALESCE(m.medication_name,'未命名藥物'),ml.note
            FROM medication_logs ml
            JOIN medications m ON m.id=ml.medication_id
            WHERE ml.patient_id=%s
              AND (ml.scheduled_at AT TIME ZONE 'Asia/Taipei')::date=%s
            ORDER BY ml.scheduled_at
            """,
            (patient_id,today),
        )
        return [{
            "scheduled_at":r[0],"taken_at":r[1],"status":r[2],
            "medication_name":r[3],"note":r[4],
        } for r in cursor.fetchall()]
    finally:
        connection.close()


def family_today_status_text(patient):
    rows = get_patient_today_status(patient["patient_id"])
    if not rows:
        return f"{patient['display_name']}今天尚無服藥紀錄。"
    status_map = {
        "scheduled":"待確認","taken":"已服藥","missed":"漏服",
        "skipped":"略過","late":"延遲",
    }
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"],0) + 1
    lines = [
        f"{patient['display_name']}今日服藥狀態：",
        "",
        "統計：" + "、".join(
            f"{status_map.get(k,k)} {v} 筆" for k,v in counts.items()
        ),
        "",
        "明細：",
    ]
    for index,row in enumerate(rows,1):
        scheduled = row["scheduled_at"].astimezone(TAIPEI_TZ).strftime("%H:%M")
        taken = ""
        if row["taken_at"]:
            taken = "｜服藥 " + row["taken_at"].astimezone(TAIPEI_TZ).strftime("%H:%M")
        lines.append(
            f"{index}. {scheduled}｜{row['medication_name']}｜"
            f"{status_map.get(row['status'],row['status'])}{taken}"
        )
    return "\n".join(lines)


def family_missed_text(patient):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT ml.scheduled_at,ml.status::text,
                   COALESCE(m.medication_name,'未命名藥物'),ml.note
            FROM medication_logs ml
            JOIN medications m ON m.id=ml.medication_id
            WHERE ml.patient_id=%s
              AND (
                    ml.status::text='missed'
                    OR (
                        ml.status::text='scheduled'
                        AND ml.scheduled_at < CURRENT_TIMESTAMP - INTERVAL '30 minutes'
                    )
              )
              AND ml.scheduled_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'
            ORDER BY ml.scheduled_at DESC
            """,
            (patient["patient_id"],),
        )
        rows = cursor.fetchall()
    finally:
        connection.close()
    if not rows:
        return f"{patient['display_name']}最近 7 天沒有漏服或逾時未確認紀錄。"
    lines = [f"{patient['display_name']}最近 7 天的漏服通知："]
    for index,row in enumerate(rows,1):
        when = row[0].astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
        status = "漏服" if row[1] == "missed" else "逾時未確認"
        lines.append(f"{index}. {when}｜{row[2]}｜{status}")
        if row[3]:
            lines.append(f"   備註：{row[3]}")
    return "\n".join(lines)


def family_discomfort_text(patient):
    records = list_patient_abnormal_reports(patient["patient_id"],days=None)
    if not records:
        return f"{patient['display_name']}目前沒有不舒服紀錄。"
    lines = [f"{patient['display_name']}的不舒服紀錄："]
    for index,record in enumerate(records[:30],1):
        when = record["occurred_at"].astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
        lines.extend([
            "",
            f"{index}. {when}｜{record.get('report_type') or '其他問題'}",
            f"程度：{record.get('severity') or '未分級'}",
            f"回報者：{record['reporter_name']}（{record['reporter_role']}）",
            f"說明：{record.get('description') or '未填寫'}",
        ])
    return "\n".join(lines)


def family_adherence_text(patient,days):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT status::text,COUNT(*)
            FROM medication_logs
            WHERE patient_id=%s
              AND scheduled_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
            GROUP BY status::text
            """,
            (patient["patient_id"],days),
        )
        counts = dict(cursor.fetchall())
    finally:
        connection.close()
    total = sum(counts.values())
    if total == 0:
        return f"{patient['display_name']}最近 {days} 天沒有服藥紀錄。"
    taken = counts.get("taken",0) + counts.get("late",0)
    rate = round(taken / total * 100,1)
    return (
        f"{patient['display_name']}最近 {days} 天服藥率統計：\n\n"
        f"總排程：{total} 筆\n"
        f"已服藥：{counts.get('taken',0)} 筆\n"
        f"延遲服藥：{counts.get('late',0)} 筆\n"
        f"漏服：{counts.get('missed',0)} 筆\n"
        f"略過：{counts.get('skipped',0)} 筆\n"
        f"待確認：{counts.get('scheduled',0)} 筆\n\n"
        f"服藥率：{rate}%"
    )


def upsert_family_emergency_contact(
    patient_id,priority_order,contact_name,relationship,
    phone_number,line_user_id=None,
):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO emergency_contacts (
                patient_id,contact_name,relationship,phone_number,
                line_user_id,priority_order,is_active,updated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,TRUE,CURRENT_TIMESTAMP)
            ON CONFLICT (patient_id,priority_order)
            DO UPDATE SET
                contact_name=EXCLUDED.contact_name,
                relationship=EXCLUDED.relationship,
                phone_number=EXCLUDED.phone_number,
                line_user_id=EXCLUDED.line_user_id,
                is_active=TRUE,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                patient_id,contact_name,relationship,phone_number,
                line_user_id,priority_order,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def send_monitor_patient_selection(event,user_id,action,family_id):
    patients = list_family_patients(family_id)
    if not patients:
        reply_text(event.reply_token,"目前家庭尚未新增長者。")
        return True
    if len(patients) == 1:
        return handle_monitor_selected_patient(event,user_id,action,patients[0])
    set_operation_state(user_id,action,"monitor_select_patient",{})
    items = [
        postback_item(
            p["display_name"],
            f"action=family_monitor_select_patient&next_action={action}&patient_id={p['patient_id']}",
            f"選擇 {p['display_name']}",
        )
        for p in patients[:12]
    ]
    reply_message(
        event.reply_token,
        make_quick_reply_message(
            f"{MONITOR_ACTIONS[action]}\n請選擇長者：",items
        ),
    )
    return True


def handle_monitor_selected_patient(event,user_id,action,patient):
    if action == "family_monitor_today_status":
        reply_text(event.reply_token,family_today_status_text(patient))
        return True
    if action == "family_monitor_missed":
        reply_text(event.reply_token,family_missed_text(patient))
        return True
    if action == "family_monitor_discomfort":
        reply_text(event.reply_token,family_discomfort_text(patient))
        return True
    if action == "family_monitor_adherence":
        set_operation_state(
            user_id,action,"monitor_adherence_days",
            {
                "patient_id":str(patient["patient_id"]),
                "patient_name":patient["display_name"],
            },
        )
        reply_message(
            event.reply_token,
            make_quick_reply_message(
                f"請選擇 {patient['display_name']} 的統計期間：",
                [
                    postback_item("最近 7 天","action=family_monitor_adherence_7"),
                    postback_item("最近 30 天","action=family_monitor_adherence_30"),
                    postback_item("取消","action=family_cancel"),
                ],
            ),
        )
        return True
    if action == "family_monitor_emergency":
        contacts = list_elder_contacts(patient["patient_id"])
        current = []
        for i in range(2):
            if i < len(contacts):
                contact = contacts[i]
                current.append(
                    f"聯絡人 {i+1}：{contact['name']}｜"
                    f"{contact.get('phone') or '未填電話'}"
                )
            else:
                current.append(f"聯絡人 {i+1}：尚未設定")
        set_operation_state(
            user_id,action,"monitor_contact_select",
            {
                "patient_id":str(patient["patient_id"]),
                "patient_name":patient["display_name"],
            },
        )
        reply_message(
            event.reply_token,
            make_quick_reply_message(
                (
                    f"{patient['display_name']}的指定緊急聯絡人：\n"
                    + "\n".join(current)
                    + "\n\n請選擇要設定的位置："
                ),
                [
                    postback_item(
                        "設定聯絡人 1",
                        "action=family_monitor_contact_select&priority=1",
                    ),
                    postback_item(
                        "設定聯絡人 2",
                        "action=family_monitor_contact_select&priority=2",
                    ),
                    postback_item("取消","action=family_cancel"),
                ],
            ),
        )
        return True
    return False


def handle_family_monitor_postback(event,action,params):
    user_id = get_user_id(event)
    family = ensure_family_admin(user_id)
    family_id = family["id"]

    if action in MONITOR_ACTIONS:
        return send_monitor_patient_selection(event,user_id,action,family_id)

    if action == "family_monitor_select_patient":
        next_action = params.get("next_action",[None])[0]
        patient_id = params.get("patient_id",[None])[0]
        if next_action not in MONITOR_ACTIONS:
            raise RuntimeError("監控功能資料已逾時")
        patient = get_family_patient(family_id,patient_id)
        if not patient:
            raise RuntimeError("找不到這位長者")
        return handle_monitor_selected_patient(event,user_id,next_action,patient)

    if action in {"family_monitor_adherence_7","family_monitor_adherence_30"}:
        state = get_operation_state(user_id)
        payload = state.get("payload",{}) if state else {}
        patient = get_family_patient(family_id,payload.get("patient_id"))
        if not patient:
            raise RuntimeError("統計資料已逾時")
        days = 7 if action.endswith("_7") else 30
        clear_operation_state(user_id)
        reply_text(event.reply_token,family_adherence_text(patient,days))
        return True

    if action == "family_monitor_contact_select":
        state = get_operation_state(user_id)
        payload = state.get("payload",{}) if state else {}
        priority = params.get("priority",[None])[0]
        if not payload.get("patient_id") or priority not in {"1","2"}:
            raise RuntimeError("聯絡人設定資料已逾時")
        payload["priority"] = int(priority)
        set_operation_state(
            user_id,"family_monitor_emergency",
            "waiting_monitor_contact_name",payload
        )
        reply_text(event.reply_token,f"請輸入緊急聯絡人 {priority} 的姓名：")
        return True
    return False

# =========================================================
# 家屬報表紀錄
# =========================================================

REPORT_ACTIONS = {
    "family_report_today": ("今日紀錄", 1),
    "family_report_7days": ("7天紀錄", 7),
    "family_report_30days": ("30天紀錄", 30),
    "family_report_abnormal": ("異常統計", None),
    "family_report_summary": ("匯出摘要", 30),
}


def list_patient_medication_logs(patient_id, days):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                ml.scheduled_at,
                ml.taken_at,
                ml.status::text,
                ml.note,
                COALESCE(m.medication_name, '未命名藥物'),
                COALESCE(u.display_name, '未記錄')
            FROM medication_logs ml
            LEFT JOIN medications m ON m.id = ml.medication_id
            LEFT JOIN app_users u ON u.id = ml.reported_by
            WHERE ml.patient_id = %s
              AND ml.scheduled_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
            ORDER BY ml.scheduled_at DESC
            """,
            (patient_id, days),
        )
        return [{
            "scheduled_at": row[0],
            "taken_at": row[1],
            "status": row[2],
            "note": row[3],
            "medication_name": row[4],
            "reported_by": row[5],
        } for row in cursor.fetchall()]
    finally:
        connection.close()


def list_patient_abnormal_reports(patient_id, days=None):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                ar.id,
                ar.report_type,
                ar.severity,
                ar.description,
                ar.occurred_at,
                ar.created_at,
                COALESCE(u.display_name, '未知使用者'),
                COALESCE(r.name_zh_tw, r.code, '未知身份')
            FROM abnormal_reports ar
            LEFT JOIN app_users u ON u.id = ar.reported_by
            LEFT JOIN roles r ON r.id = u.role_id
            WHERE ar.patient_id = %s
              AND ar.is_active = TRUE
              AND (%s IS NULL OR ar.occurred_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 day'))
            ORDER BY ar.occurred_at DESC, ar.created_at DESC
            """,
            (patient_id, days, days),
        )
        return [{
            "id": row[0],
            "report_type": row[1],
            "severity": row[2],
            "description": row[3],
            "occurred_at": row[4],
            "created_at": row[5],
            "reporter_name": row[6],
            "reporter_role": row[7],
        } for row in cursor.fetchall()]
    finally:
        connection.close()


def medication_log_report_text(patient, days):
    logs = list_patient_medication_logs(patient["patient_id"], days)
    title = "今日紀錄" if days == 1 else f"最近 {days} 天紀錄"

    if not logs:
        return f"{patient['display_name']}的{title}：\n目前沒有服藥紀錄。"

    status_names = {
        "scheduled": "待確認",
        "taken": "已服藥",
        "missed": "漏服",
        "skipped": "略過",
        "late": "延遲服藥",
    }
    counts = {}
    for item in logs:
        status = item["status"]
        counts[status] = counts.get(status, 0) + 1

    lines = [
        f"{patient['display_name']}的{title}：",
        "",
        f"總紀錄：{len(logs)} 筆",
        "狀態統計：" + "、".join(
            f"{status_names.get(key, key)} {value} 筆"
            for key, value in counts.items()
        ),
        "",
        "紀錄明細：",
    ]

    for index, item in enumerate(logs[:30], 1):
        scheduled = item["scheduled_at"].strftime("%Y-%m-%d %H:%M")
        status = status_names.get(item["status"], item["status"])
        lines.append(
            f"{index}. {scheduled}｜{item['medication_name']}｜{status}"
        )
        if item.get("note"):
            lines.append(f"   備註：{item['note']}")

    if len(logs) > 30:
        lines.append(f"\n其餘 {len(logs) - 30} 筆未顯示。")
    return "\n".join(lines)


def abnormal_report_text(patient):
    records = list_patient_abnormal_reports(patient["patient_id"], days=None)
    if not records:
        return (
            f"{patient['display_name']}目前沒有長者或看護回報的不舒服紀錄。"
        )

    severity_names = {
        "mild": "輕微",
        "moderate": "中等",
        "severe": "嚴重",
        "critical": "緊急",
        "normal": "一般",
    }
    severity_count = {}
    type_count = {}

    for record in records:
        severity = record.get("severity") or "未分級"
        report_type = record.get("report_type") or "其他不舒服"
        severity_count[severity] = severity_count.get(severity, 0) + 1
        type_count[report_type] = type_count.get(report_type, 0) + 1

    lines = [
        f"{patient['display_name']}的不舒服紀錄統計：",
        "",
        f"總紀錄：{len(records)} 筆",
        "程度統計：" + "、".join(
            f"{severity_names.get(key, key)} {value} 筆"
            for key, value in severity_count.items()
        ),
        "症狀統計：" + "、".join(
            f"{key} {value} 筆"
            for key, value in sorted(
                type_count.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        ),
        "",
        "所有不舒服紀錄：",
    ]

    for index, record in enumerate(records[:40], 1):
        happened = record["occurred_at"].strftime("%Y-%m-%d %H:%M")
        severity = severity_names.get(
            record.get("severity"),
            record.get("severity") or "未分級",
        )
        lines.extend([
            (
                f"{index}. {happened}｜"
                f"{record.get('report_type') or '其他不舒服'}｜{severity}"
            ),
            (
                f"   回報者：{record['reporter_name']}"
                f"（{record['reporter_role']}）"
            ),
            f"   說明：{record.get('description') or '未填寫'}",
        ])

    if len(records) > 40:
        lines.append(f"\n其餘 {len(records) - 40} 筆未顯示。")
    return "\n".join(lines)


def patient_medical_summary_text(patient):
    medications = list_patient_medications(
        patient["patient_id"],
        active_only=True,
    )
    abnormal_records = list_patient_abnormal_reports(
        patient["patient_id"],
        days=None,
    )

    lines = [
        "【長者用藥與不舒服摘要】",
        f"長者：{patient['display_name']}",
        f"產生時間：{taipei_now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "一、目前使用中的藥物",
    ]

    if not medications:
        lines.append("目前沒有使用中的藥物。")
    else:
        for index, medication in enumerate(medications, 1):
            inventory = _medication_inventory_values(medication)
            lines.extend([
                "",
                f"{index}. {medication['medication_name']}",
                f"含量：{medication.get('dosage') or '未標示'}",
                f"用法：{medication.get('instructions') or '未標示'}",
                f"調劑日期：{inventory['dispense_date'] or '未標示'}",
                f"處方天數：{inventory['course_days'] or '未標示'}",
                (
                    "剩餘量："
                    f"{_format_quantity(inventory['remaining'])} "
                    f"{medication['quantity_unit']}"
                ),
            ])

    lines.extend(["", "二、長者／看護回報的不舒服紀錄"])

    if not abnormal_records:
        lines.append("目前沒有不舒服紀錄。")
    else:
        for index, record in enumerate(abnormal_records[:30], 1):
            happened = record["occurred_at"].strftime("%Y-%m-%d %H:%M")
            lines.extend([
                "",
                (
                    f"{index}. {happened}｜"
                    f"{record.get('report_type') or '其他不舒服'}"
                ),
                (
                    f"回報者：{record['reporter_name']}"
                    f"（{record['reporter_role']}）"
                ),
                f"程度：{record.get('severity') or '未分級'}",
                f"說明：{record.get('description') or '未填寫'}",
            ])

        if len(abnormal_records) > 30:
            lines.append(
                f"\n其餘 {len(abnormal_records) - 30} 筆未顯示。"
            )

    lines.extend([
        "",
        "此摘要僅整理系統內已有紀錄，不能替代醫師診斷。",
    ])
    return "\n".join(lines)


def send_report_patient_selection(event, user_id, action, family_id):
    patients = list_family_patients(family_id)
    if not patients:
        reply_text(
            event.reply_token,
            "目前家庭尚未新增長者，請先到「家庭管理」新增長者。",
        )
        return True

    if len(patients) == 1:
        return handle_report_selected_patient(
            event,
            user_id,
            action,
            patients[0],
        )

    set_operation_state(
        user_id,
        action,
        "report_select_patient",
        {},
    )
    items = [
        postback_item(
            patient["display_name"],
            (
                "action=family_report_select_patient"
                f"&next_action={action}"
                f"&patient_id={patient['patient_id']}"
            ),
            f"選擇 {patient['display_name']}",
        )
        for patient in patients[:12]
    ]
    reply_message(
        event.reply_token,
        make_quick_reply_message(
            f"{REPORT_ACTIONS[action][0]}\n請選擇長者：",
            items,
        ),
    )
    return True


def handle_report_selected_patient(event, user_id, action, patient):
    clear_operation_state(user_id)

    if action in {
        "family_report_today",
        "family_report_7days",
        "family_report_30days",
    }:
        days = REPORT_ACTIONS[action][1]
        reply_text(
            event.reply_token,
            medication_log_report_text(patient, days),
        )
        return True

    if action == "family_report_abnormal":
        reply_text(
            event.reply_token,
            abnormal_report_text(patient),
        )
        return True

    if action == "family_report_summary":
        reply_text(
            event.reply_token,
            patient_medical_summary_text(patient),
        )
        return True

    return False


def handle_family_report_postback(event, action, params):
    user_id = get_user_id(event)
    if not user_id:
        reply_text(event.reply_token, "無法取得您的 LINE User ID。")
        return True

    family = ensure_family_admin(user_id)
    family_id = family["id"]

    if action in REPORT_ACTIONS:
        return send_report_patient_selection(
            event,
            user_id,
            action,
            family_id,
        )

    if action == "family_report_select_patient":
        next_action = params.get("next_action", [None])[0]
        patient_id = params.get("patient_id", [None])[0]

        if next_action not in REPORT_ACTIONS:
            raise RuntimeError("報表功能資料已逾時")

        patient = get_family_patient(family_id, patient_id)
        if not patient:
            raise RuntimeError("找不到這位長者，或長者已不在此家庭")

        return handle_report_selected_patient(
            event,
            user_id,
            next_action,
            patient,
        )

    return False

# =========================================================
# OpenAI
# =========================================================

def gpt_response(user_text):
    response = openai_client.responses.create(
        prompt={
            "id": (
                "pmpt_69e86fa11c1c8193bf0389182d0c664c"
                "0cc0ed66294ebdce"
            ),
            "version": "3",
        },
        input=user_text,
    )

    answer = getattr(
        response,
        "output_text",
        "",
    ).strip()

    return answer or "目前沒有取得回應，請再試一次。"


def save_medication_scan_analysis(scan_id, result=None, error_message=None):
    """Save a completed/failed scan without changing medication schedules."""
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        if result is not None:
            cursor.execute(
                """
                UPDATE ai_medication_scans
                SET processing_status='completed',
                    original_text=%s,
                    parsed_result=%s::jsonb
                WHERE id=%s
                """,
                (
                    result["display_text"],
                    json.dumps(result, ensure_ascii=False),
                    scan_id,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE ai_medication_scans
                SET processing_status='failed', original_text=%s
                WHERE id=%s
                """,
                (safe_text(error_message or "辨識失敗", 1000), scan_id),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def process_medication_scan(scan_id, image_path):
    """Decode QR locally, analyze the bag with GPT-4.1, and persist it."""
    try:
        result = analyze_medication_image(
            client=openai_client,
            image_path=image_path,
            model=os.getenv("MEDICATION_VISION_MODEL", "gpt-4.1"),
        )
        save_medication_scan_analysis(scan_id, result=result)
        return result
    except Exception as error:
        try:
            save_medication_scan_analysis(
                scan_id,
                error_message=f"{type(error).__name__}: {error}",
            )
        except Exception:
            app.logger.error(traceback.format_exc())
        raise


# =========================================================
# Flask 路由
# =========================================================

google_health = register_google_health(app, get_db_connection)

apple_health = register_apple_health(
    app,
    get_db_connection,
)


@app.route("/", methods=["GET"])
def home():
    return (
        "LINE Bot is running. "
        "Database: PostgreSQL"
    )


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get(
        "X-Line-Signature",
        "",
    )
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)

    except InvalidSignatureError:
        abort(400)

    except Exception:
        app.logger.error(traceback.format_exc())
        abort(500)

    return "OK"



# =========================================================
# Bot 加入群組：回傳並印出 Group ID
# =========================================================

@handler.add(JoinEvent)
def handle_join(event):
    source = getattr(event, "source", None)
    source_type = getattr(source, "type", None)
    group_id = getattr(source, "group_id", None)
    room_id = getattr(source, "room_id", None)
    conversation_id = group_id or room_id

    if not conversation_id:
        return

    print("=" * 60, flush=True)
    print(f"LINE {source_type or 'conversation'} ID：{conversation_id}", flush=True)
    print("=" * 60, flush=True)
    app.logger.info("Bot joined %s, ID=%s", source_type, conversation_id)

    label = "群組 ID" if group_id else "多人聊天室 ID"
    reply_text(
        event.reply_token,
        f"Bot 已成功加入！\n\nLINE {label}：\n{conversation_id}\n\n請複製這組 ID，回到 Bot 私人聊天室，點擊「家庭群組 ID」完成綁定。",
    )


# =========================================================
# 加入好友
# =========================================================

@handler.add(FollowEvent)
def handle_follow(event):
    user_id = get_user_id(event)

    if not user_id:
        return

    try:
        user = get_user(user_id)

        if not user:
            reply_role_selection(event.reply_token)
            return

        role = user["role"]
        role_setting = ROLE_CONFIG.get(role)

        if not role_setting:
            reply_role_selection(event.reply_token)
            return

        menu_linked = False

        try:
            rich_menu_id = bind_role_rich_menu(
                user_id,
                role,
            )
            menu_linked = True

            if user.get("rich_menu_id") != rich_menu_id:
                save_user(
                    user_id=user_id,
                    display_name=user.get("display_name") or "使用者",
                    role=role,
                    rich_menu_id=rich_menu_id,
                    picture_url=user.get("picture_url"),
                    language=user.get("language"),
                )

        except Exception as error:
            app.logger.error(
                "重新綁定 Rich Menu 失敗：%s",
                error,
            )
            app.logger.error(traceback.format_exc())

        menu_text = (
            "已載入原本的功能選單。"
            if menu_linked
            else "身份資料已恢復，但功能選單尚未載入。"
        )

        reply_text(
            event.reply_token,
            (
                f"{user.get('display_name') or '使用者'}，"
                "歡迎回來！\n"
                f"目前身份：{role_setting['name']}\n"
                f"LINE User ID：{user_id}\n"
                f"{menu_text}"
            ),
        )

    except Exception:
        app.logger.error(traceback.format_exc())


# =========================================================
# 文字訊息
# =========================================================

@handler.add(
    MessageEvent,
    message=TextMessageContent,
)
def handle_text_message(event):
    user_id = get_user_id(event)

    try:
        if master_roles.handle_text(event):
            return
        user = get_user(user_id) if user_id else None

        if user_id and not user:
            reply_role_selection(event.reply_token)
            return

        user_text = (event.message.text or "").strip()

        if user_text in {"連結 Google Health", "健康連結狀態", "同步健康步數", "解除 Google Health"}:
            if getattr(event.source, "type", None) != "user":
                reply_text(event.reply_token, "請在 Bot 私人聊天室操作健康資料連結。")
                return
            if not google_health:
                reply_text(event.reply_token, "Google Health 功能尚未啟用。")
                return
            try:
                if user_text == "連結 Google Health":
                    result = "請用外部瀏覽器開啟以下連結（10 分鐘有效、僅能使用一次）。請勿轉傳，請登入你自己的 Google Health 帳號：\n" + google_health.issue_link(user["id"])
                elif user_text == "健康連結狀態":
                    result = google_health.status(user["id"])
                elif user_text == "同步健康步數":
                    result = google_health.sync(user["id"])
                else:
                    result = google_health.disconnect(user["id"])
                reply_text(event.reply_token, result)
            except HealthError as error:
                reply_text(event.reply_token, str(error))
            except Exception:
                app.logger.error("Google Health command failed")
                reply_text(event.reply_token, "健康資料操作未完成，請稍後再試或檢查部署設定。")
            return

        if user_text == "取消" and user_id:
            clear_operation_state(user_id)
            reply_text(event.reply_token, "已取消本次操作。")
            return

        if user and user_text in {
            "選單",
            "呼叫選單",
            "開啟選單",
            "重新載入選單",
            "重新綁定選單",
            "載入選單",
            "menu",
            "Menu",
        }:
            source = getattr(event, "source", None)
            source_type = getattr(source, "type", None)
            is_group_conversation = source_type in {"group", "room"}

            if is_group_conversation:
                # Rich Menu 不會顯示在群組底部；群組改傳分層 Quick Reply。
                rich_menu_id = get_role_rich_menu_id(user["role"]) or "group-flex"
                open_temporary_rich_menu_session(
                    event,
                    user_id,
                    user["role"],
                    rich_menu_id,
                )
                reply_message(
                    event.reply_token,
                    create_group_quick_menu(user["role"]),
                )
            else:
                rich_menu_id = bind_role_rich_menu(user_id, user["role"])
                save_user(
                    user_id=user_id,
                    display_name=user.get("display_name") or "使用者",
                    role=user["role"],
                    rich_menu_id=rich_menu_id,
                    picture_url=user.get("picture_url"),
                    language=user.get("language"),
                )
                role_name = ROLE_CONFIG.get(user["role"], {}).get(
                    "name", user["role"]
                )
                reply_text(
                    event.reply_token,
                    f"已載入「{role_name}」專用功能選單。",
                )
            return

        if user_id and user_text in {
            "關閉選單",
            "隱藏選單",
            "close menu",
            "Close menu",
        }:
            source_type = getattr(getattr(event, "source", None), "type", None)
            if source_type in {"group", "room"}:
                delete_temporary_group_menu_session(user_id)
            else:
                close_temporary_rich_menu_session(user_id)
            reply_text(
                event.reply_token,
                "功能選單已關閉。下次需要時請再輸入「選單」。",
            )
            return

        if user_id and user and user.get("role") == "family":
            if handle_family_text_input(event, user_text, user_id):
                return

        if user_id and user and user.get("role") == "caregiver":
            caregiver_state = get_operation_state(user_id)
            if caregiver_state and caregiver_state.get("step") == "waiting_caregiver_issue_text":
                payload = caregiver_state.get("payload", {})
                patient = get_selected_caregiver_patient(user_id)
                if str(patient["patient_id"]) != str(payload.get("patient_id")):
                    clear_operation_state(user_id)
                    reply_text(event.reply_token, "The selected patient changed. Please report the issue again.")
                    return
                caregiver = _caregiver_user(user_id)
                label = save_caregiver_issue(
                    patient, caregiver["id"], payload.get("issue_type", "other"), user_text,
                )
                clear_operation_state(user_id)
                reply_text(
                    event.reply_token,
                    f"Recorded: {label}\nTime: {taipei_now().strftime('%Y-%m-%d %H:%M')}\nDetails: {user_text}",
                )
                return

        if user_id:
            elder_state = get_operation_state(user_id)
            if elder_state:
                if user_text in {"取消", "cancel", "Cancel"}:
                    clear_operation_state(user_id)
                    reply_text(event.reply_token, "已取消本次操作。")
                    return

                step = elder_state.get("step")
                payload = elder_state.get("payload", {})

                if step == "waiting_elder_discomfort_text":
                    patient = get_elder_patient_by_line_user_id(user_id)
                    occurred_at = save_elder_discomfort(
                        patient,
                        patient["user_id"],
                        "其他問題",
                        user_text,
                    )
                    clear_operation_state(user_id)
                    reply_text(
                        event.reply_token,
                        (
                            "已記錄目前不舒服的情況。\n"
                            f"時間：{occurred_at.strftime('%Y-%m-%d %H:%M')}\n"
                            "家屬端可在不舒服紀錄與異常統計中查看。"
                        ),
                    )
                    return

                if step == "waiting_elder_calendar_title":
                    payload["calendar_title"] = user_text[:255]
                    set_operation_state(
                        user_id,
                        "elder_calendar_add",
                        "waiting_elder_calendar_location",
                        payload,
                    )
                    reply_text(
                        event.reply_token,
                        "請輸入地點，例如：埔里基督教醫院。沒有地點請輸入「未填寫」。",
                    )
                    return

                if step == "waiting_elder_calendar_location":
                    payload["calendar_location"] = (
                        None if user_text in {"未填寫", "無", "沒有"} else user_text[:500]
                    )
                    set_operation_state(
                        user_id,
                        "elder_calendar_add",
                        "waiting_elder_calendar_datetime",
                        payload,
                    )
                    reply_message(
                        event.reply_token,
                        make_quick_reply_message(
                            "請選擇日期與時間：",
                            [
                                datetime_item(
                                    "選擇日期時間",
                                    "action=elder_calendar_save_datetime&mode=add",
                                    mode="datetime",
                                    minimum=taipei_now().strftime("%Y-%m-%dT%H:%M"),
                                ),
                                postback_item("取消", "action=elder_cancel"),
                            ],
                        ),
                    )
                    return
        answer = gpt_response(
            user_text
        )

        reply_text(
            event.reply_token,
            answer,
        )

    except Exception as error:
        app.logger.error(traceback.format_exc())

        try:
            reply_text(
                event.reply_token,
                f"系統錯誤：{error}",
            )
        except Exception:
            app.logger.error(traceback.format_exc())


# =========================================================
# 圖片訊息
# =========================================================

@handler.add(
    MessageEvent,
    message=ImageMessageContent,
)
def handle_image_message(event):
    user_id = get_user_id(event)

    try:
        if master_roles.prepare(event):
            return
        if user_id and not get_user(user_id):
            reply_role_selection(event.reply_token)
            return

        os.makedirs(TMP_DIR, exist_ok=True)

        image_path = os.path.join(
            TMP_DIR,
            f"{event.message.id}.jpg",
        )

        api_client, blob_api = get_blob_api()

        try:
            image_content = blob_api.get_message_content(
                message_id=event.message.id
            )

            with open(image_path, "wb") as image_file:
                image_file.write(image_content)

        finally:
            api_client.close()


        state = get_operation_state(user_id) if user_id else None
        if state and state.get("step") == "waiting_elder_prescription_image":
            patient = get_elder_patient_by_line_user_id(user_id)
            connection = get_db_connection()
            try:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    INSERT INTO ai_medication_scans (
                        patient_id,
                        uploaded_by,
                        line_message_id,
                        image_path,
                        processing_status,
                        created_at
                    )
                    VALUES (%s,%s,%s,%s,'uploaded',CURRENT_TIMESTAMP)
                    RETURNING id
                    """,
                    (
                        patient["patient_id"],
                        patient["user_id"],
                        event.message.id,
                        image_path,
                    ),
                )
                scan_id = cursor.fetchone()[0]
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

            result = process_medication_scan(scan_id, image_path)
            clear_operation_state(user_id)
            reply_text(
                event.reply_token,
                safe_text(
                    "藥袋辨識完成，家屬可在「藥袋紀錄」中查看。\n\n"
                    + result["display_text"]
                ),
            )
            return

        user = get_user(user_id) if user_id else None
        if user and user.get("role") == "caregiver":
            patient = get_selected_caregiver_patient(user_id)
            caregiver = _caregiver_user(user_id)
            connection = get_db_connection()
            try:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    INSERT INTO ai_medication_scans (
                        patient_id,uploaded_by,line_message_id,image_path,
                        processing_status,created_at
                    ) VALUES (%s,%s,%s,%s,'uploaded',CURRENT_TIMESTAMP)
                    RETURNING id
                    """,
                    (patient["patient_id"], caregiver["id"], event.message.id, image_path),
                )
                scan_id = cursor.fetchone()[0]
                connection.commit()
            except Exception:
                connection.rollback(); raise
            finally:
                connection.close()
            result = process_medication_scan(scan_id, image_path)
            reply_text(
                event.reply_token,
                safe_text(
                    f"{patient['patient_name']} – Prescription analysis complete\n\n"
                    + result["display_text"]
                ),
            )
            return

        reply_text(
            event.reply_token,
            "已收到圖片。",
        )

    except Exception:
        app.logger.error(traceback.format_exc())

        try:
            reply_text(
                event.reply_token,
                "圖片處理失敗，請稍後再試。",
            )
        except Exception:
            app.logger.error(traceback.format_exc())


# =========================================================
# Postback：身份選擇
# =========================================================

@handler.add(PostbackEvent)
def handle_postback(event):
    action = None
    try:
        params = parse_qs(
            event.postback.data or ""
        )

        action = params.get(
            "action",
            [None],
        )[0]

        role = params.get(
            "role",
            [None],
        )[0]

        if master_roles.handle_postback(event, action, params):
            return

        postback_user_id = get_user_id(event)
        postback_user = get_user(postback_user_id) if postback_user_id else None

        source_type = getattr(getattr(event, "source", None), "type", None)
        is_group_conversation = source_type in {"group", "room"}
        is_group_menu_action = (
            action in {"group_quick_menu", "close_group_quick_menu"}
            or action in ELDER_MEDICATION_ACTIONS
            or action in CAREGIVER_ACTIONS
            or action in FAMILY_ACTIONS
        )

        if is_group_conversation and is_group_menu_action:
            if action == "close_group_quick_menu":
                delete_temporary_group_menu_session(postback_user_id)
                reply_text(
                    event.reply_token,
                    "選單已關閉。下次需要時請在群組重新輸入「選單」。",
                )
                return

            if not has_active_group_menu_session(postback_user_id):
                reply_text(
                    event.reply_token,
                    "這份選單已超過 10 分鐘，請重新輸入「選單」。",
                )
                return

            refresh_temporary_rich_menu_session(postback_user_id)

            if action == "group_quick_menu":
                requested_page = params.get("menu", [None])[0]
                reply_message(
                    event.reply_token,
                    create_group_quick_menu(
                        postback_user.get("role") if postback_user else None,
                        requested_page,
                    ),
                )
                return

        # Rich Menu 的 postback（包含 richmenuswitch data）都視為仍在使用，
        # 將群組暫時選單的有效時間重新延長 10 分鐘。
        if not is_group_conversation:
            refresh_temporary_rich_menu_session(postback_user_id)

        app.logger.info(
            "POSTBACK action=%s user_id=%s user_role=%s raw_data=%s",
            action,
            postback_user_id,
            postback_user.get("role") if postback_user else None,
            event.postback.data or "",
        )

        if action in ELDER_MEDICATION_ACTIONS:
            if handle_elder_medication_postback(event, action, params):
                return

        if action in CAREGIVER_ACTIONS:
            if handle_caregiver_postback(event, action, params):
                return

        if action in FAMILY_ACTIONS:
            if not postback_user or postback_user.get("role") != "family":
                raise RuntimeError(
                    "目前選單與使用者身份不一致："
                    f"收到家屬功能 action={action}，"
                    f"目前身份={postback_user.get('role') if postback_user else 'unknown'}。"
                    "請重新建立並綁定正確的 Rich Menu。"
                )
            if handle_family_postback(event, action, params):
                return

        if action != "select_role":
            return

        user_id = get_user_id(event)

        if not user_id:
            reply_text(
                event.reply_token,
                "無法取得您的 LINE User ID。",
            )
            return

        existing_user = get_user(user_id)

        if existing_user:
            existing_role = existing_user["role"]
            role_name = ROLE_CONFIG.get(
                existing_role,
                {},
            ).get(
                "name",
                existing_role,
            )

            try:
                rich_menu_id = bind_role_rich_menu(
                    user_id,
                    existing_role,
                )

                save_user(
                    user_id=user_id,
                    display_name=(
                        existing_user.get("display_name")
                        or "使用者"
                    ),
                    role=existing_role,
                    rich_menu_id=rich_menu_id,
                    picture_url=existing_user.get("picture_url"),
                    language=existing_user.get("language"),
                )

                message = (
                    "您的身份已經設定完成。\n"
                    f"目前身份：{role_name}\n"
                    f"LINE User ID：{user_id}\n"
                    "已重新載入專用功能選單。"
                )

            except Exception as error:
                app.logger.error(traceback.format_exc())
                message = (
                    "您的身份已經設定完成。\n"
                    f"目前身份：{role_name}\n"
                    f"LINE User ID：{user_id}\n"
                    "但重新載入功能選單失敗："
                    f"{error}"
                )

            reply_text(
                event.reply_token,
                message,
            )
            return

        if role not in ROLE_CONFIG:
            reply_text(
                event.reply_token,
                "身份資料不正確，請重新操作。",
            )
            return

        role_setting = ROLE_CONFIG[role]
        profile = get_line_profile(user_id)

        display_name = (
            profile.get("display_name")
            or "使用者"
        )

        rich_menu_id = get_role_rich_menu_id(role)

        # 先儲存身份，避免 Rich Menu 綁定失敗時資料遺失
        save_user(
            user_id=user_id,
            display_name=display_name,
            role=role,
            rich_menu_id=rich_menu_id,
            picture_url=profile.get("picture_url"),
            language=profile.get("language"),
        )

        record_role_selection(
            line_user_id=user_id,
            role=role,
        )

        menu_linked = False
        menu_error = None

        try:
            bind_role_rich_menu(
                user_id,
                role,
            )
            menu_linked = True

        except Exception as error:
            menu_error = str(error)
            app.logger.error(
                "Rich Menu 綁定失敗：%s",
                error,
            )
            app.logger.error(traceback.format_exc())

        menu_status = (
            f"已載入「{role_setting['name']}」專用功能選單。"
            if menu_linked
            else (
                "身份已成功儲存，但功能選單載入失敗。\n"
                f"原因：{menu_error or '未知錯誤'}"
            )
        )

        reply_text(
            event.reply_token,
            (
                "身份設定完成！\n\n"
                f"名稱：{display_name}\n"
                f"身份：{role_setting['name']}\n"
                f"LINE User ID：{user_id}\n\n"
                f"{menu_status}"
            ),
        )

    except Exception as error:
        app.logger.error(traceback.format_exc())

        try:
            reply_text(
                event.reply_token,
                f"功能操作失敗（action={action or 'unknown'}）：{error}",
            )
        except Exception:
            app.logger.error(traceback.format_exc())


# =========================================================
# 初始化與啟動
# =========================================================

master_roles = MasterRoles(globals())
init_database()
master_roles.initialize()

threading.Thread(
    target=temporary_rich_menu_cleanup_worker,
    name="temporary-rich-menu-cleanup",
    daemon=True,
).start()


if __name__ == "__main__":
    port = int(
        os.environ.get(
            "PORT",
            5000,
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
