import os
import openpyxl  # Ensure PyInstaller bundles the Excel engine
import sys
import webbrowser
from threading import Timer
import sqlite3
import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash, generate_password_hash
import urllib.parse
import secrets
import string

import math
# =========================================================
# 🛡️ منطقة الأمان: تحديد المسارات بذكاء (الحل النهائي)
# =========================================================

if getattr(sys, 'frozen', False):
    # الحالة 1: البرنامج شغال ملف EXE
    # المسار الحقيقي لملف الـ exe (عشان نحط جنبه الداتا بيز)
    BASE_DIR = os.path.dirname(sys.executable)
    # المسار الداخلي للملفات المضغوطة (HTML و CSS) جوه الـ exe
    RESOURCES_DIR = sys._MEIPASS 
else:
    # الحالة 2: البرنامج شغال كود Python عادي
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCES_DIR = BASE_DIR

# 1. تحديد مكان الداتا بيز (دائماً جنب الملف التشغيلي عشان الحفظ)
DATABASE = os.path.join(BASE_DIR, 'workspace.db')

# 2. تحديد مكان القوالب والصور (يا إما جوه الـ exe يا إما في الفولدر العادي)
TEMPLATE_DIR = os.path.join(RESOURCES_DIR, 'templates')
STATIC_DIR = os.path.join(RESOURCES_DIR, 'static')

# 3. إعداد Flask بالمسارات المحسوبة
app = Flask(__name__, 
            template_folder=TEMPLATE_DIR, 
            static_folder=STATIC_DIR)

def _load_or_create_secret_key():
    key_path = os.path.join(BASE_DIR, '.secret_key')
    try:
        with open(key_path, 'r') as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    with open(key_path, 'w') as f:
        f.write(key)
    return key

app.secret_key = _load_or_create_secret_key()
MANAGER_PHONE = "201070671508"

# --- دالة الاتصال بالداتا بيز (تستخدم المسار الثابت) ---
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# =========================================================
# 🛡️ حماية CSRF: توكن لكل جلسة، والتحقق منه في أي POST
# =========================================================
def get_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(16)
    return session['_csrf_token']

@app.context_processor
def inject_csrf_token():
    return {'csrf_token': get_csrf_token}

@app.before_request
def check_csrf_token():
    if request.method == 'POST':
        submitted = request.form.get('csrf_token')
        expected = session.get('_csrf_token')
        if not expected or not submitted or not secrets.compare_digest(submitted, expected):
            flash("انتهت صلاحية الجلسة، برجاء المحاولة مرة أخرى.", "error")
            return redirect(request.referrer or url_for('dashboard'))

# =========================================================
# 🔒 منع محاولات تخمين كلمة المرور (Brute-force lockout)
# =========================================================
_LOGIN_ATTEMPTS = {}
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 60

def is_login_locked(username):
    entry = _LOGIN_ATTEMPTS.get(username)
    if not entry:
        return False
    count, locked_until = entry
    if locked_until and datetime.datetime.now() < locked_until:
        return True
    return False

def register_failed_login(username):
    count, _ = _LOGIN_ATTEMPTS.get(username, (0, None))
    count += 1
    locked_until = None
    if count >= LOGIN_MAX_ATTEMPTS:
        locked_until = datetime.datetime.now() + datetime.timedelta(seconds=LOGIN_LOCKOUT_SECONDS)
    _LOGIN_ATTEMPTS[username] = (count, locked_until)

def clear_failed_login(username):
    _LOGIN_ATTEMPTS.pop(username, None)

# =========================================================
# 🚪 منع الحجز المزدوج للقاعات الخاصة (Private/Meeting)
# =========================================================
def is_exclusive_room(room_name):
    return 'Private' in room_name or 'Meeting' in room_name

def room_has_open_visit(c, room_id):
    c.execute("SELECT visit_id FROM Visits WHERE room_id=? AND check_out_time IS NULL LIMIT 1", (room_id,))
    return c.fetchone() is not None

# =========================================================
# 🔧 ترقية قاعدة البيانات (إضافة أعمدة ناقصة لقواعد البيانات القديمة)
# =========================================================
def _ensure_column(c, table, column, ddl):
    c.execute(f"PRAGMA table_info({table})")
    columns = [row['name'] for row in c.fetchall()]
    if column not in columns:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

def run_startup_migrations():
    if not os.path.exists(DATABASE):
        return
    conn = get_db()
    c = conn.cursor()

    # أعمدة اتضافت بمرور الوقت لبعض قواعد البيانات القديمة (عبر سكربتات منفصلة)
    # ومش موجودة أساساً في كل نسخة من workspace.db - بنضمن وجودها دايماً هنا
    _ensure_column(c, 'Coupons', 'visit_id', 'visit_id INTEGER REFERENCES Visits(visit_id)')
    _ensure_column(c, 'Visits', 'manual_discount', 'manual_discount REAL DEFAULT 0')
    _ensure_column(c, 'Students', 'year', 'year TEXT')

    # جدول الإعدادات (حد كروت النت المجانية + أسعار الشرائح) - لازم يكون موجود
    # دايماً، حتى لو الداتا بيز اتعملت قبل ما الجدول ده يتضاف للسكيما الرئيسية
    c.execute("""
        CREATE TABLE IF NOT EXISTS Settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT
        )
    """)
    c.execute("INSERT OR IGNORE INTO Settings (setting_key, setting_value) VALUES ('internet_free_limit', '2')")
    c.execute("INSERT OR IGNORE INTO Settings (setting_key, setting_value) VALUES ('employee_discount_cap', '15')")

    conn.commit()
    conn.close()

# =========================================================
# 🆕 أول تشغيل على جهاز جديد: إنشاء الداتا بيز وحساب المدير تلقائياً
# =========================================================
def bootstrap_fresh_database():
    import setup_database

    needs_schema = True
    if os.path.exists(DATABASE):
        probe = sqlite3.connect(DATABASE)
        try:
            row = probe.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='Users'"
            ).fetchone()
            needs_schema = row is None
        finally:
            probe.close()

    if needs_schema:
        setup_database.create_database()

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM Users")
    if c.fetchone()[0] == 0:
        alphabet = string.ascii_letters + string.digits
        password = ''.join(secrets.choice(alphabet) for _ in range(10))
        c.execute("INSERT INTO Users (username, password, role) VALUES (?, ?, 'manager')",
                  ('admin', generate_password_hash(password, method='pbkdf2:sha256')))
        conn.commit()

        note = (f"اسم المستخدم: admin\nكلمة المرور: {password}\n"
                "سجّل الدخول بيها وأضف حسابات الموظفين من (إدارة المستخدمين)، وبعدها امسح الملف ده.\n")
        try:
            with open(os.path.join(BASE_DIR, 'initial_admin_password.txt'), 'w', encoding='utf-8') as f:
                f.write(note)
        except OSError:
            pass
        print("=" * 50)
        print(">>> FIRST RUN: admin account created")
        print(f">>> username: admin    password: {password}")
        print("=" * 50)
    conn.close()

bootstrap_fresh_database()
run_startup_migrations()

# ... (كمل باقي الكود زي ما هو من غير تغيير) ...

import math

def calculate_dynamic_cost(hours, room_name, hourly_rate):
    # ---------------------------------------------------------
    # 1. لو الغرفة خاصة (Private) أو اجتماعات (Meeting)
    # ---------------------------------------------------------
    if 'Private' in room_name or 'Meeting' in room_name:
        # تقريب لأقرب نص ساعة لفوق + حد أدنى ساعة واحدة
        billed_hours = math.ceil(hours * 2) / 2
        if billed_hours < 1.0:
            billed_hours = 1.0
        return billed_hours * hourly_rate

    # ---------------------------------------------------------
    # 2. لو الغرفة عادية (Shared / Silent) - نظام الشرائح الديناميكي
    # ---------------------------------------------------------
    else:
        # الشرائح الافتراضية (تُستخدم لو لم تُضبط في الداتا بيز)
        DEFAULTS = {
            'tier_price_1h':  10,
            'tier_price_3h':  25,
            'tier_price_6h':  35,
            'tier_price_9h':  40,
            'tier_price_12h': 50,
            'tier_price_16h': 60,
            'tier_price_24h': 70,
        }

        # جلب الأسعار من الداتا بيز
        conn = get_db()
        c = conn.cursor()
        tier_prices = dict(DEFAULTS)  # ابدأ بالقيم الافتراضية
        for key in DEFAULTS:
            c.execute("SELECT setting_value FROM Settings WHERE setting_key=?", (key,))
            row = c.fetchone()
            if row:
                tier_prices[key] = float(row['setting_value'])
        conn.close()

        # ترتيب الشرائح: (الحد الأقصى للساعات بسماحية 3 دقايق, مفتاح السعر)
        # سماحية 15 دقيقة (0.25 ساعة) بعد كل باقة قبل ما يقفز للباقة اللي بعدها
        GRACE_PERIOD = 0.25
        TIERS = [
            (1  + GRACE_PERIOD, 'tier_price_1h'),
            (3  + GRACE_PERIOD, 'tier_price_3h'),
            (6  + GRACE_PERIOD, 'tier_price_6h'),
            (9  + GRACE_PERIOD, 'tier_price_9h'),
            (12 + GRACE_PERIOD, 'tier_price_12h'),
            (16 + GRACE_PERIOD, 'tier_price_16h'),
        ]

        for limit, key in TIERS:
            if hours <= limit:
                return float(tier_prices[key])

        # لو تجاوز الـ 16 ساعة → باقة اليوم الكامل
        return float(tier_prices['tier_price_24h'])
    
# =================================================
# 🛑 كود إغلاق البرنامج (يوضع في app.py)
# =================================================

# دالة مساعدة لتنفيذ أمر بعد إرسال الرد للمتصفح
# -------------------------------------------------------
# ضع هذا الكود في ملف app.py (استبدل دالة shutdown القديمة)
# -------------------------------------------------------

# دالة مساعدة (تأكد أنها موجودة مرة واحدة في الملف)
def from_flask_run_after_request(func):
    from flask import after_this_request
    return after_this_request(func)

@app.route('/shutdown', methods=['POST'])
def shutdown():
    # 1. السماح بالإغلاق للجميع (بدون شرط تسجيل الدخول)
    
    # 2. تصميم صفحة الوداع (HTML كامل)
    response_html = """
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>تم إغلاق النظام</title>
        <style>
            body { 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                text-align: center; 
                padding-top: 80px; 
                background-color: #e9ecef; 
                margin: 0;
            }
            .message-box { 
                background: white; 
                padding: 40px; 
                border-radius: 12px; 
                box-shadow: 0 4px 15px rgba(0,0,0,0.1); 
                display: inline-block; 
                max-width: 500px;
                border-top: 5px solid #dc3545;
            }
            h1 { color: #dc3545; margin-bottom: 10px; }
            p { font-size: 18px; color: #6c757d; }
            .icon { font-size: 50px; margin-bottom: 20px; display: block; }
        </style>
    </head>
    <body>
        <div class="message-box">
            <span class="icon">🛑</span>
            <h1>تم إغلاق النظام بنجاح</h1>
            <p>تم إنهاء البرنامج وفصل الاتصال.<br>يمكنك الآن إغلاق هذه النافذة بأمان.</p>
        </div>
        <script>
            // محاولة إغلاق المتصفح أوتوماتيكياً بعد 3 ثواني
            setTimeout(function(){ window.close(); }, 3000);
        </script>
    </body>
    </html>
    """
    
    # 3. عملية القتل الرحيم للبرنامج (بعد إرسال الصفحة للمتصفح)
    @from_flask_run_after_request
    def kill_process(response):
        import os
        import threading
        import time
        
        def die():
            # ننتظر ثانية واحدة حتى تظهر الصفحة للعميل ثم نقتل البرنامج
            time.sleep(1)
            os._exit(0)
        
        threading.Thread(target=die).start()
        return response

    return response_html    


@app.route("/edit_product/<int:product_id>", methods=['GET', 'POST'])
def edit_product(product_id):
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    
    if request.method == 'POST':
        c.execute("""UPDATE Products SET name=?, purchase_price=?, sale_price=?, stock_quantity=? 
                     WHERE product_id=?""",
                  (request.form['name'], float(request.form['purchase_price']), 
                   float(request.form['sale_price']), float(request.form['stock_quantity']), product_id))
        conn.commit()
        conn.close()
        flash("تم تعديل المنتج بنجاح", "success")
        return redirect(url_for('manage_products'))
        
    c.execute("SELECT * FROM Products WHERE product_id=?", (product_id,))
    product = c.fetchone()
    conn.close()
    return render_template('edit_product.html', product=product)
# --- المسارات الأساسية ---
@app.route("/",methods=['GET', 'POST'])
@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if is_login_locked(username):
            flash(f"تم إيقاف الدخول مؤقتاً بسبب محاولات كثيرة خاطئة. حاول بعد {LOGIN_LOCKOUT_SECONDS} ثانية.", "error")
            return redirect(url_for('login'))

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM Users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user['password'], password):
            clear_failed_login(username)
            session['user_id'] = user['user_id']
            session['username'] = user['username']
            session['role'] = user['role']
            session.pop('active_shift_id', None)
            return redirect(url_for('dashboard'))
        else:
            register_failed_login(username)
            flash("اسم المستخدم أو كلمة المرور غير صحيحة", "error")
            return redirect(url_for('login'))

    return render_template('login.html')

@app.route("/logout")
def logout():
    session.clear()
    flash("تم تسجيل خروجك بنجاح", "success")
    return redirect(url_for('login'))

