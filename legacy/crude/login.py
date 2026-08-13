from tkinter import *
from tkinter import messagebox
import os
import sys
import mysql.connector
from mysql.connector.errors import Error
py=sys.executable


#creating window
class Lib(Tk):
    def __init__(self):
        super().__init__()
        self.a = StringVar()
        self.b = StringVar()
        self.maxsize(1200, 700)
        self.minsize(1200, 700)
        self.title("SALARY MANAGEMENT SYSTEM - APEX THERMOCON")


#verifying input
        def chex():
            if len(self.user_text.get()) < 0:
                messagebox.showinfo(" INVALID USERNAME OR PASSWORD" )
            elif len(self.pass_text.get()) < 0:
                messagebox.showinfo(" INVALID USERNAME OR PASSWORD")
            else:
                try:
                    conn = mysql.connector.connect(host='localhost',
                                         database='new_salary',
                                         user='root',
                                         password='123456')
                    cursor = conn.cursor()
                    user = self.user_text.get()
                    password = self.pass_text.get()
                    cursor.execute('Select * from `admin` where username= %s AND password = %s ',(user,password,))
                    pc = cursor.fetchone()
                    if pc and user=='admin':
                        self.destroy()
                        os.system('%s %s' % (py, 'menuPage.py'))
                    else:
                        messagebox.showinfo('Error', 'Username and password not found')
                        self.user_text.delete(0, END)
                        self.pass_text.delete(0, END)
                except Error:
                    messagebox.showinfo('Error',"Something Goes Wrong,Try restarting")

        def check():
            self.label = Label(self, text="LOGIN", bg = 'white' , fg = 'black', font=("Garamond", 24,'bold'))
            self.label.place(x=550, y=160)
            self.label1 = Label(self, text="User-Id" , bg = 'white' , fg = 'black', font=("Garamond", 18, 'bold'))
            self.label1.place(x=370, y=250)
            self.user_text = Entry(self, textvariable=self.a, width=40, font=('calibre',15,'normal'))
            self.user_text.place(x=480, y=250)
            self.label2 = Label(self, text="Password" , bg = 'white' , fg = 'black', font=("Garamond", 18, 'bold'))
            self.label2.place(x=340, y=320)
            self.pass_text = Entry(self, show='*', textvariable=self.b, width=40, font=('calibre',15,'normal'))
            self.pass_text.place(x=480, y=315)
            self.butt = Button(self, text="Login",bg ='white', font=10, width=8, command=chex).place(x=580, y=370)
            self.label3 = Label(self, text="SALARY MANAGEMENT SYSTEM", bg='white', fg='black', font=("Garamond", 24, 'bold'))
            self.label3.place(x=350, y=100)


        check()

Lib().mainloop()

