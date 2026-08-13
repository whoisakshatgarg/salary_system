from tkinter import *
from tkinter import messagebox
from tkinter import ttk
from tkinter import filedialog
import os
import sys
import mysql.connector
from mysql.connector import Error
from datetime import datetime
py = sys.executable

#creating window
class Add(Tk):
    def __init__(self):
        super().__init__()
        self.maxsize(500,450)
        self.minsize(500,450)
        self.title('Add Employee - APEX THERMOCON')
        self.canvas = Canvas(width=500, height=450)
        self.canvas.pack()
        n = StringVar()
        b = IntVar()
        pf = StringVar()
        ot = StringVar()
        es = StringVar()
        dn = StringVar()
        dept = StringVar()
        rem = IntVar()
            
#verifying input
        def asi():
            if len(n.get()) < 1:
                messagebox.showerror("Oop's", "Please Enter Employee Name!")
            elif len(dept.get()) == 0:
                messagebox.showerror("Oop's", "Please select option for Department!")
            elif b.get() < 10:
                messagebox.showerror("Oop's", "Please Enter Employee Salary!")
            elif len(pf.get()) == 0:
                messagebox.showerror("Oop's","Please select option for PF!")
            elif len(es.get()) == 0:
                messagebox.showerror("Oop's", "Please select option for ESI!")
            elif len(dn.get()) == 0:
                messagebox.showerror("Oop's", "Please select option for Day/Night Shift!")
            elif len(ot.get()) == 0:
                messagebox.showerror("Oop's", "Please select option for Overtime!")
            else:
                try:
                    self.conn = mysql.connector.connect(host='localhost',
                                                database='new_salary',
                                                user='root', password='123456')
                    self.myCursor = self.conn.cursor()
                    self.myCursor.execute("SELECT emp_id FROM emp ORDER BY emp_id DESC LIMIT 1")
                    self.rows = self.myCursor.fetchall()
                    if self.rows == []:
                        emp_no = 1
                    else:
                        emp_no = int(self.rows[0][0]) + 1
                    name = n.get()
                    salary = b.get()
                    pf_status = pf.get()
                    es_status = es.get()
                    ot_status = ot.get()
                    dn_status = dn.get()
                    depart = dept.get() 
                    advance = rem.get()
                    rem_months = 12 - datetime.now().month

                    self.myCursor.execute("INSERT into emp(emp_id,emp_name,salary,PF,ESI,Overtime,Rem_Advance,shift,dept) values (%s,%s,%s,%s,%s,%s,%s,%s,%s);",[str(emp_no),name,salary,pf_status,es_status,ot_status,advance,dn_status,depart])
                    self.conn.commit()
                    if ot_status == "N":
                        self.myCursor.execute("INSERT into remaining_holidays(emp_id,holidays) values (%s,%s);",[str(emp_no),rem_months])
                        self.conn.commit()
                    messagebox.showinfo("Done","Employee Inserted Successfully")
                    ask = messagebox.askyesno("Confirm","Do you want to add another employee?")
                    if ask:
                        self.destroy()
                        os.system('%s %s' % (py, 'salary-system/addEmployee.py'))
                    else:
                        self.destroy()
                        self.myCursor.close()
                        self.conn.close()
                except mysql.connector.Error as err:
                    print(err)
                    messagebox.showerror("Error","Something went wrong")

        # label and input box
        self.label1 = Label(self, text="Add Employee", bg = 'white' , fg = 'black', font=("garamond", 24,'bold'))
        self.label1.place(x=147, y=10)
        Label(self, text='Employee Details',bg='white', fg='white', font=('garamond', 25, 'bold')).pack()
        Label(self, text='Name',bg='white', font=('garamond', 10, 'bold')).place(x=70, y=70)
        Entry(self, textvariable=n, width=30).place(x=200, y=70)
        Label(self, text='Base Salary',bg='white', font=('garamond', 10, 'bold')).place(x=70, y=139)
        Entry(self, textvariable=b, width=30).place(x=200, y=139)
        Label(self, text='Department',bg='white', font=('garamond', 10, 'bold')).place(x=70, y=105)
        c= ttk.Combobox(self,textvariable=dept,values=["CNC", "QA", "Conventional", "Production", "Buffing","Cutting", "Admin", "Electrical", "Accounts","Stores"],width=40,state="readonly").place(x = 200, y = 105)
        Label(self, text='PF',bg='white', font=('garamond', 10, 'bold')).place(x=70, y=172)
        pfy_radio = Radiobutton(text="Yes", variable=pf, value="Y")
        pfy_radio.place(x=205, y=172)
        pfn_radio = Radiobutton(text="No", variable=pf, value="N")
        pfn_radio.place(x=255, y=172)

        Label(self, text='ESI',bg='white', font=('garamond', 10, 'bold')).place(x=70, y=207)
        esiy_radio = Radiobutton(text="Yes", variable=es, value="Y")
        esiy_radio.place(x=205, y=207)
        esin_radio = Radiobutton(text="No", variable=es, value="N")
        esin_radio.place(x=255, y=207)

        Label(self, text='Day/Night',bg='white', font=('garamond', 10, 'bold')).place(x=70, y=242)
        y_radio = Radiobutton(text="Day", variable=dn, value="D")
        y_radio.place(x=205, y=242)
        n_radio = Radiobutton(text="Night", variable=dn, value="N")
        n_radio.place(x=255, y=242)

        Label(self, text='Overtime (Y/N)',bg='white', font=('garamond', 10, 'bold')).place(x=70, y=277)
        y_radio = Radiobutton(text="Yes", variable=ot, value="Y")
        y_radio.place(x=205, y=277)
        n_radio = Radiobutton(text="No", variable=ot, value="N")
        n_radio.place(x=255, y=277)

        Label(self, text='Remaining Advance',bg='white', font=('garamond', 10, 'bold')).place(x=70, y=320)
        Entry(self, textvariable=rem, width=30).place(x=200, y=320)
        
        Button(self, text="Submit",width = 15,command=asi).place(x=180, y=370)

Add().mainloop()
