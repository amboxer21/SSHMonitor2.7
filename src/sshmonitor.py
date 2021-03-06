#/usr/bin/env python

import re
import os
import sys
import time
import fcntl
import ctypes
import select
import logging
import smtplib
import threading
import subprocess
import logging.handlers

from ctypes import cdll
from optparse import OptionParser
from email.mime.text import MIMEText

class Logging(object):

    @staticmethod
    def log(level,message):
        comm = re.search("(WARN|INFO|ERROR)", str(level), re.M)
        try:
            handler = logging.handlers.WatchedFileHandler(
                os.environ.get("LOGFILE","/var/log/sshmonitor.log")
            )
            formatter = logging.Formatter(logging.BASIC_FORMAT)
            handler.setFormatter(formatter)
            root = logging.getLogger()
            root.setLevel(os.environ.get("LOGLEVEL", str(level)))
            root.addHandler(handler)
            # Log all calls to this class in the logfile no matter what.
            if comm is None:
                print(str(level) + " is not a level. Use: WARN, ERROR, or INFO!")
                return
            elif comm.group() == 'ERROR':
                logging.error(str(time.asctime(time.localtime(time.time()))
                    + " - SSHMonitor - "
                    + str(message)))
            elif comm.group() == 'INFO':
                logging.info(str(time.asctime(time.localtime(time.time()))
                    + " - SSHMonitor - "
                    + str(message)))
            elif comm.group() == 'WARN':
                logging.warn(str(time.asctime(time.localtime(time.time()))
                    + " - SSHMonitor - "
                    + str(message)))
            if options.verbose or str(level) == 'ERROR':
                print("(" + str(level) + ") "
                    + str(time.asctime(time.localtime(time.time()))
                    + " - SSHMonitor - "
                    + str(message)))
        except IOError as eIOError:
            if re.search('\[Errno 13\] Permission denied:', str(eIOError), re.M | re.I):
                print("(ERROR) SSHMonitor - Must be sudo to run SSHMonitor!")
                sys.exit(0)
            print("(ERROR) SSHMonitor - IOError in Logging class => "
                + str(eIOError))
            logging.error(str(time.asctime(time.localtime(time.time()))
                + " - SSHMonitor - IOError => "
                + str(eIOError)))
        except Exception as eLogging:
            print("(ERROR) SSHMonitor - Exception in Logging class => "
                + str(eLogging))
            logging.error(str(time.asctime(time.localtime(time.time()))
                + " - SSHMonitor - Exception => " 
                + str(eLogging)))
            pass
        return

class Version(object):

    @staticmethod
    def python():
        python_version = re.search('\d\.\d\.\d', str(sys.version), re.I | re.M)
        if python_version is not None:
            return python_version.group()
        return "None"

    @staticmethod
    def python_is_version(version=None):
        if re.search('^'+str(version)+'\.\d+\.\d+', str(Version.python()), re.M | re.I) is None:
            return False
        return True

class FileOpts(object):

    def __init__(self,logfile):
        if not self.dir_exists(self.root_directory()):
            self.mkdir_p(self.root_directory())

        for f in ['failed','successful','banned']:
            if not self.file_exists(self.root_directory() + "/" + f):
                self.create_file(self.root_directory() + "/" + f)

        if not self.file_exists(logfile):
            self.create_file(logfile)

    def root_directory(self):
        return "/etc/sshguard"

    def failed_path(self):
        return str(self.root_directory()) + '/failed'

    def successful_path(self):
        return str(self.root_directory()) + '/successful'

    def banned_path(self):
        return str(self.root_directory()) + '/banned'

    def current_directory(self):
        return str(os.getcwd())

    def file_exists(self,file_name):
        return os.path.isfile(file_name)

    def create_file(self,file_name):
        if not self.file_exists(file_name):
            Logging.log("INFO", "Creating file " + str(file_name) + ".")
            open(file_name, 'w')

    def dir_exists(self,dir_path):
        return os.path.isdir(dir_path)

    def mkdir_p(self,dir_path):
        try:
            Logging.log("INFO", "Creating directory " + str(dir_path))
            os.makedirs(dir_path)
        except OSError as e:
            if e.errno == errno.EEXIST and self.dir_exists(dir_path):
                pass
            else:
                Logging.log("ERROR", "mkdir error: " + str(e))
                raise

