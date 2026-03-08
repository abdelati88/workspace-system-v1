import sqlite3

# اسم الداتا بيز
DB_FILE = 'workspace.db'

def wipe_students():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # 1. مسح كل الطلاب (القديم والغلط)
        cursor.execute("DELETE FROM Students")
        
        # 2. تصفير العداد (عشان الـ ID يبدأ من 1 تاني)
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='Students'")
        
        conn.commit()
        conn.close()
        
        print("✅ تم مسح جميع الطلاب القدامى بنجاح.")
        print("🚀 دلوقتي تقدر تشغل import_data.py وهينزلوا صح.")
        
    except Exception as e:
        print(f"❌ حدث خطأ: {e}")

if __name__ == "__main__":
    wipe_students()