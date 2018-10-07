import sys
import hashlib
import base64
import struct
import requests
import urllib3
import time
import logging
from datetime import datetime, timedelta
import threading

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from const import Const
sys.path.append("./NintendoClients")

from nintendo.nex import backend, authentication, friends, nintendo_notification
from nintendo import account

class NINTENDO_SERVER_ERROR(Const):
    SUCCESS = 0
    NO_ERROR = 0
    PRUDP_DISCONNECTED = 1


class process_friend:
    def __init__(self, fc):
        self.fc = fc
        self.pid = int(fc) & 0xffffffff
        self.added_time = datetime.utcnow()
        self.lfcs = None
    @classmethod
    def from_pid(cls, pid):
        return cls(PID2FC(pid))

class FLists(object):
    def __init__(self):
        self.notadded = list()
        self.added = list()
        self.lfcs = list()
        self.remove = list()
        self.lock = threading.Lock()

## FC2PID(pid)
## convert a pid to the friend code
def PID2FC(principal_id):
    checksum = hashlib.sha1(struct.pack('<L', principal_id)).digest()[0] >> 1
    return '{:012d}'.format(principal_id | checksum << 32)

## is_valid_fc(fc)
## generates checksum from pid and compares checksum byte to generated checksum
def is_valid_fc(fc):
    cur_cs = int(fc)>>32
    principal_id = int(fc) & 0xffffffff
    checksum = hashlib.sha1(struct.pack('<L', principal_id)).digest()[0] >> 1
    return cur_cs == checksum

## FC2PID(fc)
## convert a friend code to the pid, just removes the checksum
def FC2PID(fc):
    return int(fc) & 0xffffffff

def FormattedFriendCode(fc):
    return fc[0:4]+"-"+fc[4:8]+"-"+fc[8:12]

class NASCInteractor(object):

    @classmethod
    def nintendo_base64_encode(cls, data):
        return base64.b64encode(data).decode('ascii').replace('+', '.').replace('/', '-').replace('=', '*')
    @classmethod
    def nintendo_base64_decode(cls, s):
        return base64.b64decode(s.replace('.', '+').replace('-', '/').replace('*', '='))

    def __init__(self, identity):
        self.ErrorCount = 0
        self.client = None
        self.backend = None
        self.blob = {
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
        self.blob_enc = {}
        for k in self.blob:
            self.blob_enc[k] = NASCInteractor.nintendo_base64_encode(self.blob[k])
        self.port = 0
        self.host = ""
        self.pid = str(identity['user_id'])
        self.password = identity['password']
        self.token = None
        self.lfcs = identity['lfcs']
        self.connected=False
    def getNASCBits(self):
        print(f"Getting a NASC token for {self.blob['csnum'].decode('ascii')}..")
        resp = requests.post("https://nasc.nintendowifi.net/ac", headers={'User-Agent': 'CTR FPD/000B', 'X-GameID': '00003200'}, data=self.blob_enc, cert='ClCertA.pem', verify=False)
        print (resp.text)
        bits = dict(map(lambda a: a.split("="), resp.text.split("&")))
        self.token = bits['token']
        bits_dec = {}
        for k in bits:
            bits_dec[k] = NASCInteractor.nintendo_base64_decode(bits[k])
        self.host, port = bits_dec['locator'].decode().split(':')
        self.port = int(port)

    def connect(self):
        if not self.client is None:
            self.reconnect()
        else:
            self.getNASCBits()
            self.backend = backend.BackEndClient(
                friends.FriendsTitle.ACCESS_KEY,
                friends.FriendsTitle.NEX_VERSION,
                backend.Settings("friends.cfg")
            )
            self.backend.connect(self.host, self.port)
            self.backend.login(
                self.pid, self.password,
                auth_info = None,
                login_data = authentication.AccountExtraInfo(168823937, 2134704128, 0, self.token),
            )
            self.client = friends.Friends3DSClient(self.backend)
            self.connected=True
    def reconnect(self):
        self.backend.close()
        self.getNASCBits()
        self.backend.connect(self.host, self.port)
        self.backend.login(
            self.pid, self.password,
            auth_info = None,
            login_data = authentication.AccountExtraInfo(168823937, 2134704128, 0, self.token),
        )
        self.connected=True
    def disconnect(self):
        self.backend.close()
    def IsConnected(self):
        return self.PRUDUP_isConnected()
    def PRUDUP_isConnected(self):
        ## client is Friends3dsClient
        ## client.client is backend.secure_client (service client)
        ## client.client.client is prudp client (which is what i see failing)
        return self.client.client.client.is_connected()
    def SetNotificationHandler(self,handler_function):
        if self.connected == True:
            self.backend.nintendo_notification_server.handler = handler_function()
            return True
        return False
    def _ConnectionError(self):
        self.ErrorCount += 1
    def _ConnectionSuccess(self):
        self.ErrorCount = 0
    def Error(self):
        return self.ErrorCount
    ##AddFriendPID(pid)
    ## Adds a friend based on pid but only if prdudp is connected
    def AddFriendPID(self,pid):
        if not self.PRUDUP_isConnected():
            self._ConnectionError()
            print("[",datetime.now(),"] Unable to add friend:",FormattedFriendCode(PID2FC(pid)))
            return None
        rel = self.client.add_friend_by_principal_id(self.lfcs, pid)
        #TODO: HANDLE ERRORS RETURNED
        print("[",datetime.now(),"] Added friend:",FormattedFriendCode(PID2FC(pid)))
        return rel
    ## AddFriendFC(fc)
    ## adds a friend by friend code, converts fc to pid and then calls AddFriendPID
    def AddFriendFC(self,fc):
        return self.AddFriendPID(FC2PID(fc))

    def RemoveFriendPID(self,pid):
        if not self.PRUDUP_isConnected():
            self._ConnectionError()
            print("[",datetime.now(),"] Unable to remove friend:",FormattedFriendCode(PID2FC(pid)))
            return False
        ##TODO: MORE HANDLING ERRORS
        rel = self.client.remove_friend(pid)
        print("[",datetime.now(),"] Removed friend:",FormattedFriendCode(PID2FC(pid)))
        return True

    def RemoveFriendFC(self,fc):
        return self.RemoveFriendPID(FC2PID(fc))

    def RefreshFriendData(self,pid):
        if not self.PRUDUP_isConnected():
            self._ConnectionError()
            return None
        else:
            self._ConnectionSuccess()
            return self.client.sync_friend(self.lfcs, [pid], [])[0]
    def UpdatePresence(self,gameid,msg,Unk = True):
        if not self.PRUDUP_isConnected():
            self._ConnectionError()
            return None
        else:
            self._ConnectionSuccess()
            presence = friends.NintendoPresenceV1(0xffffffff, friends.GameKey(gameid, 0), msg, 0, 0, 0, 0, 0, 0, b"")
            self.client.update_presence(presence, Unk)
    def GetAllFriends(self):
        if not self.PRUDUP_isConnected():
            self._ConnectionError()
            return []
        else:
            self._ConnectionSuccess()
            return self.client.get_all_friends()
