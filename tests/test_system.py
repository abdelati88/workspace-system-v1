"""
اختبار شامل للسيستم على نسخة مؤقتة (داتا بيز جديدة فاضية) من غير ما يلمس الداتا الحقيقية.

الاستخدام:  venv/bin/python tests/test_system.py
"""
import datetime
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = tempfile.mkdtemp(prefix='task-test-')
for item in ('app.py', 'setup_database.py', 'templates', 'Static'):
    src = os.path.join(REPO, item)
    (shutil.copytree if os.path.isdir(src) else shutil.copy)(src, os.path.join(WORK, item))
sys.path.insert(0, WORK)

import app as A  # noqa: E402
import openpyxl  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

A.app.config['TESTING'] = True
results = []


def check(name, cond, detail=''):
    results.append(cond)
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail else ''))


def q(sql, *args):
    conn = A.get_db()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def run(sql, *args):
    conn = A.get_db()
    cur = conn.execute(sql, args)
    conn.commit()
    conn.close()
    return cur.lastrowid


def login(user):
    client = A.app.test_client()
    with client.session_transaction() as s:
        s['_csrf_token'] = 't'
    client.post('/login', data={'username': user, 'password': 'pw', 'csrf_token': 't'})
    return client


def post(client, url, **data):
    data['csrf_token'] = 't'
    return client.post(url, data=data)


def checkin(client, student_id, room, minutes_ago=30):
    t = datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago)
    post(client, '/perform_check_in', student_id=student_id, room_id=ROOMS[room],
         manual_time=t.strftime('%H:%M'), manual_date=t.strftime('%Y-%m-%d'))
    return q("SELECT MAX(visit_id) FROM Visits")[0]


for user, role in (('boss', 'manager'), ('emp', 'employee'), ('emp2', 'employee')):
    run("INSERT INTO Users (username, password, role) VALUES (?, ?, ?)",
        user, generate_password_hash('pw', method='pbkdf2:sha256'), role)
ROOMS = {r['name']: r['room_id'] for r in A.get_db().execute("SELECT room_id, name FROM Rooms")}
TEA = q("SELECT product_id FROM Products WHERE name='شاي'")[0]
COFFEE = q("SELECT product_id FROM Products WHERE name='قهوة'")[0]

# ---------------------------------------------------------------- pricing
check('tier: 70 min inside grace = 10', A.calculate_dynamic_cost(70 / 60, 'Shared', 10) == 10)
check('tier: 80 min = 3h tier 25', A.calculate_dynamic_cost(80 / 60, 'Shared', 10) == 25)
check('private: 70 min -> 1.5h = 15', A.calculate_dynamic_cost(70 / 60, 'Private', 10) == 15)

# ---------------------------------------------------------------- search / registration loop
s1 = run("INSERT INTO Students (name, phone, college) VALUES ('أحمد محمد علي', '1012345678', 'X')")   # old import: no leading 0
run("INSERT INTO Students (name, phone, college) VALUES ('فاطمة حسن', '010 9999 8888', 'X')")
emp = login('emp')
r = emp.get('/check_in?search_query=01012345678')
check('check-in finds phone saved without leading zero', f'/select_room/{s1}' in r.headers.get('Location', ''))
r = emp.get('/check_in?search_query=احمد علي')
check('check-in finds name typed without hamza / out of order', f'/select_room/{s1}' in r.headers.get('Location', ''))
r = emp.get('/check_in?search_query=فاطمه')
check('check-in: ه matches ة', '/select_room/' in r.headers.get('Location', ''))
r = emp.get('/check_in?search_query=٠١٠٩٩٩٩٨٨٨٨')
check('check-in: Arabic digits + spaces stored', '/select_room/' in r.headers.get('Location', ''))
r = post(emp, '/register_student', name='Someone', phone='0101 234 5678', college='', year='', next_url='select_room')
check('register with existing phone continues with that student (no loop)',
      f'/select_room/{s1}' in r.headers.get('Location', '') and q("SELECT COUNT(*) FROM Students")[0] == 2)
r = post(emp, '/register_student', name='  New   Guy ', phone='١٠٥٥٥٤٤٤٣٣', college='C', year='1', next_url='')
check('new student saved with clean phone', q("SELECT phone FROM Students WHERE name='New Guy'")[0] == '01055544433')
check('old POST check-in does not crash', post(emp, '/check_in', search_query='x').status_code == 200)

