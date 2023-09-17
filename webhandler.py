import aiohttp
import logging
import sys
from datetime import datetime


class WebsiteHandler():
    session: aiohttp.ClientSession

    def __init__(self, url, fc, active, ver):
        self.url = url
        self.myFC = fc
        self.active = active
        self.ver = ver
        self.ErrorCount = 0
        self.TotalErrors = 0

    def IsConnected(self):
        return self.ErrorCount == 0

    def SetActive(self, active):
        self.active = active

    def _ServerError(self):
        self.ErrorCount += 1
        self.TotalErrors += 1

    def _ServerSuccess(self):
        self.ErrorCount = 0

    async def BottersOnlineCount(self):
        try:
            botter_list = await self.session.get(self.url + "/botters.php")
            if botter_list.status == 200:
                server_response = await botter_list.text()
                self._ServerSuccess()
                try:
                    return int(server_response.split("\n")[0])
                except ValueError:
                    return 0
            else:
                logging.warning("Server responded with HTTP code %s", botter_list.status)
        except Exception as e:
            logging.error("Exception found: %s\n%s\n%s\n%s", e, sys.exc_info()[0].__name__, sys.exc_info()[2].tb_frame.f_code.co_filename, sys.exc_info()[2].tb_lineno)
        self._ServerError()
        return 0

    async def getClaimedList(self):
        try:
            fc_req = await self.session.get(self.url + "/getList.php", params={'me': self.myFC})
            if fc_req.status == 200:
                server_response = await fc_req.text()
                if not server_response.startswith('error') and not server_response.startswith('nothing'):
                    fc_list = [x for x in server_response.split("\n") if len(x) == 12]
                    return fc_list
                else:
                    return []
            else:
                logging.warning("Server responded with HTTP code %s", fc_req.status)
        except Exception:
            self._ServerError()
        return []

    async def getNewList(self):
        try:
            fc_req = await self.session.get(self.url + "/getfcs.php", params={'me': self.myFC, 'active': self.active, 'ver': self.ver})
            if fc_req.status == 200:
                self._ServerSuccess()
                server_response = await fc_req.text()
                if not server_response.startswith('error') and not server_response.startswith('nothing'):
                    fc_list = [x for x in server_response.split("\n") if len(x) == 12]
                    return fc_list
            else:
                logging.warning("Server responded with HTTP code %s", fc_req.status_code)
                print(f"[ {datetime.now()} ] WebHandler: Generic Connection error {fc_req.status_code}")
                self._ServerError()
        except Exception:
            self._ServerError()
        return []

    async def UpdateLFCS(self, fc, lfcs):
        try:
            lfcs_req = await self.session.get(self.url + "/setlfcs.php", params={'lfcs': '{:016x}'.format(lfcs), 'fc': fc})
            if lfcs_req.status == 200:
                self._ServerSuccess()
                server_response = await lfcs_req.text()
                if not server_response.startswith('error'):
                    return True
            else:
                logging.warning("Server responded with HTTP code %s", lfcs_req.status)
                logging.warning("Server response: %s", await lfcs_req.text())
                print(f"[ {datetime.now()} ] WebHandler: Generic Connection error {lfcs_req.status}")
                print(f"[ {datetime.now()} ] Server response: {await lfcs_req.text()}")
                self._ServerError()
        except Exception as e:
            print("[", datetime.now(), "] Got exception!!", e, "\n", sys.exc_info()[0].__name__, sys.exc_info()[2].tb_frame.f_code.co_filename, sys.exc_info()[2].tb_lineno)
            logging.error("Exception found: %s\n%s\n%s\n%s", e, sys.exc_info()[0].__name__, sys.exc_info()[2].tb_frame.f_code.co_filename, sys.exc_info()[2].tb_lineno)
            self._ServerError()
        return False

    async def TimeoutFC(self, fc):
        timeout_req = await self.session.get(self.url + "/timeout.php", params={'me': self.myFC, 'fc': fc})
        if timeout_req.status == 200:
            self._ServerSuccess()
            server_response = await timeout_req.text()
            if not server_response.startswith('error'):
                return True
        else:
            logging.warning("Server responded with HTTP code %s", timeout_req.status)
            print(f"[ {datetime.now()} ] WebHandler: Generic Connection error {timeout_req.status}")
            self._ServerError()
        return False

    async def ClaimFC(self, fc):
        resp = await self.session.get(self.url + "/claimfc.php", params={'fc': fc, 'me': self.myFC})
        if resp.status == 200:
            self._ServerSuccess()
            server_response = await resp.text()
            if server_response.startswith('success'):
                return True
        else:
            logging.warning("Server responded with HTTP code %s", resp.status)
            print(f"[ {datetime.now()} ] Generic Connection issue: {resp.status}")
            self._ServerError()
        return False

    async def ResetFC(self, fc):
        reset_req = await self.session.get(self.url + "/trustedreset.php", params={'me': self.myFC, 'fc': fc})
        if reset_req.status == 200:
            self._ServerSuccess()
            server_response = await reset_req.text()
            print(server_response)
            if not server_response.startswith('error'):
                return True
        else:
            logging.warning("Server responded with HTTP code %s", reset_req.status)
            print(f"[ {datetime.now()} ] WebHandler: Generic Connection error {reset_req.status}")
            self._ServerError()
        return False

    def GetBotSettings(self):
        return False, True

    def ResetBotSettings(self):
        return True
