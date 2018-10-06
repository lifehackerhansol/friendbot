import sys
import logging
import hashlib
import struct
import time
import requests
import base64
import random
import threading
import queue
import yaml
import urllib3
import npyscreen
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from datetime import datetime, timedelta

#import sqlite3

# sys.path fuckery
# dont try this at home, kids
sys.path.append("./NintendoClients")

from nintendo.nex import backend, authentication, friends, nintendo_notification
from nintendo import account

logging.basicConfig(level=logging.WARN)

def nintendo_base64_encode(data):
        return base64.b64encode(data).decode('ascii').replace('+', '.').replace('/', '-').replace('=', '*')

def nintendo_base64_decode(s):
        return base64.b64decode(s.replace('.', '+').replace('-', '/').replace('*', '='))

class process_friend:
    def __init__(self, fc):
        self.fc = fc
        self.pid = int(fc) & 0xffffffff
        self.added_time = datetime.utcnow()
        self.lfcs = None
    @classmethod
    def from_pid(cls, pid):
        return cls(pid_to_fc(pid))

class cSettings(object):
    def __init__(self):
        self.version = 0x200
        self.active=1
        self.friendcode = ""
        self.pid = 0
        self.BotterCount=0
        self.ServerErrorCount=0
        self.NintendoErrorCount=0


identity_path = "identity.yaml"
if len(sys.argv) >= 2:
    identity_path = sys.argv[1]
identity = yaml.load(open(identity_path, 'r'))


###################################### VARIABLES AND SHIT
UI = False

client = None
sbackend = None
RunSettings = cSettings()
lfcs_queue = list()
remove_queue = list()
added_friends = list()
sh_running = True
sh_path = "http://www.mechanicaldragon.xyz/part1dumper"
getfc_interval = 5 # time between get fc requests
error_interval = 10 # 10 seconds if the server upload/download errors
shitsbroke_interval = 900 #15 minutes if it errors 10 times
nintysbroke_interval = 7200 # 2 hours if nintendo breaks

timeout_interval = 600 # 10 minutes to timeout friends

my_friendseed = identity['lfcs']
BotterCount = 0
starttime=datetime.utcnow()


start_time = datetime.utcnow()
game_shuffle_time = datetime.utcnow()

random_games =  [
    # Skylanders games
    0x0004000000165E00, 0x0004000000131200, 0x0004000000036E00, 0x0004000000091D00, 0x00040000000E6500,
    # Mama games
    0x000400000004E400
]

def reconnect():
    global client, identity, sbackend
    if not client is None:
        if not client.client is None:
            client.client.close()
    if not sbackend is None:
        sbackend.close()
    blob = {
    'gameid': b'00003200',
    'sdkver': b'000000',
    'titleid': b'0004013000003202',
    'gamecd': b'----',
    'gamever': b'0011',
    'mediatype': b'0',
    'makercd': b'00',
    'servertype': b'L1',
    'fpdver': b'000C',
    'unitcd': b'2', # ?
    'macadr': identity['mac_address'].encode('ascii'), # 3DS' wifi MAC
    'bssid': identity['bssid'].encode('ascii'), # current AP's wifi MAC
    'apinfo': identity['apinfo'].encode('ascii'),
    'fcdcert': open(identity['cert_filename'], 'rb').read(),
    'devname': identity['name'].encode('utf16'),
    'devtime': b'340605055519',
    'lang': b'01',
    'region': b'02',
    'csnum': identity['serial'].encode('ascii'),
    'uidhmac': identity['uid_hmac'].encode('ascii'), # TODO: figure out how this is calculated. b'213dc099',
    'userid': str(identity['user_id']).encode('ascii'),
    'action': b'LOGIN',
    'ingamesn': b''
    }

    blob_enc = {}
    for k in blob:
        blob_enc[k] = nintendo_base64_encode(blob[k])
    print(f"Getting a NASC token for {blob['csnum'].decode('ascii')}..")
    resp = requests.post("https://nasc.nintendowifi.net/ac", headers={'User-Agent': 'CTR FPD/000B', 'X-GameID': '00003200'}, data=blob_enc, cert='ClCertA.pem', verify=False)
    print (resp.text)
    bits = dict(map(lambda a: a.split("="), resp.text.split("&")))
    bits_dec = {}
    for k in bits:
        bits_dec[k] = nintendo_base64_decode(bits[k])
    host, port = bits_dec['locator'].decode().split(':')
    port = int(port)

    pid = str(identity['user_id'])
    password = identity['password']

    sbackend = backend.BackEndClient(
        friends.FriendsTitle.ACCESS_KEY,
        friends.FriendsTitle.NEX_VERSION,
        backend.Settings("friends.cfg")
    )
    sbackend.connect(host, port)
    sbackend.login(
        pid, password,
        auth_info = None,
        login_data = authentication.AccountExtraInfo(168823937, 2134704128, 0, bits['token']),
    )
    client = friends.Friends3DSClient(sbackend)
    sbackend.nintendo_notification_server.handler = NotificationHandler()
