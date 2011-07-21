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

import time
import tempfile
import os
from twisted.python import log
from twisted.internet import defer, utils

from buildbot.util import deferredLocked
from buildbot.changes import base
from buildbot.util import epoch2datetime

class GitPoller(base.PollingChangeSource):
    """This source will poll a remote git repo for changes and submit
    them to the change master."""
    
    compare_attrs = ["repourl", "branch", "workdir",
                     "pollInterval", "gitbin", "usetimestamps",
                     "remoteName",
                     "category", "project", "bare"]
                     
    def __init__(self, repourl, branch='master', 
                 workdir=None, pollInterval=10*60, 
                 gitbin='git', usetimestamps=True,
                 category=None, project=None,
                 pollinterval=-2, fetch_refspec=None,
                 localBranch=None, remoteName='origin',
                 encoding='utf-8', bare=False):
        # for backward compatibility; the parameter used to be spelled with 'i'
        if pollinterval != -2:
            pollInterval = pollinterval
        if project is None: project = ''

        self.repourl = repourl
        self.branch = branch
        self.pollInterval = pollInterval
        self.fetch_refspec = fetch_refspec
        self.encoding = encoding
        self.lastChange = time.time()
        self.lastPoll = time.time()
        self.gitbin = gitbin
        self.workdir = workdir
        self.usetimestamps = usetimestamps
        self.category = category
        self.project = project
        self.changeCount = 0
        self.commitInfo  = {}
        self.remoteName = remoteName
        self.localBranch = localBranch or branch
        self.initLock = defer.DeferredLock()
        self.bare = bare
        
        if self.workdir == None:
            self.workdir = tempfile.gettempdir() + '/gitpoller_work'
            log.msg("WARNING: gitpoller using deprecated temporary workdir " +
                    "'%s'; consider setting workdir=" % self.workdir)

    def startService(self):
        # make our workdir absolute, relative to the master's basedir
        if not os.path.isabs(self.workdir):
            self.workdir = os.path.join(self.master.basedir, self.workdir)
            log.msg("gitpoller: using workdir '%s'" % self.workdir)

        # initialize the repository we'll use to get changes; note that
        # startService is not an event-driven method, so this method will
        # instead acquire self.initLock immediately when it is called.
        if os.path.exists(self.workdir + r'/.git'):
            if self.bare:
                log.msg("gitpoller: workdir %s is not bare, switching off option" % \
                        self.workdir)
                self.bare = False
            log.msg("GitPoller workdir repository already exists")
            d = self.setupRepository()
            d.addErrback(log.err, 'while setup GitPoller repository')
        
        elif self.bare and os.path.isdir(self.workdir + '/objects/info') \
                and os.path.exists(self.workdir + '/config'):
            log.msg("GitPoller bare repository already exists")
            d = self.setupRepository()
            d.addErrback(log.err, 'while setup GitPoller repository')
        else:
            d = self.initRepository()
            d.addCallback(self.setupRepository)
            d.addErrback(log.err, 'while initializing GitPoller repository')

        # call this *after* initRepository, so that the initLock is locked first
        base.PollingChangeSource.startService(self)

    @deferredLocked('initLock')
    def initRepository(self):
        d = defer.succeed(None)
        def make_dir(_):
            dirpath = os.path.dirname(self.workdir.rstrip(os.sep))
            if not os.path.exists(dirpath):
                log.msg('gitpoller: creating parent directories for workdir')
                os.makedirs(dirpath)
        d.addCallback(make_dir)

        def git_init(_):
            log.msg('gitpoller: initializing working dir from %s' % self.repourl)
            args = ['init', self.workdir]
            if self.bare:
                args.append('--bare')
            d = utils.getProcessOutputAndValue(self.gitbin, args, 
                    env=dict(PATH=os.environ['PATH']))
            d.addCallback(self._convert_nonzero_to_failure)
            d.addErrback(self._stop_on_failure)
            return d
        d.addCallback(git_init)
        return d
    
    @deferredLocked('initLock')
    def setupRepository(self, res=None):
        d = defer.succeed(res)
        def git_remote_list(_):
            """ Get the list of remotes from git
            """
            def git_remote_read(res):
                """ Parses stdout of 'git remote' and finds out if our remote is there
                """
                (stdout, stderr, code) = res
                if code != 0:
                    return defer.succeed(False)
                remotes = map(str.strip, stdout.split('\n'))
                if self.remoteName in remotes:
                    return defer.succeed(True)
                else:
                    return defer.succeed(False)
            
            d = utils.getProcessOutputAndValue(self.gitbin, ['remote'],
                    path=self.workdir, env=dict(PATH=os.environ['PATH']))
            d.addCallback(git_remote_read)
            return d
        
        d.addCallback(git_remote_list)
        
        def git_fetch_remote(_):
            args = ['fetch', self.remoteName]
            self._extend_with_fetch_refspec(args)
            d = utils.getProcessOutputAndValue(self.gitbin, args,
                    path=self.workdir, env=dict(PATH=os.environ['PATH']))
            d.addCallback(self._convert_nonzero_to_failure)
            d.addErrback(self._stop_on_failure)
            return d

        def git_skip_output(_):
            return defer.succeed(None)
        #def git_getlastchange(self, res):
        #    return self.master.db.changes.getLatestBranchChange(branch=self.branch,
        #        category=self.category, project=self.project)
            
        def git_remote_add(res):
            if res is True:
                # TODO: check branch names and reset, if needed
                d = utils.getProcessOutput(self.gitbin,
                    ['branch', '--no-color'],
                    path=self.workdir, env=dict(PATH=os.environ['PATH']))
                d.addCallback(self._init_master)
                d.addErrback(self._stop_on_failure)
                return d
            d = utils.getProcessOutputAndValue(self.gitbin,
                    ['remote', 'add', self.remoteName, self.repourl],
                    path=self.workdir, env=dict(PATH=os.environ['PATH']))
            d.addCallback(self._convert_nonzero_to_failure)
            d.addErrback(self._stop_on_failure)
            d.addCallback(git_fetch_remote)
            d.addCallback(git_skip_output)
            d.addCallback(self._init_master)
            return d
        d.addCallback(git_remote_add)
        d.addCallback(self._get_rev)
        return d

    def _init_master(self, res=None):
        """ if res is given, it should be the output of 'git branch', to work incrementaly
        """
        currentBranches = []
        if res is not None:
            currentBranches = [ b[2:].strip() for b in res.split('\n') ]
        
        if self.localBranch in currentBranches:
            log.msg("gitpoller: local branch %s is already setup" % self.localBranch)
            return defer.succeed(None)
        else:
            log.msg('gitpoller: checking out %s' % self.branch)
            args = []
            if self.bare:
                # We create a branch (no checkout, for bare), but not allow it
                # to automatically update its head to the remote side
                args = ['branch', '-f', '--no-track', \
                        self.localBranch, '%s/%s' % (self.remoteName, self.branch)]
            elif self.localBranch == 'master':
                # repo is already on branch 'master', so reset
                args = ['reset', '--hard', '%s/%s' % (self.remoteName, self.branch)]
            else:
                args = ['checkout', '-b', self.localBranch, '%s/%s' % (self.remoteName, self.branch)]
            
            d = utils.getProcessOutputAndValue(self.gitbin, args,
                    path=self.workdir, env=dict(PATH=os.environ['PATH']))
            d.addCallback(self._convert_nonzero_to_failure)
            d.addErrback(self._stop_on_failure)
            return d

    def _get_rev(self, res):
        d = utils.getProcessOutputAndValue(self.gitbin,
                ['rev-parse', self.localBranch],
                path=self.workdir, env={})
        d.addCallback(self._convert_nonzero_to_failure)
        d.addErrback(self._stop_on_failure)
        d.addCallback(lambda (out, err, code) : out.strip())
        
        def print_rev(rev):
            log.msg("gitpoller: finished initializing working dir from %s at rev %s"
                    % (self.repourl, rev))
        d.addCallback(print_rev)
        return d

    def describe(self):
        status = ""
        if not self.master:
            status = "[STOPPED - check log]"
        str = 'GitPoller watching the remote git repository %s, branch: %s %s' \
                % (self.repourl, self.branch, status)
        return str

    @deferredLocked('initLock')
    def poll(self):
        d = self._get_changes()
        d.addCallback(self._process_changes)
        d.addErrback(self._process_changes_failure)
        d.addCallback(self._catch_up)
        d.addErrback(self._catch_up_failure)
        return d

    def _get_commit_comments(self, rev):
        args = ['log', rev, '--no-walk', r'--format=%s%n%b']
        d = utils.getProcessOutput(self.gitbin, args, path=self.workdir, env=dict(PATH=os.environ['PATH']), errortoo=False )
        def process(git_output):
            stripped_output = git_output.strip().decode(self.encoding)
            if len(stripped_output) == 0:
                raise EnvironmentError('could not get commit comment for rev')
            return stripped_output
        d.addCallback(process)
        return d

    def _get_commit_timestamp(self, rev):
        # unix timestamp
        args = ['log', rev, '--no-walk', r'--format=%ct']
        d = utils.getProcessOutput(self.gitbin, args, path=self.workdir, env=dict(PATH=os.environ['PATH']), errortoo=False )
        def process(git_output):
            stripped_output = git_output.strip()
            if self.usetimestamps:
                try:
                    stamp = float(stripped_output)
                except Exception, e:
                        log.msg('gitpoller: caught exception converting output \'%s\' to timestamp' % stripped_output)
                        raise e
                return stamp
            else:
                return None
        d.addCallback(process)
        return d

    def _get_commit_files(self, rev):
        args = ['log', rev, '--name-only', '--no-walk', r'--format=%n']
        d = utils.getProcessOutput(self.gitbin, args, path=self.workdir, env=dict(PATH=os.environ['PATH']), errortoo=False )
        def process(git_output):
            fileList = git_output.split()
            return fileList
        d.addCallback(process)
        return d
            
    def _get_commit_name(self, rev):
        args = ['log', rev, '--no-walk', r'--format=%aE']
        d = utils.getProcessOutput(self.gitbin, args, path=self.workdir, env=dict(PATH=os.environ['PATH']), errortoo=False )
        def process(git_output):
            stripped_output = git_output.strip().decode(self.encoding)
            if len(stripped_output) == 0:
                raise EnvironmentError('could not get commit name for rev')
            return stripped_output
        d.addCallback(process)
        return d

    def _get_changes(self):
        log.msg('gitpoller: polling git repo at %s' % self.repourl)

        self.lastPoll = time.time()
        
        # get a deferred object that performs the fetch
        args = ['fetch', self.remoteName]
        self._extend_with_fetch_refspec(args)

        # This command always produces data on stderr, but we actually do not care
        # about the stderr or stdout from this command. We set errortoo=True to
        # avoid an errback from the deferred. The callback which will be added to this
        # deferred will not use the response.
        d = utils.getProcessOutput(self.gitbin, args,
                    path=self.workdir,
                    env=dict(PATH=os.environ['PATH']), errortoo=True )

        # TODO perhaps read the output
        return d

    @defer.deferredGenerator
    def _process_changes(self, unused_output):
        # get the change list
        revListArgs = ['log', '%s..%s/%s' % (self.localBranch, self.remoteName, self.branch), r'--format=%H']
        self.changeCount = 0
        d = utils.getProcessOutput(self.gitbin, revListArgs, path=self.workdir,
                                   env=dict(PATH=os.environ['PATH']), errortoo=False )
        wfd = defer.waitForDeferred(d)
        yield wfd
        results = wfd.getResult()

        # process oldest change first
        revList = results.split()
        if not revList:
            return

        revList.reverse()
        self.changeCount = len(revList)
            
        log.msg('gitpoller: processing %d changes: %s in "%s"'
                % (self.changeCount, revList, self.workdir) )

        for rev in revList:
            dl = defer.DeferredList([
                self._get_commit_timestamp(rev),
                self._get_commit_name(rev),
                self._get_commit_files(rev),
                self._get_commit_comments(rev),
            ], consumeErrors=True)

            wfd = defer.waitForDeferred(dl)
            yield wfd
            results = wfd.getResult()

            # check for failures
            failures = [ r[1] for r in results if not r[0] ]
            if failures:
                # just fail on the first error; they're probably all related!
                raise failures[0]

            timestamp, name, files, comments = [ r[1] for r in results ]
            d = self.master.addChange(
                   author=name,
                   revision=rev,
                   files=files,
                   comments=comments,
                   when_timestamp=epoch2datetime(timestamp),
                   branch=self.branch,
                   category=self.category,
                   project=self.project,
                   repository=self.repourl)
            wfd = defer.waitForDeferred(d)
            yield wfd
            results = wfd.getResult()

    def _process_changes_failure(self, f):
        log.msg('gitpoller: repo poll failed')
        log.err(f)
        # eat the failure to continue along the defered chain - we still want to catch up
        return None
        
    def _catch_up(self, res):
        if self.changeCount == 0:
            log.msg('gitpoller: no changes, no catch_up')
            return
        log.msg('gitpoller: catching up tracking branch')
        
        if self.bare:
            args = ['branch', '-f', '--no-track', \
                    self.localBranch, '%s/%s' % (self.remoteName, self.branch,)]
        else:
            args = ['reset', '--hard', '%s/%s' % (self.remoteName, self.branch,)]
        d = utils.getProcessOutputAndValue(self.gitbin, args, path=self.workdir, env=dict(PATH=os.environ['PATH']))
        d.addCallback(self._convert_nonzero_to_failure)
        return d

    def _catch_up_failure(self, f):
        log.err(f)
        log.msg('gitpoller: please resolve issues in local repo: %s' % self.workdir)
        # this used to stop the service, but this is (a) unfriendly to tests and (b)
        # likely to leave the error message lost in a sea of other log messages

    def _convert_nonzero_to_failure(self, res):
        "utility method to handle the result of getProcessOutputAndValue"
        (stdout, stderr, code) = res
        if code != 0:
            raise EnvironmentError('command failed with exit code %d: %s' % (code, stderr))
        return (stdout, stderr, code)

    def _stop_on_failure(self, f):
        "utility method to stop the service when a failure occurs"
        if self.running:
            d = defer.maybeDeferred(lambda : self.stopService())
            d.addErrback(log.err, 'while stopping broken GitPoller service')
        return f

    def _extend_with_fetch_refspec(self, args):
        if self.fetch_refspec:
            if type(self.fetch_refspec) in (list,set):
                args.extend(self.fetch_refspec)
            else:
                args.append(self.fetch_refspec)
