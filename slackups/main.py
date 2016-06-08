import asyncio
import logging
from collections import OrderedDict
import json
import sys
import os

import hangups
import hangups.auth
from hangups.ui.utils import get_conv_name
import appdirs

logger = logging.getLogger(__name__)

from aslack.slack_api import SlackApi
from .slack_bot import SlackBot

class User:
    def __init__(self):
        self.slack = SlackUser()
        self.slack.user = self
        self.hangouts = HangoutsUser()
        self.hangouts.user = self

class SlackUser:
    def __init__(self):
        self.client = {}
        self.server = {}
        self.id_ = None

    async def setup(self, token=None, id_=None):
        if id_:
            try:
                dirs = appdirs.AppDirs('slackups', 'slackups')
                tokencache = os.path.join(dirs.user_cache_dir, str(id_)+'-slack.json')
                token_ = open(tokencache,'r').read().strip()
            except:
                logger.info("No cached token at "+tokencache)
                if token == None:
                    return "ERROR: No token"
            else:
                token = token_

        self.token = token
        self.client = SlackApi(token)
        self.client.call = self.client.execute_method
        res = await self.client.call('auth.test')
        logger.info(res)
        if not 'ok' in res or not res['ok'] or not 'user_id' in res:
            logger.warning("Invalid slack token!")
            return "ERROR: Invalid token?"

        self.id_ = res['user_id']
        dirs = appdirs.AppDirs('slackups', 'slackups')
        tokencache = os.path.join(dirs.user_cache_dir, str(self.id_)+'-slack.json')
        open(tokencache,'w').write(token)
        logger.info("Slack user set up! "+self.id_)
        return "Slack token accepted!"

class HangoutsUser:
    def __init__(self):
        self.client = {}
        self.id_ = None
        self.userList = None
        self.convList = None

    def auth_code_f(self):
        logger.info("token returned for "+str(self.user.slack.id_))
        return self.token

    async def setup(self, token=None):
        logger.info("setting up hangups...")
        self.token = token
        dirs = appdirs.AppDirs('slackups', 'slackups')
        tokencache = os.path.join(dirs.user_cache_dir, str(self.user.slack.id_)+'-cookies.json')
        logger.info("token cache: "+tokencache)
        try:
            self.cookies = hangups.auth.get_auth(self.auth_code_f, tokencache)
        except:
            logger.warning("No hangouts auth")
            return

        logger.info("Hangouts auth seems ok")

        asyncio.ensure_future(self.run())
        logger.info("hangups scheduled for "+str(self.user.slack.id_))

        n = 0.0
        while self.userList == None and n < 10:
            await asyncio.sleep(0.1)
            n += 0.1

        if self.userList == None:
            logger.warning("Never connected")
            return

        logger.info("!!!Hangups ok!!!")
        self.id_ = 'ok'


    def setCookies(self, cookies):
        self.cookies = cookies

    async def run(self):
        self.client = hangups.Client(self.cookies)
        self.client.on_connect.add_observer(self.onHangupsConnect)
        logger.info("Connecting to hangups...")
        await self.client.connect()

    async def onHangupsConnect(self):
        logger.info("Connected to hangups")
        self.userList, self.convList = (
            await hangups.build_user_conversation_list(self.client)
        )
        self.convList.on_event.add_observer(self.onHangupsEvent)

    async def onHangupsEvent(self, convEvent):
        try:
            logger.info("Hangups Event: "+convEvent.__class__.__name__)
        except:
            logger.warning("Error handling hangups event!")