class MetaConstant(type):
    def __getattr__(cls, key):
        return cls[key]

    def __setattr__(cls, key, value):
        raise TypeError
class Const(object, metaclass=MetaConstant):
    def __getattr__(self, name):
        return self[name]
    def __setattr__(self, name, value):
        raise TypeError    

class NINTENDO_SERVER_ERROR(Const):
    SUCCESS = 0
    NO_ERROR = 0
    PRUDP_DISCONNECTED = 1

def update_presence():
    global game_shuffle_time
    global playing_title_id
    global client
    global random_games
    uptime = datetime.utcnow() - start_time
    uptime_str = ""
    if uptime.days > 7:
        uptime_str += f"{uptime.days//7}w"
    if uptime.days > 0:
        uptime_str += f"{uptime.days % 7}d"
    if uptime.seconds >= 3600:
        uptime_str += f"{uptime.seconds // 3600}h"
    if uptime.seconds >= 60:
        uptime_str += f"{(uptime.seconds % 3600) // 60}m"
    uptime_str += f"{uptime.seconds % 60}s"

    if datetime.utcnow() - game_shuffle_time > timedelta(minutes=1):
        game_shuffle_time = datetime.utcnow()
        playing_title_id = random.choice(random_games)

    s = "Domo Arigato"
    if client.client.client.is_connected():
        presence = friends.NintendoPresenceV1(0xffffffff, friends.GameKey(playing_title_id, 0), s, 0, 0, 0, 0, 0, 0, b"")
        client.update_presence(presence, True)
    else:
        RunSettings.NintendoErrorCount += 1
    

def pid_to_fc(principal_id):
    checksum = hashlib.sha1(struct.pack('<L', principal_id)).digest()[0] >> 1
    return '{:012d}'.format(principal_id | checksum << 32)
def is_valid_fc(fc):
    cur_cs = int(fc)>>32
    principal_id = int(fc) & 0xffffffff
    checksum = hashlib.sha1(struct.pack('<L', principal_id)).digest()[0] >> 1
    return cur_cs == checksum

class NotificationHandler(nintendo_notification.NintendoNotificationHandler):
    def __init__(self):
        self.name_cache = {}

    def process_notification_event(self, event):
        global lfcs_queue, added_friends
        if event.type == nintendo_notification.NotificationType.FRIEND_REQUEST_COMPLETE:
            #print("Friend request completed for pid {}!!!!!".format(event.pid))
            #rel = client.sync_friend(my_friendseed, [event.pid], [])[0]
            p = process_friend.from_pid(event.pid)
            #p.lfcs = rel.friend_code
            #print("Created friend:\npid",p.pid,"\nfc",p.fc)
            lfcs_queue.append(p)
            #print("lfcs queue now",len(lfcs_queue))
            #print("added friends now",len(added_friends))
            added_friends = [x for x in added_friends if x.pid != event.pid]
            #print("added friends updated to",len(added_friends))

