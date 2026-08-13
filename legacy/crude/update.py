from tkinter import *
from tkinter import ttk
from tkinter import messagebox
import os
import sys
import mysql.connector
from mysql.connector import Error
py = sys.executable

# creating window
class EmployeeManagement(Tk):
    def __init__(self):
        super().__init__()
        self.maxsize(500, 450)
        self.minsize(500, 450)
        self.title('UPDATE EMPLOYEE DETAILS - APEX THERMOCON')
        self.canvas = Canvas(width=500, height=450)
        self.canvas.pack()
        n = StringVar()
        b = IntVar()
        pf = StringVar()
        ot = StringVar()
        es = StringVar()
        dn = StringVar()
        emp_id = IntVar()
        dept = StringVar()
        rem = IntVar()

        # function to fetch employee details
        def fetch_emp_details():
            try:
                self.conn = mysql.connector.connect(host='localhost',
                                                    database='salary',
                                                    user='root', password='123456')
                self.myCursor = self.conn.cursor()
                self.myCursor.execute("SELECT * FROM emp WHERE emp_id = %s", (emp_id.get(),))
                self.row = self.myCursor.fetchone()
                if self.row:
                    n.set(self.row[0])
                    b.set(self.row[1])
                    pf.set(self.row[2])
                    es.set(self.row[3])
                    ot.set(self.row[4])
                    rem.set(self.row[5])
                    dn.set(self.row[7])
                    dept.set(self.row[8])
                else:
                    messagebox.showerror("Error", "Employee not found")
                self.myCursor.close()
                self.conn.close()
            except mysql.connector.Error as err:
                messagebox.showerror("Error", "Something went wrong")

        # function to add or update employee
        def add_or_update_employee():
            if len(n.get()) < 1:
                messagebox.showerror("Oop's", "Please Enter Employee Name!")
            elif len(dept.get()) == 0:
                messagebox.showerror("Oop's", "Please Enter Department!")
            elif b.get() < 10:
                messagebox.showerror("Oop's", "Please Enter Employee Salary!")
            elif pf.get() not in ["Y", "N"]:
                messagebox.showerror("Oop's", "Please select option for PF!")
            elif es.get() not in ["Y", "N"]:
                messagebox.showerror("Oop's", "Please select option for ESI!")
            elif ot.get() not in ["Y", "N"]:
                messagebox.showerror("Oop's", "Please select option for Overtime!")
            elif dn.get() not in ["D", "N"]:
                messagebox.showerror("Oop's", "Please select option for Day/Night Option!")
            else:
                try:
                    self.conn = mysql.connector.connect(host='localhost',
                                                        database='salary',
                                                        user='root', password='123456')
                    self.myCursor = self.conn.cursor()
                    if emp_id.get() == 0:
                        self.myCursor.execute("SELECT emp_id FROM emp ORDER BY emp_id DESC LIMIT 1")
                        self.rows = self.myCursor.fetchall()
                        if self.rows == []:
                            emp_no = 1
                        else:
                            emp_no = int(self.rows[0][0]) + 1
                        advance = 0
                        self.myCursor.execute("INSERT INTO emp(emp_id, emp_name, salary, PF, ESI, Overtime, Rem_Advance, shift,dept) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,%s)",
                                              (emp_no, n.get(), b.get(), pf.get(), es.get(), ot.get(), advance, dn.get(),dept.get()))
                        messagebox.showinfo("Done", "Employee Inserted Successfully")
                    else:
                        self.myCursor.execute("UPDATE emp SET emp_name = %s, salary = %s, PF = %s, ESI = %s, Overtime = %s, shift = %s, dept = %s, Rem_Advance = %s  WHERE emp_id = %s",
                                              (n.get(), b.get(), pf.get(), es.get(), ot.get(),dn.get(), dept.get(), rem.get(), emp_id.get()))
                        messagebox.showinfo("Done", "Employee Updated Successfully")
                    self.conn.commit()
                    self.myCursor.close()
                    self.conn.close()
                except mysql.connector.Error as err:
                    print(err)
                    messagebox.showerror("Error", "Something went wrong")

        # label and input box
        Label(self, text="Update Information", bg='white', fg='black', font=("garamond", 24, 'bold')).place(x=120, y=20)
        Label(self, text='Employee ID', bg='white', font=('garamond', 10, 'bold')).place(x=70, y=90)
        Entry(self, textvariable=emp_id, width=30).place(x=200, y=90)
        Button(self, text="Fetch Details", command=fetch_emp_details).place(x=390, y=90)
        Label(self, text='Name', bg='white', font=('garamond', 10, 'bold')).place(x=70, y=123)
        Entry(self, textvariable=n, width=30).place(x=200, y=123)
        Label(self, text='Department', bg='white', font=('garamond', 10, 'bold')).place(x=70, y=156)
        c= ttk.Combobox(self,textvariable=dept,values=["CNC", "QA", "Traditional", "Buffing", "Cutting", "Admin"],width=40,state="readonly").place(x = 200, y = 156)
        Label(self, text='Base Salary', bg='white', font=('garamond', 10, 'bold')).place(x=70, y=189)
        Entry(self, textvariable=b, width=30).place(x=200, y=189)
        Label(self, text='PF', bg='white', font=('garamond', 10, 'bold')).place(x=70, y=222)
        Radiobutton(self, text="Yes", variable=pf, value="Y").place(x=200, y=222)
        Radiobutton(self, text="No", variable=pf, value="N").place(x=250, y=222)
        Label(self, text='ESI', bg='white', font=('garamond', 10, 'bold')).place(x=70, y=257)
        Radiobutton(self, text="Yes", variable=es, value="Y").place(x=200, y=257)
        Radiobutton(self, text="No", variable=es, value="N").place(x=250, y=257)
        Label(self, text='Overtime', bg='white', font=('garamond', 10, 'bold')).place(x=70, y=292)
        Radiobutton(self, text="Yes", variable=ot, value="Y").place(x=200, y=292)
        Radiobutton(self, text="No", variable=ot, value="N").place(x=250, y=292)
        Label(self, text='Day/Night', bg='white', font=('garamond', 10, 'bold')).place(x=70, y=327)
        Radiobutton(self, text="Day", variable=dn, value="D").place(x=200, y=327)
        Radiobutton(self, text="Night", variable=dn, value="N").place(x=250, y=327)
        Label(self, text='Remaining Advance', bg='white', font=('garamond', 10, 'bold')).place(x=70, y=365)
        Entry(self, textvariable=rem, width=30).place(x=200, y=365)
        Button(self, text="Update", width=15, command=add_or_update_employee).place(x=180, y=400)

EmployeeManagement().mainloop()
