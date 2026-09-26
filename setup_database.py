import sqlite3
import os
import sys


def resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def create_database():
    db_path: str = resource_path('workspace.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    print(f"⏳ جاري إنشاء وتحديث قاعدة البيانات في: {db_path}")

    # 1. Users (المستخدمين)
    c.execute("""
    CREATE TABLE IF NOT EXISTS Users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('employee', 'manager'))
    );
    """)

    # 2. Students (الطلاب - شامل عمود الفرقة)
    c.execute("""
    CREATE TABLE IF NOT EXISTS Students (
        student_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT NOT NULL UNIQUE,
        college TEXT,
        year TEXT, -- (تعديل: تمت إضافة عمود الفرقة)
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. Rooms (الغرف)
    c.execute("""
    CREATE TABLE IF NOT EXISTS Rooms (
        room_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        hourly_rate REAL NOT NULL
    );
    """)
    try:
        rooms_data = [('Shared', 10.0), ('Silent', 10.0), ('Private', 10.0)]
        c.executemany("INSERT INTO Rooms (name, hourly_rate) VALUES (?, ?)", rooms_data)
        conn.commit()
    except sqlite3.IntegrityError:
        pass

    # 4. Products (المنتجات)
    c.execute("""
    CREATE TABLE IF NOT EXISTS Products (
        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        purchase_price REAL NOT NULL,
        sale_price REAL NOT NULL,
        stock_quantity REAL NOT NULL DEFAULT 0
    );
    """)
    # إضافة منتجات افتراضية لو الجدول فاضي
    c.execute("SELECT count(*) FROM Products")
    if c.fetchone()[0] == 0:
        products_data = [
            ('شاي', 2.0, 5.0, 1000), ('قهوة', 5.0, 10.0, 1000), 
            ('اندومي', 7.0, 10.0, 50), ('نسكافيه', 8.0, 20.0, 1000), 
            ('مياه', 3.0, 10.0, 50)
        ]
        c.executemany("INSERT INTO Products (name, purchase_price, sale_price, stock_quantity) VALUES (?, ?, ?, ?)", products_data)
        conn.commit()

    # 5. Shifts (الورديات)
    c.execute("""
    CREATE TABLE IF NOT EXISTS Shifts (
        shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        start_time DATETIME NOT NULL,
        end_time DATETIME,
        revenue_hours REAL DEFAULT 0,
        revenue_sales REAL DEFAULT 0,
        total_expenses REAL DEFAULT 0,
        total_discounts REAL DEFAULT 0,
        net_cash REAL DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES Users(user_id)
    );
    """)

    # 6. Coupons (الكوبونات) - لازم ينشأ قبل Visits عشان الربط
    c.execute("""
    CREATE TABLE IF NOT EXISTS Coupons (
        coupon_id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        discount_type TEXT NOT NULL CHECK(discount_type IN ('percentage', 'fixed')),
        discount_value REAL NOT NULL,
        is_used INTEGER NOT NULL DEFAULT 0,
        expiry_date DATETIME,
        student_id INTEGER,
        visit_id INTEGER,
        FOREIGN KEY(student_id) REFERENCES Students(student_id),
        FOREIGN KEY(visit_id) REFERENCES Visits(visit_id)
    );
    """)

    # 7. Reservations (حجوزات القاعات) - لازم ينشأ قبل Visits
    c.execute("""
    CREATE TABLE IF NOT EXISTS Reservations (
        reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id INTEGER NOT NULL,
        client_name TEXT NOT NULL,
        phone TEXT NOT NULL,
        start_time DATETIME NOT NULL,
        end_time DATETIME NOT NULL,
        agreed_price REAL,
        discount_type TEXT,
        discount_value REAL DEFAULT 0,
        status TEXT DEFAULT 'confirmed',
        notes TEXT,
        FOREIGN KEY(room_id) REFERENCES Rooms(room_id)
    );
    """)

    # 8. Visits (الزيارات - شامل طريقة الدفع وربط الحجز)
    c.execute("""
    CREATE TABLE IF NOT EXISTS Visits (
        visit_id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        shift_id INTEGER NOT NULL,
        room_id INTEGER NOT NULL,
        check_in_time DATETIME NOT NULL,
        check_out_time DATETIME,
        duration_hours REAL,
        total_cost REAL,
        
        coupon_id INTEGER,
        total_discount REAL DEFAULT 0,
        payment_method TEXT DEFAULT 'cash', -- (تعديل: طريقة الدفع)
        reservation_id INTEGER,             -- (تعديل: ربط بالحجز)
        manual_discount REAL DEFAULT 0,     -- (تعديل: قيمة الخصم اليدوي منفصلة)

        FOREIGN KEY(student_id) REFERENCES Students(student_id),
        FOREIGN KEY(user_id) REFERENCES Users(user_id),
        FOREIGN KEY(shift_id) REFERENCES Shifts(shift_id),
        FOREIGN KEY(room_id) REFERENCES Rooms(room_id),
        FOREIGN KEY(coupon_id) REFERENCES Coupons(coupon_id),
        FOREIGN KEY(reservation_id) REFERENCES Reservations(reservation_id)
    );
    """)

    # 9. Sales (المبيعات)
    c.execute("""
    CREATE TABLE IF NOT EXISTS Sales (
        sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        shift_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        total_price REAL NOT NULL,
        sale_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        visit_id INTEGER,
        FOREIGN KEY(product_id) REFERENCES Products(product_id),
        FOREIGN KEY(user_id) REFERENCES Users(user_id),
        FOREIGN KEY(shift_id) REFERENCES Shifts(shift_id),
        FOREIGN KEY(visit_id) REFERENCES Visits(visit_id)
    );
    """)

    # 10. Expenses (المصروفات)
    c.execute("""
    CREATE TABLE IF NOT EXISTS Expenses (
        expense_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        shift_id INTEGER NOT NULL,
        description TEXT NOT NULL,
        amount REAL NOT NULL,
        expense_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES Users(user_id),
        FOREIGN KEY(shift_id) REFERENCES Shifts(shift_id)
    );
    """)
    
    # 11. MembershipTypes (أنواع الباقات - بالأسعار الجديدة)
    c.execute("""
    CREATE TABLE IF NOT EXISTS MembershipTypes (
        type_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        total_hours REAL NOT NULL,
        days_valid INTEGER NOT NULL,
        price REAL NOT NULL
    );
    """)
    # إضافة أو تحديث الأسعار لتطابق الورقة
    packages_data = [
        ('باقة المحترفين', 200.0, 30, 600.0), # السعر الجديد
        ('باقة المتقدمين', 100.0, 20, 450.0), # السعر الجديد
        ('باقة المبتدئين', 50.0, 10, 300.0)   # السعر الجديد
    ]
    # بنعمل Check لو الباقات مش موجودة نضيفها
    c.execute("SELECT count(*) FROM MembershipTypes")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO MembershipTypes (name, total_hours, days_valid, price) VALUES (?, ?, ?, ?)", packages_data)
        conn.commit()

    # 12. StudentSubscriptions (اشتراكات الطلاب)
    c.execute("""
    CREATE TABLE IF NOT EXISTS StudentSubscriptions (
        sub_id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        type_id INTEGER NOT NULL,
        start_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        end_date DATETIME NOT NULL,
        remaining_hours REAL NOT NULL,
        is_active INTEGER DEFAULT 1,
        FOREIGN KEY(student_id) REFERENCES Students(student_id),
        FOREIGN KEY(type_id) REFERENCES MembershipTypes(type_id)
    );
    """)

    # 13. Books (الكتب والمكتبة) - جديد
    c.execute("""
    CREATE TABLE IF NOT EXISTS Books (
        book_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        author TEXT,
        category TEXT, 
        price REAL NOT NULL, 
        rental_fee_per_day REAL DEFAULT 5.0, 
        is_available INTEGER DEFAULT 1
    );
    """)

    # 14. Borrowings (الاستعارات) - جديد
    c.execute("""
    CREATE TABLE IF NOT EXISTS Borrowings (
        borrow_id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        book_id INTEGER NOT NULL,
        borrow_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        return_date DATETIME,
        deposit_paid REAL NOT NULL,
        final_cost REAL DEFAULT 0,
        status TEXT DEFAULT 'active',
        FOREIGN KEY(student_id) REFERENCES Students(student_id),
        FOREIGN KEY(book_id) REFERENCES Books(book_id)
    );
    """)

    # 15. Settings (إعدادات النظام: حد كروت النت المجانية + أسعار الشرائح)
    c.execute("""
    CREATE TABLE IF NOT EXISTS Settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT
    );
    """)
    c.execute("INSERT OR IGNORE INTO Settings (setting_key, setting_value) VALUES ('internet_free_limit', '2')")
    c.execute("INSERT OR IGNORE INTO Settings (setting_key, setting_value) VALUES ('employee_discount_cap', '15')")

    print("🎉 تم إنشاء هيكل قاعدة البيانات بالكامل (شامل جميع التحديثات)!")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    create_database()