def get_list():
    global added_friends, lfcs_queue, remove_queue, RunSettings
    fc_list = requests.get(sh_path+"/getList.php", params={'me': RunSettings.friendcode})
    if fc_list.status_code == 200:
        RunSettings.ServerErrorCount=0
        if not fc_list.text.startswith('error') and not fc_list.text.startswith('nothing'):
            fc_set = set([x for x in fc_list.text.split("\n") if len(x)==12])
            extralist=[x for x in added_friends if x.fc not in fc_set]
            added_friends = [x for x in added_friends if x.fc in fc_set]
            for p in extralist:
                remove_queue.append(p.pid)
            for fc in fc_set:
                if fc == '':
                    continue
                if fc == 'nothing':
                    break
                if not is_valid_fc(fc):
                    continue
                if len([x for x in added_friends if x.fc == fc]) > 0:
                    continue
                if len([x for x in lfcs_queue if x.fc == fc]) > 0:
                    continue
                if len([x for x in remove_queue if x == (FC2PID(fc))]) > 0:
                    continue
                print("[",datetime.now(),"] Adding previously claimed friend:",fc)

                connected, rel = AddFriendFC(fc)
                if connected==NINTENDO_SERVER_ERROR.SUCCESS:
                    if rel.is_complete==True:
                        p = process_friend(fc)
                        p.lfcs = rel.friend_code
                        lfcs_queue.append(p)
                        #added_friends = [x for x in added_friends if x.pid != p.pid]
                    else:
                        added_friends.append(process_friend(fc))
    else:
        RunSettings.ServerErrorCount+=1
## FC2PID(fc)
## convert a friend code to the pid, just removes the checksum
def FC2PID(fc):
    return int(fc) & 0xffffffff
## AddFriendFC(fc)
## adds a friend by friend code, converts fc to pid and then calls AddFriendPID
def AddFriendFC(fc):
    return AddFriendPID(FC2PID(fc))
##AddFriendPID(pid)
## Adds a friend based on pid but only if prdudp is connected
def AddFriendPID(pid):
    global client
    if not client.client.client.is_connected():
        print("[",datetime.now(),"] Unable to add friend:",pid_to_fc(pid))
        return NINTENDO_SERVER_ERROR.PRUDP_DISCONNECTED, None
    rel = client.add_friend_by_principal_id(my_friendseed, pid)
    print("[",datetime.now(),"] Added friend:",pid_to_fc(pid))
    return NINTENDO_SERVER_ERROR.SUCCESS, rel
def RemoveFriendPID(pid):
    global client
    if not client.client.client.is_connected():
        print("[",datetime.now(),"] Unable to remove friend:",pid_to_fc(pid))
        return NINTENDO_SERVER_ERROR.PRUDP_DISCONNECTED, None
    rel = client.remove_friend(pid)
    print("[",datetime.now(),"] Removed friend:",pid_to_fc(pid))
    return NINTENDO_SERVER_ERROR.SUCCESS, rel

def RemoveFriendFC(fc):
    return RemoveFriendPID(FC2PID(fc))

## GetBotters
## returns int of # of botters online
def GetBotters():
    botter_list = requests.get(sh_path+"/botters.php")
    if botter_list.status_code == 200:
        RunSettings.ServerErrorCount=0
        try: 
            return int(botter_list.text.split("\n")[0])
        except ValueError:
            return -1
    else:
        RunSettings.ServerErrorCount+=1

def RefreshClientData(pid):
    global client
    if not client.client.client.is_connected():
        return NINTENDO_SERVER_ERROR.PRUDP_DISCONNECTED, None
    else:
        return NINTENDO_SERVER_ERROR.SUCCESS, client.sync_friend(my_friendseed, [pid], [])[0]

## Handle_LFCSQueue()
## iterate through lfcs queue and attempt to upload the data to the server
def Handle_LFCSQueue():
    global client,remove_queue,lfcs_queue,server_error_count
    while len(lfcs_queue) > 0 :
        p = lfcs_queue[0]
        if p.lfcs is None:
            connected, rel = RefreshClientData(p.pid)
            if connected != NINTENDO_SERVER_ERROR.SUCCESS:
                return False, connected
            p.lfcs=rel.friend_code
        lfcs_res = requests.get(sh_path+"/setlfcs.php", params={'lfcs': '{:016x}'.format(p.lfcs),'fc':p.fc})
        if lfcs_res.status_code == 200:
            RunSettings.ServerErrorCount=0
            #print("lfcs result: ", lfcs_res.text)
            if not lfcs_res.text.startswith('success'):
                print("[",datetime.now(),"] Error setting resulting lfcs. Response:",lfcs_res.text)
                return False, NINTENDO_SERVER_ERROR.SUCCESS
            else:
                print("[",datetime.now(),"] LFCS for",p.fc,"uploaded successfully")
                lfcs_queue.pop(0)
                remove_queue.append(p.pid)
        else:
            #lfcs_queue.put(p.pid)
            print("[",datetime.now(),"] Generic Server error",lfcs_res.status_code)
            RunSettings.ServerErrorCount+=1
            return False, NINTENDO_SERVER_ERROR.SUCCESS
    return True, NINTENDO_SERVER_ERROR.SUCCESS

