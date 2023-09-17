import base64
import hashlib
import logging
import queue
import struct
import time
from datetime import datetime, timedelta

import urllib3
from nintendo.nex import backend, friends, settings

from const import Const

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class Friends3DS:
	TITLE_ID_EUR = 0x0004013000003202
	TITLE_ID_USA = 0x0004013000003202
	TITLE_ID_JAP = 0x0004013000003202
	LATEST_VERSION = 20

	# Friends 3DS has no product code
	PRODUCT_CODE_EUR = "----"
	PRODUCT_CODE_USA = "----"
	PRODUCT_CODE_JAP = "----"

	GAME_SERVER_ID = 0x3200
	ACCESS_KEY = "ridfebb9"
	NEX_VERSION = 20000


class NINTENDO_SERVER_ERROR(Const):
    SUCCESS = 0
    NO_ERROR = 0
    PRUDP_DISCONNECTED = 1


class process_friend:
    def __init__(self, fc, resync_interval=180):
        self.fc = fc
        self.pid = int(fc) & 0xffffffff
        self.added_time = datetime.utcnow()
        self.resync_time = datetime.utcnow() + timedelta(seconds=resync_interval)
        self.lfcs = None
        self.added = True

    @classmethod
    def from_pid(cls, pid, resync_interval=180):
        return cls(PID2FC(pid), resync_interval)


class FLists(object):
    def __init__(self):
        self.notadded = list()
        self.added = list()
        self.lfcs = list()
        self.remove = list()
        self.newlfcs = queue.Queue()


# FC2PID(pid)
# convert a pid to the friend code
def PID2FC(principal_id):
    checksum = hashlib.sha1(struct.pack('<L', principal_id)).digest()[0] >> 1
    return '{:012d}'.format(principal_id | checksum << 32)


# is_valid_fc(fc)
# generates checksum from pid and compares checksum byte to generated checksum
def is_valid_fc(fc):
    cur_cs = int(fc) >> 32
    principal_id = int(fc) & 0xffffffff
    checksum = hashlib.sha1(struct.pack('<L', principal_id)).digest()[0] >> 1
    return cur_cs == checksum


# FC2PID(fc)
# convert a friend code to the pid, just removes the checksum
def FC2PID(fc):
    return int(fc) & 0xffffffff


