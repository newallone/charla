import os
import sys
from fnmatch import fnmatch


from six import u

from circuits import Timer
from circuits.net.events import close
from circuits.protocols.irc import reply, response
from circuits.protocols.irc.replies import Message, ERR_NOSUCHNICK, ERROR
from circuits.protocols.irc.replies import ERR_NOOPERHOST, ERR_NOPRIVILEGES
from circuits.protocols.irc.replies import ERR_PASSWDMISMATCH, RPL_YOUREOPER


from ..models import User
from ..events import rehashed
from ..plugin import BasePlugin
from ..commands import BaseCommands
from ..plugins import load, query, unload


class Commands(BaseCommands):

    def oper(self, sock, source, name, password):
        user = User.objects.filter(sock=sock).first()
        if user.oper:
            return

        oline = self.parent._get_oline(user)

        if oline is None:
            return ERR_NOOPERHOST()

        if (name, password) == oline:
            user.modes += u("o")
            user.save()
            return RPL_YOUREOPER()

        return ERR_PASSWDMISMATCH()

    def load(self, sock, source, name):
        user = User.objects.filter(sock=sock).first()
        if not user.oper:
            yield ERR_NOPRIVILEGES()
            return

        name = str(name)  # We store plugin names as str/bytes/ascii (not unicode)

        result = yield self.call(load(name), "plugins")
        yield Message(u("NOTICE"), u("*"), result.value)

    def reload(self, sock, source, name):
        user = User.objects.filter(sock=sock).first()
        if not user.oper:
            yield ERR_NOPRIVILEGES()
            return

        name = str(name)  # We store plugin names as str (not unicode)
        result = yield self.call(query(name), "plugins")

        if result.value is None:
            yield Message(u("NOTICE"), u("*"), u("No such plugin: {0}").format(name))
            return

        result = yield self.call(unload(name), "plugins")
        yield Message(u("NOTICE"), u("*"), result.value)

        result = yield self.call(load(name), "plugins")
        yield Message(u("NOTICE"), u("*"), result.value)

    def unload(self, sock, source, name):
        user = User.objects.filter(sock=sock).first()
        if not user.oper:
            yield ERR_NOPRIVILEGES()
            return

        name = str(name)  # We store plugin names as str (not unicode)
        result = yield self.call(query(name), "plugins")

        if result.value is None:
            yield Message(u("NOTICE"), u("*"), u("No such plugin: {0}").format(name))
            return

        result = yield self.call(unload(name), "plugins")
        yield Message(u("NOTICE"), u("*"), result.value)

    def die(self, sock, source):
        user = User.objects.filter(sock=sock).first()
        if not user.oper:
            return ERR_NOPRIVILEGES()

        raise SystemExit(0)

    def restart(self, sock, source):
        user = User.objects.filter(sock=sock).first()
        if not user.oper:
            yield ERR_NOPRIVILEGES()
            return

        yield self.call(close(), "server")

        args = sys.argv[:]
        self.parent.logger.info(u("Restarting... Args: {0}".format(args)))

        args.insert(0, sys.executable)
        if sys.platform == 'win32':
            args = ["\"{0}\"".format(arg) for arg in args]

        os.execv(sys.executable, args)

    def kill(self, sock, source, target, reason=None):
        user = User.objects.filter(sock=sock).first()
        if not user.oper:
            return ERR_NOPRIVILEGES()

        nick = User.objects.filter(nick=target).first()
        if nick is None:
            return ERR_NOSUCHNICK(target)

        reason = u("Killed by {0}: {1}").format(user.nick, reason or nick.nick)

        self.fire(response.create("quit", nick.sock, nick.source, reason, disconnect=False))
        self.fire(reply(nick.sock, ERROR(nick.host, reason)), "server")
        Timer(1, close(nick.sock), "server").register(self)

    def rehash(self, sock, source):
        user = User.objects.filter(sock=sock).first()
        if not user.oper:
            return ERR_NOPRIVILEGES()

        self.parent.config.reload_config()

        self.fire(rehashed(), "server")

        return Message(u("NOTICE"), u("*"), u("Configuration reloaded"))


class Admin(BasePlugin):

    def init(self, *args, **kwargs):
        super(Admin, self).init(*args, **kwargs)

        self._load_config()

        Commands(*args, **kwargs).register(self)

    def _load_config(self):
        self.olines = self.config.get("admin.olines", {})

    def _get_oline(self, user):
        for k, v in self.olines.items():
            if fnmatch(user.prefix, k):
                return v

    def rehashed(self):
        self._load_config()