class Mail(object):

    __disabled__ = False

    @staticmethod
    def send(sender,to,password,port,subject,body):
        try:
            if not Mail.__disabled__:
                message = MIMEText(body)
                message['Subject'] = subject
                mail = smtplib.SMTP('smtp.gmail.com',port)
                mail.starttls()
                mail.login(sender,password)
                mail.sendmail(sender, to, message.as_string())
                mail.quit()
                Logging.log("INFO", "(Mail.send) - Sent email successfully!")
            else:
                Logging.log("WARN", "(Mail.send) - Sending mail has been disabled!")
        except smtplib.SMTPAuthenticationError:
            Logging.log("WARN", "(Mail.send) - Could not athenticate with password and username!")
        except Exception as e:
            Logging.log("ERROR",
                "(Mail.send) - Unexpected error in Mail.send() error e => "
                + str(e))
            pass

class Tail(object):

    def __init__(self):
        self.buffer       = str()
        self.tail_command = ['/usr/bin/sudo', '/usr/bin/tail', '-F', '-n0']

    def process(self,filename):

        process = subprocess.Popen(
            self.tail_command + [filename], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    
        # set non-blocking mode for file
        function_control = fcntl.fcntl(process.stdout, fcntl.F_GETFL)
        fcntl.fcntl(process.stdout, fcntl.F_SETFL, function_control | os.O_NONBLOCK)
    
        function_control = fcntl.fcntl(process.stderr, fcntl.F_GETFL)
        fcntl.fcntl(process.stderr, fcntl.F_SETFL, function_control | os.O_NONBLOCK)
        
        return process
    
    def f(self, filename):

        empty   = None
        process = self.process(filename)
        
        while True:
            reads, writes, errors = select.select(
                [process.stdout, process.stderr], [], [process.stdout, process.stderr], 0.1
            )
            if process.stdout in reads:
                self.buffer += str(process.stdout.read())
                lines = self.buffer.split('\n')
                
                if '' in lines[-1]:
                    #whole line received
                    self.buffer = str()
                else:
                    self.buffer = lines[-1]

                # Start of Python interoperability patch
                if not lines[:-1]:
                    empty = True
                    lines = lines[-1]
                else:
                    empty = False
                    lines = lines[:-1]
    
                if lines and not empty:
                    for line in lines:
                        yield line
                else:
                    yield lines
                # End of Python interoperability patch
                    
            if process.stderr in reads:
                stderr_input = process.stderr.read()
    
            if process.stderr in errors or process.stdout in errors:
                print("Error received. Errors: ", errors)
                process = self.process(filename)
    
class SSHMonitor(object):
    
    def __init__(self, config_dict={}):

        self.email          = config_dict['email']
        self.logfile        = config_dict['logfile']
        self.password       = config_dict['password']
        self.email_port     = config_dict['email_port']
        self.disable_log    = config_dict['disable_log']
        self.regex_failed   = config_dict['regex_failed']
        self.regex_success  = config_dict['regex_success']
        self.regex_blocked  = config_dict['regex_blocked']
        self.disable_email  = config_dict['disable_email']
        self.libmasquerade  = config_dict['libmasquerade']
        self.notify_with_ui = config_dict['notify_with_ui']

        self.tail = Tail()

        self.credential_sanity_check()
        self.logfile_sanity_check(self.logfile)
        self.display_options()

        if self.notify_with_ui and self.libmasquerade is None:
            Logging.log('WARN', '/usr/lib/libmasquerade.so not found! '
                + 'UI notifications will not work.') 

    def display_options(self):
        verbose = {}
        if config_dict['verbose']:
            for option in config_dict.keys():
                verbose[option] = config_dict[option]
            Logging.log("INFO", "Options: " + str(verbose))

    def credential_sanity_check(self):
        if not self.disable_email and (self.email is None or self.password is None):
            Logging.log("ERROR",
                "(SSHMonitor.__init__) - Both E-mail and password are required!")
            parser.print_help()
            sys.exit(0)

    def logfile_sanity_check(self,logfile):

        if os.path.exists(logfile):
            config_dict['logfile'] = logfile
            Logging.log("INFO", "logfile(1): " + str(config_dict['logfile']))
        elif logfile == '/var/log/auth.log' and not os.path.exists(logfile):
            for log_file in ('messages',):
                if os.path.exists('/var/log/' + str(log_file)):
                    config_dict['logfile'] = '/var/log/' + str(log_file)
                    Logging.log("INFO", "logfile(2): " + str(config_dict['logfile']))
                    break
                else:
                    Logging.log("ERROR","Log file " 
                        + logfile
                        + " does not exist. Please specify which log to use.")
                    sys.exit(0)

    @staticmethod
    def start_thread(proc,*args):
        try:
            t = threading.Thread(target=proc,args=args)
            t.daemon = True
            t.start()
        except Exception as eStartThread:
            Logging.log("ERROR",
                "Threading exception eStartThread => "
                + str(eStartThread))

    def log_attempt(self,title,ip,date):
        if title == "success":
            w_file = fileOpts.successful_path() 
        elif title == "failed":
            w_file = fileOpts.failed_path()
        elif title == "banned":
            w_file = fileOpts.banned_path()
        else:
            Logging.log("WARN", str(title) + " is not a known title name/type.")
            return
    
        if self.disable_log:
            Logging.log("INFO", "Logging SSH attempts have been disabled.")
            return
        Logging.log("INFO", "Logging SSH actions to file: " + str(w_file))
        f = open(w_file, 'a+')
        f.write(str(ip) + " - " + str(date) + "\n")
        f.close()
    
    def tail_file(self):

        while(True):

            try:

                for line in self.tail.f(self.logfile):

                    #"Accepted password for nobody from 200.255.100.101 port 58972 ssh2"
                    success = re.search(self.regex_success, line, re.I | re.M)
                    failed  = re.search(self.regex_failed, line, re.I | re.M)
                    blocked = re.search(self.regex_blocked, line, re.I | re.M)

                    if success is not None:
                        Logging.log("INFO", "Successful SSH login from "
                            + success.group(3))
                        if self.notify_with_ui and self.libmasquerade is not None:
                            SSHMonitor.start_thread(self.libmasquerade.masquerade,'anthony',
                                "New ssh connection from "
                                + success.group(3)
                                + " For user "
                                + success.group(2)
                                + " at "
                                + success.group(1))
                        SSHMonitor.start_thread(self.log_attempt,"success", success.group(3), success.group(1))
                        SSHMonitor.start_thread(Mail.send,self.email, self.email, self.password, self.email_port,
                            'New SSH Connection',"New ssh connection from "
                            + success.group(3)
                            + " for user "
                            + success.group(2)
                            + " at "
                            + success.group(1))
                        time.sleep(1)
                    elif failed is not None:
                        Logging.log("INFO", "Failed SSH login from "
                            + failed.group(2))
                        if self.notify_with_ui and self.libmasquerade is not None:
                            SSHMonitor.start_thread(self.libmasquerade.masquerade,'anthony',
                                'Failed SSH attempt',"Failed ssh attempt from "
                                + failed.group(2)
                                + " at "
                                + failed.group(1))
                        SSHMonitor.start_thread(self.log_attempt,"failed", failed.group(2), failed.group(1))
                        SSHMonitor.start_thread(Mail.send,self.email, self.email, self.password, self.email_port,
                            'Failed SSH attempt',"Failed ssh attempt from "
                            + failed.group(2)
                            + " at "
                            + failed.group(1))
                        time.sleep(1)
                    elif blocked is not None:
                        Logging.log("INFO", "IP address "
                            + blocked.group(2) 
                            + " was banned!")
                        if self.notify_with_ui and self.libmasquerade is not None:
                            SSHMonitor.start_thread(self.libmasquerade.masquerade,'anthony',
                                'SSH IP Blocked'
                                + blocked.group(2)
                                + " was banned at "
                                + blocked.group(1)
                                + " for too many failed attempts.")
                        SSHMonitor.start_thread(self.log_attempt, "banned", blocked.group(2), blocked.group(1))
                        SSHMonitor.start_thread(Mail.send,self.email, self.email, self.password, self.email_port,
                            'SSH IP Blocked'
                            + blocked.group(2)
                            + " was banned at "
                            + blocked.group(1)
                            + " for too many failed attempts.")
                        time.sleep(1)
            except IOError as ioError:
                Logging.log("ERROR", "IOError: " + str(ioError))
            except KeyboardInterrupt:
                Logging.log("INFO", " [Control C caught] - Exiting SSHMonitor now!")
                break
            time.sleep(1)

if __name__ == '__main__':

    parser = OptionParser()
    parser.add_option('--regex-failed',
        dest='regex_failed',
        default='(^.*[0-9]*:[0-9]*:[0-9]*).*sshd.*Failed password for.*from (.*) port.*$',
        help='Use custom regex to parse and monitor your logs for failed attempts.')
    parser.add_option('--regex-success',
        dest='regex_success',
        default='(^.*[0-9]*:[0-9]*:[0-9]*).*sshd.*Accepted password for (.*) from (.*) port.*$', 
        help='Use custom regex to parse and monitor your logs for successful connections.')
    parser.add_option('--regex-blocked',
        dest='regex_blocked',
        default='(^.*[0-9]*:[0-9]*:[0-9]*).*sshguard.*Blocking (.*) for.*$', 
        help='Use custom regex to parse and monitor your logs for blocked ip address.')
    parser.add_option('-D', '--disable-email',
        dest='disable_email', action='store_true', default=False,
        help='This option allows you to disable the sending of E-mails.')
    parser.add_option('-E', '--email-port',
        dest='email_port', type='int', default=587,
        help='E-mail port defaults to port 587')
    parser.add_option("-g", "--disable-log",
        dest='disable_log', action="store_true", default=False,
        help='SSHMonitor autologs IPs by default - This turns logging off.')
    parser.add_option("-v", "--verbose",
        dest='verbose', action="store_true", default=False,
        help='This option prints the args passed to SSHMonitor on the '
            + 'command line.')
    parser.add_option("-n", "--notify-with-ui",
        dest='notify_with_ui', action="store_true", default=False,
        help='Notifies you of any SSH activity through a GTK2 user '
            + 'interface.')
    parser.add_option("-l", "--log-file",
        dest='logfile', default='/var/log/auth.log',
        help='This is the log file that SSHMonitor tails to '
            + 'monitor for ssh activity. The log defaults to '
            + '/var/log/auth.log')
    parser.add_option('-e', '--email',
        dest='email',
        help='This argument is required unless you pass the '
            + 'pass the --disable-email flag on the command line. '
            + 'Your E-mail address is used to notify you that'
            + 'there is activity related to ssh attempts.')
    parser.add_option('-p', '--password',
        dest='password',
        help='This argument is required unless you pass the '
            + 'pass the --disable-email flag on the command line. '
            + 'Your E-mail password is used to send an E-mail of the ip '
            + 'of the user sshing into your box, successful or not.')
    (options, args) = parser.parse_args()

    Mail.__disabled__ = options.disable_email

    try:
        if Version.python_is_version(2):
            path = "/usr/lib/libmasquerade.so"
            libmasquerade = cdll.LoadLibrary(path)
            Logging.log("INFO","Using Python version "+str(Version.python())+".")
        else:
            path = "/usr/lib/libmasquerade.so"
            libmasquerade = cdll.LoadLibrary(path)
            Logging.log("INFO","Using Python version "+str(Version.python())+".")
    except OSError:
        libmasquerade = None
        pass

    config_dict = {
        'email': options.email,
        'logfile': options.logfile,
        'verbose': options.verbose,
        'password': options.password,
        'libmasquerade': libmasquerade,
        'email_port': options.email_port,
        'disable_log': options.disable_log,
        'regex_failed': options.regex_failed,
        'regex_success': options.regex_success,
        'regex_blocked': options.regex_blocked,
        'disable_email': options.disable_email,
        'notify_with_ui': options.notify_with_ui
    }

    fileOpts = FileOpts(options.logfile)
    SSHMonitor(config_dict).tail_file()
