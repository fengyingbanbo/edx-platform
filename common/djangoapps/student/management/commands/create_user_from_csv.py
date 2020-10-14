#!/usr/bin/env python
# encoding: utf-8
from optparse import make_option
from django.core.management.base import BaseCommand
from openedx.core.djangoapps.user_authn.views.registration_form import AccountCreationForm
from student.models import create_comments_service_user
from student.helpers import do_create_account, AccountValidationError
from track.management.tracked_command import TrackedCommand
# 解析csv
# import unicodecsv  # utf-8 ,也可以用pandas:
import pandas as pd

def create_user(username, password, email, name):
    form = AccountCreationForm(data={
        'username': username,
        'email': email,
        'password': password,
        'name': name,
    },
                               tos_required=False)
    try:
        user, _, reg = do_create_account(form)
        reg.activate()
        reg.save()
        #create_comments_service_user(user) #这会促发网络请求
        return user
    except AccountValidationError as e:
        print (e.message)

# wget https://raw.githubusercontent.com/edx/edx-platform/named-release/dogwood.rc/common/djangoapps/student/management/commands/create_user.py
class Command(TrackedCommand):
    help = """
    example:
        # Enroll a user test@example.com into the demo course
        # The username and name will default to "test"
        sudo -u www-data /edx/bin/python.edxapp /edx/app/edxapp/edx-platform/manage.py lms create_user_from_csv --help --settings devstack
        sudo -u edxapp /edx/bin/python.edxapp /edx/app/edxapp/edx-platform/manage.py lms create_user_from_csv --csv /tmp/student.csv --settings devstack

    """
    help = u"批量导入用户"
   


    def add_arguments(self, parser):
        # Positional arguments
        parser.add_argument('csv',
                            help='path of students csv',
                            type=str)
 

    def handle(self, *args, **options):
        csv = options['csv'] 
        df = pd.read_csv(csv) 
        list_label = df.columns.values 
        list_data =df.values.tolist()  
        print(list_data)     
        for item in list_data:
            print (item[2])
            username = item[0]
            email = item[1]
            name = item[2]
            password = username
            create_user(username, password, email, name)

        # 缺乏读写csv的技巧,next和边界，按header读取
        # http://python3-cookbook.readthedocs.io/zh_CN/latest/c06/p01_read_write_csv_data.html