import sqlite3

conn = sqlite3.connect('workspace.db')
c = conn.cursor()

try:
    # إنشاء جدول الإعدادات
    c.execute("""
        CREATE TABLE IF NOT EXISTS Settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT
        )
    """)
    
    # إضافة القيمة الافتراضية (2 كارت مجاناً)
    # بنستخدم INSERT OR IGNORE عشان لو شغلت السكربت مرتين ميمسحش القيمة اللي انت ظبطتها
    c.execute("INSERT OR IGNORE INTO Settings (setting_key, setting_value) VALUES ('internet_free_limit', '2')")
    
    conn.commit()
    print("✅ تم إنشاء جدول الإعدادات وضبط الحد المجاني الافتراضي على 2.")
except Exception as e:
    print(f"⚠️ حدث خطأ: {e}")

conn.close()