# ---------------------------------------------------------------- shift with catering split
post(emp, '/start_shift')
shift1 = q("SELECT MAX(shift_id) FROM Shifts")[0]
v1 = checkin(emp, s1, 'Shared', 30)                                  # 10 EGP time
post(emp, f'/add_sale/{v1}', **{f'quantity_{TEA}': 2})               # 10 catering (visit)
post(emp, '/add_sale/0', **{f'quantity_{COFFEE}': 1})                 # 10 catering (cash)
pkg = q("SELECT * FROM MembershipTypes ORDER BY price LIMIT 1")
s3 = q("SELECT student_id FROM Students WHERE name='New Guy'")[0]
post(emp, '/sell_subscription', confirm_sub='1', student_id=s3, type_id=pkg['type_id'])   # package
post(emp, '/log_expense', description='x', amount='5')

# no discount cap for employees (amount and percentage)
v2 = checkin(emp, 2, 'Shared', 200)                                    # 35 EGP (6h tier)
r = post(emp, f'/apply_manual_discount/{v2}', manual_type='fixed', manual_value='30')
page = emp.get(r.headers['Location']).get_data(as_text=True)
check('invoice shows 30 EGP employee discount', 'خصم خاص: 30.00' in page)
post(emp, f'/confirm_payment/{v2}', payment_method='cash', manual_disc_type='fixed', manual_disc_value='30')
check('employee discount not capped (30)', q("SELECT total_discount FROM Visits WHERE visit_id=?", v2)[0] == 30)
v1_pay = post(emp, f'/confirm_payment/{v1}', payment_method='cash', manual_disc_type='percentage', manual_disc_value='50')
check('employee 50% discount applied', q("SELECT total_discount FROM Visits WHERE visit_id=?", v1)[0] == 5)

preview = emp.get('/shift_summary_preview').get_data(as_text=True)
# time 10 + 35, package, minus expense 5, minus discounts 35 ; catering 20 separate
exp_net = 10 + 35 + pkg['price'] - 5 - 35
check('preview shows net without catering', f'{exp_net:.2f}' in preview and 'الكافيتريا (لوحدها)' in preview)
r = post(emp, '/end_shift')
check('end shift redirects to report page (no resubmit error on Back)', f'/shift_report/{shift1}' in r.headers.get('Location', ''))
S = q("SELECT * FROM Shifts WHERE shift_id=?", shift1)
check('stored net excludes catering', S['net_cash'] == exp_net, f"{S['net_cash']} vs {exp_net}")
check('stored catering = 20', S['revenue_catering'] == 20, S['revenue_catering'])
check('stored revenue_sales = package only', S['revenue_sales'] == pkg['price'])
report = emp.get(f'/shift_report/{shift1}').get_data(as_text=True)
check('shift report page shows both totals', f'{exp_net:.2f}' in report and '20.00' in report and f'{exp_net + 20:.2f}' in report)
check('whatsapp text has catering line', 'whatsapp' in report and '%D8%A7%D9%84%D9%83%D8%A7%D9%81%D9%8A%D8%AA%D8%B1%D9%8A%D8%A7' in report)
check('other employee cannot open the report', login('emp2').get(f'/shift_report/{shift1}').status_code == 302)

boss = login('boss')
fin = boss.get('/financial_reports').get_data(as_text=True)
check('financial report: day net without catering + catering card', f'{exp_net:.2f}' in fin and '20.00' in fin)

# manager edits a catering item after close: catering changes, net does not
sale_id = q("SELECT sale_id FROM Sales WHERE visit_id=?", v1)[0]
post(boss, f'/edit_sale_quantity/{sale_id}', new_quantity=4)
S2 = q("SELECT * FROM Shifts WHERE shift_id=?", shift1)
check('closed-shift catering edit: catering +10, net unchanged',
      S2['revenue_catering'] == 30 and S2['net_cash'] == exp_net, f"{S2['revenue_catering']} / {S2['net_cash']}")
legacy = run("INSERT INTO Shifts (user_id, start_time, end_time, revenue_sales, net_cash) VALUES (1, '2026-01-01 08:00:00', '2026-01-01 16:00:00', 50, 50)")
legacy_sale = run("INSERT INTO Sales (product_id, user_id, shift_id, quantity, total_price) VALUES (?, 1, ?, 1, 5)", TEA, legacy)
post(boss, f'/delete_sale_item/{legacy_sale}')
L = q("SELECT * FROM Shifts WHERE shift_id=?", legacy)
check('old shift (before split) keeps old behaviour', L['net_cash'] == 45 and L['revenue_catering'] is None)