def Handle_FriendTimeouts():
    global added_friends, remove_queue, RunSettings
    oldfriends = [x for x in added_friends if (datetime.utcnow()-timedelta(seconds=timeout_interval)) > x.added_time]
    added_friends = [x for x in added_friends if (datetime.utcnow()-timedelta(seconds=timeout_interval)) <= x.added_time]
    for x in oldfriends:
        print("[",datetime.now(),"] Friend code timeout:",x.fc)
        fc_to = requests.get(sh_path+"/timeout.php", params={'me': RunSettings.friendcode, 'fc':x.fc})
        remove_queue.append(x.pid)

def Handle_RemoveQueue():
    global remove_queue
    while len(remove_queue) > 0:
        pid = remove_queue[0]
        resp, rel = RemoveFriendPID(pid)
        if resp == NINTENDO_SERVER_ERROR.SUCCESS:
            remove_queue.pop(0)
        else:
            return False
    return True
def Process_FriendList(flist):
    global added_friends, lfcs_queue, RunSettings
    for fc in flist:
        if fc == '':
            continue
        if fc == 'nothing':
            break
        if not is_valid_fc(fc):
            continue
        if ClaimFriend(fc) == False:
            continue
        #print ("[",datetime.now(),"] Friend code seems valid, adding friend",fc)
        connected, rel = AddFriendFC(fc)
        if connected!=NINTENDO_SERVER_ERROR.SUCCESS:
            return False
        if rel.is_complete==True:
            p = process_friend(fc)
            p.lfcs = rel.friend_code
            lfcs_queue.append(p)
            print("[",datetime.now(),"] Friend code already added and exchanged",fc,"added to lfcs_queue list")
        else:
            added_friends.append(process_friend(fc))
            print("[",datetime.now(),"] Friend code",fc,"added to added_friends list")
    return True
def ClaimFriend(fc):
    global RunSettings, added_friends, lfcs_queue
    resp = requests.get(sh_path+"/claimfc.php",params={'fc':fc,'me':RunSettings.friendcode})
    if resp.status_code == 200:
        RunSettings.ServerErrorCount=0
        if resp.text.startswith('success'):
            if fc == RunSettings.friendcode:
                return False
            ## if fc already exists in added friends
            if len([x for x in added_friends if x.fc == fc]) > 0:
                return False
            print("[",datetime.now(),"] Friend code claimed,",fc)
            return True
        else:
            print ("[",datetime.now(),"] Friend code already claimed,",fc)
            return False
    elif resp.status_code >= 500 and resp.status_code <= 599:
        print("[",datetime.now(),"] Server Error:",resp.status_code)
    else:
        print("[",datetime.now(),"] Generic Connection issue:",resp.status_code)
    RunSettings.ServerErrorCount+=1
    return False

def GetFriends():
    global added_friends, lfcs_queue, RunSettings
    fc_list = requests.get(sh_path+"/getfcs.php", params={'me': RunSettings.friendcode, 'active': RunSettings.active, 'ver': RunSettings.version})
    if fc_list.status_code != 200:
        print("[",datetime.now(),"] Generic Connection error",fc_list.status_code)
        RunSettings.ServerErrorCount+=1
        return False, fc_list.status_code
    RunSettings.ServerErrorCount=0
    flist = fc_list.text.split("\n")
    if len(flist) > 0 and not flist[0].startswith("nothing") and RunSettings.active == 1:
        print ("[",datetime.now(),"] List of friends grabbed,",len(flist),"friends found")
        return Process_FriendList(flist)
    else:
        print("[",datetime.now(),"] No Friends Found, Waiting")
        return True

