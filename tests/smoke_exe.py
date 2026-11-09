"""
تجربة كاملة على الـ EXE نفسه بعد الـ build (بتشتغل في GitHub Actions على ويندوز).

بيدخل بحساب الأدمن اللي اتعمل في أول تشغيل، ويعمل دورة شغل حقيقية:
فتح وردية ← تسجيل طالب ← دخول ← مشتريات ← تحصيل ← قفل الوردية
وبعدين يتأكد من الأرقام المتسجلة في الداتا بيز، ومن إن كل الصفحات بتفتح.

الاستخدام: python tests/smoke_exe.py <فولدر الـ EXE>
"""
import copy
import datetime
from concurrent.futures import ThreadPoolExecutor
import threading
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



def parallel_posts(path, **data):
    """Two independent browser sessions submit the same action concurrently."""
    barrier = threading.Barrier(2)
    cookies = list(jar)
    def send():
        local_jar = http.cookiejar.CookieJar()
        for cookie in cookies:
            local_jar.set_cookie(copy.copy(cookie))
        local = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(local_jar))
        req = urllib.request.Request(BASE + path, data=urllib.parse.urlencode(dict(data, csrf_token=csrf)).encode())
        barrier.wait(timeout=10)
        with local.open(req, timeout=30) as response:
            response.read()
            return response.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send) for _ in range(2)]
        return [f.result(timeout=40) for f in futures]


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

shift = db_one("SELECT end_time, revenue_hours, revenue_catering, net_cash FROM Shifts WHERE shift_id=?", shift_id)
time_cost = db_one("SELECT total_cost FROM Visits WHERE visit_id=?", visit_id)[0]
check('shift closed', shift[0] is not None)
# الكافيتريا لوحدها ومش داخلة في صافي الوردية
check('shift totals correct (catering separate from net)',
      shift[1] == time_cost and shift[2] == 2 * tea_price and shift[3] == time_cost,
      f'hours={shift[1]} catering={shift[2]} net={shift[3]} expected_net={time_cost}')
status, report = get(f'/shift_report/{shift_id}')
check('shift report page opens', status == 200 and 'الكافيتريا' in report)

# Second cycle: concurrent shift start + concurrent subscription payment.
check('parallel shift requests respond', parallel_posts('/start_shift') == [200, 200])
check('parallel start creates one shift', db_one("SELECT COUNT(*) FROM Shifts WHERE end_time IS NULL")[0] == 1)
second_shift = db_one("SELECT shift_id FROM Shifts WHERE end_time IS NULL")[0]
type_id, hours, package_price = db_one("SELECT type_id,total_hours,price FROM MembershipTypes ORDER BY type_id LIMIT 1")
post('/sell_subscription', confirm_sub='1', student_id=student_id, type_id=type_id)
start = datetime.datetime.now() - datetime.timedelta(minutes=30)
post('/perform_check_in', student_id=student_id, room_id=room_id,
     manual_date=start.strftime('%Y-%m-%d'), manual_time=start.strftime('%H:%M'))
second_visit = db_one("SELECT visit_id FROM Visits WHERE student_id=? AND check_out_time IS NULL", student_id)[0]
post(f'/add_sale/{second_visit}', **{f'quantity_{tea_id}': 1})
check('parallel payments respond', parallel_posts(f'/confirm_payment/{second_visit}', payment_method='subscription') == [200, 200])
remaining = db_one("SELECT remaining_hours FROM StudentSubscriptions WHERE student_id=? ORDER BY sub_id DESC LIMIT 1", student_id)[0]
duration, method = db_one("SELECT duration_hours,payment_method FROM Visits WHERE visit_id=?", second_visit)
check('subscription deducted exactly once', method == 'subscription' and abs(hours - remaining - duration) < 0.000001)
post(f'/add_sale/{second_visit}', **{f'quantity_{tea_id}': 1})
check('stale form cannot add to paid visit', db_one("SELECT SUM(quantity) FROM Sales WHERE visit_id=?", second_visit)[0] == 1)
_, receipt = get(f'/show_invoice/{second_visit}')
check('paid receipt hides checkout action', f'action="/confirm_payment/{second_visit}"' not in receipt)
post('/end_shift')
net, catering = db_one("SELECT net_cash,revenue_catering FROM Shifts WHERE shift_id=?", second_shift)
check('subscription shift reconciles', net == package_price and catering == tea_price,
      f'net={net}, expected={package_price}, catering={catering}')

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
