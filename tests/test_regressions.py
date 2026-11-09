"""Regression tests for money/data safety. Never imports or writes the real database.
Run: python -m unittest discover -s tests -p 'test_regressions.py' -v
Concurrency tests force overlaps at read boundaries; no sleeps or network servers.
"""
import contextlib
import datetime as dt
import importlib.util
import io
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from unittest import mock

import openpyxl
from flask import request, template_rendered


class SafetyRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='task-regressions-')
        cls.work = Path(cls.temp.name)
        repo = Path(__file__).resolve().parents[1]
        for name in ('app.py', 'setup_database.py', 'templates', 'Static'):
            src = repo / name
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, cls.work / name)
        sys.path.insert(0, str(cls.work))
        spec = importlib.util.spec_from_file_location('regression_app', cls.work / 'app.py')
        cls.A = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(cls.A)
        cls.A.app.config['TESTING'] = True
        cls.baseline = cls.work / 'baseline.db'
        shutil.copy2(cls.A.DATABASE, cls.baseline)

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(cls.work))
        cls.temp.cleanup()

    def setUp(self):
        shutil.copy2(self.baseline, self.A.DATABASE)
        self.client = self.new_client()
        self.post('/start_shift')
        self.shift = self.scalar('SELECT MAX(shift_id) FROM Shifts')
        self.room = self.scalar("SELECT room_id FROM Rooms WHERE name='Shared'")
        self.tea = self.scalar("SELECT product_id FROM Products WHERE name='شاي'")
        self.pkg = self.scalar('SELECT MIN(type_id) FROM MembershipTypes')

    def new_client(self, role='manager'):
        c = self.A.app.test_client()
        with c.session_transaction() as session:
            session.update(user_id=1, username='review', role=role, _csrf_token='test')
        return c

    def query(self, sql, *args):
        with contextlib.closing(self.A.get_db()) as conn:
            return conn.execute(sql, args).fetchall()

    def scalar(self, sql, *args):
        return self.query(sql, *args)[0][0]

    def execute(self, sql, *args):
        with contextlib.closing(self.A.get_db()) as conn:
            cursor = conn.execute(sql, args)
            conn.commit()
            return cursor.lastrowid

    def post(self, path, **data):
        return self.client.post(path, data=dict(data, csrf_token='test'))

    def student(self, phone='01012345678', name='Review Student'):
        return self.execute('INSERT INTO Students(name,phone) VALUES (?,?)', name, phone)

    def visit(self, sid=None, minutes=30, reservation=None):
        sid = sid or self.student()
        start = (dt.datetime.now() - dt.timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S')
        return self.execute('''INSERT INTO Visits(student_id,user_id,shift_id,room_id,check_in_time,reservation_id)
                               VALUES (?,1,?,?,?,?)''', sid, self.shift, self.room, start, reservation)

    def invoice(self, v, query=''):
        captured = []
        def receive(sender, template, context, **extra):
            captured.append(context)
        with template_rendered.connected_to(receive, self.A.app):
            response = self.client.get(f'/show_invoice/{v}{query}')
        self.assertEqual(response.status_code, 200)
        return captured[-1], response.get_data(as_text=True)

    def import_file(self, rows, replace=True):
        wb = openpyxl.Workbook()
        wb.active.append(['name', 'phone'])
        for row in rows:
            wb.active.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        wb.close()
        buf.seek(0)
        return self.client.post('/import_students', data={
            'csrf_token': 'test', 'replace_all': '1' if replace else '0',
            'excel_file': (buf, 'students.xlsx')}, content_type='multipart/form-data')

    def sale(self, v, quantity=1, product=None):
        pid = product or self.tea
        self.post(f'/add_sale/{v}', **{f'quantity_{pid}': str(quantity)})
        return self.scalar('SELECT MAX(sale_id) FROM Sales')

    def card(self):
        return self.execute('''INSERT INTO Products(name,purchase_price,sale_price,stock_quantity)
                               VALUES ('كارت نت',1,5,100)''')

    def test_replace_rejects_empty_file(self):
        sid = self.student()
        self.import_file([])
        self.assertEqual(self.scalar('SELECT student_id FROM Students'), sid)
        self.assertIn('تم إلغاء الاستبدال', self.client.get('/import_report').get_data(as_text=True))

    def test_replace_rejects_all_invalid_rows(self):
        sid = self.student()
        self.import_file([['Bad number', '0101']])
        self.assertEqual(self.scalar('SELECT student_id FROM Students'), sid)

    def test_replace_rejects_partial_invalid_file_without_updates(self):
        sid = self.student()
        self.import_file([['New', '01200000001'], ['Bad number', '0101']])
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Students'), 1)
        self.assertEqual(self.scalar('SELECT student_id FROM Students'), sid)

    def test_replace_rejects_duplicates_without_deleting(self):
        sid = self.student()
        self.import_file([['A', '01200000001'], ['B', '01200000001']])
        self.assertEqual(self.scalar('SELECT student_id FROM Students'), sid)

    def test_append_still_imports_valid_rows_and_reports_errors(self):
        self.student()
        self.import_file([['New', '01200000001'], ['Bad', '0101']], replace=False)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Students'), 2)
        self.assertIn('ناقص', self.client.get('/import_report').get_data(as_text=True))

    def test_replace_makes_restorable_backup_and_keeps_history(self):
        old = self.student()
        keeper = self.student('01200000002', 'History')
        self.visit(keeper)
        self.import_file([['Fresh', '01200000003'], ['Updated', '01200000002']])
        backups = sorted((self.work / 'backups').glob('students-before-import-*.db'))
        self.assertTrue(backups)
        with contextlib.closing(sqlite3.connect(backups[-1])) as backup:
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(backup.execute('SELECT name FROM Students WHERE student_id=?', (old,)).fetchone()[0], 'Review Student')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Students'), 2)
        self.assertEqual(self.scalar('SELECT name FROM Students WHERE student_id=?', keeper), 'Updated')

    def test_backup_failure_does_not_delete_students(self):
        sid = self.student()
        with mock.patch.object(self.A.os, 'makedirs', side_effect=PermissionError('test backup failure')):
            with self.assertLogs(self.A.app.logger, level='ERROR'):
                self.import_file([['New', '01200000001']])
        self.assertEqual(self.scalar('SELECT student_id FROM Students'), sid)

    def test_unpaid_delete_does_not_reduce_closed_shift(self):
        v = self.visit()
        sale = self.sale(v)
        self.post('/end_shift')
        self.post(f'/delete_sale_item/{sale}')
        self.assertEqual(self.scalar('SELECT revenue_catering FROM Shifts WHERE shift_id=?', self.shift), 0)

    def test_unpaid_edit_does_not_change_closed_shift(self):
        v = self.visit()
        sale = self.sale(v)
        self.post('/end_shift')
        self.post(f'/edit_sale_quantity/{sale}', new_quantity=3)
        self.assertEqual(self.scalar('SELECT revenue_catering FROM Shifts WHERE shift_id=?', self.shift), 0)
        self.post('/start_shift')
        self.post(f'/confirm_payment/{v}', payment_method='cash')
        new_shift = self.scalar('SELECT MAX(shift_id) FROM Shifts')
        self.post('/end_shift')
        self.assertEqual(self.scalar('SELECT revenue_catering FROM Shifts WHERE shift_id=?', new_shift), 15)

    def test_paid_correction_changes_catering_not_net(self):
        v = self.visit()
        sale = self.sale(v)
        self.post(f'/confirm_payment/{v}', payment_method='cash')
        self.post('/end_shift')
        self.post(f'/edit_sale_quantity/{sale}', new_quantity=3)
        row = self.query('SELECT net_cash,revenue_catering FROM Shifts WHERE shift_id=?', self.shift)[0]
        self.assertEqual(tuple(row), (10, 15))
        self.post(f'/delete_sale_item/{sale}')
        self.assertEqual(self.scalar('SELECT revenue_catering FROM Shifts WHERE shift_id=?', self.shift), 0)

    def test_stale_form_cannot_add_sale_to_paid_visit(self):
        v = self.visit()
        self.client.get(f'/add_sale/{v}')
        self.post(f'/confirm_payment/{v}', payment_method='cash')
        stock = self.scalar('SELECT stock_quantity FROM Products WHERE product_id=?', self.tea)
        self.sale(v)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Sales WHERE visit_id=?', v), 0)
        self.assertEqual(self.scalar('SELECT stock_quantity FROM Products WHERE product_id=?', self.tea), stock)

    def test_nonexistent_visit_cannot_create_orphan_sale(self):
        self.sale(9999)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Sales'), 0)

    def test_delete_free_cards_redistributes_allowance(self):
        v = self.visit(); card = self.card()
        first = self.sale(v, 2, card)
        self.sale(v, 1, card)
        self.post(f'/delete_sale_item/{first}')
        self.assertEqual(self.scalar('SELECT SUM(total_price) FROM Sales WHERE visit_id=?', v), 0)
        self.assertEqual(self.scalar('SELECT SUM(quantity) FROM Sales WHERE visit_id=?', v), 1)

    def test_edit_free_cards_reprices_all_lines(self):
        v = self.visit(); card = self.card()
        first = self.sale(v, 2, card)
        self.sale(v, 2, card)
        self.post(f'/edit_sale_quantity/{first}', new_quantity=1)
        self.assertEqual(self.scalar('SELECT SUM(total_price) FROM Sales WHERE visit_id=?', v), 5)
        self.post(f'/edit_sale_quantity/{first}', new_quantity=3)
        self.assertEqual(self.scalar('SELECT SUM(total_price) FROM Sales WHERE visit_id=?', v), 15)

    def test_paid_card_correction_updates_shift_and_keeps_original_prices(self):
        v = self.visit(); card = self.card()
        first = self.sale(v, 2, card)
        self.sale(v, 2, card)
        self.post(f'/confirm_payment/{v}', payment_method='cash')
        self.post('/end_shift')
        self.execute('UPDATE Products SET sale_price=50 WHERE product_id=?', card)
        self.execute("UPDATE Settings SET setting_value='0' WHERE setting_key='internet_free_limit'")
        self.post(f'/delete_sale_item/{first}')
        self.assertEqual(self.scalar('SELECT revenue_catering FROM Shifts WHERE shift_id=?', self.shift), 0)
        self.assertEqual(self.scalar('SELECT SUM(total_price) FROM Sales WHERE visit_id=?', v), 0)

    def test_paid_invoice_uses_stored_duration_and_discount(self):
        v = self.visit(minutes=120)
        end = (dt.datetime.now() - dt.timedelta(minutes=90)).strftime('%Y-%m-%d %H:%M:%S')
        self.execute('''UPDATE Visits SET check_out_time=?,duration_hours=0.5,total_cost=10,
                        total_discount=5,manual_discount=5 WHERE visit_id=?''', end, v)
        ctx, html = self.invoice(v, '?m_type=fixed&m_value=99')
        self.assertEqual(ctx['visit_details']['final_total'], 5)
        self.assertEqual(ctx['visit_details']['duration_hours'], 0.5)
        self.assertNotIn(f'action="/confirm_payment/{v}"', html)
        self.assertNotIn(f'action="/apply_coupon/{v}"', html)

    def test_subscription_receipt_does_not_charge_time_again(self):
        sid = self.student(); v = self.visit(sid)
        self.post('/sell_subscription', confirm_sub=1, student_id=sid, type_id=self.pkg)
        self.sale(v)
        self.post(f'/confirm_payment/{v}', payment_method='subscription')
        ctx, _ = self.invoice(v)
        self.assertEqual(ctx['visit_details']['total_time_cost'], 0)
        self.assertEqual(ctx['visit_details']['final_total'], 5)

    def test_coupon_preview_matches_undiscounted_reservation_payment(self):
        sid = self.student()
        res = self.execute('''INSERT INTO Reservations(room_id,client_name,phone,start_time,end_time,
                            discount_type,discount_value,status) VALUES (?,'Review','01012345678',
                            '2026-01-01 10:00:00','2026-01-01 11:00:00','none',0,'active')''', self.room)
        v = self.visit(sid, reservation=res)
        self.execute("INSERT INTO Coupons(code,discount_type,discount_value,student_id) VALUES ('TEST5','fixed',5,?)", sid)
        ctx, _ = self.invoice(v, '?applied_code=TEST5')
        self.post(f'/confirm_payment/{v}', payment_method='cash', applied_coupon_code='TEST5')
        self.assertEqual(ctx['discount_info']['amount'], 5)
        self.assertEqual(self.scalar('SELECT total_discount FROM Visits WHERE visit_id=?', v), 5)
        self.assertEqual(self.scalar("SELECT is_used FROM Coupons WHERE code='TEST5'"), 1)

    def test_invalid_coupon_cannot_reappear_in_preview(self):
        sid = self.student(); v = self.visit(sid)
        self.execute("INSERT INTO Coupons(code,discount_type,discount_value,is_used) VALUES ('USED','fixed',5,1)")
        ctx, _ = self.invoice(v, '?applied_code=USED')
        self.assertEqual(ctx['discount_info']['amount'], 0)

    def test_cleanup_keeps_borrower_and_rejects_get(self):
        self.student(name='Same Name')
        second = self.student('01012345678'[::-1], 'Same Name')
        book = self.execute("INSERT INTO Books(title,price) VALUES ('Test Book',100)")
        self.post('/borrow_book', student_phone='01012345678'[::-1], book_id=book)
        self.assertEqual(self.client.get('/clean_duplicates').status_code, 405)
        self.post('/clean_duplicates')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Students WHERE student_id=?', second), 1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Borrowings WHERE student_id=?', second), 1)

    def test_future_manual_entry_rejected(self):
        sid = self.student()
        future = dt.datetime.now() + dt.timedelta(days=1)
        self.post('/perform_check_in', student_id=sid, room_id=self.room,
                  manual_date=future.strftime('%Y-%m-%d'), manual_time=future.strftime('%H:%M'))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Visits'), 0)

    def test_existing_future_visit_cannot_credit_subscription(self):
        sid = self.student(); v = self.visit(sid, minutes=-60)
        self.post('/sell_subscription', confirm_sub=1, student_id=sid, type_id=self.pkg)
        before = self.scalar('SELECT remaining_hours FROM StudentSubscriptions')
        self.post(f'/confirm_payment/{v}', payment_method='subscription')
        self.assertEqual(self.scalar('SELECT remaining_hours FROM StudentSubscriptions'), before)
        self.assertIsNone(self.scalar('SELECT check_out_time FROM Visits WHERE visit_id=?', v))

    def test_error_after_subscription_debit_rolls_back_everything(self):
        sid = self.student(); v = self.visit(sid)
        self.post('/sell_subscription', confirm_sub=1, student_id=sid, type_id=self.pkg)
        before = self.scalar('SELECT remaining_hours FROM StudentSubscriptions')
        class FailVisitUpdate(sqlite3.Cursor):
            def execute(self, sql, *args):
                if 'UPDATE Visits SET' in sql:
                    raise ValueError('Injected after subscription debit')
                return super().execute(sql, *args)
        def cursor(conn):
            return sqlite3.Connection.cursor(conn, factory=FailVisitUpdate)
        with mock.patch.object(self.A.RequestConnection, 'cursor', cursor):
            with self.assertLogs(self.A.app.logger, level='ERROR'):
                self.post(f'/confirm_payment/{v}', payment_method='subscription')
        self.assertEqual(self.scalar('SELECT remaining_hours FROM StudentSubscriptions'), before)
        self.assertIsNone(self.scalar('SELECT check_out_time FROM Visits WHERE visit_id=?', v))

    def test_malformed_multi_item_sale_does_not_commit_partial_sale(self):
        v = self.visit()
        products = self.query('SELECT product_id FROM Products ORDER BY product_id')
        self.assertGreaterEqual(len(products), 2)
        with self.assertLogs(self.A.app.logger, level='ERROR'):
            self.post(f'/add_sale/{v}', **{f'quantity_{products[0][0]}':'1', f'quantity_{products[1][0]}':'bad'})
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Sales'), 0)

    def test_rejected_reservation_does_not_save_partial_student(self):
        occupant = self.student()
        private = self.scalar("SELECT room_id FROM Rooms WHERE name='Private'")
        v = self.visit(occupant)
        self.execute('UPDATE Visits SET room_id=? WHERE visit_id=?', private, v)
        res = self.execute("""INSERT INTO Reservations(room_id,client_name,phone,start_time,end_time,
                            discount_type,discount_value,status) VALUES (?,'New Person','01200000099',
                            '2026-01-01 10:00:00','2026-01-01 11:00:00','none',0,'confirmed')""", private)
        self.post(f'/start_reservation_visit/{res}')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Students'), 1)
        self.assertEqual(self.scalar('SELECT status FROM Reservations WHERE reservation_id=?', res), 'confirmed')

    def test_reset_keeps_master_data_without_second_connection_lock(self):
        self.student()
        self.post('/reset_data_keep_users')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Visits'), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Shifts'), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Students'), 1)
        self.assertGreater(self.scalar('SELECT COUNT(*) FROM Products'), 0)

    def test_migration_adds_snapshots_without_rewriting_sales(self):
        v = self.visit(); self.sale(v)
        self.execute('ALTER TABLE Sales DROP COLUMN unit_price')
        self.execute('ALTER TABLE Sales DROP COLUMN free_limit')
        self.execute('ALTER TABLE Shifts DROP COLUMN revenue_catering')
        self.A.run_startup_migrations()
        self.A.run_startup_migrations()
        self.assertEqual(self.scalar('SELECT total_price FROM Sales'), 5)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Students'), 1)
        self.assertIsNone(self.scalar('SELECT unit_price FROM Sales'))

    def test_coupon_and_manual_discount_match_preview(self):
        sid = self.student(); v = self.visit(sid)
        self.execute("INSERT INTO Coupons(code,discount_type,discount_value) VALUES ('FIVE','fixed',5)")
        ctx, _ = self.invoice(v, '?applied_code=FIVE&m_type=percentage&m_value=50')
        self.post(f'/confirm_payment/{v}', payment_method='cash', applied_coupon_code='FIVE',
                  manual_disc_type='percentage', manual_disc_value=50)
        self.assertEqual(ctx['visit_details']['final_total'], 0)
        self.assertEqual(self.scalar('SELECT total_discount FROM Visits WHERE visit_id=?', v), 10)
        self.assertEqual(self.scalar('SELECT manual_discount FROM Visits WHERE visit_id=?', v), 5)

    def test_reservation_discount_has_priority_and_manual_is_capped(self):
        sid = self.student()
        res = self.execute("""INSERT INTO Reservations(room_id,client_name,phone,start_time,end_time,
                            discount_type,discount_value,status) VALUES (?,'Review','01012345678',
                            '2026-01-01 10:00:00','2026-01-01 11:00:00','percentage',20,'active')""", self.room)
        v = self.visit(sid, reservation=res)
        self.execute("INSERT INTO Coupons(code,discount_type,discount_value) VALUES ('FIVE','fixed',5)")
        ctx, _ = self.invoice(v, '?applied_code=FIVE&m_type=fixed&m_value=30')
        self.post(f'/confirm_payment/{v}', payment_method='cash', applied_coupon_code='FIVE',
                  manual_disc_type='fixed', manual_disc_value=30)
        self.assertEqual(ctx['discount_info']['amount'], 2)
        self.assertEqual(self.scalar('SELECT manual_discount FROM Visits WHERE visit_id=?', v), 8)
        self.assertEqual(self.scalar("SELECT is_used FROM Coupons WHERE code='FIVE'"), 0)

    def test_sqlite_and_unhandled_errors_rollback_partial_debit(self):
        sid = self.student(); v = self.visit(sid)
        self.post('/sell_subscription', confirm_sub=1, student_id=sid, type_id=self.pkg)
        before = self.scalar('SELECT remaining_hours FROM StudentSubscriptions')
        for error in (sqlite3.IntegrityError, RuntimeError):
            with self.subTest(error=error.__name__):
                class FailUpdate(sqlite3.Cursor):
                    def execute(self, sql, *args):
                        if 'UPDATE Visits SET' in sql:
                            raise error('Injected after debit')
                        return super().execute(sql, *args)
                def cursor(conn):
                    return sqlite3.Connection.cursor(conn, factory=FailUpdate)
                with mock.patch.object(self.A.RequestConnection, 'cursor', cursor):
                    if error is RuntimeError:
                        with self.assertRaises(RuntimeError):
                            self.post(f'/confirm_payment/{v}', payment_method='subscription')
                    else:
                        with self.assertLogs(self.A.app.logger, level='ERROR'):
                            self.post(f'/confirm_payment/{v}', payment_method='subscription')
                self.assertEqual(self.scalar('SELECT remaining_hours FROM StudentSubscriptions'), before)
                self.assertIsNone(self.scalar('SELECT check_out_time FROM Visits WHERE visit_id=?', v))

    def test_concurrent_stock_sale_cannot_oversell_last_item(self):
        v = self.visit(); card = self.card()
        self.execute('UPDATE Products SET stock_quantity=1 WHERE product_id=?', card)
        self.overlap(f'/add_sale/{v}', {f'quantity_{card}':'1'},
                     f'/add_sale/{v}', {f'quantity_{card}':'1'}, 'prepare_card_prices')
        self.assertEqual(self.scalar('SELECT SUM(quantity) FROM Sales WHERE product_id=?', card), 1)
        self.assertEqual(self.scalar('SELECT stock_quantity FROM Products WHERE product_id=?', card), 0)

    def test_concurrent_start_creates_only_one_shift(self):
        self.execute('DELETE FROM Shifts')
        self.overlap('/start_shift', {}, '/start_shift', {}, 'get_open_shift_id')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Shifts WHERE end_time IS NULL'), 1)

    def test_concurrent_book_return_refunds_once(self):
        self.student()
        book = self.execute("INSERT INTO Books(title,price) VALUES ('Test Book',100)")
        self.post('/borrow_book', student_phone='01012345678', book_id=book)
        borrow = self.scalar('SELECT MAX(borrow_id) FROM Borrowings')
        self.overlap(f'/return_book/{borrow}', {}, f'/return_book/{borrow}', {}, 'get_open_shift_id')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM Expenses'), 1)
        self.assertEqual(self.scalar('SELECT SUM(amount) FROM Expenses'), 95)

    def overlap(self, first_path, first_data, second_path, second_data, hook):
        """Pause request 1 after its state read, start request 2, then release 1.
        Assert request 2 attempted DB access but cannot complete ahead of 1.
        """
        first_ready, second_entered, release = (threading.Event() for _ in range(3))
        original_hook = getattr(self.A, hook)
        original_db = self.A.get_db
        def pause(*args, **kwargs):
            value = original_hook(*args, **kwargs)
            if request.headers.get('X-Test') == 'first':
                first_ready.set()
                if not release.wait(5):
                    raise TimeoutError('First request was not released')
            return value
        def observe_db():
            if request.headers.get('X-Test') == 'second':
                second_entered.set()
            return original_db()
        clients = [self.new_client(), self.new_client()]
        def send(index, path, data):
            return clients[index].post(path, data=dict(data, csrf_token='test'),
                                       headers={'X-Test': 'first' if index == 0 else 'second'})
        with mock.patch.object(self.A, hook, pause), mock.patch.object(self.A, 'get_db', observe_db):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(send, 0, first_path, first_data)
                try:
                    self.assertTrue(first_ready.wait(5))
                    second = pool.submit(send, 1, second_path, second_data)
                    self.assertTrue(second_entered.wait(5))
                    with self.assertRaises(FutureTimeout):
                        second.result(timeout=0.15)
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=10).status_code, 302)
                self.assertEqual(second.result(timeout=10).status_code, 302)

    def test_concurrent_payment_deducts_subscription_once(self):
        sid = self.student(); v = self.visit(sid, minutes=60)
        self.post('/sell_subscription', confirm_sub=1, student_id=sid, type_id=self.pkg)
        before = self.scalar('SELECT remaining_hours FROM StudentSubscriptions')
        self.overlap(f'/confirm_payment/{v}', {'payment_method':'subscription'},
                     f'/confirm_payment/{v}', {'payment_method':'subscription'}, 'calculate_dynamic_cost')
        deducted = before - self.scalar('SELECT remaining_hours FROM StudentSubscriptions')
        duration = self.scalar('SELECT duration_hours FROM Visits WHERE visit_id=?', v)
        self.assertAlmostEqual(deducted, duration, places=6)

    def test_close_before_payment_blocks_late_collection(self):
        v = self.visit()
        self.overlap('/end_shift', {}, f'/confirm_payment/{v}', {'payment_method':'cash'}, 'compute_shift_totals')
        self.assertIsNone(self.scalar('SELECT check_out_time FROM Visits WHERE visit_id=?', v))
        self.assertEqual(self.scalar('SELECT net_cash FROM Shifts WHERE shift_id=?', self.shift), 0)

    def test_payment_before_close_is_included_in_shift(self):
        v = self.visit()
        self.overlap(f'/confirm_payment/{v}', {'payment_method':'cash'}, '/end_shift', {}, 'calculate_dynamic_cost')
        self.assertIsNotNone(self.scalar('SELECT check_out_time FROM Visits WHERE visit_id=?', v))
        self.assertEqual(self.scalar('SELECT net_cash FROM Shifts WHERE shift_id=?', self.shift), 10)


if __name__ == '__main__':
    unittest.main()
