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
# Copyright Buildbot Team Members, P. Christeas 2011

import os
from twisted.python import log
from twisted.internet import defer, utils

from buildbot.changes import gitpoller
from buildbot.util import epoch2datetime

class GitMultiPoller(gitpoller.GitPoller):
    """This source will poll a remote git repo at multiple branches
    """

    compare_attrs = gitpoller.GitPoller.compare_attrs + ['branchSpecs']

    def __init__(self, branchSpecs=False, allHistory=False, **kwargs):
        """
            @param branchSpecs A list of branch or (branch, localBranch [,props]) ,
                branches to fetch. If just a string, localBranch will be assumed to be equal.
                The third, `props` item of the tuple can be a dict to be passed transparently
                to _doAddChange()
        """

        assert not kwargs.get('branch', False), "You should not specify a (single) branch!"
        kwargs['branch'] = None
        gitpoller.GitPoller.__init__(self, **kwargs)
        assert isinstance(branchSpecs, (list, tuple)), branchSpecs
        def str2tuple(branch):
            if isinstance(branch, (tuple,list)):
                if len(branch) == 3:
                    return tuple(branch)
                elif len(branch) == 2:
                    return (branch[0], branch[1], None)
                else:
                    raise IndexError("branchSpecs tuples must have 2-3 items, not %d" % len(branch))
            elif isinstance(branch, basestring):
                # note: we can't handle non-ascii yet
                return (str(branch), str(branch), None)
            else:
                raise TypeError("Can't handle %s in branchSpecs item" % type(branch))

        self.branchSpecs = map(str2tuple, branchSpecs)
        self.allHistory = allHistory

    def describe(self):
        status = ""
        if not self.master:
            status = "[STOPPED - check log]"
        str = 'GitPoller watching the remote git repository %s, branch(es): %s %s' \
                % (self.repourl, (', '.join([bs[0] for bs in self.branchSpecs])), status)
        return str

    def _catch_up(self, res):
        if self.changeCount == 0:
            log.msg('gitpoller: no changes, no catch_up')
            return
        log.msg('gitpoller: catching up tracking branches')

        def _set_branch(res, branch, localBranch):
            args = ['branch', '-f', '--no-track', \
                    localBranch, '%s/%s' % (self.remoteName, branch)]
            d = utils.getProcessOutputAndValue(self.gitbin, args,
                    path=self.workdir, env=dict(PATH=os.environ['PATH']))
            d.addCallback(self._convert_nonzero_to_failure)
            return d

        def _checkout_branch(res, localBranch):
            args = ['checkout', '-f', localBranch]
            d = utils.getProcessOutputAndValue(self.gitbin, args,
                    path=self.workdir, env=dict(PATH=os.environ['PATH']))
            d.addCallback(self._convert_nonzero_to_failure)
            return d

        def _reset_branch(res, branch):
            args = ['reset', '--hard', '%s/%s' % (self.remoteName, branch)]
            d = utils.getProcessOutputAndValue(self.gitbin, args,
                    path=self.workdir, env=dict(PATH=os.environ['PATH']))
            d.addCallback(self._convert_nonzero_to_failure)
            return d

        deds = []
        for branch, localBranch, props in self.branchSpecs:
            # Note, always doing it the "bare" way, so that we don't need
            # to checkout branches all the time
            if self.bare:
                d = _set_branch(None, branch, localBranch)
            else:
                d = _checkout_branch(None, localBranch)
                d.addCallback(_reset_branch, branch=branch)
            deds.append(d)

        return defer.DeferredList(deds)

    def _init_master(self, res=None):
        """ if res is given, it should be the output of 'git branch', to work incrementaly
        """
        currentBranches = []
        if res is not None:
            assert isinstance(res, basestring), type(res)
            currentBranches = [ b[2:].strip() for b in res.split('\n') ]

        if self.allHistory:
            self.allHistory = [ localBranch for b, localBranch, p in self.branchSpecs]
        deds = []
        for branch, localBranch, props in self.branchSpecs:
            if localBranch in currentBranches:
                # we don't need to do anything, branch is here
                # not even need to update it, because _catch_up will do that
                if self.allHistory:
                    self.allHistory.remove(localBranch)
                continue

            args = []
            if self.bare:
                # We create a branch (no checkout, for bare), but not allow it
                # to automatically update its head to the remote side
                log.msg('gitpoller: branching from %s/%s' % (self.remoteName, branch))
                args = ['branch', '-f', '--no-track', \
                        localBranch, '%s/%s' % (self.remoteName, branch)]
            elif localBranch == 'master':
                log.msg('gitpoller: checking master from %s/%s' % (self.remoteName, branch))
                d = utils.getProcessOutputAndValue(self.gitbin, ['checkout', '-f','master'],
                    path=self.workdir, env=dict(PATH=os.environ['PATH']))
                d.addCallback(self._convert_nonzero_to_failure)
                d.addErrback(self._stop_on_failure)
                deds.append(d)
                args = ['reset', '--hard', '%s/%s' % (self.remoteName, branch)]
            else:
                log.msg('gitpoller: checking out %s/%s' % (self.remoteName, branch))
                args = ['checkout', '-b', localBranch, '%s/%s' % (self.remoteName, branch)]

            d = utils.getProcessOutputAndValue(self.gitbin, args,
                    path=self.workdir, env=dict(PATH=os.environ['PATH']))
            d.addCallback(self._convert_nonzero_to_failure)
            d.addErrback(self._stop_on_failure)
            deds.append(d)
        
        if self.allHistory:
            log.msg("gitpoller: Still need to get history from %s branch(es)" % \
                        ', '.join(self.allHistory))

        return defer.DeferredList(deds, fireOnOneErrback=True)


    #: separator, much like e-mail mimetype boundary
    log_separator_commit = '----- HarZZypDCzU -----'
    log_separator_files = '----- ZCZbb85R0B0 -----'
    log_separator_fields = '----- XqcaaUtPdAo -----'

    #: map of field name to 'git log --format' specifiers, so that we get
    # more data with one 'git log' operation
    log_fields = { 'hash': '%H' , 'subject': '+%s', 'body': '+%b',
            'name': '%aE', 'timestamp': '%ct'}

    log_arguments = ['--first-parent', '--name-only']

    @defer.deferredGenerator
    def _process_changes(self, unused_output):
        log.msg("Processing changes in %d branches" % len(self.branchSpecs))
        # first, format the '--format' expression
        format_str = self.log_separator_commit + '%n'
        for key, fmt in self.log_fields.items():
            if fmt.startswith('+'):
                continue
            format_str += '%s: %s%%n' %(key, fmt)

        # second iteration, for multiline fields
        for key, fmt in self.log_fields.items():
            if fmt.startswith('+'):
                format_str += '%s:+%%n%s%%n%s%%n' % (key, fmt[1:], self.log_separator_fields)
        format_str += self.log_separator_files # but no newline needed here

        self.changeCount = 0
        
        currentBranches = None
        if self.allHistory:
            currentBranches = [ '%s/%s' % (self.remoteName, branch) \
                                for branch, localBranch, p in self.branchSpecs \
                                    if localBranch not in self.allHistory]
            # print "allHistory, already know:", currentBranches

        for branch, localBranch, props in self.branchSpecs:
            revListArgs = ['log',] + self.log_arguments + \
                    [ '--format='+format_str,]
            historic_mode = False
            if self.allHistory and localBranch in self.allHistory:
                # so, we need to scan the full history of that branch, rather than
                # its newer commmits.
                # We need a starting point, so we'll use the merge base of all other
                # branches to this
                historic_mode = True
                if currentBranches:
                    d = utils.getProcessOutput(self.gitbin,
                                    ['merge-base', '--octopus'] + currentBranches, path=self.workdir,
                                    env=dict(PATH=os.environ['PATH']), errortoo=False )
                    wfd = defer.waitForDeferred(d)
                    yield wfd
                    results = wfd.getResult()
                    assert results, "No merge-base result"
                    revListArgs.append('%s..%s/%s' % \
                            (results.strip(), self.remoteName, branch))
                else:
                    # no other branch existed before this, so scan till the dawn of time
                    revListArgs.append('%s/%s' % (self.remoteName, branch))
                currentBranches.append('%s/%s' % (self.remoteName, branch)) # mark its contents as known
                self.allHistory.remove(localBranch)
            else:
                revListArgs.append('%s..%s/%s' % (localBranch, self.remoteName, branch))
            # hope it's not too much output ...
            # log.msg("gitpoller: revListArgs: %s" % ' '.join(revListArgs))
            d = utils.getProcessOutput(self.gitbin, revListArgs, path=self.workdir,
                                    env=dict(PATH=os.environ['PATH']), errortoo=False )
            wfd = defer.waitForDeferred(d)
            yield wfd
            results = wfd.getResult()

            revList = []
            revDict = None #: current commit being parsed
            bodyField = None #: key of current 'body' field
            inFiles = False

            for rline in results.splitlines():
                if rline == self.log_separator_commit:
                    if revDict:
                        revList.append(revDict)
                    revDict = {}
                    bodyField = None
                    inFiles = False
                    continue
                if revDict is None:
                    raise ValueError("Unknown line outside of commit: %r" % rline[:60])

                if rline == self.log_separator_files:
                    bodyField = None
                    inFiles = True
                    revDict['files'] = []
                    continue

                if bodyField:
                    if rline == self.log_separator_fields:
                        bodyField = None # close it
                    else:
                        revDict[bodyField] += rline + '\n'
                    continue

                if inFiles:
                    if rline:
                        revDict['files'].append(rline.strip())
                    continue

                # last case, a simple field
                assert rline
                fld, val = rline.split(':',1)
                assert fld in self.log_fields, "invalid field: %s" % fld
                if val.startswith('+'):
                    assert self.log_fields[fld].startswith('+') #fishy!
                    assert val == '+' # don't expect content here
                    revDict[fld] = val[1:]
                    bodyField = fld
                else:
                    assert val[0] == ' ', val
                    revDict[fld] = val[1:]

            if revDict:
                revList.append(revDict)
                revDict = None

            # process oldest change first
            if not revList:
                continue # with other branches

            revList.reverse()
            self.changeCount += len(revList)

            log.msg('gitpoller: processed %d changes in: "%s" %s'
                    % (self.changeCount, self.workdir, localBranch) )

            dl = defer.DeferredList( \
                [ self._doAddChange(branch=branch, revDict=revDict,
                                    historic=historic_mode, props=props) \
                    for revDict in revList])
            wfd = defer.waitForDeferred(dl)
            yield wfd
            wfd.getResult()
        # end for

    def _doAddChange(self, branch, revDict, historic=False, props=None):
        """ add a change from values of revDict

            @param branch the branch being examined
            @param revDict a dictionary of values from 'git log', according to `log_fields`
            @param props  the optional 3rd item of self.branchSpecs

            @return a deferred
        """
        comments = revDict['subject'] + '\n\n' + revDict['body']

        d = self.master.addChange(
                author=revDict['name'],
                revision=revDict['hash'],
                files=revDict['files'],
                comments=comments,
                when_timestamp=epoch2datetime(float(revDict['timestamp'])),
                branch=branch,
                category=self.category,
                project=self.project,
                repository=self.repourl,
                skip_build=historic)
        return d

    def _get_rev(self, res):
        log.msg("gitpoller: finished initializing working dir from %s"
                    % (self.repourl))
        return defer.succeed(None)

#eof