def sh_thread():
    global client, my_friendseed, sh_running, sh_path, lfcs_queue, getfc_interval, spinner, fc_count, db, remove_queue, added_friends, timeout_interval, server_error_count, shitsbroke_interval, RunSettings, nintysbroke_interval
    #print("Running bot as",myFriendCode[0:4]+"-"+myFriendCode[4:8]+"-"+myFriendCode[8:])
    while sh_running:
        serverError=0
        try:
            if not client.client.client.is_connected():
                print("[",datetime.now(),"] PRUDP died, restarting connection")
                reconnect()
            RunSettings.BotterCount=GetBotters()
#            print ("GetList")
            get_list()
#            print("added friends list,",len(added_friends))
#            print("lfcs list,",len(lfcs_queue))
#            print("remove friends list,",len(remove_queue))
            #print("Dumping known lfcs\'s")
#            print("handle lfcs")
            complete, resp = Handle_LFCSQueue()
            if  resp == NINTENDO_SERVER_ERROR.SUCCESS:
                if complete == False:
                    print("[",datetime.now(),"] could not complete LFCS queue processing")
            else:
                print ("[",datetime.now(),"] Nintendo service error code:",resp)
                continue
#            print("handle timeouts")
            Handle_FriendTimeouts()
 #           print("Handle RemoveQueue")
            if not Handle_RemoveQueue():
                print("[",datetime.now(),"] Could not handle RemoveQueue")
                continue
#            print("GetFriends")
            GetFriends()
            if sh_running!=False:
                if RunSettings.NintendoErrorCount>5:
                    time.sleep(nintysbroke_interval) # ffs nintendo come back pleeeease
                elif RunSettings.ServerErrorCount>9:
                    time.sleep(shitsbroke_interval) # oh no, my website broke again, gfdi
                elif RunSettings.ServerErrorCount>0:
                    time.sleep(error_interval) # maybe it's not crashed...maybe its just overloaded
                else:
                    time.sleep(getfc_interval) # all good, wait for a few seconds to not overload the website

        except Exception as e:
            print("[",datetime.now(),"] Got exception!!", e,"\n",sys.exc_info()[0].__name__, sys.exc_info()[2].tb_frame.f_code.co_filename, sys.exc_info()[2].tb_lineno)
        ## client is Friends3dsClient
        ## client.client is backend.secure_client (service client)
        ## client.client.client is prudp client (which is what i see failing)
def SpacedFriendCode(fc):
    return fc[0:4]+"-"+fc[4:8]+"-"+fc[8:12]

class ExitButton(npyscreen.ButtonPress):
    def whenPressed(self):
        self.parent.parentApp.switchForm(None)
class P1BotForm(npyscreen.FormBaseNew): 
    global added_friends, lfcs_queue, remove_queue, starttime,client,BotterCount,RunSettings
    def while_waiting(self): 
        #npyscreen.notify_wait("Update") 
        self.lblRuntime.value = str(datetime.utcnow()-starttime).split(".")[0]
        self.lblBotCount.value = str(BotterCount)
        self.lblMyFriendCode.value = SpacedFriendCode(RunSettings.friendcode)
        #self.lblCurrentFriends.value = str(len(added_friends))
        flist = [x.fc[0:4]+"-"+x.fc[4:8]+"-"+x.fc[8:12] for x in added_friends]
        self.addedfriendslist.footer = "("+str(len(added_friends))+")"
        self.addedfriendslist.values = flist
        flist = [x.fc[0:4]+"-"+x.fc[4:8]+"-"+x.fc[8:12] for x in lfcs_queue]
        self.lfcslist.footer="("+str(len(lfcs_queue))+")"
        self.lfcslist.values = flist
        self.unfriendlist.footer="("+str(len(remove_queue))+")"
        flist = [x for x in remove_queue]
        self.unfriendlist.values = flist
        connected=client.client.client.is_connected()
        if connected:
            self.lblConnected.value="Connected"
        else:
            self.lblConnected.value="Disconnected"
        self.display() 
    def create(self): 
        #self.date_widget = self.add(npyscreen.FixedText, value=datetime.now(), editable=False) 
        self.lblConnected  = self.add(npyscreen.TitleText, name = "Friend Service:",value=str(False),editable=False, use_two_lines=False,begin_entry_at=20 )
        self.nextrely -= 1
        self.nextrelx += 40
        self.lblBotCount  = self.add(npyscreen.TitleText, name = "BotCount:",value="0",editable=False, use_two_lines=False,begin_entry_at=20 )
        self.nextrelx -= 40
        self.nextrely += 1
        self.lblMyFriendCode  = self.add(npyscreen.TitleText, name = "My Friend Code:",value="",editable=False, use_two_lines=False,begin_entry_at=20 )
        #self.nextrely += 1
        #self.lblCurrentFriends = self.add(npyscreen.TitleText, name = "Current Friends:",value="",editable=False, use_two_lines=False,begin_entry_at=20)
        self.nextrely += 1
        self.lblRuntime = self.add(npyscreen.TitleText, name = "Run Time:", value="0", editable=False,use_two_lines=False,begin_entry_at=20)
        self.nextrely += 1
        self.addedfriendslist = self.add(npyscreen.BoxTitle, name = "Friends", editable=False,height=15,width=25)
        self.nextrely -= 15
        self.nextrelx += 26
        self.lfcslist = self.add(npyscreen.BoxTitle, name = "LFCS Upload", editable=False,height=15,width=25)
        self.nextrely -= 15
        self.nextrelx += 26
        self.unfriendlist = self.add(npyscreen.BoxTitle, name = "Unfriend", editable=False,height=15,width=25)
        self.nextrely += 1
        self.nextrelx -= 52
        self.exitButton = self.add(ExitButton, name="Exit")
        #self.how_exited_handers[npyscreen.wgwidget.EXITED_ESCAPE] = self.exit_application

