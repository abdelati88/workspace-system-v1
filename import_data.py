import sqlite3
import pandas as pd
import os

EXCEL_FILE = 'students.xlsx'
DB_FILE = 'workspace.db'

def clean_phone_number(raw_phone):
    """ تنظيف التليفون """
    if pd.isna(raw_phone): return ""
    s = str(raw_phone).replace(" ", "").replace("-", "").strip()
    if "." in s: s = s.split(".")[0]
    return s

def import_students():
    if not os.path.exists(EXCEL_FILE):
        print(f"❌ ملف '{EXCEL_FILE}' غير موجود!")
        return

    print("⏳ جاري قراءة الملف...")
    
    try:
        # قراءة الملف بدون عناوين (header=None)
        df = pd.read_excel(EXCEL_FILE, header=None)
        
        # 1. تحديد بداية البيانات (نتجاهل أول سطر لو فيه عناوين)
        start_row = 0
        first_row_vals = df.iloc[0].astype(str).values
        if any('الاسم' in s for s in first_row_vals):
            start_row = 1
            print("👀 تم تجاهل سطر العناوين.")
        
        # 2. كشف عدد الأعمدة وتحديد الترتيب الصحيح
        num_cols = df.shape[1]
        print(f"📊 عدد الأعمدة التي تم قراءتها: {num_cols}")
        
        # الترتيب الافتراضي (لو 5 أعمدة: كود - اسم - كلية - فرقة - تليفون)
        # B=1, C=2, D=3, E=4
        col_name = 1
        col_college = 2
        col_year = 3
        col_phone = 4
        
        # التصحيح الذكي (لو 4 أعمدة بس: اسم - كلية - فرقة - تليفون)
        # ده السيناريو اللي حصل معاك (الباندا طيرت عمود الكود)
        if num_cols == 4:
            print("⚠️ تنبيه: تم اكتشاف 4 أعمدة فقط (تم إزاحة الترتيب).")
            col_name = 0    # الاسم بقى هو الأول
            col_college = 1 # الكلية بقت التاني
            col_year = 2    # الفرقة بقت التالت
            col_phone = 3   # التليفون بقى الرابع
            
        elif num_cols < 4:
            print("❌ خطأ: عدد الأعمدة في الملف قليل جداً!")
            return

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        added = 0
        print("\n🚀 بدء الاستيراد...")
        
        # اللف على البيانات
        for index, row in df.iloc[start_row:].iterrows():
            try:
                # قراءة البيانات بالترتيب المظبوط
                name = str(row[col_name]).strip()
                
                # تأكيد أخير إن ده اسم مش رقم
                # لو الاسم طلع رقم (زي الكود)، يبقى محتاجين نزحزح كمان خانة
                if name.isdigit() and num_cols >= 5:
                     # محاولة تصحيح يدوية لو الترتيب لسه بايظ
                     name = str(row[col_name + 1]).strip()
                     # وباقي الأعمدة تتشفت
                
                college = str(row[col_college]).strip()
                year = str(row[col_year]).strip()
                phone = clean_phone_number(row[col_phone])
                
                # تنظيف النصوص
                if college == 'nan': college = "غير محدد"
                if year == 'nan': year = "غير محدد"

                # تجاهل الصفوف غير الصالحة
                if not name or len(phone) < 5 or name == 'nan' or 'الاسم' in name:
                    continue
                
                # إدخال البيانات
                cursor.execute("""
                    INSERT INTO Students (name, phone, college, year) 
                    VALUES (?, ?, ?, ?)
                """, (name, phone, college, year))
                
                added += 1
                print(f"✅ تم إضافة: {name} | {college} | {year}")

            except sqlite3.IntegrityError:
                print(f"⚠️ مكرر: {name}")
            except Exception as e:
                # print(f"خطأ في صف: {e}") 
                pass

        conn.commit()
        conn.close()
        print(f"\n🎉 تم الانتهاء! إجمالي الطلاب: {added}")

    except Exception as e:
        print(f"❌ حدث خطأ: {e}")


if __name__ == "__main__":
    import_students()
    input("\nاضغط Enter للخروج...")