class AdminBot(SlackBot):

    async def setup(self):
        self.api.call = self.api.execute_method
        print("Bot identity: %s / %s / %s"%(self.id_, self.user, self.full_name))
        self.chans = {}
        self.users = {}
        await asyncio.wait([self.getims(),self.getchans(),self.getusers()])

    async def getuser(self, userid):
        if userid in self.users:
            return self.users[userid]

        user = await self.api.call('users.info', user=userid)

        if 'ok' not in user or not user['ok'] or 'user' not in user:
            logger.warning("Error listing IMs: "+str(user))

        user = user['user']

        self.users[user['id']] = user

        return user

    async def getusers(self):
        users = await self.api.call('users.list')

        if 'ok' not in users or not users['ok'] or 'members' not in users:
            logger.warning("Error listing IMs: "+str(users))

        users = users['members']

        for user in users:
            self.users[user['id']] = user

    async def getims(self):
        ims = await self.api.call('im.list')
        if 'ok' not in ims or not ims['ok'] or 'ims' not in ims:
            logger.warning("Error listing IMs: "+str(ims))

        ims = ims['ims']

        for im in ims:
            self.chans[im['id']] = im

    async def getchan(self, chanid):
        if chanid in self.chans:
            return self.chans[chanid]

        channel = await self.api.call('channels.info',channel=chanid)
        if 'ok' not in channel or not channel['ok'] or 'channel' not in channel:
            logger.warning("Error getting chan: "+str(channel))

        channel = channel['channel']

        self.chans[channel['id']] = channel

        return channel


    async def getchans(self):
        channels = await self.api.call('channels.list')
        if 'ok' not in channels or not channels['ok'] or 'channels' not in channels:
            logger.warning("Error listing Chans: "+str(channels))

        channels = channels['channels']

        for channel in channels:
            self.chans[channel['id']] = channel

    async def getim(self, userid):
        for channel in self.chans:
            if 'is_im' in channel:
                if channel['user'] == userid:
                    return channel

        res = await self.api.call('im.open', user=userid)
        if 'ok' not in res or not res['ok'] or 'channel' not in res:
            logger.warning("Error opening IM to "+str(userid)+": "+str(res))

        return await self.getchan(res['channel']['id'])

    def messageForMe(self, data):
        return self.message_is_to_me(data)

    def messageNotForMe(self, data):
        return not self.message_is_to_me(data)

    async def handleCommand(self, data):
        print("Got command: "+str(data))
        return dict(channel=data['channel'], text='got command')

    async def handleSlackMsg(self, data):
        #ignore confirmations of messages sent
        if 'reply_to' in data:
            return None

        if not 'type' in data:
            print("Missing command type: "+str(data))
            return None
        mtype = data['type']
        if mtype == 'message':
            logger.debug(str(data))

            if 'subtype' in data:
                subtype = data['subtype']
            else:
                subtype = None

            if not 'user' in data:
                return None

            if subtype == 'bot_message':
                return None

            msg = data['text']
            userid = data['user']
            chanid = data['channel']

            #ignore my own sent messages
            if userid == self.id_:
                return None

            logger.info("MSG: %s -> %s [%s]: %s" % (userid, chanid, subtype, msg))

            chan = await self.getchan(chanid)

            if subtype == 'channel_join' and chan['name'] == 'general':
                im = await self.getim(userid)
                asyncio.ensure_future(self.api.call('chat.postMessage', channel=im['id'], text='Greetings '+userid))
                return None

            if 'is_im' in chan:
                parts = msg.split(' ',2)
                helpmsg = "Please tell me 'slack <slacktoken>' or 'hangouts <hangoutstoken>'\nURL to get a slack token: https://api.slack.com/docs/oauth-test-tokens\nURL to get a hangouts token: "+hangups.auth.OAUTH2_LOGIN_URL 
                if len(parts) < 2:
                    return dict(channel=chanid, text=helpmsg)
                if msg[0] == "'":
                    return dict(channel=chanid, text="Don't actually enter the ' you dummy")
                if parts[1][0:4] == "&lt;":
                    return dict(channel=chanid, text="Don't actually enter the < you dummy")
                if parts[0] == 'slack':
                    slacktoken = parts[1]
                    try:
                        res = await self.main.addslackuser(slacktoken=slacktoken)
                    except:
                        logger.exception("Adding slack token")
                        return dict(channel=chanid, text="Something went wrong with your slack token :(")
                    asyncio.ensure_future(self.api.call('chat.postMessage', channel=chanid, text='Slack token accepted!'))
                elif parts[0] == 'hangouts':
                    hangoutstoken = parts[1]
                    try:
                        res = await self.main.addhangoutsuser(slackid=userid, hangoutstoken=hangoutstoken)
                    except:
                        logger.exception("Adding hangouts token")
                        return dict(channel=chanid, text="Something went wrong with your hangouts token :(")
                    return dict(channel=chanid, text="Hangouts connected! Now go ahead and chat.")
                else:
                    return dict(channel=chanid, text=helpmsg)

            suser = await self.main.getslackuser(userid)
            if suser == None:
                im = await self.getim(userid)
                asyncio.ensure_future(self.api.call('chat.postMessage', channel=im['id'], text='Greetings '+userid+', I need your slack token'))
                return None
            if suser.hangouts.id_ == None:
                im = await self.getim(userid)
                asyncio.ensure_future(self.api.call('chat.postMessage', channel=im['id'], text='Greetings '+userid+', I need your hangouts token'))
                return None

            return dict(channel=chanid, text="[Sending msg]");

        elif mtype == 'user_typing':
            pass
        elif mtype == 'reconnect_url':
            pass
        else:
            print("Unhandled msg: "+str(data['type']))
        return None

    MESSAGE_FILTERS = OrderedDict([
        (messageForMe, handleCommand),
        (messageNotForMe, handleSlackMsg),
        ])

