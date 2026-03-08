import sqlite3

conn = sqlite3.connect('workspace.db')
c = conn.cursor()

try:
    # بنضيف عمود جديد لتسجيل قيمة الخصم اليدوي منفصلاً
    c.execute("ALTER TABLE Visits ADD COLUMN manual_discount REAL DEFAULT 0")
    conn.commit()
    print("✅ تم إضافة عمود 'manual_discount' بنجاح!")
except sqlite3.OperationalError:
    print("⚠️ العمود موجود بالفعل.")

conn.close()