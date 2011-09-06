# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members


import os, sys, time

class Follower:
    def follow(self, timeout=None):
        from twisted.internet import reactor
        from buildbot.scripts.logwatcher import LogWatcher
        self.rc = 0
        print "Following twistd.log until startup finished.."
        lw = LogWatcher("twistd.log")
        if timeout is not None:
            lw.TIMEOUT_DELAY = float(timeout)
        self.timeout = lw.TIMEOUT_DELAY
        d = lw.start()
        d.addCallbacks(self._success, self._failure)
        reactor.run()
        return self.rc

    def _success(self, processtype):
        from twisted.internet import reactor
        print "The %s appears to have (re)started correctly." % processtype
        self.rc = 0
        reactor.stop()

    def _failure(self, why):
        from twisted.internet import reactor
        from buildbot.scripts.logwatcher import BuildmasterTimeoutError, \
             ReconfigError, BuildslaveTimeoutError, BuildSlaveDetectedError
        if why.check(BuildmasterTimeoutError):
            print """
The buildmaster took more than %d seconds to start, so we were unable to
confirm that it started correctly. Please 'tail twistd.log' and look for a
line that says 'configuration update complete' to verify correct startup.
""" % int(self.timeout)
        elif why.check(BuildslaveTimeoutError):
            print """
The buildslave took more than %d seconds to start and/or connect to the
buildmaster, so we were unable to confirm that it started and connected
correctly. Please 'tail twistd.log' and look for a line that says 'message
from master: attached' to verify correct startup. If you see a bunch of
messages like 'will retry in 6 seconds', your buildslave might not have the
correct hostname or portnumber for the buildmaster, or the buildmaster might
not be running. If you see messages like
   'Failure: twisted.cred.error.UnauthorizedLogin'
then your buildslave might be using the wrong botname or password. Please
correct these problems and then restart the buildslave.
""" % int(self.timeout)
        elif why.check(ReconfigError):
            print """
The buildmaster appears to have encountered an error in the master.cfg config
file during startup. It is probably running with an empty configuration right
now. Please inspect and fix master.cfg, then restart the buildmaster.
"""
        elif why.check(BuildSlaveDetectedError):
            print """
Buildslave is starting up, not following logfile.
"""
        else:
            print """
Unable to confirm that the buildmaster started correctly. You may need to
stop it, fix the config file, and restart.
"""
            print why
        self.rc = 1
        reactor.stop()


def start(config):
    os.chdir(config['basedir'])
    if (not os.path.exists("buildbot.tac") and
        not os.path.exists("Makefile.buildbot")):
        print "This doesn't look like a buildbot base directory:"
        print "No buildbot.tac or Makefile.buildbot file."
        print "Giving up!"
        sys.exit(1)
    if config['quiet']:
        return launch(config)

    # we probably can't do this os.fork under windows
    from twisted.python.runtime import platformType
    if platformType == "win32":
        return launch(config)

    # fork a child to launch the daemon, while the parent process tails the
    # logfile
    if os.fork():
        # this is the parent
        timeout = None
        if 'timeout' in config and config['timeout']:
            timeout = float(config['timeout'])
        rc = Follower().follow(timeout=timeout)
        sys.exit(rc)
    # this is the child: give the logfile-watching parent a chance to start
    # watching it before we start the daemon
    time.sleep(0.2)
    launch(config)

def launch(config):
    sys.path.insert(0, os.path.abspath(os.getcwd()))
    if os.path.exists("/usr/bin/make") and os.path.exists("Makefile.buildbot"):
        # Preferring the Makefile lets slave admins do useful things like set
        # up environment variables for the buildslave.
        cmd = "make -f Makefile.buildbot start"
        if not config['quiet']:
            print cmd
        os.system(cmd)
    else:
        # see if we can launch the application without actually having to
        # spawn twistd, since spawning processes correctly is a real hassle
        # on windows.
        argv = ["twistd",
                "--no_save",
                "--logfile=twistd.log", # windows doesn't use the same default
                "--python=buildbot.tac"]
        sys.argv = argv

        # this is copied from bin/twistd. twisted-2.0.0 through 2.4.0 use
        # _twistw.run . Twisted-2.5.0 and later use twistd.run, even for
        # windows.
        from twisted.scripts import twistd
        twistd.run()

