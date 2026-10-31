"""
تجربة كاملة على الـ EXE نفسه بعد الـ build (بتشتغل في GitHub Actions على ويندوز).

بيدخل بحساب الأدمن اللي اتعمل في أول تشغيل، ويعمل دورة شغل حقيقية:
فتح وردية ← تسجيل طالب ← دخول ← مشتريات ← تحصيل ← قفل الوردية
وبعدين يتأكد من الأرقام المتسجلة في الداتا بيز، ومن إن كل الصفحات بتفتح.

الاستخدام: python tests/smoke_exe.py <فولدر الـ EXE>
"""
import http.cookiejar
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:5009'
DIST = sys.argv[1]

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
csrf = None


def get(path):
    global csrf
    with opener.open(BASE + path, timeout=30) as r:
        body = r.read().decode('utf-8')
        m = re.search(r'name="csrf_token" value="([^"]+)"', body)
        if m:
            csrf = m.group(1)
        return r.status, body


def post(path, **data):
    data['csrf_token'] = csrf
    req = urllib.request.Request(BASE + path, data=urllib.parse.urlencode(data).encode('utf-8'))
    with opener.open(req, timeout=30) as r:
        return r.status, r.read().decode('utf-8')


def db_one(sql, *args):
    conn = sqlite3.connect(os.path.join(DIST, 'workspace.db'))
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail else ''))
    if not cond:
        sys.exit(1)


with open(os.path.join(DIST, 'initial_admin_password.txt'), encoding='utf-8') as f:
    password = re.search(r'كلمة المرور: (\S+)', f.read()).group(1)

get('/login')
post('/login', username='admin', password=password)
_, body = get('/dashboard')
check('admin login', 'action="/start_shift"' in body)

post('/start_shift')
shift_id = db_one("SELECT shift_id FROM Shifts WHERE end_time IS NULL")[0]
check('shift opened', shift_id is not None)

post('/register_student', name='Smoke Test', phone='01099990000', college='CI', year='1', next_url='')
student_id = db_one("SELECT student_id FROM Students WHERE phone='01099990000'")[0]
room_id = db_one("SELECT room_id FROM Rooms WHERE name='Shared'")[0]
post('/perform_check_in', student_id=student_id, room_id=room_id)
visit_id = db_one("SELECT visit_id FROM Visits WHERE student_id=? AND check_out_time IS NULL", student_id)[0]
check('student checked in', visit_id is not None)

tea_id, tea_price = db_one("SELECT product_id, sale_price FROM Products WHERE name='شاي'")
post(f'/add_sale/{visit_id}', **{f'quantity_{tea_id}': 2})

status, invoice = get(f'/show_invoice/{visit_id}')
check('invoice opens', status == 200 and 'Smoke Test' in invoice)
post(f'/confirm_payment/{visit_id}', payment_method='cash')

status, _ = get('/shift_summary_preview')
check('shift summary opens', status == 200)
post('/end_shift')

shift = db_one("SELECT end_time, revenue_hours, revenue_sales, net_cash FROM Shifts WHERE shift_id=?", shift_id)
time_cost = db_one("SELECT total_cost FROM Visits WHERE visit_id=?", visit_id)[0]
expected_net = time_cost + 2 * tea_price
check('shift closed', shift[0] is not None)
check('shift totals correct', shift[1] == time_cost and shift[2] == 2 * tea_price and shift[3] == expected_net,
      f'hours={shift[1]} sales={shift[2]} net={shift[3]} expected_net={expected_net}')

pages = ['/dashboard', '/check_in', '/check_out_list', '/select_sale_recipient', '/add_sale/0', '/log_expense',
         '/sell_subscription', '/manage_settings', '/manage_students', '/manage_products', '/manage_users',
         '/manage_rooms', '/manage_coupons', '/financial_reports', f'/shift_details/{shift_id}', '/analytics_report',
         '/subscriptions_list', '/reservations', '/manage_subscription_types', '/library', '/register_student']
broken = []
for page in pages:
    try:
        status, _ = get(page)
        if status != 200:
            broken.append(f'{page} -> {status}')
    except Exception as e:  # noqa: BLE001 - any failure here is a broken page
        broken.append(f'{page} -> {e}')
check(f'all {len(pages)} pages open', not broken, ', '.join(broken))

print('EXE smoke test passed')