@app.route("/dashboard")
def dashboard():
    if 'user_id' not in session:
        flash("يرجى تسجيل الدخول أولاً", "error")
        return redirect(url_for('login'))

    role = session.get('role')
    conn = get_db()
    c = conn.cursor()

    # --- 1. إحصائيات الغرف الحية (Live Monitor) ---
    c.execute("""
        SELECT R.room_id, R.name, COUNT(V.visit_id) as active_count
        FROM Rooms R
        LEFT JOIN Visits V ON R.room_id = V.room_id AND V.check_out_time IS NULL
        GROUP BY R.room_id
        ORDER BY R.name
    """)
    room_stats = c.fetchall()

    # --- 2. (الجزء الجديد) فحص الحجوزات القادمة خلال ساعة ---
    # --- 2. (تعديل جديد) فحص الحجوزات القادمة وتجهيز رسالة للعميل ---
    notify_alert = None
    notify_link = None
    
    current_time = datetime.datetime.now()
    one_hour_later = current_time + datetime.timedelta(hours=1)
    
    # هات الحجز القريب
    c.execute("""
        SELECT R.*, RM.name as room_name 
        FROM Reservations R 
        JOIN Rooms RM ON R.room_id = RM.room_id
        WHERE R.status = 'confirmed' 
        AND R.start_time > ? AND R.start_time <= ?
        ORDER BY R.start_time ASC
        LIMIT 1
    """, (current_time, one_hour_later))
    
    near_res = c.fetchone()
    
    if near_res:
        time_only = near_res['start_time'].split(' ')[1] # الساعة بس (مثلاً 15:30)
        
        # (أ) تجهيز رسالة شيك للعميل
        msg = f"مرحباً أ/ {near_res['client_name']} 👋،\n\nنود تذكيركم بموعد حجزكم في Workspace (قاعة {near_res['room_name']}) اليوم الساعة {time_only}.\n\nنحن في انتظاركم! 🌹"
        
        encoded_alert = urllib.parse.quote(msg)
        
        # (ب) استخدام رقم العميل المسجل في الحجز (مع إضافة كود مصر 20)
        client_phone = "20" + str(near_res['phone'])
        
        # تجهيز الرابط للعميل
        notify_link = f"https://web.whatsapp.com/send?phone={client_phone}&text={encoded_alert}"
        
        # نص الشريط الأصفر للموظف
        notify_alert = f"⏰ تذكير: حجز ({near_res['client_name']}) الساعة {time_only} - القاعة: {near_res['room_name']}"
    conn.close()

    user_id = session.get('user_id')
    conn = get_db() # فتحنا اتصال جديد عشان اللي فات اتقفل
    c = conn.cursor()
    c.execute("SELECT shift_id FROM Shifts WHERE user_id = ? AND end_time IS NULL", (user_id,))
    open_shift = c.fetchone()
    conn.close()

    if open_shift:
        session['active_shift_id'] = open_shift['shift_id']
    else:
        session.pop('active_shift_id', None)

    if role == 'manager':
        return render_template('manager_dashboard.html',
                               room_stats=room_stats,
                               notify_alert=notify_alert,
                               notify_link=notify_link)

    elif role == 'employee':
        return render_template('employee_dashboard.html',
                               room_stats=room_stats,
                               notify_alert=notify_alert,
                               notify_link=notify_link)

    return redirect(url_for('logout'))# --- عمليات الوردية ---


