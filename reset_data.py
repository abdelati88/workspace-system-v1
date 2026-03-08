import sqlite3

def reset_system():
    print("⚠️ تحذير: أنت على وشك مسح جميع بيانات العمل (الطلاب، المبيعات، الفواتير، الكتب، الحجوزات).")
    confirm = input("هل أنت متأكد؟ اكتب 'yes' للمتابعة: ")
    
    if confirm.lower() != 'yes':
        print("تم إلغاء العملية.")
        return

    conn = sqlite3.connect('workspace.db')
    c = conn.cursor()

    # قائمة الجداول التي سيتم تفريغها (بيانات الحركة)
    tables_to_clear = [
        'Visits',                # الزيارات
        'Sales',                 # المبيعات
        'Expenses',              # المصروفات
        'Borrowings',            # استعارات الكتب
        'Reservations',          # حجوزات القاعات
        'Coupons',               # الكوبونات
        'StudentSubscriptions',  # اشتراكات الباقات
        'Shifts',                # الورديات والحسابات
        'Students',              # سجلات الطلاب
        'Books'                  # الكتب (لو عايز تمسحها وتبدأ من جديد، لو مش عايز شيل السطر ده)
    ]

    print("⏳ جاري تنظيف النظام...")

    for table in tables_to_clear:
        try:
            # مسح البيانات من الجدول
            c.execute(f"DELETE FROM {table}")
            
            # تصفير العداد (عشان أول طالب ياخد رقم 1 تاني)
            c.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
            print(f"✅ تم تصفير جدول: {table}")
        except sqlite3.OperationalError:
            print(f"⚠️ الجدول {table} غير موجود (ربما لم يتم إنشاؤه بعد).")

    # (اختياري) تصفير كميات المنتجات (إرجاع المخزون للصفر)
    # لو عايز تمسح المنتجات نفسها، ضيف 'Products' للقائمة فوق
    # هنا بنصفر الكمية بس وبنسيب الأسماء والأسعار
    c.execute("UPDATE Products SET stock_quantity = 0")
    print("✅ تم تصفير مخزون المنتجات.")

    conn.commit()
    conn.close()
    
    print("\n🎉 تم تصفير النظام بنجاح! جاهز للتسليم.")

if __name__ == "__main__":
    reset_system()