class Main:
    def __init__(self):
        self.users = []
        self.channels = []

    async def getslackuser(self, id_, token=None):
        if id_ == None:
            return None

        for user in self.users:
            if user.slack.id_ == id_:
                return user
        
        user = User()
        res = await user.slack.setup(token=token, id_=id_)
        if user.slack.id_ == None:
            return None

        self.users.append(user)

        return user

    def gethangoutsuser(self, id_):
        if id_ == None:
            return None

            if user.hangouts.id_ == id_:
                return user

        return None

    async def addslackuser(self, slackid=None, slacktoken=None):
        suser = await self.getslackuser(id_=slackid, token=slacktoken)
        logger.info("Adding user: "+str(slacktoken))
        if suser:
            logger.warning("Slack user already exists! Re setting up??")
            return await suser.slack.setup(slacktoken)

        user = User()
        self.users.append(user)
        await user.slack.setup(slacktoken)

    async def addhangoutsuser(self, slackid=None, hangoutstoken=None):
        huser = await self.getslackuser(id_=slackid)
        logger.info("Adding user: "+str(hangoutstoken))
        if huser:
            logger.warning("Hangouts user already exists! Re setting up??")
            return await huser.hangouts.setup(hangoutstoken)

        user = User()
        self.users.append(user)
        await user.hangouts.setup(hangoutstoken)


    async def slackSetup(self):
        dirs = appdirs.AppDirs('slackups', 'slackups')
        botTokenPath = os.path.join(dirs.user_config_dir, 'bot.token')
        adminTokenPath = os.path.join(dirs.user_config_dir, 'admin.token')
        try:
            botToken = open(botTokenPath,'r').read().strip()
        except:
            logger.exception("Loading bot token %s", botTokenPath)
            sys.exit(0)
        try:
            adminToken = open(adminTokenPath,'r').read().strip()
        except:
            logger.exception("Loading admin token %s", adminTokenPath)
            sys.exit(0)
        self.slackAPI = SlackApi(adminToken)
        self.slackAPI.call = self.slackAPI.execute_method
        channels = await self.slackAPI.call('channels.list')
        print("Got channels: "+str(channels))
        groups = await self.slackAPI.call('groups.list')
        print("Got groups: "+str(channels))
        ims = await self.slackAPI.call('im.list')
        print("Got IMs: "+str(ims))

        self.bot = await AdminBot.from_api_token(botToken)
        self.bot.main = self
        await self.bot.setup()


    def run(self):
        self.loop = loop = asyncio.get_event_loop()
        loop.run_until_complete(self.slackSetup())
        try:
            loop.run_until_complete(self.bot.join_rtm())
        finally:
            logger.info("Goodbye")

