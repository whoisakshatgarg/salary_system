from tkinter import *
from tkinter import messagebox
import os
import sys
from tkinter import ttk
import mysql.connector
from mysql.connector import Error
from datetime import datetime
py=sys.executable

class MainWin(Tk):
    def __init__(self):
        super().__init__()
        self.canvas = Canvas(width=1600, height=800)
        self.canvas.pack()
        self.maxsize(830,450)
        self.minsize(830,450)
        self.title('SALARY MANAGEMENT SYSTEM - APEX THERMOCON')
        self.a = StringVar()
        self.b = StringVar()
        self.mymenu = Menu(self)
        year = datetime.now().year
        month = datetime.now().month
        yes = "Y"

        if month == 1:
            try:
                self.conn = mysql.connector.connect(host='localhost',
                                            database='new_salary',
                                            user='root', password='123456')
                self.myCursor = self.conn.cursor()
                self.myCursor.execute("select * from reset_holidays where year=%s",[year])
                self.rows = self.myCursor.fetchall()
                if self.rows == []:
                        self.myCursor.execute("insert into reset_holidays values(%s,%s)",[year,yes])
                        self.conn.commit()
                        self.myCursor.execute("UPDATE remaining_holidays SET holidays=12")
                        self.conn.commit()
                self.myCursor.close()
                self.conn.close()
            except mysql.connector.Error as err:
                messagebox.showerror("Error","Something went wrong")

        def a_e():
            os.system('%s %s' % (py, 'addEmployee.py'))

        def s_e():
            os.system('%s %s' % (py, 'searchEmployee.py'))

        def u_e():
            os.system('%s %s' % (py, 'update.py'))

        def c_s():
            os.system('%s %s' % (py,'calculateSalaryTable.py'))

        def s_adv():
            os.system('%s %s' % (py,'viewAdvances.py'))

        def s_s():
            os.system('%s %s' % (py,'viewPay.py'))
        
        def e_s():
            os.system('%s %s' % (py,'exportSal.py'))

        def att_hist():
            os.system('%s %s' % (py,'viewAttendance.py'))

        def log():
            conf = messagebox.askyesno("Confirm", "Are you sure you want to Logout?")
            if conf:
                self.destroy()
                os.system('%s %s' % (py, 'salary-system/login.py'))

        self.addEmp = Button(self, text="ADD EMPLOYEE", width=30, font=('Garamond', 15), command=a_e).place(x=70, y=50)
        self.searchEmp = Button(self, text="SEARCH EMPLOYEE", width=30, font=('Garamond', 15), command=s_e).place(x=70, y=115)
        self.updateEmp = Button(self, text="UPDATE EMPLOYEE", width=30, font=('Garamond', 15), command=u_e).place(x=70, y=190)
        self.calcSal = Button(self, text="CALCULATE SALARY", width=30, font=('Garamond', 15), command=c_s).place(x=425, y=50)
        self.searchAdv = Button(self, text="VIEW ADVANCES", width=30, font=('Garamond', 15), command=s_adv).place(x=425, y=115)
        self.searchSal = Button(self, text="VIEW SALARIES", width=30, font=('Garamond', 15), command=s_s).place(x=425, y=190)
        self.searchSal = Button(self, text="VIEW ATTENDANCE HISTORY", width=30, font=('Garamond', 15), command=att_hist).place(x=425, y=265)
        self.searchSal = Button(self, text="EXPORT SALARY SLIP", width=30, font=('Garamond', 15), command=e_s).place(x=70, y=265)
        self.logout = Button(self, text="LOGOUT", width=20,bg="red", font=('Garamond', 15), command=log).place(x=300, y=340)

MainWin().mainloop()