@app.route("/room_view/<int:room_id>")
def room_view(room_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # 1. هات اسم الغرفة
    c.execute("SELECT name FROM Rooms WHERE room_id=?", (room_id,))
    room = c.fetchone()
    
    # 2. هات الطلاب الموجودين في الغرفة دي حالياً (active)
    c.execute("""
        SELECT V.visit_id, S.name, S.phone, V.check_in_time, V.student_id 
        FROM Visits V 
        JOIN Students S ON V.student_id = S.student_id 
        WHERE V.room_id = ? AND V.check_out_time IS NULL
    """, (room_id,))
    students = c.fetchall()
    
    conn.close()
    
    return render_template('room_view.html', room_name=room['name'], students=students)



@app.route("/start_shift", methods=['GET','POST'])
def start_shift():
    if 'user_id' not in session: return redirect(url_for('login'))
    user_id = session.get('user_id')
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT shift_id FROM Shifts WHERE user_id = ? AND end_time IS NULL", (user_id,))
    existing = c.fetchone()
    if existing:
        # الوردية مفتوحة بالفعل في الداتا بيز لكن السيشن نسيها (مثلاً بعد إعادة تشغيل السيرفر) - نستعيدها
        session['active_shift_id'] = existing['shift_id']
        flash("لديك وردية مفتوحة بالفعل، تم استعادتها.", "info")
    else:
        start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO Shifts (user_id, start_time) VALUES (?, ?)", (user_id, start_time))
        conn.commit()
        session['active_shift_id'] = c.lastrowid
        flash("تم بدء الوردية بنجاح!", "success")
    conn.close()
    return redirect(url_for('dashboard'))

@app.route("/end_shift", methods=['GET', 'POST'])
def end_shift():
    if 'active_shift_id' not in session: return redirect(url_for('dashboard'))
    shift_id = session.get('active_shift_id')
    
    conn = get_db()
    c = conn.cursor()
    end_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 1. إعادة الحسابات للتخزين النهائي
    c.execute("""SELECT V.total_cost, R.name as room_name 
                 FROM Visits V JOIN Rooms R ON V.room_id = R.room_id
                 WHERE V.shift_id = ? AND V.payment_method = 'cash' AND V.check_out_time IS NOT NULL""", (shift_id,))
    visits = c.fetchall()

    rev_shared = 0.0
    rev_private = 0.0
    for v in visits:
        if 'Private' in v['room_name'] or 'Meeting' in v['room_name']:
            rev_private += v['total_cost']
        else:
            rev_shared += v['total_cost']
            
    rev_hours = rev_shared + rev_private
    c.execute("""
        SELECT SUM(total_price) FROM Sales 
        WHERE shift_id = ? 
        AND (visit_id IS NULL OR visit_id IN (SELECT visit_id FROM Visits WHERE check_out_time IS NOT NULL))
    """, (shift_id,))
    rev_sales = c.fetchone()[0] or 0
    c.execute("SELECT SUM(amount) FROM Expenses WHERE shift_id = ?", (shift_id,))
    expenses = c.fetchone()[0] or 0
    c.execute("SELECT SUM(total_discount) FROM Visits WHERE shift_id = ? AND check_out_time IS NOT NULL", (shift_id,))
    discounts = c.fetchone()[0] or 0
    
    net_cash = (rev_hours + rev_sales) - expenses - discounts
    
    # 2. تحديث قاعدة البيانات
    c.execute("""UPDATE Shifts SET end_time=?, revenue_hours=?, revenue_sales=?, 
                 total_expenses=?, total_discounts=?, net_cash=? WHERE shift_id=?""", 
              (end_time, rev_hours, rev_sales, expenses, discounts, net_cash, shift_id))
    conn.commit()
    conn.close()
    
    # 3. تجهيز بيانات الطباعة
    shift_data = {
        'id': shift_id,
        'end_time': end_time,
        'employee': session.get('username'),
        'rev_shared': rev_shared,
        'rev_private': rev_private,
        'rev_sales': rev_sales,
        'expenses': expenses,
        'discounts': discounts,
        'net_cash': net_cash
    }

    # 4. رابط الواتساب
    report_text = f"تقرير وردية {shift_id}\nموظف: {session.get('username')}\nصافي: {net_cash}"
    encoded_msg = urllib.parse.quote(report_text)
    whatsapp_link = f"https://web.whatsapp.com/send?phone={MANAGER_PHONE}&text={encoded_msg}"
    
    session.pop('active_shift_id', None)
    
    return render_template('shift_ended_success.html', whatsapp_link=whatsapp_link, data=shift_data)
@app.route("/register_student", methods=['GET', 'POST'])
def register_student():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        college = request.form['college']
        year = request.form['year']
        next_url = request.form.get('next_url')
        # --- (1) استلام وتنظيف الوقت والتاريخ اليدوي من الفورم ---
        raw_time = request.form.get('manual_time', '').strip()
        manual_date = request.form.get('manual_date', '').strip()
        print(f">>> DEBUG register_student: Manual Time Received: '{raw_time}', Manual Date: '{manual_date}'")

        # نفس لوجيك perform_check_in: نتجاهل كلمة "None" أو format غلط
        clean_manual_time = ''
        if raw_time and raw_time.lower() != 'none':
            parsed_time = None
            try:
                parsed_time = datetime.datetime.strptime(raw_time.strip(), "%I:%M %p").time()
            except ValueError:
                try:
                    parsed_time = datetime.datetime.strptime(raw_time.strip(), "%H:%M").time()
                except ValueError:
                    pass

            if parsed_time is not None:
                clean_manual_time = raw_time
                print(f">>> DEBUG register_student: Valid manual_time: '{clean_manual_time}'")
            else:
                print(f">>> DEBUG register_student: Invalid format '{raw_time}', aborting.")
                flash("صيغة الوقت غير صحيحة", "error")
                return redirect(url_for('register_student', phone=phone, name=name, next=next_url, manual_time=raw_time))
        
        conn = get_db()
        c = conn.cursor()
        try:
            c.execute("INSERT INTO Students (name, phone, college, year) VALUES (?, ?, ?, ?)", 
                      (name, phone, college, year))
            conn.commit()
            sid = c.lastrowid
            flash(f"تم تسجيل {name} بنجاح", "success")
            conn.close()
            
            # --- (2) تمرير الوقت والتاريخ اليدوي المنظف للخطوة التالية ---
            if next_url == 'select_room': 
                return redirect(url_for('select_room', student_id=sid, manual_time=clean_manual_time, manual_date=manual_date))
            
            if next_url == 'sell_sub': 
                return redirect(url_for('sell_subscription', student_id=sid))
                
            return redirect(url_for('dashboard'))
            
        except sqlite3.IntegrityError:
            flash("الطالب مسجل بالفعل", "error")
            conn.close()
    
    return render_template('register_student.html', 
                           prefilled_phone=request.args.get('phone', ''), 
                           prefilled_name=request.args.get('name', ''), 
                           next_url=request.args.get('next', ''), 
                           manual_time=request.args.get('manual_time', ''),
                           manual_date=request.args.get('manual_date', ''))
@app.route("/check_in", methods=['GET', 'POST'])
def check_in():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    if request.method == 'POST':
        query = request.form['search_query']
        # --- السطر الجديد لسحب الوقت والتاريخ اليدوي ---
        manual_time = request.form.get('manual_time') 
        manual_date = request.form.get('manual_date')
        
        conn = get_db()
        c = conn.cursor()
        pattern = f"%{query}%"
        c.execute("SELECT * FROM Students WHERE phone = ? OR name LIKE ?", (query, pattern))
        students = c.fetchall()
        conn.close()
        
        if len(students) == 0:
            flash("طالب غير مسجل", "info")
            phone = query if query.isdigit() else ""
            name = query if not query.isdigit() else ""
            # بنبعت الوقت لصفحة التسجيل لو الطالب جديد
            return redirect(url_for('register_student', phone=phone, name=name, next='select_room', manual_time=manual_time, manual_date=manual_date))
            
        elif len(students) == 1:
            # بنبعت الوقت لصفحة اختيار الغرفة عشان يستخدمه في الـ Insert النهائي
            return redirect(url_for('select_room', student_id=students[0]['student_id'], manual_time=manual_time, manual_date=manual_date))
            
        else:
            # لو فيه كذا طالب بنفس الاسم، بنبعت الوقت لصفحة الاختيار
            return render_template('select_student.html', students=students, manual_time=manual_time, manual_date=manual_date)
            
    return render_template('check_in.html')


@app.route("/select_room/<int:student_id>")
def select_room(student_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    # 1. السطر ده هو اللي "بيمسك" الوقت والتاريخ اللي جاي من الصفحة اللي فاتت
    manual_time = request.args.get('manual_time', '') 
    manual_date = request.args.get('manual_date', '')
    
    conn = get_db()
    c = conn.cursor()
    
    # هات بيانات الغرف (زي ما هي عندك)
    c.execute("SELECT * FROM Rooms")
    rooms = c.fetchall()
    
    # هات بيانات الطالب (زي ما هي عندك)
    c.execute("SELECT * FROM Students WHERE student_id = ?", (student_id,))
    student = c.fetchone()
    conn.close()
    
    # 2. بنبعت الـ manual_time لملف الـ HTML عشان يفضل محفوظ
    return render_template('select_room.html', 
                           rooms=rooms, 
                           student=student, 
                           manual_time=manual_time,
                           manual_date=manual_date)
    
@app.route("/perform_check_in", methods=['POST'])
def perform_check_in():
    if 'active_shift_id' not in session: return redirect(url_for('dashboard'))
    
    sid = request.form.get('student_id')
    rid = request.form.get('room_id')
    manual_time = request.form.get('manual_time', '').strip()
    manual_date = request.form.get('manual_date', '').strip()

    # طباعة واضحة في التيرمينال للتشخيص
    print(f">>> DEBUG: Manual Time Received: '{manual_time}', Manual Date Received: '{manual_date}'")

    check_in_time = None

    now = datetime.datetime.now()

    # نتجاهل أي قيمة فارغة أو كلمة "None" كنص
    if manual_time and manual_time.lower() != 'none':
        parsed_time = None
        try:
            parsed_time = datetime.datetime.strptime(manual_time.strip(), "%I:%M %p").time()
        except ValueError:
            try:
                parsed_time = datetime.datetime.strptime(manual_time.strip(), "%H:%M").time()
            except ValueError:
                pass
                
        if parsed_time is not None:
            if manual_date and manual_date.lower() != 'none':
                try:
                    parsed_date = datetime.datetime.strptime(manual_date, "%Y-%m-%d").date()
                    dt_check_in = datetime.datetime.combine(parsed_date, parsed_time)
                    print(f">>> DEBUG: Explicit manual_date '{manual_date}' provided. Bypassing midnight boundary.")
                except ValueError:
                    print(f">>> DEBUG: Invalid manual_date format '{manual_date}'. Falling back to now.date()")
                    dt_check_in = datetime.datetime.combine(now.date(), parsed_time)
                    if dt_check_in > now:
                        dt_check_in -= datetime.timedelta(days=1)
            else:
                dt_check_in = datetime.datetime.combine(now.date(), parsed_time)
                
                # معالجة تخطي منتصف الليل (إذا كان الوقت مستقبلياً، نفترض أنه بالأمس)
                if dt_check_in > now:
                    print(f">>> DEBUG: Future time '{manual_time}' detected with no manual_date. Assuming yesterday.")
                    dt_check_in -= datetime.timedelta(days=1)
                
            check_in_time = dt_check_in.strftime("%Y-%m-%d %H:%M:%S")
            print(f">>> DEBUG: Using MANUAL check_in_time: '{check_in_time}'")
        else:
            print(f">>> DEBUG: Invalid time format '{manual_time}', aborting.")
            flash("صيغة الوقت غير صحيحة", "error")
            return redirect(url_for('select_room', student_id=sid))
    else:
        check_in_time = now.strftime("%Y-%m-%d %H:%M:%S")
        print(f">>> DEBUG: Using CURRENT TIME as check_in_time: '{check_in_time}'")

    conn = get_db()
    c = conn.cursor()

    c.execute("""SELECT visit_id FROM Visits
                 WHERE student_id = ? AND check_out_time IS NULL
                 LIMIT 1""", (sid,))
    if c.fetchone():
        conn.close()
        flash("هذا الطالب متواجد بالفعل في النظام ولم يسجل خروج", "error")
        return redirect(url_for('check_in'))

    c.execute("SELECT name FROM Rooms WHERE room_id=?", (rid,))
    room_row = c.fetchone()
    if room_row and is_exclusive_room(room_row['name']) and room_has_open_visit(c, rid):
        conn.close()
        flash(f"غرفة {room_row['name']} مشغولة حالياً بشخص آخر.", "error")
        return redirect(url_for('select_room', student_id=sid))

    c.execute("""INSERT INTO Visits (student_id, user_id, shift_id, room_id, check_in_time)
                 VALUES (?, ?, ?, ?, ?)""",
              (sid, session['user_id'], session['active_shift_id'], rid, check_in_time))
    conn.commit()
    conn.close()
    flash("تم تسجيل الدخول بنجاح", "success")
    return redirect(url_for('dashboard'))

@app.route("/check_out_list")
def check_out_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT V.visit_id, S.name, S.phone, V.check_in_time 
                 FROM Visits V JOIN Students S ON V.student_id = S.student_id 
                 WHERE V.check_out_time IS NULL""")
    visits = c.fetchall()
    conn.close()
    return render_template('check_out_list.html', active_visits=visits)

# --- الفواتير والدفع (مع الكوبونات والاشتراكات) ---
@app.route("/show_invoice/<int:visit_id>")
def show_invoice(visit_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    c = conn.cursor()
    
    # 1. جلب بيانات الزيارة والغرفة والطالب
    c.execute("""SELECT V.*, S.name, S.phone, R.name as room_name, R.hourly_rate
                 FROM Visits V 
                 JOIN Students S ON V.student_id = S.student_id
                 JOIN Rooms R ON V.room_id = R.room_id
                 WHERE V.visit_id=?""", (visit_id,))
    visit_data = c.fetchone()
    
    if not visit_data: return redirect(url_for('dashboard'))
    
    # 2. حساب الوقت والتكلفة
    check_in = datetime.datetime.strptime(visit_data['check_in_time'], "%Y-%m-%d %H:%M:%S")
    now = datetime.datetime.now()
    duration_hours = (now - check_in).total_seconds() / 3600.0
    
    # استخدام دالة التسعير الجديدة (حسب نوع الغرفة)
    time_cost = calculate_dynamic_cost(duration_hours, visit_data['room_name'], visit_data['hourly_rate'])
    
    # 3. حساب المبيعات
    # 3. حساب المبيعات (تفصيلي عشان نعرف نمسح)
    # 3. حساب المبيعات (تعديل: جلب التفاصيل عشان نعرف نمسح)
    c.execute("""SELECT S.sale_id, P.name, S.quantity, S.total_price 
                 FROM Sales S JOIN Products P ON S.product_id=P.product_id 
                 WHERE S.visit_id=?""", (visit_id,))
    sales_items = c.fetchall()
    
    # حساب الإجمالي يدوياً لأننا شيلنا التجميع من الداتا بيز
    sales_total = sum(s['total_price'] for s in sales_items)
    # 4. البحث عن اشتراك نشط
    c.execute("""
        SELECT * FROM StudentSubscriptions
        WHERE student_id = ? AND is_active = 1 AND remaining_hours >= ? AND end_date >= ?
        ORDER BY end_date ASC
    """, (visit_data['student_id'], duration_hours, now.strftime("%Y-%m-%d")))
    active_sub = c.fetchone()
    
    # --- بداية التعديل الخاص بالخصومات ---
    
    # أ. استلام الخصم اليدوي من الرابط (لو موجود)
   # --- بداية التعديل الخاص بالخصومات ---
    
    # أ. استلام الخصم اليدوي من الرابط
    manual_disc_type = request.args.get('m_type')
    manual_disc_value = float(request.args.get('m_value', 0))

    discount_info = {"code": None, "amount": 0}
    
    # حساب تكلفة الوقت قبل الخصومات الأخرى لتطبيق النسبة عليها
    # ملاحظة: الخصومات كلها بتطبق على تكلفة الوقت (Time Cost) فقط
    
    # حساب قيمة الخصم اليدوي
    manual_discount_amount = 0
    if manual_disc_value > 0:
        if manual_disc_type == 'percentage':
            manual_discount_amount = time_cost * (manual_disc_value / 100.0)
        else: # fixed
            manual_discount_amount = manual_disc_value
        manual_discount_amount = min(manual_discount_amount, time_cost) # لا يتجاوز تكلفة الوقت

        # الموظف (غير المدير) محدود بسقف الخصم من الإعدادات؛ نفس السقف المطبق فعلياً عند الدفع
        if session.get('role') != 'manager':
            c.execute("SELECT setting_value FROM Settings WHERE setting_key='employee_discount_cap'")
            cap_row_preview = c.fetchone()
            cap_preview = float(cap_row_preview['setting_value']) if cap_row_preview else 15.0
            manual_discount_amount = min(manual_discount_amount, cap_preview)

    # هنا هنمرر المبلغ النهائي المحسوب وليس القيمة الأصلية
    # عشان نعرف نعرضه صح في الفاتورة لو طلب نسبة
    manual_discount_display = manual_discount_amount
    # ---------------------------------------------
    
    # ب. التحقق من خصم الحجز المسبق (أولوية)
    
    # ب. التحقق من خصم الحجز المسبق (أولوية)
    if 'reservation_id' in visit_data and visit_data['reservation_id']:
        c.execute("SELECT * FROM Reservations WHERE reservation_id=?", (visit_data['reservation_id'],))
        res_data = c.fetchone()
        if res_data and res_data['discount_type'] != 'none':
            if res_data['discount_type'] == 'percentage':
                disc_val = time_cost * (res_data['discount_value'] / 100.0)
            else:
                disc_val = res_data['discount_value']
            discount_info = {"code": "عرض حجز", "amount": min(disc_val, time_cost)}

    # ج. لو مفيش خصم حجز، نشوف الكوبون
    if discount_info['amount'] == 0:
        applied_code = request.args.get('applied_code')
        if applied_code and not request.args.get('error_message'):
            c.execute("SELECT * FROM Coupons WHERE code=?", (applied_code,))
            cp = c.fetchone()
            if cp:
                if cp['discount_type'] == 'percentage':
                    disc_val = time_cost * (cp['discount_value'] / 100.0)
                else:
                    disc_val = cp['discount_value']
                discount_info = {"code": cp['code'], "amount": min(disc_val, time_cost)}
                flash("تم تطبيق الكوبون", "success")

    c.execute("SELECT setting_value FROM Settings WHERE setting_key='employee_discount_cap'")
    cap_row = c.fetchone()
    employee_discount_cap = float(cap_row['setting_value']) if cap_row else 15.0

    conn.close()
    
    # د. الجمع النهائي للخصومات (كوبون + يدوي)
    total_discount = discount_info['amount'] + manual_discount_amount
    
    grand_total_before = time_cost + sales_total
    final_total = max(0, grand_total_before - total_discount)

    visit_details = {
        'visit_id': visit_id,
        'room_name': visit_data['room_name'],
        'hourly_rate': visit_data['hourly_rate'],
        'check_in_time': visit_data['check_in_time'],
        'duration_hours': duration_hours,
        'total_time_cost': time_cost,
        'total_sales_cost': sales_total,
        'grand_total': grand_total_before,
        'final_total': final_total,
        'payment_method': visit_data['payment_method'] # عشان العرض في الفاتورة لو اتقفلت
    }
    
    return render_template('invoice.html', 
                           student=visit_data, 
                           visit_details=visit_details, 
                           sales_items=sales_items,
                           discount_info=discount_info,
                           # تمرير قيمة الخصم المحسوبة بدلاً من القيمة الخام
                           manual_discount_amount=manual_discount_amount, 
                           # تمرير نوع وقيمة الخصم اليدوي الأصلية للعرض في الفورم لو كان موجود
                           manual_disc_type=manual_disc_type,
                           manual_disc_value=manual_disc_value,
                           active_sub=active_sub,
                           employee_discount_cap=employee_discount_cap)
@app.route("/apply_coupon/<int:visit_id>", methods=['POST'])
def apply_coupon(visit_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    code = request.form['coupon_code'].upper().strip()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT V.student_id FROM Visits V WHERE V.visit_id=?", (visit_id,))
    visit_row = c.fetchone()
    c.execute("SELECT * FROM Coupons WHERE code=?", (code,))
    coupon = c.fetchone()
    error = None
    if not coupon: error = "الكود غير موجود"
    elif coupon['is_used']: error = "الكود مستخدم من قبل"
    elif coupon['student_id'] and visit_row and coupon['student_id'] != visit_row['student_id']:
        error = "هذا الكوبون مخصص لطالب آخر"
    elif coupon['expiry_date'] and datetime.datetime.strptime(coupon['expiry_date'], '%Y-%m-%d').date() < datetime.datetime.now().date():
        error = "الكود منتهي الصلاحية"

    conn.close()
    if error: flash(error, "error")
    return redirect(url_for('show_invoice', visit_id=visit_id, applied_code=code, error_message=error))

@app.route("/confirm_payment/<int:visit_id>", methods=['POST'])
def confirm_payment(visit_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    payment_method = request.form.get('payment_method', 'cash')
    coupon_code = request.form.get('applied_coupon_code')
    # الخصم اليدوي: بيتحسب من نوعه وقيمته هنا في السيرفر (مش من قيمة جاهزة جاية من المتصفح)
    manual_disc_type = request.form.get('manual_disc_type', 'fixed')
    try:
        manual_disc_value = float(request.form.get('manual_disc_value', 0))
    except ValueError:
        manual_disc_value = 0
    manual_disc_value = max(0, manual_disc_value)
    # سقف الخصم الخاص للموظف (غير المدير) - بييجي من الإعدادات، والمدير من غير سقف
    employee_discount_cap = None
    if session.get('role') != 'manager':
        settings_conn = get_db()
        settings_row = settings_conn.execute(
            "SELECT setting_value FROM Settings WHERE setting_key='employee_discount_cap'"
        ).fetchone()
        settings_conn.close()
        employee_discount_cap = float(settings_row['setting_value']) if settings_row else 15.0

    conn = get_db()
    c = conn.cursor()
    
    # جلب البيانات
    c.execute("""SELECT V.*, R.name as room_name, R.hourly_rate 
                 FROM Visits V JOIN Rooms R ON V.room_id = R.room_id 
                 WHERE V.visit_id=?""", (visit_id,))
    visit = c.fetchone()
    
    if not visit: return redirect(url_for('dashboard'))
    
    c.execute("SELECT name FROM Students WHERE student_id=?", (visit['student_id'],))
    student_name = c.fetchone()['name']

    # حساب الوقت والتكلفة
    check_in = datetime.datetime.strptime(visit['check_in_time'], "%Y-%m-%d %H:%M:%S")
    out_time = datetime.datetime.now()
    duration = (out_time - check_in).total_seconds() / 3600.0
    final_time_cost = calculate_dynamic_cost(duration, visit['room_name'], visit['hourly_rate'])
    
    # تهيئة المتغيرات
    final_discount = 0
    coupon_id_save = None
    
    # 1. لو باقة
    if payment_method == 'subscription':
        c.execute("""
            SELECT * FROM StudentSubscriptions
            WHERE student_id=? AND is_active=1 AND remaining_hours >= ? AND end_date >= ?
            ORDER BY end_date ASC
        """, (visit['student_id'], duration, out_time.strftime("%Y-%m-%d")))
        sub = c.fetchone()
        if sub and sub['remaining_hours'] >= duration:
            new_bal = sub['remaining_hours'] - duration
            c.execute("UPDATE StudentSubscriptions SET remaining_hours=? WHERE sub_id=?", (new_bal, sub['sub_id']))
            # التحصيل من الباقة: لا يتم احتساب تكلفة وقت نقدية في درج الوردية.
            final_time_cost = 0.0
            conn.commit()
        else:
            flash("رصيد الباقة لا يكفي! تم التحويل للكاش.", "error")
            payment_method = 'cash'

    # 2. لو كاش (نحسب الخصومات)
    if payment_method == 'cash':
        # أ. خصم الحجز (Reservation)
        if 'reservation_id' in visit and visit['reservation_id']:
             c.execute("SELECT * FROM Reservations WHERE reservation_id=?", (visit['reservation_id'],))
             res_data = c.fetchone()
             if res_data and res_data['discount_type'] != 'none':
                if res_data['discount_type'] == 'percentage':
                    disc = final_time_cost * (res_data['discount_value'] / 100.0)
                else:
                    disc = res_data['discount_value']
                final_discount = min(disc, final_time_cost)

        # ب. كوبون (Coupon)
        elif coupon_code:
            c.execute("SELECT * FROM Coupons WHERE code=?", (coupon_code,))
            cp = c.fetchone()
            if cp and not cp['is_used'] and (not cp['student_id'] or cp['student_id'] == visit['student_id']):
                coupon_id_save = cp['coupon_id']
                if cp['discount_type'] == 'percentage':
                    disc = final_time_cost * (cp['discount_value'] / 100.0)
                else:
                    disc = cp['discount_value']

                final_discount = min(disc, final_time_cost)
                c.execute("UPDATE Coupons SET is_used=1, visit_id=? WHERE coupon_id=?", (visit_id, cp['coupon_id']))

        # ج. حساب الخصم اليدوي وإضافته للإجمالي (بحد أقصى تكلفة الوقت المتبقية)
        remaining_for_manual = max(0, final_time_cost - final_discount)
        if manual_disc_value > 0:
            if manual_disc_type == 'percentage':
                manual_disc = final_time_cost * (manual_disc_value / 100.0)
            else:
                manual_disc = manual_disc_value
            manual_disc = min(manual_disc, remaining_for_manual)
            # الموظف (غير المدير) محدود بسقف الخصم من الإعدادات؛ المدير بلا سقف
            if employee_discount_cap is not None:
                manual_disc = min(manual_disc, employee_discount_cap)
        else:
            manual_disc = 0
        final_discount += manual_disc
        final_discount = max(0, min(final_discount, final_time_cost))
    else:
        manual_disc = 0

    # تحديث الوردية
    current_shift_id = session.get('active_shift_id')
    shift_update = current_shift_id if current_shift_id else visit['shift_id']

    # الحفظ النهائي (لاحظ إضافة manual_discount للقائمة)
    c.execute("""UPDATE Visits SET 
                 check_out_time=?, duration_hours=?, total_cost=?, 
                 total_discount=?, manual_discount=?, coupon_id=?, payment_method=?, shift_id=? 
                 WHERE visit_id=?""",
              (out_time.strftime("%Y-%m-%d %H:%M:%S"), duration, final_time_cost, 
               final_discount, manual_disc, coupon_id_save, payment_method, shift_update, visit_id))
    
    conn.commit()
    conn.close()
    flash(f"تم تحصيل الفاتورة للطالب {student_name}", "success")
    return redirect(url_for('dashboard'))
# --- المبيعات والمصروفات ---
@app.route("/select_sale_recipient")
def select_sale_recipient():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT V.visit_id, S.name, R.name as room_name FROM Visits V JOIN Students S ON V.student_id=S.student_id JOIN Rooms R ON V.room_id=R.room_id WHERE V.check_out_time IS NULL")
    visits = c.fetchall()
    conn.close()
    return render_template('select_sale_recipient.html', active_visits=visits)

@app.route("/shift_summary_preview")
def shift_summary_preview():
    if 'active_shift_id' not in session: 
        flash("لا يوجد وردية مفتوحة حالياً.", "error")
        return redirect(url_for('dashboard'))

    shift_id = session.get('active_shift_id')
    conn = get_db()
    c = conn.cursor()
    
    # 1. إيرادات الساعات (كاش فقط) مقسّمة بنوع الغرفة
    c.execute("""SELECT V.total_cost, R.name as room_name 
                 FROM Visits V JOIN Rooms R ON V.room_id = R.room_id
                 WHERE V.shift_id = ? AND V.payment_method = 'cash' AND V.check_out_time IS NOT NULL""", (shift_id,))
    visits_cash = c.fetchall()

    rev_shared = 0.0
    rev_private = 0.0
    for v in visits_cash:
        if 'Private' in v['room_name'] or 'Meeting' in v['room_name']:
            rev_private += v['total_cost']
        else:
            rev_shared += v['total_cost']

    rev_hours_total = rev_shared + rev_private


    # 2. مبيعات الكافيتريا (فقط المنتجات العادية)
    # 3. مبيعات الباقات (فقط المنتجات من نوع باقة)
    # ملاحظة: لا يوجد حقل category في Products، سنستخدم الاسم كتمييز مؤقت
    c.execute("""
        SELECT SUM(S.total_price) as cafeteria_sales
        FROM Sales S
        JOIN Products P ON S.product_id = P.product_id
        WHERE S.shift_id = ?
          AND (S.visit_id IS NULL OR S.visit_id IN (SELECT visit_id FROM Visits WHERE check_out_time IS NOT NULL))
          AND (P.name NOT LIKE '%باقة%')
    """, (shift_id,))
    cafeteria_sales = c.fetchone()[0] or 0

    c.execute("""
        SELECT SUM(S.total_price) as package_sales
        FROM Sales S
        JOIN Products P ON S.product_id = P.product_id
        WHERE S.shift_id = ?
          AND (S.visit_id IS NULL OR S.visit_id IN (SELECT visit_id FROM Visits WHERE check_out_time IS NOT NULL))
          AND (P.name LIKE '%باقة%')
    """, (shift_id,))
    package_sales = c.fetchone()[0] or 0

    # 2.b — تفصيلة الكافيتريا: كل صنف × نوع البيع (نقدي أو مضاف لزيارة)
    # نفس فلتر مبيعات الكافيتريا (كاش مباشر + زيارات اتقفلت في الوردية دي)
    # عشان مجموع الـ cash + visit يطابق إجمالي الكارت اللي فوق بالظبط.
    c.execute("""
        SELECT P.name           AS product_name,
               CASE WHEN S.visit_id IS NULL THEN 'cash' ELSE 'visit' END AS sale_type,
               SUM(S.quantity)  AS total_qty,
               SUM(S.total_price) AS total_revenue
        FROM Sales S
        JOIN Products P ON S.product_id = P.product_id
        WHERE S.shift_id = ?
          AND (S.visit_id IS NULL OR S.visit_id IN (SELECT visit_id FROM Visits WHERE check_out_time IS NOT NULL))
          AND (P.name NOT LIKE '%باقة%')
        GROUP BY P.product_id, P.name,
                 CASE WHEN S.visit_id IS NULL THEN 1 ELSE 0 END
        ORDER BY sale_type ASC, total_revenue DESC
    """, (shift_id,))
    cafeteria_breakdown = []
    cafeteria_cash_total = 0.0
    cafeteria_visit_total = 0.0
    for row in c.fetchall():
        revenue = row['total_revenue'] or 0
        cafeteria_breakdown.append({
            'product_name':  row['product_name'],
            'sale_type':     row['sale_type'],
            'total_qty':     row['total_qty'] or 0,
            'total_revenue': revenue,
        })
        if row['sale_type'] == 'cash':
            cafeteria_cash_total += revenue
        else:
            cafeteria_visit_total += revenue
    
    # 3. المصروفات
    c.execute("SELECT SUM(amount) FROM Expenses WHERE shift_id = ?", (shift_id,))
    expenses = c.fetchone()[0] or 0
    
    # 4. الخصومات
    c.execute("SELECT SUM(total_discount) FROM Visits WHERE shift_id = ? AND check_out_time IS NOT NULL", (shift_id,))
    total_discounts = c.fetchone()[0] or 0
    
    try:
        c.execute("SELECT SUM(manual_discount) FROM Visits WHERE shift_id = ? AND check_out_time IS NOT NULL", (shift_id,))
        manual_discounts = c.fetchone()[0] or 0
    except sqlite3.OperationalError:
        manual_discounts = 0
    
    net_cash = (rev_hours_total + cafeteria_sales + package_sales) - expenses - total_discounts

    # 5. ─── تفاصيل كل زيارة (الجدول التفصيلي) ───
    # نجيب: (أ) كل زيارات الوردية الحالية (مقفولة أو مفتوحة)
    #       (ب) أي زيارة لسه مفتوحة من ورديات قديمة (الـ "ghost visits")
    # عشان الموظف يشوف كل اللي فعلياً جوا المكان ويقدر يقفلهم.
    c.execute("""
        SELECT
            S.name        AS student_name,
            R.name        AS room_name,
            V.check_in_time,
            V.check_out_time,
            V.duration_hours,
            V.total_cost,
            V.total_discount,
            V.payment_method,
            V.visit_id,
            V.shift_id
        FROM Visits V
        JOIN Students S ON V.student_id = S.student_id
        JOIN Rooms    R ON V.room_id    = R.room_id
        WHERE V.shift_id = ? OR V.check_out_time IS NULL
        ORDER BY V.check_in_time ASC
    """, (shift_id,))
    raw_visits = c.fetchall()

    # لكل زيارة نجيب مبيعاتها من الكافيتريا
    # فصل الزيارات: خرجوا vs لسه جوا
    checked_out_visits = []
    still_inside_visits = []

    for v in raw_visits:
        c.execute("""
            SELECT P.name, SL.quantity, SL.total_price
            FROM Sales SL JOIN Products P ON SL.product_id = P.product_id
            WHERE SL.visit_id = ?
        """, (v['visit_id'],))
        sale_items = c.fetchall()
        sales_summary = ", ".join(
            f"{row['name']} ×{row['quantity']}" for row in sale_items
        ) if sale_items else "—"
        sales_total = sum(row['total_price'] for row in sale_items)

        check_in_fmt = "—"
        if v['check_in_time']:
            try:
                check_in_fmt = datetime.datetime.strptime(v['check_in_time'], "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
            except ValueError:
                check_in_fmt = str(v['check_in_time'])

        check_out_fmt = "(لم يخرج بعد)"
        if v['check_out_time']:
            try:
                check_out_fmt = datetime.datetime.strptime(v['check_out_time'], "%Y-%m-%d %H:%M:%S").strftime("%H:%M")
            except ValueError:
                check_out_fmt = str(v['check_out_time'])

        time_cost = v['total_cost'] or 0
        total_discount = v['total_discount'] or 0
        
        # Calculate final total paid (time + cafeteria - discount)
        total_paid = max(0, (time_cost + sales_total) - total_discount)

        entry = {
            'student_name':   v['student_name'],
            'room_name':      v['room_name'],
            'check_in_time':  check_in_fmt,
            'check_out_time': check_out_fmt,
            'duration_hours': round(v['duration_hours'] or 0, 2),
            'total_cost':     time_cost,
            'total_discount': total_discount,
            'payment_method': v['payment_method'] or '—',
            'sales_summary':  sales_summary,
            'sales_total':    sales_total,
            'total_paid':     total_paid,
            'is_checked_out': v['check_out_time'] is not None,
        }

        if v['check_out_time']:
            checked_out_visits.append(entry)
        else:
            still_inside_visits.append(entry)

    conn.close()
    
    summary = {
        'rev_shared':       rev_shared,
        'rev_private':      rev_private,
        'cafeteria_sales':  cafeteria_sales,
        'cafeteria_cash':   cafeteria_cash_total,
        'cafeteria_visit':  cafeteria_visit_total,
        'package_sales':    package_sales,
        'expenses':         expenses,
        'discounts':        total_discounts,
        'manual_discounts': manual_discounts,
        'net_cash':         net_cash,
        'shift_id':         shift_id,
        'employee':         session.get('username'),
    }

    return render_template('shift_summary_preview.html', summary=summary,
                           checked_out_visits=checked_out_visits,
                           still_inside_visits=still_inside_visits,
                           cafeteria_breakdown=cafeteria_breakdown)
@app.route("/add_sale/<int:visit_id>", methods=['GET', 'POST'])
def add_sale(visit_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    c = conn.cursor()
    
    student_name = None
    if visit_id != 0:
        c.execute("SELECT S.name FROM Visits V JOIN Students S ON V.student_id=S.student_id WHERE V.visit_id=?", (visit_id,))
        row = c.fetchone()
        if row: student_name = row['name']

    # --- إعدادات كروت النت ---
    INTERNET_CARD_NAME = "كارت نت"
    c.execute("SELECT setting_value FROM Settings WHERE setting_key='internet_free_limit'")
    setting_row = c.fetchone()
    FREE_LIMIT = int(setting_row['setting_value']) if setting_row else 2

    if request.method == 'POST':
        # =========================================================
        # 🔥 (تصحيح الخطأ) التأكد من وجود وردية قبل البيع
        # =========================================================
        shift_id = session.get('active_shift_id')
        
        # لو مفيش وردية في السيشن (زي حالة المدير أو نسي يفتح)
        if not shift_id:
            # 1. دور في الداتا بيز يمكن فيه وردية مفتوحة والسيشن نسيها
            c.execute("SELECT shift_id FROM Shifts WHERE user_id = ? AND end_time IS NULL", (session['user_id'],))
            existing_shift = c.fetchone()
            
            if existing_shift:
                shift_id = existing_shift['shift_id']
            else:
                # 2. لو مفيش خالص، افتح وردية جديدة فوراً (عشان البيعة متضربش)
                start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                c.execute("INSERT INTO Shifts (user_id, start_time) VALUES (?, ?)", (session['user_id'], start_time))
                conn.commit()
                shift_id = c.lastrowid
            
            # سجل الوردية في السيشن عشان العمليات الجاية
            session['active_shift_id'] = shift_id
        # =========================================================

        total = 0
        skipped = []
        c.execute("SELECT * FROM Products")
        prods = c.fetchall()

        for p in prods:
            pid = p['product_id']
            qty = int(request.form.get(f"quantity_{pid}", 0))

            if qty > 0:
                if p['stock_quantity'] < qty:
                    skipped.append(f"{p['name']} (المتاح: {p['stock_quantity']})")
                    continue

                unit_price = p['sale_price']
                line_total = 0

                # منطق كروت النت
                if visit_id != 0 and INTERNET_CARD_NAME in p['name']:
                    c.execute("SELECT SUM(quantity) FROM Sales WHERE visit_id=? AND product_id=?", (visit_id, pid))
                    result = c.fetchone()[0]
                    prev_qty = result if result else 0
                    
                    for i in range(1, qty + 1):
                        if (prev_qty + i) <= FREE_LIMIT:
                            line_total += 0
                        else:
                            line_total += unit_price
                else:
                    line_total = qty * unit_price

                total += line_total
                v_save = visit_id if visit_id != 0 else None
                
                # التسجيل باستخدام shift_id المضمون
                c.execute("INSERT INTO Sales (product_id, user_id, shift_id, quantity, total_price, visit_id) VALUES (?, ?, ?, ?, ?, ?)",
                          (pid, session['user_id'], shift_id, qty, line_total, v_save))
                
                # خصم المخزون
                c.execute("UPDATE Products SET stock_quantity = stock_quantity - ? WHERE product_id=?", (qty, pid))

        conn.commit()
        conn.close()
        if skipped:
            flash("عفواً، المخزون لا يكفي لهذه المنتجات فتم تجاهلها: " + "، ".join(skipped), "error")
        flash(f"تم تسجيل مبيعات بقيمة {total} ج", "success")
        return redirect(url_for('dashboard'))

    c.execute("SELECT * FROM Products ORDER BY name")
    prods = c.fetchall()
    conn.close()
    return render_template('add_sale.html', products=prods, student_name=student_name, visit_id=visit_id)
@app.route("/log_expense", methods=['GET', 'POST'])
def log_expense():
    # 1. حماية: لازم يكون مسجل دخول
    if 'user_id' not in session: 
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()

    if request.method == 'POST':
        # 2. حماية: لازم يكون فيه وردية مفتوحة
        shift_id = session.get('active_shift_id')
        if not shift_id:
            flash("⚠️ لا توجد وردية مفتوحة لتسجيل المصروف!", "error")
            return redirect(url_for('dashboard'))

        # استلام البيانات
        desc = request.form['description']
        try:
            amt = float(request.form['amount'])
        except ValueError:
            flash("❌ برجاء إدخال مبلغ صحيح", "error")
            return redirect(url_for('log_expense'))

        # 3. إدخال المصروف في الداتا بيز
        # (بستخدم user_id و shift_id من السيشن)
        c.execute("INSERT INTO Expenses (user_id, shift_id, description, amount) VALUES (?, ?, ?, ?)", 
                  (session['user_id'], shift_id, desc, amt))
        
        # 4. تحديث المخزن (الجزء العبقري في كودك)
        # بنجيب كل المنتجات عشان نعرف أسمائها والـ ID
        c.execute("SELECT * FROM Products")
        prods = c.fetchall()
        
        for p in prods:
            # بنشوف هل الموظف كتب رقم في خانة المنتج ده ولا لأ
            # اسم الخانة في الـ HTML هو stock_add_ID
            input_name = f"stock_add_{p['product_id']}"
            qty_added = float(request.form.get(input_name, 0))
            
            if qty_added > 0:
                # تزويد العدد في المخزن
                c.execute("UPDATE Products SET stock_quantity = stock_quantity + ? WHERE product_id=?", 
                          (qty_added, p['product_id']))
        
        conn.commit()
        conn.close()
        
        flash(f"✅ تم تسجيل المصروف ({amt} ج) وتحديث المخزن بنجاح", "success")
        return redirect(url_for('dashboard'))
        
    # في حالة الـ GET (فتح الصفحة)
    # لازم نبعت قائمة المنتجات عشان الجدول يظهر
    c.execute("SELECT * FROM Products ORDER BY name")
    prods = c.fetchall()
    conn.close()
    
    return render_template('log_expense.html', products=prods)


# --- حذف المنتجات ---
@app.route("/delete_product/<int:product_id>", methods=['POST'])
def delete_product(product_id):
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM Products WHERE product_id=?", (product_id,))
        conn.commit()
        flash("تم حذف المنتج من المخزن بنجاح.", "success")
    except sqlite3.Error as e:
        flash(f"لا يمكن حذف المنتج لأنه مرتبط بعمليات بيع سابقة. (الخطأ: {e})", "error")
        
    conn.close()
    return redirect(url_for('manage_products'))
# --- الاشتراكات ---
@app.route("/sell_subscription", methods=['GET', 'POST'])
def sell_subscription():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    c = conn.cursor()
    pre_sid = request.args.get('student_id')
    
    if request.method == 'POST':
        if 'search_query' in request.form:
            q = request.form['search_query']
            c.execute("SELECT * FROM Students WHERE phone LIKE ? OR name LIKE ?", (f"%{q}%", f"%{q}%"))
            studs = c.fetchall()
            c.execute("SELECT * FROM MembershipTypes")
            types = c.fetchall()
            return render_template('sell_subscription.html', students=studs, types=types)
        elif 'confirm_sub' in request.form:
            sid = request.form['student_id']
            tid = request.form['type_id']

            if not sid:
                c.execute("SELECT * FROM MembershipTypes")
                types = c.fetchall()
                flash("يرجى اختيار الطالب أولاً قبل تفعيل الباقة.", "error")
                return render_template('sell_subscription.html', students=[], types=types)

            shift_id = session.get('active_shift_id')
            if not shift_id:
                flash("لا يمكن بيع الباقة بدون وردية مفتوحة.", "error")
                return redirect(url_for('dashboard'))

            c.execute("SELECT * FROM MembershipTypes WHERE type_id=?", (tid,))
            pkg = c.fetchone()
            if not pkg:
                flash("نوع الباقة غير موجود.", "error")
                return redirect(url_for('sell_subscription'))

            start = datetime.datetime.now()
            end = start + datetime.timedelta(days=pkg['days_valid'])
            c.execute("INSERT INTO StudentSubscriptions (student_id, type_id, start_date, end_date, remaining_hours) VALUES (?, ?, ?, ?, ?)",
                      (sid, tid, start, end, pkg['total_hours']))

            # تسجيل بيع الباقة في جدول المبيعات ليظهر في إجمالي كاش الوردية.
            c.execute("SELECT product_id FROM Products WHERE name = ?", ('اشتراك باقة',))
            package_product = c.fetchone()
            if package_product:
                package_product_id = package_product['product_id']
            else:
                c.execute(
                    "INSERT INTO Products (name, purchase_price, sale_price, stock_quantity) VALUES (?, ?, ?, ?)",
                    ('اشتراك باقة', 0.0, 0.0, 0)
                )
                package_product_id = c.lastrowid

            c.execute(
                "INSERT INTO Sales (product_id, user_id, shift_id, quantity, total_price, visit_id) VALUES (?, ?, ?, ?, ?, NULL)",
                (package_product_id, session['user_id'], shift_id, 1, pkg['price'])
            )

            conn.commit()
            flash("تم تفعيل الباقة", "success")
            return redirect(url_for('dashboard'))
            
    c.execute("SELECT * FROM MembershipTypes")
    types = c.fetchall()
    studs = []
    if pre_sid:
        c.execute("SELECT * FROM Students WHERE student_id=?", (pre_sid,))
        studs = c.fetchall()
    conn.close()
    return render_template('sell_subscription.html', types=types, students=studs)

# --- الإدارة (المدير) ---





@app.route("/manage_settings", methods=['GET', 'POST'])
def manage_settings():
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))

    # مفاتيح الشرائح مع قيمها الافتراضية
    TIER_KEYS = {
        'tier_price_1h':  10,
        'tier_price_3h':  25,
        'tier_price_6h':  35,
        'tier_price_9h':  40,
        'tier_price_12h': 50,
        'tier_price_16h': 60,
        'tier_price_24h': 70,
    }

    conn = get_db()
    c = conn.cursor()

    if request.method == 'POST':
        # --- حفظ حد كروت النت ---
        new_limit = request.form.get('internet_free_limit', '2')
        c.execute("REPLACE INTO Settings (setting_key, setting_value) VALUES ('internet_free_limit', ?)", (new_limit,))

        # --- حفظ سقف الخصم الخاص اللي يقدر الموظف (غير المدير) يعمله من غير رجوع للمدير ---
        new_cap = request.form.get('employee_discount_cap', '15')
        c.execute("REPLACE INTO Settings (setting_key, setting_value) VALUES ('employee_discount_cap', ?)", (new_cap,))

        # --- حفظ أسعار الشرائح ---
        for key in TIER_KEYS:
            value = request.form.get(key)
            if value is not None:
                c.execute("REPLACE INTO Settings (setting_key, setting_value) VALUES (?, ?)", (key, value))

        conn.commit()
        flash("تم تحديث الإعدادات بنجاح ✅", "success")

    # --- قراءة القيم الحالية للعرض ---
    # حد كروت النت
    c.execute("SELECT setting_value FROM Settings WHERE setting_key='internet_free_limit'")
    row = c.fetchone()
    current_limit = int(row['setting_value']) if row else 2

    # سقف الخصم الخاص للموظف
    c.execute("SELECT setting_value FROM Settings WHERE setting_key='employee_discount_cap'")
    row = c.fetchone()
    employee_discount_cap = float(row['setting_value']) if row else 15.0

    # أسعار الشرائح
    tier_prices = {}
    for key, default in TIER_KEYS.items():
        c.execute("SELECT setting_value FROM Settings WHERE setting_key=?", (key,))
        row = c.fetchone()
        tier_prices[key] = float(row['setting_value']) if row else default

    conn.close()
    return render_template('manage_settings.html', current_limit=current_limit,
                           employee_discount_cap=employee_discount_cap, tier_prices=tier_prices)

@app.route("/manage_students")
def manage_students():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    q = request.args.get('query', '')
    
    if q:
        pat = f"%{q}%"
        c.execute("""
            SELECT * FROM Students 
            WHERE name LIKE ? 
               OR phone LIKE ? 
               OR college LIKE ? 
               OR year LIKE ? 
            ORDER BY name
        """, (pat, pat, pat, pat))
    else:
        c.execute("SELECT * FROM Students ORDER BY name")
        
    students = c.fetchall()
    conn.close()
    
    return render_template('manage_students.html', students=students, query=q)

@app.route("/delete_student/<int:student_id>", methods=['POST'])
def delete_student(student_id):
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    
    conn = get_db()
    c = conn.cursor()
    
    # التحقق من عدم وجود زيارة نشطة للطالب
    c.execute("SELECT visit_id FROM Visits WHERE student_id = ? AND check_out_time IS NULL", (student_id,))
    active_visit = c.fetchone()
    
    if active_visit:
        conn.close()
        flash("لا يمكن حذف طالب متواجد حالياً في مساحة العمل. قم بإنهاء زيارته أولاً.", "error")
        return redirect(url_for('manage_students'))
        
    # إذا لم يكن هناك زيارة نشطة، قم بحذفه
    c.execute("DELETE FROM Students WHERE student_id = ?", (student_id,))
    conn.commit()
    conn.close()
    
    flash("تم حذف الطالب بنجاح", "success")
    return redirect(url_for('manage_students'))


@app.route("/import_students", methods=['POST'])
def import_students():
    """استيراد الطلاب من ملف Excel"""
    if 'user_id' not in session:
        return redirect(url_for('login'))


    # الأعمدة المطلوبة بالترتيب (يجب أن تكون موجودة في الصف الأول)
    REQUIRED_COLS = {'name', 'phone', 'college', 'year'}

    file = request.files.get('excel_file')
    if not file or file.filename == '':
        flash("⚠️ لم يتم اختيار أي ملف.", "error")
        return redirect(url_for('manage_students'))

    if not file.filename.endswith(('.xlsx', '.xls')):
        flash("⚠️ الملف يجب أن يكون بصيغة Excel (.xlsx أو .xls).", "error")
        return redirect(url_for('manage_students'))

    conn = None
    wb = None
    try:
        wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
        ws = wb.active

        # قراءة رأس الجدول (الصف الأول)
        header_row = [str(cell.value).strip().lower() if cell.value else '' for cell in next(ws.iter_rows(min_row=1, max_row=1))]

        # التحقق من وجود الأعمدة المطلوبة
        missing = REQUIRED_COLS - set(header_row)
        if missing:
            flash(
                f"❌ خطأ في ترويسة الملف: الأعمدة التالية مفقودة أو غلط في الاسم: "
                f"{', '.join(sorted(missing))}. "
                f"الأعمدة الموجودة في ملفك: {', '.join([h for h in header_row if h])}",
                "error"
            )
            return redirect(url_for('manage_students'))

        # خريطة (اسم العمود → رقم العمود)
        col_map = {name: idx for idx, name in enumerate(header_row)}

        conn = get_db()
        c = conn.cursor()

        inserted = 0
        skipped_dup = 0
        skipped_empty = 0
        errors = []

        for row_num, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            name    = str(row[col_map['name']]).replace('\u00a0', ' ').strip()   if row[col_map['name']]    else ''
            phone   = str(row[col_map['phone']]).replace('\u00a0', '').strip()  if row[col_map['phone']]   else ''
            college = str(row[col_map['college']]).strip() if row[col_map['college']] else ''
            year    = str(row[col_map['year']]).strip()   if row[col_map['year']]    else ''

            # تنظيف: لو القيمة "None" تحويلها لفراغ
            name    = '' if name == 'None' else name
            phone   = '' if phone == 'None' else phone
            college = '' if college == 'None' else college
            year    = '' if year == 'None' else year

            # تخطي الصفوف الفارغة
            if not name and not phone:
                skipped_empty += 1
                continue

            # التحقق من الحقول الإلزامية
            if not name:
                errors.append(f"صف {row_num}: الاسم فارغ")
                continue
            if not phone:
                errors.append(f"صف {row_num}: رقم التليفون فارغ (الطالب: {name})")
                continue

            # التحقق من التكرار (نفس رقم التليفون) مع تجاهل المسافات الطرفية
            c.execute("SELECT student_id FROM Students WHERE TRIM(phone) = ?", (phone,))
            if c.fetchone():
                skipped_dup += 1
                continue

            # إدراج الطالب
            c.execute(
                "INSERT INTO Students (name, phone, college, year) VALUES (?, ?, ?, ?)",
                (name, phone, college, year)
            )
            inserted += 1

        conn.commit()

        # رسالة النتيجة
        msg_parts = []
        if inserted:
            msg_parts.append(f"✅ تم إضافة {inserted} طالب بنجاح")
        if skipped_dup:
            msg_parts.append(f"⏭️ تخطي {skipped_dup} (رقم تليفون مكرر)")
        if skipped_empty:
            msg_parts.append(f"🔘 تخطي {skipped_empty} صف فارغ")
        if errors:
            msg_parts.append(f"⚠️ {len(errors)} خطأ: " + " | ".join(errors[:5]))  # أول 5 أخطاء فقط

        final_msg = " — ".join(msg_parts) if msg_parts else "لم يتم إضافة أي طالب."
        flash(final_msg, "success" if inserted else "warning")

    except Exception as e:
        if conn:
            conn.rollback()
        flash(f"حدث خطأ أثناء الاستيراد: {str(e)}", "error")
        return redirect(url_for('manage_students'))
    finally:
        if conn:
            conn.close()
        if wb:
            wb.close()

    return redirect(url_for('manage_students'))


@app.route('/clean_duplicates')
def clean_duplicates():
    if session.get('role') != 'manager':
        return redirect(url_for('dashboard'))

    conn = get_db()
    c = conn.cursor()

    try:
        c.execute("BEGIN")

        # نجلب فقط الأسماء المكررة لتقليل حجم المعالجة
        c.execute("""
            SELECT
                student_id,
                TRIM(name) AS norm_name,
                TRIM(COALESCE(phone, '')) AS norm_phone
            FROM Students
            WHERE TRIM(COALESCE(name, '')) <> ''
              AND TRIM(name) IN (
                    SELECT TRIM(name)
                    FROM Students
                    WHERE TRIM(COALESCE(name, '')) <> ''
                    GROUP BY TRIM(name)
                    HAVING COUNT(*) > 1
              )
            ORDER BY norm_name, student_id
        """)
        rows = c.fetchall()

        by_name = {}
        for row in rows:
            raw_phone = row['norm_phone']
            normalized_phone = raw_phone.replace(' ', '')
            by_name.setdefault(row['norm_name'], []).append({
                'student_id': row['student_id'],
                'raw_phone': raw_phone,
                'phone': normalized_phone
            })

        to_delete = set()

        for _, group in by_name.items():
            # (1) حذف التكرارات المطابقة تماماً (name + phone)
            phone_buckets = {}
            for rec in group:
                phone_buckets.setdefault(rec['phone'], []).append(rec['student_id'])

            for ids in phone_buckets.values():
                ids.sort()
                if len(ids) > 1:
                    to_delete.update(ids[1:])

            # (2) حذف الرقم المعكوس إذا كان نفس الاسم
            active = [rec for rec in group if rec['student_id'] not in to_delete]
            phone_to_rec = {}
            for rec in active:
                p = rec['phone']
                if p and p not in phone_to_rec:
                    phone_to_rec[p] = rec

            for phone, rec in list(phone_to_rec.items()):
                reversed_phone = phone[::-1]
                if not phone or reversed_phone == phone:
                    continue
                if reversed_phone not in phone_to_rec:
                    continue

                # لمنع فحص نفس الزوج مرتين
                if phone > reversed_phone:
                    continue

                rec_a = rec
                rec_b = phone_to_rec[reversed_phone]

                # القرار يعتمد على الرقم بدون مسافات
                a_starts_zero = rec_a['phone'].startswith('0')
                b_starts_zero = rec_b['phone'].startswith('0')

                if a_starts_zero and not b_starts_zero:
                    to_delete.add(rec_b['student_id'])
                elif b_starts_zero and not a_starts_zero:
                    to_delete.add(rec_a['student_id'])

        # حماية: لا تحذف أي طالب له أي أثر حقيقي في النظام (زيارة، اشتراك، أو كوبون)
        # حتى لو طابق شرط "التكرار" — عشان منفقدش بيانات أو نكسر زيارة شغالة دلوقتي.
        if to_delete:
            c.execute("SELECT DISTINCT student_id FROM Visits")
            has_visits = {row['student_id'] for row in c.fetchall()}
            c.execute("SELECT DISTINCT student_id FROM StudentSubscriptions")
            has_subs = {row['student_id'] for row in c.fetchall()}
            c.execute("SELECT DISTINCT student_id FROM Coupons WHERE student_id IS NOT NULL")
            has_coupons = {row['student_id'] for row in c.fetchall()}
            has_history = has_visits | has_subs | has_coupons
            to_delete -= has_history

        if to_delete:
            c.executemany(
                "DELETE FROM Students WHERE student_id = ?",
                [(sid,) for sid in sorted(to_delete)]
            )

        deleted_count = len(to_delete)
        conn.commit()
        flash(f"✅ تم حذف {deleted_count} سجل مكرر بنجاح (تم تجاهل أي تكرار له زيارات أو اشتراكات أو كوبونات سابقة حفاظاً على البيانات).", "success")
    except Exception as e:
        conn.rollback()
        flash(f"❌ حدث خطأ أثناء حذف التكرارات: {str(e)}", "error")
    finally:
        conn.close()

    return redirect(url_for('manage_students'))




# (3) دالة استقبال الخصم اليدوي
@app.route("/apply_manual_discount/<int:visit_id>", methods=['POST'])
def apply_manual_discount(visit_id):
    if 'user_id' not in session: return redirect(url_for('login'))

    # بناخد النوع (نسبة ولا مبلغ) والقيمة
    m_type = request.form.get('manual_type', 'fixed')
    m_value = float(request.form.get('manual_value', 0))
    
    # بنرجع للفاتورة ومعانا البيانات دي في الرابط
    return redirect(url_for('show_invoice', visit_id=visit_id, m_type=m_type, m_value=m_value))

@app.route("/edit_student/<int:student_id>", methods=['GET', 'POST'])
def edit_student(student_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    c = conn.cursor()
    
    if request.method == 'POST':
        # (تعديل) هنا ضفنا year للقيمة اللي هتتحدث
        c.execute("""
            UPDATE Students 
            SET name=?, phone=?, college=?, year=? 
            WHERE student_id=?
        """, (request.form['name'], request.form['phone'], request.form['college'], request.form['year'], student_id))
        
        conn.commit()
        conn.close()
        return redirect(url_for('manage_students'))
        
    c.execute("SELECT * FROM Students WHERE student_id=?", (student_id,))
    std = c.fetchone()
    conn.close()
    return render_template('edit_student.html', student=std)

@app.route("/manage_products", methods=['GET', 'POST'])
def manage_products():
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    if request.method == 'POST':
        c.execute("INSERT INTO Products (name, purchase_price, sale_price, stock_quantity) VALUES (?, ?, ?, ?)",
                  (request.form['name'], float(request.form['purchase_price']), float(request.form['sale_price']), float(request.form['stock_quantity'])))
        conn.commit()
    c.execute("SELECT * FROM Products ORDER BY name")
    prods = c.fetchall()
    conn.close()
    return render_template('manage_products.html', products=prods)

@app.route("/manage_users", methods=['GET', 'POST'])
def manage_users():
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    if request.method == 'POST':
        hashed = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
        try:
            c.execute("INSERT INTO Users (username, password, role) VALUES (?, ?, ?)",
                      (request.form['username'], hashed, request.form['role']))
            conn.commit()
            flash("تم إضافة المستخدم بنجاح.", "success")
        except sqlite3.IntegrityError:
            flash("اسم المستخدم موجود بالفعل، اختر اسماً آخر.", "error")
    c.execute("SELECT * FROM Users WHERE role='employee'")
    emps = c.fetchall()
    conn.close()
    return render_template('manage_users.html', employees=emps)



@app.route("/edit_sale_quantity/<int:sale_id>", methods=['POST'])
def edit_sale_quantity(sale_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    new_quantity = int(request.form.get('new_quantity', 0))
    if new_quantity <= 0:
        flash("الكمية يجب أن تكون 1 أو أكثر. للحذف استخدم زر الحذف.", "error")
        # هنرجع لنفس الصفحة، محتاجين نجيب visit_id
        # (للتبسيط، هنعتمد ان الـ referer هيرجعنا، او نجيبها من الداتا بيز)
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT visit_id FROM Sales WHERE sale_id=?", (sale_id,))
        row = c.fetchone()
        conn.close()
        if row and row['visit_id']:
             return redirect(url_for('show_invoice', visit_id=row['visit_id']))
        return redirect(url_for('dashboard'))

    conn = get_db()
    c = conn.cursor()
    
    # 1. هات تفاصيل البيعة القديمة
    c.execute("""
        SELECT S.*, P.sale_price, P.stock_quantity, V.check_out_time 
        FROM Sales S 
        JOIN Products P ON S.product_id = P.product_id 
        LEFT JOIN Visits V ON S.visit_id = V.visit_id 
        WHERE S.sale_id = ?
    """, (sale_id,))
    sale = c.fetchone()
    
    if not sale:
        conn.close()
        return redirect(url_for('dashboard'))

    # 2. التحقق من الصلاحية
    is_manager = (session.get('role') == 'manager')
    is_visit_open = (sale['visit_id'] is not None and sale['check_out_time'] is None)
    
    if is_manager or is_visit_open:
        old_quantity = sale['quantity']
        quantity_diff = new_quantity - old_quantity # لو موجب يبقى زودنا، لو سالب يبقى نقصنا
        
        # التأكد من المخزون (في حالة الزيادة فقط)
        if quantity_diff > 0 and sale['stock_quantity'] < quantity_diff:
            flash(f"عفواً، الكمية المتاحة في المخزون لا تكفي للزيادة (المتاح: {sale['stock_quantity']})", "error")
        else:
            # أ. تحديث المخزون (بالعكس: لو زودنا بيع بننقص مخزون)
            c.execute("UPDATE Products SET stock_quantity = stock_quantity - ? WHERE product_id=?", 
                      (quantity_diff, sale['product_id']))
            
            # ب. إعادة حساب السعر الإجمالي للصنف (مهم عشان كروت النت وغيره)
            # ملاحظة: منطق كروت النت المعقد (أول 2 مجاناً) محتاج معالجة خاصة هنا.
            # للتبسيط: هنحسب السعر الجديد = الكمية الجديدة * سعر الوحدة (إلا لو كارت نت)
            
            # --- منطق إعادة الحساب (يدعم كروت النت) ---
            # --- منطق احترافي لإعادة الحساب (يدعم كروت النت) ---
            INTERNET_CARD_NAME = "كارت نت"
            new_total_price = 0
            
            # بنجيب اسم المنتج عشان نتأكد هو كارت نت ولا حاجة تانية
            c.execute("SELECT name FROM Products WHERE product_id=?", (sale['product_id'],))
            prod_name = c.fetchone()['name']
            
            if INTERNET_CARD_NAME in prod_name and sale['visit_id']:
                # 1. بنشوف الطالب ده أخد كام كارت في عمليات تانية "غير" اللي بنعدلها دلوقتي
                c.execute("""SELECT SUM(quantity) FROM Sales
                             WHERE visit_id=? AND product_id=? AND sale_id != ?""",
                          (sale['visit_id'], sale['product_id'], sale_id))
                prev_qty = c.fetchone()[0] or 0

                # 1.5. بنجيب حد الكروت المجانية من الإعدادات (نفس المنطق المستخدم في إضافة البيع)
                c.execute("SELECT setting_value FROM Settings WHERE setting_key='internet_free_limit'")
                limit_row = c.fetchone()
                free_limit = int(limit_row['setting_value']) if limit_row else 2

                # 2. بنحسب سعر الكمية الجديدة حتة حتة بناءً على اللي أخده قبل كدة
                for i in range(1, new_quantity + 1):
                    if (prev_qty + i) <= free_limit:
                        new_total_price += 0
                    else:
                        new_total_price += sale['sale_price'] # اللي بعد الحد المجاني
            else:
                # 3. لو منتج عادي (شاي، قهوة) يحسب الكمية في السعر فوراً
                new_total_price = new_quantity * sale['sale_price']
            
            # --- (جزء مهم جداً) تحديث إجمالي الوردية بالفرق ---
            price_diff = new_total_price - sale['total_price']
            if sale['shift_id']:
                c.execute("UPDATE Shifts SET revenue_sales = revenue_sales + ? WHERE shift_id = ?", 
                          (price_diff, sale['shift_id']))
            
            # ج. تحديث البيعة بالقيم الجديدة
            c.execute("UPDATE Sales SET quantity=?, total_price=? WHERE sale_id=?", 
                      (new_quantity, new_total_price, sale_id))
            
            conn.commit()
            flash("تم تعديل الكمية والسعر بنجاح.", "success")

    else:
        flash("عفواً، لا يمكن تعديل فاتورة مغلقة.", "error")
    
    conn.close()
    
    # 3. العودة للفاتورة
    if sale['visit_id']:
        return redirect(url_for('show_invoice', visit_id=sale['visit_id']))
    return redirect(url_for('dashboard'))

@app.route("/delete_sale_item/<int:sale_id>", methods=['POST'])
def delete_sale_item(sale_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # 1. هات تفاصيل البيعة وحالة الزيارة المرتبطة بيها
    c.execute("""
        SELECT S.*, V.check_out_time 
        FROM Sales S 
        LEFT JOIN Visits V ON S.visit_id = V.visit_id 
        WHERE S.sale_id = ?
    """, (sale_id,))
    sale = c.fetchone()
    
    if not sale:
        conn.close()
        return redirect(url_for('dashboard'))

    # 2. تحديد الصلاحية
    is_manager = (session.get('role') == 'manager')
    # الزيارة مفتوحة = يعني لسه الطالب موجود = الموظف بيصحح غلطة دلوقتي
    is_visit_open = (sale['visit_id'] is not None and sale['check_out_time'] is None)
    
    # المسموح لهم بالحذف: المدير (في أي وقت) أو الموظف (لو الزيارة لسه مفتوحة)
    if is_manager or is_visit_open:
        # أ. رجع الكمية للمخزن
        c.execute("UPDATE Products SET stock_quantity = stock_quantity + ? WHERE product_id=?",
                  (sale['quantity'], sale['product_id']))

        # ب. اخصم قيمة البيعة المحذوفة من إجمالي مبيعات الوردية
        if sale['shift_id']:
            c.execute("UPDATE Shifts SET revenue_sales = revenue_sales - ? WHERE shift_id = ?",
                      (sale['total_price'], sale['shift_id']))

        # ج. احذف البيعة
        c.execute("DELETE FROM Sales WHERE sale_id=?", (sale_id,))
        conn.commit()
        flash("تم حذف المنتج من الفاتورة.", "success")
    else:
        flash("عفواً، لا يمكن تعديل فاتورة تم إغلاقها ومحاسبتها (صلاحية مدير فقط).", "error")
    
    conn.close()

    # 3. التوجيه الذكي (نرجع لنفس الصفحة اللي كنا فيها)
    # لو الزيارة لسه مفتوحة، نرجع لفاتورة الطالب
    if is_visit_open:
        return redirect(url_for('show_invoice', visit_id=sale['visit_id']))
    
    # لو الزيارة مقفولة (مدير بيعدل)، نرجع لتفاصيل الوردية
    if sale['shift_id']:
        return redirect(url_for('shift_details', shift_id=sale['shift_id']))
        
    return redirect(url_for('dashboard'))


@app.route("/delete_user/<int:user_id>", methods=['POST'])
def delete_user(user_id):
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    if user_id != session['user_id']:
        c.execute("DELETE FROM Users WHERE user_id=?", (user_id,))
        conn.commit()
    conn.close()
    return redirect(url_for('manage_users'))

@app.route("/manage_rooms", methods=['GET', 'POST'])
def manage_rooms():
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    if request.method == 'POST':
        c.execute("INSERT INTO Rooms (name, hourly_rate) VALUES (?, ?)", (request.form['name'], float(request.form['hourly_rate'])))
        conn.commit()
    c.execute("SELECT * FROM Rooms")
    rooms = c.fetchall()
    conn.close()
    return render_template('manage_rooms.html', rooms=rooms)

@app.route("/edit_room/<int:room_id>", methods=['GET', 'POST'])
def edit_room(room_id):
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    if request.method == 'POST':
        c.execute("UPDATE Rooms SET hourly_rate=? WHERE room_id=?", (float(request.form['hourly_rate']), room_id))
        conn.commit()
        conn.close()
        return redirect(url_for('manage_rooms'))
    c.execute("SELECT * FROM Rooms WHERE room_id=?", (room_id,))
    rm = c.fetchone()
    conn.close()
    return render_template('edit_room.html', room=rm)

# --- الكوبونات والتقارير ---
@app.route("/generate_coupon/<int:student_id>", methods=['GET', 'POST'])
def generate_coupon(student_id):
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM Students WHERE student_id=?", (student_id,))
    student = c.fetchone()
    
    if request.method == 'POST':
        code = request.form['code'].upper()
        dt = request.form['discount_type']
        val = float(request.form['discount_value'])
        exp = request.form['expiry_date'] or None
        c.execute("INSERT INTO Coupons (code, discount_type, discount_value, expiry_date, student_id) VALUES (?, ?, ?, ?, ?)",
                  (code, dt, val, exp, student_id))
        conn.commit()
        
        msg = f"أهلا {student['name']}، ليك كوبون خصم {val}" + ("%" if dt=='percentage' else "ج") + f" الكود: {code}"
        link = f"https://wa.me/20{student['phone'][1:]}?text={urllib.parse.quote(msg)}"
        conn.close()
        return redirect(url_for('coupon_created', student_name=student['name'], coupon_code=code, coupon_value=val, coupon_type=dt, whatsapp_link=link))

    rnd = "GIFT-" + ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5))
    conn.close()
    return render_template('generate_coupon.html', student=student, suggested_code=rnd)

@app.route("/coupon_created")
def coupon_created():
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    whatsapp_link = request.args.get('whatsapp_link', '')
    if not whatsapp_link.startswith('https://wa.me/'):
        whatsapp_link = None
    return render_template('coupon_created.html',
                           student_name=request.args.get('student_name'),
                           coupon_code=request.args.get('coupon_code'),
                           coupon_value=request.args.get('coupon_value'),
                           coupon_type=request.args.get('coupon_type'),
                           whatsapp_link=whatsapp_link)

@app.route("/manage_coupons")
def manage_coupons():
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT C.*, S.name FROM Coupons C LEFT JOIN Students S ON C.student_id=S.student_id ORDER BY is_used")
    coupons = []
    today = datetime.datetime.now().date()
    for row in c.fetchall():
        d = dict(row)
        d['is_expired'] = False
        if d['expiry_date']:
            if datetime.datetime.strptime(d['expiry_date'], '%Y-%m-%d').date() < today:
                d['is_expired'] = True
        coupons.append(d)
    conn.close()
    return render_template('manage_coupons.html', coupons=coupons)

@app.route("/financial_reports")
def financial_reports():
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    d_str = request.args.get('report_date')
    if not d_str: d_str = datetime.date.today().strftime("%Y-%m-%d")
    
    c.execute("""SELECT S.*, U.username FROM Shifts S JOIN Users U ON S.user_id=U.user_id 
                 WHERE S.end_time IS NOT NULL AND DATE(S.start_time)=? ORDER BY S.start_time DESC""", (d_str,))
    shifts = c.fetchall()
    
    c.execute("""SELECT SUM(revenue_hours+revenue_sales) as total_rev, SUM(total_expenses) as total_exp, SUM(total_discounts) as total_disc, SUM(net_cash) as net 
                 FROM Shifts WHERE end_time IS NOT NULL AND DATE(start_time)=?""", (d_str,))
    summ_row = c.fetchone()
    summary = {
        'total_revenue': summ_row['total_rev'] or 0,
        'total_expenses': summ_row['total_exp'] or 0,
        'total_discounts': summ_row['total_disc'] or 0,
        'net_profit': summ_row['net'] or 0
    }
    conn.close()
    return render_template('financial_reports.html', shifts=shifts, summary=summary, selected_date=d_str)

@app.route("/shift_details/<int:shift_id>")
def shift_details(shift_id):
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT S.*, U.username FROM Shifts S JOIN Users U ON S.user_id=U.user_id WHERE S.shift_id=?", (shift_id,))
    shift = c.fetchone()
    if not shift: return redirect(url_for('financial_reports'))
    
    c.execute("""SELECT S.name as student_name, R.name as room_name, V.duration_hours, V.total_cost, V.total_discount, V.visit_id 
                 FROM Visits V JOIN Students S ON V.student_id=S.student_id JOIN Rooms R ON V.room_id=R.room_id 
                 WHERE V.shift_id=? AND V.check_out_time IS NOT NULL""", (shift_id,))
    visits = c.fetchall()
    
    c.execute("SELECT P.name as product_name, S.quantity, S.total_price, S.visit_id FROM Sales S JOIN Products P ON S.product_id=P.product_id WHERE S.shift_id=?", (shift_id,))
    all_sales = c.fetchall()
    
    c.execute("SELECT * FROM Expenses WHERE shift_id=?", (shift_id,))
    expenses = c.fetchall()
    
    sales_map = {}
    cash_sales = []
    for s in all_sales:
        if s['visit_id']:
            sales_map.setdefault(s['visit_id'], []).append(s)
        else:
            cash_sales.append(s)
            
    bills = []
    for v in visits:
        items = sales_map.get(v['visit_id'], [])
        s_total = sum(i['total_price'] for i in items)
        bills.append({
            'visit_details': v,
            'sales_items': items,
            'total_sales': s_total,
            'total_discount': v['total_discount'],
            'grand_total': (v['total_cost'] + s_total) - v['total_discount']
        })
        
    conn.close()
    return render_template('shift_details.html', shift=shift, student_bills=bills, cash_sales=cash_sales, expenses=expenses)

@app.route("/analytics_report")
def analytics_report():
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    
    limit = int(request.args.get('limit', 5))
    d_from = request.args.get('date_from', datetime.date.today().replace(day=1).strftime("%Y-%m-%d"))
    d_to = request.args.get('date_to', datetime.date.today().strftime("%Y-%m-%d"))
    
    d_from_sql = d_from + " 00:00:00"
    d_to_sql = d_to + " 23:59:59"
    
    # ---------------------------------------------------------
    # 1. تقارير الطلاب العاديين (Visits + Spending)
    # ---------------------------------------------------------
    # أ. الأكثر زيارة (طلاب عاديين)
    c.execute("""SELECT S.student_id, S.name, S.phone, S.college, COUNT(V.visit_id) as total_visits 
                 FROM Visits V JOIN Students S ON V.student_id=S.student_id 
                 WHERE V.check_in_time BETWEEN ? AND ? AND S.college != 'Meeting Client'
                 GROUP BY V.student_id ORDER BY total_visits DESC LIMIT ?""", 
                 (d_from_sql, d_to_sql, limit))
    top_visits = c.fetchall()
    
    # ب. الأكثر إنفاقاً (طلاب عاديين)
    c.execute("""
        WITH TC AS (SELECT student_id, SUM(total_cost) as t FROM Visits WHERE check_out_time BETWEEN ? AND ? GROUP BY student_id),
             SC AS (SELECT V.student_id, SUM(total_price) as s FROM Sales SA JOIN Visits V ON SA.visit_id=V.visit_id WHERE SA.sale_time BETWEEN ? AND ? GROUP BY V.student_id)
        SELECT S.student_id, S.name, S.college, COALESCE(TC.t, 0) as total_time_spent, COALESCE(SC.s, 0) as total_sales_spent, (COALESCE(TC.t, 0)+COALESCE(SC.s, 0)) as grand_total_spent
        FROM Students S LEFT JOIN TC ON S.student_id=TC.student_id LEFT JOIN SC ON S.student_id=SC.student_id
        WHERE S.college != 'Meeting Client'
        ORDER BY grand_total_spent DESC LIMIT ?
    """, (d_from_sql, d_to_sql, d_from_sql, d_to_sql, limit))
    top_spenders = c.fetchall()
    
    # ---------------------------------------------------------
    # 2. تقارير عملاء القاعات (Meeting Clients) - (تعديل جديد)
    # ---------------------------------------------------------
    
    # أ. الأكثر إنفاقاً (Meeting Clients Spending)
    c.execute("""
        WITH TC AS (SELECT student_id, SUM(total_cost) as t FROM Visits WHERE check_out_time BETWEEN ? AND ? GROUP BY student_id),
             SC AS (SELECT V.student_id, SUM(total_price) as s FROM Sales SA JOIN Visits V ON SA.visit_id=V.visit_id WHERE SA.sale_time BETWEEN ? AND ? GROUP BY V.student_id)
        SELECT S.student_id, S.name, S.phone, COALESCE(TC.t, 0) as total_time_spent, (COALESCE(TC.t, 0)+COALESCE(SC.s, 0)) as grand_total_spent
        FROM Students S LEFT JOIN TC ON S.student_id=TC.student_id LEFT JOIN SC ON S.student_id=SC.student_id
        WHERE S.college = 'Meeting Client'
        ORDER BY grand_total_spent DESC LIMIT ?
    """, (d_from_sql, d_to_sql, d_from_sql, d_to_sql, limit))
    top_meeting_spending = c.fetchall()

    # ب. الأكثر زيارة (Meeting Clients Visits) - جديد
    c.execute("""SELECT S.student_id, S.name, S.phone, COUNT(V.visit_id) as total_visits 
                 FROM Visits V JOIN Students S ON V.student_id=S.student_id 
                 WHERE V.check_in_time BETWEEN ? AND ? AND S.college = 'Meeting Client'
                 GROUP BY V.student_id ORDER BY total_visits DESC LIMIT ?""", 
                 (d_from_sql, d_to_sql, limit))
    top_meeting_visits = c.fetchall()

    # ---------------------------------------------------------
    # 3. المنتجات
    # ---------------------------------------------------------
    c.execute("""SELECT P.name, SUM(S.quantity) as total_quantity FROM Sales S JOIN Products P ON S.product_id=P.product_id 
                 WHERE S.sale_time BETWEEN ? AND ? GROUP BY S.product_id ORDER BY total_quantity DESC LIMIT ?""", 
                 (d_from_sql, d_to_sql, limit))
    top_prods = c.fetchall()

    # ---------------------------------------------------------
    # 4. تقارير الانقطاع (Absent)
    # ---------------------------------------------------------
    
    # أ. طلاب عاديين (انقطعوا من 10 أيام)
    ten_days_ago = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        SELECT S.student_id, S.name, S.phone, MAX(V.check_in_time) as last_visit
        FROM Students S JOIN Visits V ON S.student_id = V.student_id
        WHERE S.college != 'Meeting Client'
        GROUP BY S.student_id
        HAVING MAX(V.check_in_time) < ?
        ORDER BY last_visit DESC
    """, (ten_days_ago,))
    absent_students = c.fetchall()

    # ب. عملاء قاعات (انقطعوا من 20 يوم) - جديد
    twenty_days_ago = (datetime.datetime.now() - datetime.timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        SELECT S.student_id, S.name, S.phone, MAX(V.check_in_time) as last_visit
        FROM Students S JOIN Visits V ON S.student_id = V.student_id
        WHERE S.college = 'Meeting Client'
        GROUP BY S.student_id
        HAVING MAX(V.check_in_time) < ?
        ORDER BY last_visit DESC
    """, (twenty_days_ago,))
    absent_meeting_clients = c.fetchall()
    
    conn.close()
    return render_template('analytics_report.html', 
                           top_students_by_visits=top_visits, 
                           top_students_by_spending=top_spenders, 
                           # هنا المتغيرات الجديدة
                           top_meeting_spending=top_meeting_spending,
                           top_meeting_visits=top_meeting_visits,
                           absent_meeting_clients=absent_meeting_clients,
                           # المتغيرات القديمة
                           top_products=top_prods,
                           absent_students=absent_students,
                           limit=limit, date_from=d_from, date_to=d_to)


@app.route("/subscriptions_list")
def subscriptions_list():
    # (تعديل) شيلنا شرط المدير عشان الموظف كمان يشوفها
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT SS.*, S.name as student_name, S.phone, MT.name as type_name 
        FROM StudentSubscriptions SS
        JOIN Students S ON SS.student_id = S.student_id
        JOIN MembershipTypes MT ON SS.type_id = MT.type_id
        WHERE SS.is_active = 1
        ORDER BY SS.end_date DESC
    """)
    subs = c.fetchall()
    conn.close()
    return render_template('subscriptions_list.html', subs=subs)

# --- إدارة الحجوزات (Private Rooms) ---
@app.route("/reservations", methods=['GET', 'POST'])
def reservations():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    if request.method == 'POST':
        room_id = request.form['room_id']
        client  = request.form['client_name']
        phone   = request.form['phone']
        start   = request.form['start_time'].replace('T', ' ') + ":00"
        end     = request.form['end_time'].replace('T', ' ') + ":00"
        
        start_dt = datetime.datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        end_dt   = datetime.datetime.strptime(end,   "%Y-%m-%d %H:%M:%S")

        if start_dt < datetime.datetime.now():
            flash("⚠️ لا يمكن الحجز! يجب أن يكون تاريخ ووقت البداية مستقبلياً.", "error")
            conn.close()
            return redirect(url_for('reservations'))
        
        disc_type = request.form.get('discount_type', 'none')
        disc_val  = float(request.form.get('discount_value') or 0)

        # ─── هل هو حجز ثابت متكرر؟ ───
        is_recurring = request.form.get('is_recurring') == '1'

        if is_recurring:
            # أيام الأسبوع المختارة (0=الاثنين ... 6=الأحد بترتيب Python)
            recurring_days = [int(d) for d in request.form.getlist('recurring_days')]
            recurring_end_str = request.form.get('recurring_end_date', '')

            if not recurring_days or not recurring_end_str:
                flash("⚠️ يرجى تحديد أيام الحجز وتاريخ الانتهاء للحجز الثابت.", "error")
                conn.close()
                return redirect(url_for('reservations'))

            recurring_end_dt = datetime.datetime.strptime(recurring_end_str, "%Y-%m-%d")

            # استخراج الوقت فقط من start/end عشان نطبقه على كل يوم
            start_time_only = start_dt.strftime("%H:%M:%S")
            end_time_only   = end_dt.strftime("%H:%M:%S")

            current_day = start_dt.date()
            end_date    = recurring_end_dt.date()

            inserted = 0
            skipped  = 0

            while current_day <= end_date:
                if current_day.weekday() in recurring_days:
                    day_start = f"{current_day} {start_time_only}"
                    day_end   = f"{current_day} {end_time_only}"

                    # فحص التضارب لكل يوم على حدة
                    c.execute("""
                        SELECT reservation_id FROM Reservations
                        WHERE room_id = ? AND status = 'confirmed'
                        AND (
                            (start_time <= ? AND end_time >= ?) OR
                            (start_time <= ? AND end_time >= ?) OR
                            (start_time >= ? AND end_time <= ?)
                        )
                    """, (room_id, day_start, day_start, day_end, day_end, day_start, day_end))

                    if c.fetchone():
                        skipped += 1
                    else:
                        c.execute("""
                            INSERT INTO Reservations
                                (room_id, client_name, phone, start_time, end_time, discount_type, discount_value)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (room_id, client, phone, day_start, day_end, disc_type, disc_val))
                        inserted += 1

                current_day += datetime.timedelta(days=1)

            conn.commit()

            if inserted > 0 and skipped == 0:
                flash(f"✅ تم إنشاء {inserted} حجز متكرر بنجاح!", "success")
            elif inserted > 0:
                flash(f"✅ تم إنشاء {inserted} حجز. (تم تخطي {skipped} يوم بسبب تضارب مع حجز موجود)", "warning")
            else:
                flash("⚠️ لم يتم إنشاء أي حجز! جميع الأيام المختارة متعارضة مع حجوزات موجودة.", "error")

        else:
            # ─── الحجز العادي (الكود الأصلي) ───
            c.execute("""
                SELECT * FROM Reservations 
                WHERE room_id = ? AND status = 'confirmed'
                AND (
                    (start_time <= ? AND end_time >= ?) OR
                    (start_time <= ? AND end_time >= ?) OR
                    (start_time >= ? AND end_time <= ?)
                )
            """, (room_id, start, start, end, end, start, end))
            
            conflict = c.fetchone()
            
            if conflict:
                flash(f"⚠️ لا يمكن الحجز! الغرفة محجوزة بالفعل من {conflict['start_time']} إلى {conflict['end_time']}", "error")
            else:
                c.execute("""
                    INSERT INTO Reservations (room_id, client_name, phone, start_time, end_time, discount_type, discount_value)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (room_id, client, phone, start, end, disc_type, disc_val))
                conn.commit()
                flash("✅ تم تأكيد الحجز بنجاح!", "success")
            
    # عرض الحجوزات القادمة فقط
    c.execute("""
        SELECT R.*, RM.name as room_name 
        FROM Reservations R JOIN Rooms RM ON R.room_id = RM.room_id
        WHERE R.status = 'confirmed' AND R.end_time >= datetime('now')
        ORDER BY R.start_time ASC
    """)
    res_list = c.fetchall()
    
    # نجيب قائمة الغرف عشان الفورم
    c.execute("SELECT * FROM Rooms") 
    rooms = c.fetchall()
    
    conn.close()
   # بنجهز أقل قيمة ممكنة للإدخال في HTML (التاريخ والوقت الحالي)
    today_min = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")

    return render_template('reservations.html', reservations=res_list, rooms=rooms, today_min=today_min)



# --- إدارة الباقات ---

@app.route("/manage_subscription_types")
def manage_subscription_types():
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM MembershipTypes ORDER BY total_hours DESC")
    types = c.fetchall()
    conn.close()
    return render_template('manage_subscription_types.html', types=types)

@app.route("/edit_subscription_type/<int:type_id>", methods=['GET', 'POST'])
def edit_subscription_type(type_id):
    if session.get('role') != 'manager': return redirect(url_for('dashboard'))
    conn = get_db()
    c = conn.cursor()
    
    if request.method == 'POST':
        c.execute("""UPDATE MembershipTypes SET name=?, total_hours=?, days_valid=?, price=? 
                     WHERE type_id=?""",
                  (request.form['name'], float(request.form['total_hours']), 
                   int(request.form['days_valid']), float(request.form['price']), type_id))
        conn.commit()
        conn.close()
        flash("تم تعديل الباقة بنجاح", "success")
        return redirect(url_for('manage_subscription_types'))
        
    c.execute("SELECT * FROM MembershipTypes WHERE type_id=?", (type_id,))
    pkg = c.fetchone()
    conn.close()
    return render_template('edit_subscription_type.html', pkg=pkg)
@app.route("/cancel_reservation/<int:res_id>")
def cancel_reservation(res_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE Reservations SET status='cancelled' WHERE reservation_id=?", (res_id,))
    conn.commit()
    conn.close()
    flash("تم إلغاء الحجز.", "info")
    return redirect(url_for('reservations'))



# ==========================================
# route نقل الطالب (تغيير الغرفة)
# ==========================================
@app.route('/switch_room/<int:visit_id>', methods=['GET', 'POST'])
def switch_room(visit_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # 1. جلب بيانات الزيارة الحالية (باستخدام room_id لحل المشكلة السابقة)
    try:
        c.execute("""
            SELECT Rooms.name, Students.name 
            FROM Visits 
            JOIN Students ON Visits.student_id = Students.student_id 
            JOIN Rooms ON Visits.room_id = Rooms.room_id
            WHERE Visits.visit_id = ?
        """, (visit_id,))
        visit_data = c.fetchone()
    except Exception as e:
        print(f"Error: {e}")
        flash("خطأ في قراءة بيانات الغرفة", "error")
        return redirect(url_for('dashboard'))
    
    if not visit_data:
        flash("زيارة غير موجودة!", "error")
        return redirect(url_for('dashboard'))
        
    current_room = visit_data[0]
    student_name = visit_data[1]

    # ========================================================
    # 2. استبعاد القاعات الخاصة (Private/Meeting) من قائمة النقل
    #    (زي منطق منع الحجز المزدوج في تسجيل الدخول)
    # ========================================================
    c.execute("SELECT name, room_id FROM Rooms")
    all_rooms = [r for r in c.fetchall() if not is_exclusive_room(r['name'])]

    if request.method == 'POST':
        new_room_name = request.form['new_room']

        # نجيب الـ ID للغرفة الجديدة
        c.execute("SELECT room_id FROM Rooms WHERE name = ?", (new_room_name,))
        room_result = c.fetchone()

        if not room_result:
            flash("❌ الغرفة غير موجودة", "error")
        elif is_exclusive_room(new_room_name) and room_has_open_visit(c, room_result['room_id']):
            flash(f"غرفة {new_room_name} مشغولة حالياً بشخص آخر.", "error")
        else:
            new_room_id = room_result['room_id']

            # تحديث الزيارة
            c.execute("UPDATE Visits SET room_id = ? WHERE visit_id = ?", (new_room_id, visit_id))
            conn.commit()
            flash(f"✅ تم نقل الطالب ({student_name}) إلى {new_room_name} بنجاح", "success")

        conn.close()
        return redirect(url_for('dashboard'))

    conn.close()
    return render_template('switch_room.html', 
                         student_name=student_name, 
                         current_room=current_room, 
                         visit_id=visit_id,
                         all_rooms=all_rooms)
    
@app.route("/start_reservation_visit/<int:res_id>", methods=['POST'])
def start_reservation_visit(res_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if 'active_shift_id' not in session:
        flash("يجب فتح وردية أولاً لبدء الزيارة!", "error")
        return redirect(url_for('dashboard'))

    conn = get_db()
    c = conn.cursor()
    
    # 1. هات بيانات الحجز
    c.execute("SELECT * FROM Reservations WHERE reservation_id=?", (res_id,))
    res = c.fetchone()
    
    if not res: return redirect(url_for('reservations'))

    # 2. شوف العميل ده متسجل عندنا قبل كده ولا لأ؟ (بالتليفون)
    c.execute("SELECT student_id FROM Students WHERE phone=?", (res['phone'],))
    student = c.fetchone()
    
    student_id = None
    if student:
        student_id = student['student_id']
    else:
        # لو مش متسجل، سجله حالاً
        c.execute("INSERT INTO Students (name, phone, college) VALUES (?, ?, ?)", 
                  (res['client_name'], res['phone'], 'Meeting Client'))
        student_id = c.lastrowid
    
    # 2.5 التأكد إن القاعة (لو خاصة) مش مشغولة بزيارة تانية دلوقتي
    c.execute("SELECT name FROM Rooms WHERE room_id=?", (res['room_id'],))
    room_row = c.fetchone()
    if room_row and is_exclusive_room(room_row['name']) and room_has_open_visit(c, res['room_id']):
        conn.close()
        flash(f"غرفة {room_row['name']} مشغولة حالياً بشخص آخر.", "error")
        return redirect(url_for('reservations'))

    # 3. ابدأ الزيارة (Check-in) واربطها بالحجز
    check_in_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        c.execute("""
            INSERT INTO Visits (student_id, user_id, shift_id, room_id, check_in_time, reservation_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (student_id, session['user_id'], session['active_shift_id'], res['room_id'], check_in_time, res_id))
        
        # 4. تحديث حالة الحجز لـ "نشط"
        c.execute("UPDATE Reservations SET status='active' WHERE reservation_id=?", (res_id,))
        
        conn.commit()
        flash(f"تم تسجيل دخول {res['client_name']} بنجاح!", "success")
        
    except sqlite3.Error as e:
        flash(f"حدث خطأ: {e}", "error")

    conn.close()
    return redirect(url_for('dashboard'))



# --- إدارة المكتبة والاستعارة ---

@app.route("/library")
def library():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    c = conn.cursor()
    
    # 1. هات كل الكتب
    c.execute("SELECT * FROM Books ORDER BY is_available DESC, title")
    books = c.fetchall()
    
    # 2. هات الاستعارات النشطة (عشان نعرف نرجعها)
    c.execute("""
        SELECT B.*, BK.title, S.name as student_name 
        FROM Borrowings B 
        JOIN Books BK ON B.book_id = BK.book_id 
        JOIN Students S ON B.student_id = S.student_id 
        WHERE B.status = 'active'
    """)
    active_borrowings = c.fetchall()
    
    conn.close()
    return render_template('library.html', books=books, active_borrowings=active_borrowings)

@app.route("/add_book", methods=['POST'])
def add_book():
    if session.get('role') != 'manager': return redirect(url_for('library'))
    
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO Books (title, author, price, rental_fee_per_day) VALUES (?, ?, ?, ?)",
              (request.form['title'], request.form['author'], float(request.form['price']), 5.0)) # 5 جنيه افتراضي
    conn.commit()
    conn.close()
    flash("تم إضافة الكتاب للمكتبة", "success")
    return redirect(url_for('library'))

@app.route("/borrow_book", methods=['POST'])
def borrow_book():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    phone = request.form['student_phone']
    book_id = request.form['book_id']
    
    conn = get_db()
    c = conn.cursor()
    
    # 1. هات بيانات الطالب
    c.execute("SELECT * FROM Students WHERE phone=?", (phone,))
    student = c.fetchone()
    if not student:
        flash("هذا الطالب غير مسجل! يجب تسجيله أولاً.", "error")
        return redirect(url_for('library'))
        
    # 2. هات بيانات الكتاب
    c.execute("SELECT * FROM Books WHERE book_id=?", (book_id,))
    book = c.fetchone()
    
    # 3. تسجيل الاستعارة (ودفع التأمين)
    # التأمين بيدخل الخزنة كأنه إيراد مؤقت (أو عهدة)، بس للتبسيط هنعتبره دخل الدرج دلوقتي
    c.execute("""
        INSERT INTO Borrowings (student_id, book_id, deposit_paid) 
        VALUES (?, ?, ?)
    """, (student['student_id'], book_id, book['price']))
    
    # تحديث حالة الكتاب لـ "غير متاح"
    c.execute("UPDATE Books SET is_available=0 WHERE book_id=?", (book_id,))
    
    # تسجيل حركة في الدرج (اختياري: لو عايز التأمين يسمع في إيراد الوردية)
    # يفضل نسجله عشان الدرج يظبط، ولما يرجع نخصمه
    if 'active_shift_id' in session:
        # بنسجله في المصروفات بالسالب (أو إيراد مبيعات) .. للتبسيط هنزود إيراد المبيعات
        c.execute("UPDATE Shifts SET revenue_sales = revenue_sales + ? WHERE shift_id=?", 
                  (book['price'], session['active_shift_id']))

    conn.commit()
    conn.close()
    flash(f"تمت الاستعارة بنجاح. تم تحصيل تأمين {book['price']} ج", "success")
    return redirect(url_for('library'))

@app.route("/return_book/<int:borrow_id>", methods=['POST'])
def return_book(borrow_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # 1. هات بيانات الاستعارة
    c.execute("""
        SELECT B.*, BK.rental_fee_per_day, BK.price as book_price 
        FROM Borrowings B JOIN Books BK ON B.book_id = BK.book_id 
        WHERE B.borrow_id=?""", (borrow_id,))
    borrow = c.fetchone()
    
    # 2. حساب الأيام والتكلفة
    borrow_date = datetime.datetime.strptime(borrow['borrow_date'], "%Y-%m-%d %H:%M:%S")
    return_date = datetime.datetime.now()
    days = (return_date - borrow_date).days
    if days < 1: days = 1 # أقل حاجة يوم واحد
    
    rental_cost = days * borrow['rental_fee_per_day']
    deposit = borrow['deposit_paid']
    
    # المعادلة: المبلغ المرتجع للطالب = التأمين - الإيجار
    amount_to_return = deposit - rental_cost
    
    # 3. تحديث الداتا بيز
    c.execute("""
        UPDATE Borrowings SET return_date=?, final_cost=?, status='returned' 
        WHERE borrow_id=?""", (return_date, rental_cost, borrow_id))
        
    c.execute("UPDATE Books SET is_available=1 WHERE book_id=?", (borrow['book_id'],))
    
    # 4. تظبيط الدرج (الخزنة)
    # إحنا كنا دخلنا التأمين كله في الدرج وقت الاستعارة.
    # دلوقتي بنخرج المبلغ المرتجع للطالب من الدرج كمصروف.
    if 'active_shift_id' in session:
        # المبلغ اللي كسبناه فعلياً هو الـ rental_cost
        # المبلغ اللي خرج من الدرج هو amount_to_return
        # بنسجل عملية "إرجاع تأمين" في المصروفات
        if amount_to_return > 0:
            c.execute("INSERT INTO Expenses (user_id, shift_id, description, amount) VALUES (?, ?, ?, ?)",
                      (session['user_id'], session['active_shift_id'], f"إرجاع تأمين كتاب (استعارة #{borrow_id})", amount_to_return))
            
            # تحديث إجمالي مصروفات الوردية
            c.execute("UPDATE Shifts SET total_expenses = total_expenses + ? WHERE shift_id=?",
                      (amount_to_return, session['active_shift_id']))

    conn.commit()
    conn.close()
    
    flash(f"تم إرجاع الكتاب. مدة الإيجار: {days} يوم. التكلفة: {rental_cost} ج. المبلغ المرتجع للطالب: {amount_to_return} ج", "info")
    return redirect(url_for('library'))


import setup_database # تأكد من وجود هذا السطر

@app.route("/reset_data_keep_users", methods=['POST'])
def reset_data_keep_users():
    # 1. حماية: للمدير فقط
    if session.get('role') != 'manager':
        return redirect(url_for('dashboard'))

    conn = get_db()
    c = conn.cursor()
    
    # 2. إيقاف القيود مؤقتاً لحذف البيانات المرتبطة ببعضها
    c.execute("PRAGMA foreign_keys = OFF;")
    
    # 3. قائمة الجداول التي نريد مسح بياناتها (كل شيء ما عدا Users)
    # ملاحظة: sqlite_sequence هو جدول داخلي للعدّاد (1, 2, 3..)، بنمسحه عشان العدّاد يبدأ من 1 تاني
    c.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = c.fetchall()
    
    for table in tables:
        t_name = table['name']
        # استثناء جدول المستخدمين وجداول النظام
        if t_name not in ['Users', 'sqlite_sequence','Students','Products','Rooms']:
            c.execute(f"DELETE FROM {t_name}")
            # تصفير العداد (Auto Increment) للجدول ده
            c.execute("DELETE FROM sqlite_sequence WHERE name=?", (t_name,))
    
    conn.commit()
    conn.close()
    
    # 4. إعادة إدخال البيانات الافتراضية (الغرف، المنتجات الأساسية، الباقات)
    # لأننا مسحناها في الخطوة السابقة
    setup_database.create_database()
    
    # 5. تنظيف السيشن من أي شيفت كان مفتوح (لأننا مسحنا الشيفتات)
    session.pop('active_shift_id', None)
    
    flash("تم تصفير قاعدة البيانات بنجاح! (تم الاحتفاظ ببيانات الموظفين والمدير)", "success")
    return redirect(url_for('dashboard'))
# --- دالة فتح المتصفح ---
def open_browser():
    # بنفتح المتصفح على البورت 5001
    webbrowser.open("http://127.0.0.1:5009/login")

# --- نقطة التشغيل الرئيسية ---
if __name__ == '__main__':
    # 1. تأخير الفتح 3 ثواني لضمان عمل السيرفر
    Timer(3, open_browser).start()
    
    # 2. تشغيل التطبيق (هام جداً: debug=False)
    # ده بيمنع الـ PIN وبيمنع الـ Reload اللي بيبوظ الـ EXE
    app.run(host='127.0.0.1', port=5009, debug=False)
    #hhhhh
    