def FormattedFriendCode(fc):
    return f"{fc[0:4]} - {fc[4:8]} - {fc[8:12]}"


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
            'gamever': b'0012',
            'mediatype': b'0',
            'makercd': b'00',
            'servertype': b'L1',
            'fpdver': b'000D',
            'unitcd': b'2',  # ?
            'macadr': identity['mac_address'].encode('ascii'),  # 3DS' wifi MAC
            'bssid': identity['bssid'].encode('ascii'),  # current AP's wifi MAC
            'apinfo': identity['apinfo'].encode('ascii'),
            'fcdcert': open(identity['cert_filename'], 'rb').read(),
            'devname': identity['name'].encode('utf16'),
            'devtime': b'340605055519',
            'lang': b'01',
            'region': b'02',
            'csnum': identity['serial'].encode('ascii'),
            'uidhmac': identity['uid_hmac'].encode('ascii'),  # TODO: figure out how this is calculated. b'213dc099',
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
        self.connected = False

    def getNASCBits(self):
        print(f"Getting a NASC token for {self.blob['csnum'].decode('ascii')}..")
        with open("nasc_response.txt") as f:
            resp = f.read()
        bits = dict(map(lambda a: a.split("="), resp.split("&")))
        self.token = bits['token']
        bits_dec = {}
        for k in bits:
            bits_dec[k] = NASCInteractor.nintendo_base64_decode(bits[k])
        self.host, port = bits_dec['locator'].decode().split(':')
        self.port = int(port)

    async def connect(self):
        self.ErrorCount = 0
        if self.client is not None:
            self.disconnect()
            time.sleep(3)
        self.getNASCBits()
        set = settings.Settings('friends')
        set.configure(
            Friends3DS.ACCESS_KEY,
            Friends3DS.NEX_VERSION
        )
        self.backend = backend.connect(
            set,
            self.host,
            self.port
        )
        self.backend.login(
            self.pid,
            self.password,
            auth_info=None,
            # is this needed??
            # login_data = friends.AccountExtraInfo(168823937, 2134704128, self.token)
        )
        self.client = friends.FriendsClientV1(self.backend)
        self.connected = True

    def disconnect(self):
        if self.backend is not None:
            self.ErrorCount = 0
            self.backend.close()
            self.backend = None
            self.client = None
            self.connected = False

    def reconnect(self):
        self.disconnect()
        self.connect()

    def IsConnected(self):
        self.connected = self.PRUDUP_isConnected()
        return self.connected

    def PRUDUP_isConnected(self):
        # client is FriendsClientV1
        if self.client is not None:
            # client.client is backend.secure_client (service client)
            if self.client.client is not None:
                # client.client.client is prudp client (which is what i see failing)
                if self.client.client.client is not None:
                    return self.client.client.client.is_connected()
        return False

    def SetNotificationHandler(self, handler_function):
        if self.connected:
            self.backend.nintendo_notification_server.handler = handler_function()
            return True
        return False

    def _ConnectionError(self):
        self.ErrorCount += 1

    def _ConnectionSuccess(self):
        self.ErrorCount = 0

    def Error(self):
        return self.ErrorCount

    # AddFriendPID(pid)
    # Adds a friend based on pid but only if prdudp is connected
    async def AddFriendPID(self, pid):
        if not self.PRUDUP_isConnected():
            self._ConnectionError()
            print(f"[ {datetime.now()} ] Unable to add friend: {FormattedFriendCode(PID2FC(pid))}")
            return None
        rel = await self.client.add_friend_by_principal_id(self.lfcs, pid)
        # TODO: HANDLE ERRORS RETURNED
        print(f"[ {datetime.now()} ] Added friend: {FormattedFriendCode(PID2FC(pid))}")
        return rel

    # AddFriendFC(fc)
    # adds a friend by friend code, converts fc to pid and then calls AddFriendPID
    async def AddFriendFC(self, fc):
        return await self.AddFriendPID(FC2PID(fc))

    async def RemoveFriendPID(self, pid):
        if not self.PRUDUP_isConnected():
            self._ConnectionError()
            print(f"[ {datetime.now()} ] Unable to remove friend: {FormattedFriendCode(PID2FC(pid))}")
            return False
        # TODO: MORE HANDLING ERRORS
        await self.client.remove_friend_by_principal_id(pid)
        print(f"[ {datetime.now()} ] Removed friend: {FormattedFriendCode(PID2FC(pid))}")
        return True

    def RemoveFriendFC(self, fc):
        return self.RemoveFriendPID(FC2PID(fc))

    async def RefreshFriendData(self, pid):
        try:
            x = await self.client.sync_friend(self.lfcs, [pid], [])
            self._ConnectionSuccess()
            if len(x) > 0:
                return x[0]
        except Exception:
            self._ConnectionError()
        return None

    async def RefreshAllFriendData(self, pids):
        try:
            self._ConnectionSuccess()
            await self.client.sync_friend(self.lfcs, pids, [])
        except Exception:
            self._ConnectionError()
            return []

    async def UpdatePresence(self, gameid, msg, Unk=True):
        if not self.PRUDUP_isConnected():
            self._ConnectionError()
            return None
        else:
            self._ConnectionSuccess()
            presence = friends.NintendoPresence(0xffffffff, friends.GameKey(gameid, 0), msg, 0, 0, 0, 0, 0, 0, b"")
            await self.client.update_presence(presence, Unk)

    async def GetAllFriends(self):
        try:
            x = await self.client.get_all_friends()
            logging.info("Got all friends: %s", len(x))
            self._ConnectionSuccess()
            return x
        except Exception:
            self._ConnectionError()
            return []
