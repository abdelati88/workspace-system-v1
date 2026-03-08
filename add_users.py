import sqlite3
from werkzeug.security import generate_password_hash

# دي دالة عشان نضيف مستخدم جديد
def insert_user(username, plain_password, role):
    conn = sqlite3.connect('workspace.db')
    c = conn.cursor()

    # الخطوة الأهم: تشفير الباسورد
    hashed_password = generate_password_hash(plain_password, method='pbkdf2:sha256')

    try:
        c.execute("INSERT INTO Users (username, password, role) VALUES (?, ?, ?)",
                  (username, hashed_password, role))
        conn.commit()
        print(f"✅ تم إضافة المستخدم: {username} بنجاح!")

    except sqlite3.IntegrityError:
        # ده معناه إن المستخدم موجود قبل كده (عشان عاملين الـ username UNIQUE)
        print(f"⚠️ المستخدم {username} موجود بالفعل.")

    finally:
        conn.close()

# --- الكود هيبدأ يتنفذ من هنا ---
if __name__ == "__main__":
    print("--- بدء إضافة المستخدمين الأوائل ---")

    # 1. إضافة حساب المدير
    # خليهم أسماء سهلة دلوقتي عشان نجرب بيهم
    insert_user(
        username="admin",
        plain_password="admin123",
        role="manager"
    )

    # 2. إضافة حساب موظف (وردية الصباح)
    insert_user(
        username="emp_morning",
        plain_password="emp123",
        role="employee"
    )

    # 3. إضافة حساب موظف (وردية المساء)
    insert_user(
        username="emp_night",
        plain_password="emp456",
        role="employee"
    )

    print("--- انتهت عملية الإضافة ---")