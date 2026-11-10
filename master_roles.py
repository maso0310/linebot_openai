"""Opt-in, reversible role testing. No LINE account or family is exchanged."""
import json
import os
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS master_role_sessions (
    user_id UUID PRIMARY KEY REFERENCES app_users(id),
    original_settings JSONB,
    test_patient_id UUID REFERENCES patients(id),
    active BOOLEAN NOT NULL DEFAULT FALSE,
    disabled BOOLEAN NOT NULL DEFAULT FALSE,
    menu_pending BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

LABELS = {"family": "家屬", "elderly": "長者", "caregiver": "看護"}
COMMANDS = {"切換身份", "身份狀態", "還原身份", "停用身份切換",
            "切換為家屬", "切換為長者", "切換為看護"}


class MasterRoles:
    def __init__(self, app_functions):
        self.api = app_functions

    def initialize(self):
        connection = self.api["get_db_connection"]()
        try:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def allowed(self, line_id):
        ids = {s.strip() for s in os.getenv("MASTER_LINE_USER_IDS", "").split(",") if s.strip()}
        return (os.getenv("MASTER_ROLE_SWITCH_ENABLED", "false").lower() == "true"
                and line_id in ids)

    @contextmanager
    def _account_lock(self, line_id):
        # One account may send multiple webhooks to different Gunicorn workers.
        # Keep DB transitions and the external menu update in the same lock.
        connection = self.api["get_db_connection"]()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", ("master_roles:" + line_id,))
            yield
        finally:
            # Closing the session also releases its advisory lock after errors.
            connection.close()

    def session(self, line_id):
        connection = self.api["get_db_connection"]()
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT s.active, s.disabled, s.menu_pending, s.original_settings,
                           s.test_patient_id, u.id, u.display_name, r.code,
                           p.full_name
                    FROM master_role_sessions s
                    JOIN app_users u ON u.id=s.user_id
                    JOIN roles r ON r.id=u.role_id
                    LEFT JOIN patients p ON p.id=s.test_patient_id
                    WHERE u.line_user_id=%s
                """, (line_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                return dict(zip(("active", "disabled", "menu_pending", "original",
                                 "patient_id", "user_id", "display_name", "role",
                                 "patient_name"), row))
        finally:
            connection.close()

    def test_patient(self, line_id, role):
        session = self.session(line_id)
        if not session or not session["active"] or session["role"] != role:
            return None
        return {"patient_id": session["patient_id"], "patient_name": session["patient_name"],
                "user_id": session["user_id"], "line_user_id": line_id,
                "display_name": session["display_name"], "role": role}

    def _change(self, line_id, role=None, disable=False):
        if role is not None and not self.allowed(line_id):
            raise RuntimeError("此帳號未啟用身份切換")
        with self._account_lock(line_id):
            self._change_locked(line_id, role, disable)

    def _change_locked(self, line_id, role=None, disable=False):
        """Commit DB first; menu_pending makes a failed LINE call retryable."""
        if role is not None and role not in LABELS:
            raise RuntimeError("不支援的身份")
        if role is not None and not self.allowed(line_id):
            raise RuntimeError("此帳號未啟用身份切換")
        connection = self.api["get_db_connection"]()
        try:
            with connection.cursor() as cursor:
                # Serialize operations for this account, including the first switch.
                cursor.execute("""SELECT id, role_id, language_id, current_rich_menu_id
                                  FROM app_users WHERE line_user_id=%s AND is_active=TRUE
                                  FOR UPDATE""", (line_id,))
                user = cursor.fetchone()
                if not user:
                    raise RuntimeError("請先完成 Bot 身份設定")
                user_id = user[0]
                cursor.execute("INSERT INTO master_role_sessions (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
                cursor.execute("SELECT original_settings, active, disabled, test_patient_id FROM master_role_sessions WHERE user_id=%s FOR UPDATE", (user_id,))
                original, active, disabled, patient_id = cursor.fetchone()
                if role is not None and disabled:
                    raise RuntimeError("身份切換已停用；重新啟用需由資料庫管理者操作")
                if role is not None:
                    cursor.execute("SELECT id FROM roles WHERE code=%s AND is_active=TRUE", (role,))
                    target = cursor.fetchone()
                    if not target:
                        raise RuntimeError("資料庫尚未設定此身份")
                    if not active:
                        cursor.execute("SELECT patient_id FROM caregiver_selected_patients WHERE caregiver_user_id=%s", (user_id,))
                        selected = cursor.fetchone()
                        original = {"role_id": str(user[1]), "language_id": str(user[2]) if user[2] else None,
                                    "menu_id": user[3], "selected_patient_id": str(selected[0]) if selected else None}
                    if not patient_id:
                        # Unlinked patient: never replaces any real person's patient binding.
                        cursor.execute("""INSERT INTO patients (full_name, notes, is_active)
                                          VALUES (%s,%s,TRUE) RETURNING id""",
                                       ("身份切換測試患者", "master_role_switch：專用測試資料"))
                        patient_id = cursor.fetchone()[0]
                    else:
                        cursor.execute("UPDATE patients SET is_active=TRUE WHERE id=%s", (patient_id,))
                    cursor.execute("SELECT id FROM languages WHERE code=%s AND is_active=TRUE", ("en" if role == "caregiver" else "zh-TW",))
                    language = cursor.fetchone()
                    cursor.execute("""UPDATE app_users SET role_id=%s, language_id=%s,
                                      updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                                   (target[0], language[0] if language else user[2], user_id))
                    cursor.execute("""UPDATE master_role_sessions SET original_settings=%s::jsonb,
                                      test_patient_id=%s, active=TRUE, menu_pending=TRUE,
                                      updated_at=CURRENT_TIMESTAMP WHERE user_id=%s""",
                                   (json.dumps(original), patient_id, user_id))
                elif active:
                    cursor.execute("""UPDATE app_users SET role_id=%s, language_id=%s,
                                      current_rich_menu_id=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s""",
                                   (original["role_id"], original["language_id"], original["menu_id"], user_id))
                    if patient_id:
                        cursor.execute("UPDATE patients SET is_active=FALSE WHERE id=%s", (patient_id,))
                    cursor.execute("DELETE FROM caregiver_selected_patients WHERE caregiver_user_id=%s", (user_id,))
                    if original.get("selected_patient_id"):
                        cursor.execute("INSERT INTO caregiver_selected_patients (caregiver_user_id,patient_id) VALUES (%s,%s)", (user_id, original["selected_patient_id"]))
                    cursor.execute("""UPDATE master_role_sessions SET active=FALSE, menu_pending=TRUE,
                                      updated_at=CURRENT_TIMESTAMP WHERE user_id=%s""", (user_id,))
                if disable:
                    cursor.execute("UPDATE master_role_sessions SET disabled=TRUE WHERE user_id=%s", (user_id,))
                if role is not None or active:
                    # In-progress forms and temporary group menus must not cross identities.
                    cursor.execute("DELETE FROM user_operation_states WHERE user_id=%s", (user_id,))
                    cursor.execute("DELETE FROM temporary_rich_menu_sessions WHERE line_user_id=%s", (line_id,))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        self._sync_menu_locked(line_id)

    def sync_menu(self, line_id):
        if not line_id:
            return
        # Avoid opening a lock connection for ordinary, non-master users.
        session = self.session(line_id)
        if not session or not session["menu_pending"]:
            return
        with self._account_lock(line_id):
            self._sync_menu_locked(line_id)

    def _sync_menu_locked(self, line_id):
        session = self.session(line_id)
        if not session or not session["menu_pending"]:
            return
        if session["active"]:
            menu = self.api["bind_role_rich_menu"](line_id, session["role"])
        else:
            menu = session["original"].get("menu_id")
            if menu:
                self.api["link_rich_menu"](line_id, menu)
                self.api["record_rich_menu_binding"](line_user_id=line_id, role=session["role"], line_rich_menu_id=menu, success=True)
            else:
                self.api["unlink_rich_menu"](line_id)
        connection = self.api["get_db_connection"]()
        try:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE app_users SET current_rich_menu_id=%s WHERE id=%s", (menu, session["user_id"]))
                cursor.execute("UPDATE master_role_sessions SET menu_pending=FALSE WHERE user_id=%s", (session["user_id"],))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def prepare(self, event):
        """Revoking the env allowlist also restores an active account on its next event."""
        line_id = self.api["get_user_id"](event)
        if not line_id:
            return False
        session = self.session(line_id)
        if session and session["active"] and not self.allowed(line_id):
            self._change(line_id)
            self.api["reply_text"](event.reply_token, "測試功能已關閉，已還原原始身份。請重新操作。")
            return True
        self.sync_menu(line_id)
        return False

    def handle_text(self, event):
        if self.prepare(event):
            return True
        text = (event.message.text or "").strip()
        line_id = self.api["get_user_id"](event)
        if text not in COMMANDS | {"我的 LINE ID"}:
            return False
        reply = self.api["reply_text"]
        if getattr(event.source, "type", None) != "user":
            reply(event.reply_token, "請在 Bot 私人聊天室操作身份切換。")
            return True
        if text == "我的 LINE ID":
            reply(event.reply_token, f"LINE User ID：{line_id}")
            return True
        session = self.session(line_id)
        # Restore remains possible after disable/revocation; never grants a role.
        restoring = text in {"還原身份", "停用身份切換"} and session and (session["active"] or session["menu_pending"])
        if not restoring and (not self.allowed(line_id) or (session and session["disabled"])):
            reply(event.reply_token, "此帳號未啟用身份切換。")
            return True
        if text == "切換身份":
            items = [self.api["postback_item"](label, f"action=master_switch_role&role={role}") for role, label in LABELS.items()]
            reply_message = self.api["make_quick_reply_message"]("選擇測試身份。長者／看護使用專用測試患者；家屬保留原本家庭權限。\n可輸入「還原身份」或「停用身份切換」。", items)
            self.api["reply_message"](event.reply_token, reply_message)
        elif text in {"還原身份", "停用身份切換"}:
            self._change(line_id, disable=text == "停用身份切換")
            reply(event.reply_token, "已還原原始身份與選單。" + ("身份切換已停用。" if text == "停用身份切換" else "測試紀錄保留於測試患者，可再次切換身份。"))
        elif text.startswith("切換為"):
            role = next(role for role, label in LABELS.items() if text == "切換為" + label)
            self._change(line_id, role)
            reply(event.reply_token, f"已切換為{LABELS[role]}。輸入「還原身份」即可返回。")
        else:
            user = self.api["get_user"](line_id)
            reply(event.reply_token, f"目前身份：{LABELS.get((user or {}).get('role'), '未設定')}\n測試狀態：{'使用中' if session and session['active'] else '尚未切換'}")
        return True

    def handle_postback(self, event, action, params):
        if self.prepare(event):
            return True
        line_id = self.api["get_user_id"](event)
        if action == "master_switch_role":
            # Reuse text-command authorization; never trust the postback payload.
            role = params.get("role", [None])[0]
            if role not in LABELS:
                raise RuntimeError("不支援的身份")
            from types import SimpleNamespace
            return self.handle_text(SimpleNamespace(source=event.source, reply_token=event.reply_token,
                                                   message=SimpleNamespace(text="切換為" + LABELS[role])))
        session = self.session(line_id) if line_id else None
        if session and session["active"]:
            if action == "select_role" or (session["role"] != "family" and
                (action or "").startswith(("elder_sos", "elder_discomfort", "caregiver_emergency", "caregiver_sos", "caregiver_report_issue"))):
                self.api["reply_text"](event.reply_token, "測試身份不發送緊急／異常通知。請用「切換身份」或「還原身份」操作。")
                return True
        return False