# ---------------------------------------------------------------- Back / home / cache
for url, data in (('/manage_settings', {'internet_free_limit': '2', 'tier_grace_minutes': '15'}),
                  ('/manage_products', {'name': 'عصير', 'purchase_price': '5', 'sale_price': '10', 'stock_quantity': '10'}),
                  ('/manage_users', {'username': 'x1', 'password': 'p', 'role': 'employee'}),
                  ('/manage_rooms', {'name': 'Shared 3', 'hourly_rate': '10'})):
    check(f'POST {url} redirects', post(boss, url, **data).status_code == 302)
st = (datetime.datetime.now() + datetime.timedelta(days=2)).replace(hour=10, minute=0)
r = post(boss, '/reservations', room_id=ROOMS['Private'], client_name='Co', phone='01011',
         start_time=st.strftime('%Y-%m-%dT%H:%M'), end_time=(st + datetime.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
         discount_type='none', discount_value='0')
check('POST /reservations redirects', r.status_code == 302)
r = boss.get('/sell_subscription?search_query=احمد')
check('package search is GET and finds student', r.status_code == 200 and 'أحمد محمد علي' in r.get_data(as_text=True))
r = boss.get('/manage_products')
check('pages are not cached by the browser', r.headers.get('Cache-Control') == 'no-store')
check('home button on inner pages', 'class="home-btn"' in r.get_data(as_text=True))
check('no home button on dashboard', 'class="home-btn"' not in boss.get('/dashboard').get_data(as_text=True))
tpl_dir = os.path.join(REPO, 'templates')
check('no template uses browser history.back',
      not any('history.back' in open(os.path.join(tpl_dir, f), encoding='utf-8').read() for f in os.listdir(tpl_dir)))
check('discount-cap setting removed from settings page', 'سقف الخصم' not in boss.get('/manage_settings').get_data(as_text=True))

# ---------------------------------------------------------------- shutdown is manager-only
r = post(login('emp'), '/shutdown')
check('employee cannot shut the system down', r.status_code == 302)
check('shutdown button hidden for employees', 'إغلاق النظام' not in login('emp').get('/dashboard').get_data(as_text=True))
check('shutdown button shown for manager', 'إغلاق النظام' in boss.get('/dashboard').get_data(as_text=True))

# ---------------------------------------------------------------- Excel import
wb = openpyxl.Workbook()
ws = wb.active
ws.append(['م', 'الاسم', 'رقم الموبايل', 'الكليه', 'الفرقه'])
ws.append([1, 'طالب جديد', 1077776666, 'تجارة', 'تانية'])
ws.append([2, 'مكرر في الملف', 1077776666, '', ''])
ws.append([3, 'من غير رقم', None, '', ''])
ws.append([4, 'موجود قبل كده', '01012345678', '', ''])
ws.append([5, 'رقم ناقص', '0101', '', ''])
ws.append([None, None, None, None, None])
buf = io.BytesIO()
wb.save(buf)
buf.seek(0)
r = boss.post('/import_students', data={'csrf_token': 't', 'excel_file': (buf, 'students.xlsx')},
              content_type='multipart/form-data')
check('import redirects to report', '/import_report' in r.headers.get('Location', ''))
rep = boss.get('/import_report').get_data(as_text=True)
check('Arabic headers accepted, phone gets leading zero', q("SELECT phone FROM Students WHERE name='طالب جديد'")[0] == '01077776666')
check('report lists every skipped row with reason',
      all(x in rep for x in ('متكرر في الملف', 'رقم التليفون فاضي', 'متسجل قبل كده', 'ناقص')))
buf = io.BytesIO(b'not excel')
r = boss.post('/import_students', data={'csrf_token': 't', 'excel_file': (buf, 'old.xls')}, content_type='multipart/form-data')
check('.xls gets a clear message', r.status_code == 302)

before = q("SELECT COUNT(*) FROM Students")[0]
run("INSERT INTO Students (name, phone) VALUES ('بدون أي زيارات', '01200000099')")
wb = openpyxl.Workbook()
ws = wb.active
ws.append(['name', 'phone', 'college', 'year'])
ws.append(['أحمد محمد علي (محدث)', '01012345678', 'هندسة', '3'])   # has visits -> kept and updated
ws.append(['Fresh One', '01200000001', '', ''])
buf = io.BytesIO()
wb.save(buf)
buf.seek(0)
boss.post('/import_students', data={'csrf_token': 't', 'replace_all': '1', 'excel_file': (buf, 'all.xlsx')},
          content_type='multipart/form-data')
names = {r['name'] for r in A.get_db().execute("SELECT name FROM Students")}
check('replace mode: student with history kept and updated', 'أحمد محمد علي (محدث)' in names)
check('replace mode: students without history removed, file imported',
      'بدون أي زيارات' not in names and 'طالب جديد' not in names and 'Fresh One' in names, f'{before} -> {sorted(names)}')
check('replace mode: students with visits or packages kept', {'فاطمة حسن', 'New Guy'} <= names)
r = login('emp').post('/import_students', data={'csrf_token': 't', 'replace_all': '1', 'excel_file': (io.BytesIO(b''), 'a.xlsx')},
                      content_type='multipart/form-data')
check('replace mode is manager-only', 'Fresh One' in {r['name'] for r in A.get_db().execute("SELECT name FROM Students")})

# ---------------------------------------------------------------- earlier money fixes still hold
emp = login('emp')
post(emp, '/start_shift')
v = checkin(emp, s1, 'Shared', 20)
post(emp, f'/add_sale/{v}', **{f'quantity_{TEA}': 1})
post(emp, '/end_shift')
r = post(emp, f'/confirm_payment/{v}', payment_method='cash')
check('payment without open shift is blocked', q("SELECT check_out_time FROM Visits WHERE visit_id=?", v)[0] is None)
emp2 = login('emp2')
post(emp2, '/start_shift')
shift_b = q("SELECT MAX(shift_id) FROM Shifts")[0]
post(emp2, f'/confirm_payment/{v}', payment_method='cash')
check('visit crossing shifts: its cafeteria moves to the collecting shift',
      q("SELECT shift_id FROM Sales WHERE visit_id=?", v)[0] == shift_b)

# ---------------------------------------------------------------- every page opens
for client, who in ((boss, 'manager'), (emp2, 'employee')):
    sid = q("SELECT MIN(student_id) FROM Students")[0]
    open_visit = checkin(client, sid, 'Silent', 5) if who == 'manager' else q("SELECT MAX(visit_id) FROM Visits")[0]
    pages = ['/dashboard', f'/room_view/{ROOMS["Shared"]}', '/check_in', f'/select_room/{sid}', '/check_out_list',
             f'/show_invoice/{open_visit}', '/select_sale_recipient', '/add_sale/0', '/log_expense', '/sell_subscription',
             '/manage_students', f'/edit_student/{sid}', '/subscriptions_list', '/reservations', '/library',
             '/register_student', f'/switch_room/{open_visit}']
    if who == 'manager':
        pages += ['/manage_settings', '/manage_products', '/manage_users', '/manage_rooms', '/manage_coupons',
                  '/financial_reports', f'/shift_details/{shift1}', '/analytics_report', '/manage_subscription_types',
                  f'/edit_room/{ROOMS["Shared"]}', f'/generate_coupon/{sid}', '/import_report', f'/shift_report/{shift1}']
    bad = [(p, client.get(p).status_code) for p in pages]
    bad = [b for b in bad if b[1] != 200]
    check(f'all {len(pages)} pages open for {who}', not bad, bad)
summary = login('emp2').get('/shift_summary_preview')
check('shift review opens', summary.status_code == 200)

# ---------------------------------------------------------------- start-up: second launch does not start a second server
env = dict(os.environ, BROWSER='true')   # don't actually open a browser while testing
first = subprocess.Popen([sys.executable, 'app.py'], cwd=WORK, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(40):
        if A.server_already_running():
            break
        time.sleep(0.25)
    check('server starts and listens on 5009', A.server_already_running())
    t0 = time.time()
    second = subprocess.run([sys.executable, 'app.py'], cwd=WORK, env=env, capture_output=True, timeout=30)
    check('second launch exits quickly instead of failing', second.returncode == 0 and time.time() - t0 < 15)
    check('first server still running after second launch', first.poll() is None and A.server_already_running())
finally:
    first.terminate()
    first.wait(10)

shutil.rmtree(WORK, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
