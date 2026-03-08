import sqlite3

conn = sqlite3.connect('workspace.db')
c = conn.cursor()

try:
    # بنضيف عمود جديد اسمه year (نصي)
    c.execute("ALTER TABLE Students ADD COLUMN year TEXT")
    conn.commit()
    print("✅ تم إضافة عمود 'الفرقة' (year) بنجاح!")
except sqlite3.OperationalError:
    print("⚠️ العمود موجود بالفعل.")

conn.close()