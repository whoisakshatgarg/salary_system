from tkinter import *
from tkinter import ttk
from tkinter import messagebox
from tkinter import filedialog
import os
import sys
import mysql.connector
from mysql.connector import Error
py = sys.executable

#creating window
class Search(Tk):
    def __init__(self):
        super().__init__()
        f = StringVar()
        g = StringVar()
        self.maxsize(1450,500)
        self.minsize(1450,500)
        self.canvas = Canvas(width=1200, height=500)
        self.title('SEARCH EMPLOYEE DETAILS- APEX THERMOCON')
        self.canvas.pack()
        l1=Label(self,text="Search Employees",bg='white', font=("Garamond",20,'bold')).place(x=480,y=20)
        l = Label(self, text="Search By",bg='white', font=("Garamond", 15, 'bold')).place(x=260, y=96)

        def insert(data):
            self.listTree.delete(*self.listTree.get_children())
            
            for row in data:
                try:
                    connection = mysql.connector.connect(host='localhost',
                                                        database='salary',
                                                        user='root',
                                                        password='123456')
                    query = f"select holidays from remaining_holidays where emp_id={row[6]}"
                    cursor = connection.cursor()
                    cursor.execute(query)
                    result = cursor.fetchone()
                    if result:
                        holidays = result[0]
                    else:
                        holidays = "NA"
                except Error:
                    print("Error while connecting to MySQL", Error)
                self.listTree.insert("", 'end', text=row[6], values=(row[0], row[1], row[8], row[2],row[3], row[4], row[7], row[5], holidays))
        
        def ge():
            if (len(g.get())) == 0:
                messagebox.showinfo('Error', 'First select an item')
            elif (len(f.get())==0 and g.get()!='All Employees'):
                messagebox.showinfo('Error', 'Enter the '+g.get())
            elif g.get() == 'Employee Name':
                try:
                    self.conn = mysql.connector.connect(host='localhost',
                                                        database='salary',
                                                        user='root',
                                                        password='123456')
                    self.mycursor = self.conn.cursor()
                    self.mycursor.execute("Select * from emp where emp_name LIKE %s",['%'+f.get()+'%'])
                    self.pc = self.mycursor.fetchall()
                    if self.pc:
                        insert(self.pc)
                    else:
                        messagebox.showinfo("Oop's","Either Employee Name is incorrect or not in the database")
                except Error:
                    messagebox.showerror("Error","Something went wrong")
            elif g.get() == 'Employee ID':
                try:
                    self.conn = mysql.connector.connect(host='localhost',
                                                        database='salary',
                                                        user='root',
                                                        password='123456')
                    self.mycursor = self.conn.cursor()
                    self.mycursor.execute("Select * from emp where emp_id = %s", [f.get()])
                    self.pc = self.mycursor.fetchall()
                    if self.pc:
                        insert(self.pc)
                    else:
                        messagebox.showinfo("Oop's","Employee ID not found")
                except Error:
                    messagebox.showerror("Error","Something went wrong")
            elif g.get() == 'All Employees':
                try:
                    self.conn = mysql.connector.connect(host='localhost',
                                                        database='salary',
                                                        user='root',
                                                        password='123456')
                    self.mycursor = self.conn.cursor()
                    self.mycursor.execute("Select * from emp")
                    self.pc = self.mycursor.fetchall()
                    if self.pc:
                        insert(self.pc)
                    else:
                        messagebox.showinfo("Oop's","Database is empty!")
                except Error:
                    messagebox.showerror("Error","Something went wrong")

        b=Button(self,text="Find",width=15,bg='white',font=("Courier new",10,'bold'),command=ge).place(x=860,y=138)
        c=ttk.Combobox(self,textvariable=g,values=["Employee Name","Employee ID","All Employees"],width=40,state="readonly").place(x = 380, y = 100)
        en = Entry(self,textvariable=f,width=43).place(x=380,y=155)
        la = Label(self, text="Enter",bg='white', font=("Garamond", 15, 'bold')).place(x=300, y=150)

        def handle(event):
            if self.listTree.identify_region(event.x,event.y) == "separator":
                return "break"

        self.listTree = ttk.Treeview(self, height=13,columns=('Employee Name', 'Base Salary',"Department", 'PF','ESI', 'Overtime', 'Day/Night', 'Remainder Advance',"Remaining Holidays"))
        self.vsb = ttk.Scrollbar(self,orient="vertical",command=self.listTree.yview)
        self.listTree.configure(yscrollcommand=self.vsb.set)
        self.listTree.heading("#0", text='Emp ID', anchor='center')
        self.listTree.column("#0", width=70, anchor='center')
        self.listTree.heading("Employee Name", text='Employee Name')
        self.listTree.column("Employee Name", width=250, anchor='center')
        self.listTree.heading("Base Salary", text='Base Salary')
        self.listTree.column("Base Salary", width=200, anchor='center')
        self.listTree.heading("Department", text='Department')
        self.listTree.column("Department", width=150, anchor='center')
        self.listTree.heading("PF", text='PF')
        self.listTree.column("PF", width=100, anchor='center')
        self.listTree.heading("ESI", text='ESI')
        self.listTree.column("ESI", width=100, anchor='center')
        self.listTree.heading("Overtime", text='Overtime')
        self.listTree.column("Overtime", width=100, anchor='center')
        self.listTree.heading("Day/Night", text='Day/Night')
        self.listTree.column("Day/Night", width=100, anchor='center')
        self.listTree.heading("Remainder Advance", text='Remainder Advance')
        self.listTree.column("Remainder Advance", width=180, anchor='center')
        self.listTree.heading("Remaining Holidays", text='Rem Holidays')
        self.listTree.column("Remaining Holidays", width=90, anchor='center')
        self.listTree.bind('<Button-1>', handle)
        self.listTree.place(x=40, y=200)
        self.vsb.place(x=1380,y=200,height=287)
        ttk.Style().configure("Treeview", font=('Garamond', 15))
Search().mainloop()