class Part1Bot(npyscreen.NPSAppManaged):
    keypress_timeout_default = 10
    def onStart(self):
        P1Form = self.addForm("MAIN", P1BotForm, name="Part1Bot") 

#myFriendCode = pid_to_fc(identity['user_id'])
RunSettings.friendcode = pid_to_fc(identity['user_id'])
print("Running system as",RunSettings.friendcode)

if UI == False:
    print("\n\n********** Type \'q\' and press enter to quit at any time **************\n\n")

reconnect()
#print (client.client.client.is_connected())
#all = client.get_all_friends()
#print(len(all),"friends")
#print(all)
playing_title_id = random.choice(random_games)
update_presence()


sh_thread_obj = threading.Thread(target=sh_thread)
sh_thread_obj.daemon = True
sh_thread_obj.start()

def presence_thread():
    global RunSettings
    while True:
        if not client.client.client.is_connected():
            RunSettings.NintendoErrorCount += 1
        else:
            RunSettings.NintendoErrorCount = 0
        time.sleep(30)
        update_presence()

def website_heartbeat():
    global RunSettings
    while True:
        if RunSettings.friendcode != "":
            fc_list = requests.get(sh_path+"/getfcs.php", params={'me': RunSettings.friendcode, 'active': RunSettings.active, 'ver': RunSettings.version})
        time.sleep(30)

whb_thread_obj = threading.Thread(target=website_heartbeat)
whb_thread_obj.daemon = True
whb_thread_obj.start = True

p_thread_obj = threading.Thread(target=presence_thread)
p_thread_obj.daemon = True
p_thread_obj.start()
run_app = True

if UI==True:
    App = Part1Bot().run()
else:
    while run_app:
        x=input("")
        x=x.lower()
        if x=='q' or x=="quit":
            run_app = False


print("Application quit initiated, closing")
sh_running = False
sh_thread_obj.join()
print("Removing claims on friends")
#print("added friends list,",len(added_friends))
#print("lfcs list,",len(lfcs_queue))
#print("remove friends list,",len(remove_queue))

while len(added_friends) > 0:
    x = added_friends[0]
    print("Removing",x.fc)
    uncl_resp = requests.get(sh_path+"/trustedreset.php", params={'me': RunSettings.friendcode, 'fc':x.fc})
    if uncl_resp.text.startswith("success"):
        client.remove_friend(x.pid)
        added_friends.pop(0)

client.update_presence(friends.NintendoPresenceV1(0xffffffff, friends.GameKey(0x0004000000033500, 0), 'good bye', 0, 0, 0, 0, 0, 0, b""), False)

sbackend.close()
