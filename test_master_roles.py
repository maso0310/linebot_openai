"""State-transition tests; all DB and LINE operations stay offline."""
import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from master_roles import MasterRoles


OWNER = "U" + "1" * 32


class Database:
    def __init__(self):
        self.user = ["owner", "role-family", "lang-zh-TW", "original-menu"]
        self.session = None
        self.patient_active = False
        self.selected = "original-selected-patient"
        self.operation = True
        self.temporary = True
        self.statements = []

    def connect(self):
        return Connection(self)


class Connection:
    def __init__(self, db):
        self.db = db
        self.snapshot = copy.deepcopy(db.__dict__)

    def cursor(self):
        return Cursor(self.db)

    def commit(self):
        self.snapshot = copy.deepcopy(self.db.__dict__)

    def rollback(self):
        self.db.__dict__.update(copy.deepcopy(self.snapshot))

    def close(self):
        pass


class Cursor:
    def __init__(self, db):
        self.db = db
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def fetchone(self):
        return copy.deepcopy(self.row)

    def execute(self, sql, args=()):
        import json
        sql = " ".join(sql.split())
        db = self.db
        db.statements.append((sql, args))
        self.row = None
        if sql.startswith("SELECT pg_advisory_lock"):
            pass
        elif sql.startswith("SELECT s.active"):
            if db.session:
                s = db.session
                self.row = (s['active'], s['disabled'], s['pending'], s['original'], s['patient'],
                            db.user[0], 'Owner', db.user[1].removeprefix('role-'), 'Test patient')
        elif sql.startswith("SELECT id, role_id"):
            self.row = db.user if args == (OWNER,) else None
        elif sql.startswith("INSERT INTO master_role_sessions"):
            if db.session is None:
                db.session = dict(original=None, active=False, disabled=False, patient=None, pending=False)
        elif sql.startswith("SELECT original_settings"):
            s = db.session
            self.row = (s['original'], s['active'], s['disabled'], s['patient'])
        elif sql.startswith("SELECT id FROM roles"):
            self.row = ('role-' + args[0],)
        elif sql.startswith("SELECT id FROM languages"):
            self.row = ('lang-' + args[0],)
        elif sql.startswith("SELECT patient_id FROM caregiver_selected"):
            self.row = (db.selected,) if db.selected else None
        elif sql.startswith("INSERT INTO patients"):
            assert 'linked_user_id' not in sql
            db.patient_active = True
            self.row = ('test-patient',)
        elif sql.startswith("UPDATE patients"):
            db.patient_active = 'is_active=TRUE' in sql
        elif sql.startswith("UPDATE app_users SET role_id"):
            db.user[1:3] = args[:2]
            if 'current_rich_menu_id' in sql:
                db.user[3] = args[2]
        elif sql.startswith("UPDATE app_users SET current_rich_menu_id"):
            db.user[3] = args[0]
        elif sql.startswith("UPDATE master_role_sessions SET original_settings"):
            db.session.update(original=json.loads(args[0]), patient=args[1], active=True, pending=True)
        elif sql.startswith("UPDATE master_role_sessions SET active=FALSE"):
            db.session.update(active=False, pending=True)
        elif sql.startswith("UPDATE master_role_sessions SET disabled=TRUE"):
            db.session['disabled'] = True
        elif sql.startswith("UPDATE master_role_sessions SET menu_pending=FALSE"):
            db.session['pending'] = False
        elif sql.startswith("DELETE FROM caregiver_selected"):
            db.selected = None
        elif sql.startswith("INSERT INTO caregiver_selected"):
            db.selected = args[1]
        elif sql.startswith("DELETE FROM user_operation_states"):
            db.operation = False
        elif sql.startswith("DELETE FROM temporary_rich_menu_sessions"):
            db.temporary = False
        else:
            raise AssertionError('Unexpected SQL: ' + sql)


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, MASTER_ROLE_SWITCH_ENABLED='true', MASTER_LINE_USER_IDS=OWNER)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = Database()
        self.replies = []
        self.menus = []
        self.fail_menu = False
        def bind(line_id, role):
            if self.fail_menu:
                raise RuntimeError('LINE unavailable')
            self.menus.append(role)
            return 'menu-' + role
        self.api = dict(get_db_connection=self.db.connect, bind_role_rich_menu=bind,
                        link_rich_menu=lambda uid, menu: self.menus.append(menu),
                        unlink_rich_menu=lambda uid: self.menus.append(None),
                        record_rich_menu_binding=lambda **kw: None,
                        get_user_id=lambda e: getattr(e.source, 'user_id', None),
                        reply_text=lambda token, text: self.replies.append(text),
                        get_user=lambda uid: {'role': self.db.user[1].removeprefix('role-')})
        self.roles = MasterRoles(self.api)

    def event(self, text='', user=OWNER, source='user'):
        return SimpleNamespace(source=SimpleNamespace(type=source, user_id=user),
                               message=SimpleNamespace(text=text), reply_token='reply')

    def test_switch_all_roles_then_restore_exact_original(self):
        initial = copy.deepcopy(self.db.user)
        for role in ('elderly', 'caregiver', 'family', 'elderly'):
            self.roles._change(OWNER, role)
            self.assertEqual(self.db.session['original']['role_id'], initial[1])
        self.assertFalse(self.db.operation)
        self.assertFalse(self.db.temporary)
        self.assertEqual(self.roles.test_patient(OWNER, 'elderly')['patient_id'], 'test-patient')
        self.assertIsNone(self.roles.test_patient(OWNER, 'caregiver'))
        self.roles._change(OWNER)
        self.assertEqual(self.db.user, initial)
        self.assertEqual(self.db.selected, 'original-selected-patient')
        self.assertFalse(self.db.patient_active)
        self.assertFalse(self.db.session['active'])
        self.assertEqual(self.menus[-1], 'original-menu')

    def test_unauthorized_switch_does_not_touch_database(self):
        with self.assertRaises(RuntimeError):
            self.roles._change('another-account', 'elderly')
        self.assertEqual(self.db.statements, [])

    def test_disabled_survives_new_instance_and_old_button(self):
        self.roles._change(OWNER, 'elderly')
        self.roles._change(OWNER, disable=True)
        restarted = MasterRoles(self.api)
        self.assertTrue(restarted.handle_postback(self.event(), 'master_switch_role', {'role': ['caregiver']}))
        self.assertEqual(self.db.user[1], 'role-family')
        self.assertTrue(self.db.session['disabled'])

    def test_menu_failure_does_not_lose_restore_snapshot(self):
        self.fail_menu = True
        with self.assertRaises(RuntimeError):
            self.roles._change(OWNER, 'elderly')
        self.assertTrue(self.db.session['pending'])
        self.assertEqual(self.db.session['original']['menu_id'], 'original-menu')
        self.fail_menu = False
        MasterRoles(self.api).sync_menu(OWNER)
        self.assertFalse(self.db.session['pending'])
        self.roles._change(OWNER)
        self.assertEqual(self.db.user[3], 'original-menu')

    def test_environment_revocation_restores_on_next_event(self):
        self.roles._change(OWNER, 'caregiver')
        os.environ['MASTER_ROLE_SWITCH_ENABLED'] = 'false'
        self.assertTrue(self.roles.prepare(self.event()))
        self.assertEqual(self.db.user[1], 'role-family')
        self.assertFalse(self.db.session['active'])

    def test_allowlist_removal_also_restores(self):
        self.roles._change(OWNER, 'elderly')
        os.environ['MASTER_LINE_USER_IDS'] = ''
        self.roles.prepare(self.event())
        self.assertEqual(self.db.user[1], 'role-family')

    def test_group_cannot_switch_or_show_id(self):
        for command in ('切換為長者', '我的 LINE ID'):
            self.assertTrue(self.roles.handle_text(self.event(command, source='group')))
        self.assertIsNone(self.db.session)
        self.assertNotIn(OWNER, ''.join(self.replies))

    def test_forged_role_rejected(self):
        with self.assertRaises(RuntimeError):
            self.roles.handle_postback(self.event(), 'master_switch_role', {'role': ['admin']})
        self.assertIsNone(self.db.session)

    def test_testing_does_not_send_emergency_notifications(self):
        self.roles._change(OWNER, 'caregiver')
        for action in ('caregiver_sos_notify_all', 'caregiver_report_issue', 'caregiver_emergency'):
            self.assertTrue(self.roles.handle_postback(self.event(), action, {}))

    def test_unrecognized_messages_continue_existing_handler(self):
        self.assertFalse(self.roles.handle_text(self.event('hello')))

    def test_sql_error_rolls_back_changes(self):
        initial = copy.deepcopy(self.db.user)
        original_execute = Cursor.execute
        def fail(cursor, sql, args=()):
            if 'UPDATE master_role_sessions SET original_settings' in sql:
                raise RuntimeError('database error')
            return original_execute(cursor, sql, args)
        with patch.object(Cursor, 'execute', fail), self.assertRaises(RuntimeError):
            self.roles._change(OWNER, 'elderly')
        self.assertEqual(self.db.user, initial)
        self.assertIsNone(self.db.session)


if __name__ == '__main__':
    unittest.main()
