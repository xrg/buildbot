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

from buildbot import interfaces, util
from buildbot.util import collections, NotABranch
from buildbot.sourcestamp import SourceStamp
from buildbot.status.builder import SUCCESS, WARNINGS
from buildbot.schedulers import base

class Scheduler(base.BaseScheduler, base.ClassifierMixin):
    fileIsImportant = None
    compare_attrs = ('name', 'treeStableTimer', 'builderNames',
                     'fileIsImportant', 'properties', 'change_filter')

    def __init__(self, name, shouldntBeSet=NotABranch, treeStableTimer=None,
                builderNames=None, branch=NotABranch, fileIsImportant=None,
                properties={}, categories=None, change_filter=None):
        """
        @param name: the name of this Scheduler
        @param treeStableTimer: the duration, in seconds, for which the tree
                                must remain unchanged before a build is
                                triggered. This is intended to avoid builds
                                of partially-committed fixes. If None, then
                                a separate build will be made for each
                                Change, regardless of when they arrive.
        @param builderNames: a list of Builder names. When this Scheduler
                             decides to start a set of builds, they will be
                             run on the Builders named by this list.

        @param fileIsImportant: A callable which takes one argument (a Change
                                instance) and returns True if the change is
                                worth building, and False if it is not.
                                Unimportant Changes are accumulated until the
                                build is triggered by an important change.
                                The default value of None means that all
                                Changes are important.

        @param properties: properties to apply to all builds started from
                           this scheduler
        
        @param change_filter: a buildbot.schedulers.filter.ChangeFilter instance
                              used to filter changes for this scheduler

        @param branch: The branch name that the Scheduler should pay
                       attention to. Any Change that is not in this branch
                       will be ignored. It can be set to None to only pay
                       attention to the default branch.
        @param categories: A list of categories of changes to accept
        """
        assert shouldntBeSet is NotABranch, \
                "pass arguments to Scheduler using keyword arguments"

        base.BaseScheduler.__init__(self, name, builderNames, properties)
        self.make_filter(change_filter=change_filter, branch=branch, categories=categories)
        self.treeStableTimer = treeStableTimer
        self.stableAt = None
        self.branch = branch
        if fileIsImportant:
            assert callable(fileIsImportant)
            self.fileIsImportant = fileIsImportant

    def get_initial_state(self, max_changeid):
        return {"last_processed": max_changeid}

    def run(self):
        db = self.parent.db
        d = db.runInteraction(self.classify_changes)
        d.addCallback(lambda ign: db.runInteraction(self._process_changes))
        return d

    def _process_changes(self, t):
        db = self.parent.db
        res = db.scheduler_get_classified_changes(self.schedulerid, t)
        (important, unimportant) = res
        return self.decide_and_remove_changes(t, important, unimportant)

    def decide_and_remove_changes(self, t, important, unimportant):
        """Look at the changes that need to be processed and decide whether
        to queue a BuildRequest or sleep until something changes.

        If I decide that a build should be performed, I will add the
        appropriate BuildRequest to the database queue, and remove the
        (retired) changes that went into it from the scheduler_changes table.

        Returns wakeup_delay: either None, or a float indicating when this
        scheduler wants to be woken up next. The Scheduler is responsible for
        padding its desired wakeup time by about a second to avoid frenetic
        must-wake-up-at-exactly-8AM behavior. The Loop may silently impose a
        minimum delay request of a couple seconds to prevent this sort of
        thing, but Schedulers must still add their own padding to avoid at
        least a double wakeup.
        """

        if not important:
            return None
        all_changes = important + unimportant
        most_recent = max([c.when for c in all_changes])
        if self.treeStableTimer is not None:
            now = time.time()
            self.stableAt = most_recent + self.treeStableTimer
            if self.stableAt > now:
                # Wake up one second late, to avoid waking up too early and
                # looping a lot.
                return self.stableAt + 1.0

        # ok, do a build
        self.stableAt = None
        self._add_build_and_remove_changes(t, important, unimportant)
        return None

    def _add_build_and_remove_changes(self, t, important, unimportant):
        # the changes are segregated into important and unimportant
        # changes, but we need it ordered earliest to latest, based
        # on change number, since the SourceStamp will be created
        # based on the final change.
        all_changes = sorted(important + unimportant, key=lambda c : c.number)

        db = self.parent.db
        if self.treeStableTimer is None:
            # each *important* Change gets a separate build.  Unimportant
            # builds get ignored.
            for c in sorted(important, key=lambda c : c.number):
                ss = SourceStamp(changes=[c])
                ssid = db.get_sourcestampid(ss, t)
                self.create_buildset(ssid, "scheduler", t)
        else:
            # if we had a treeStableTimer, then trigger a build for the
            # whole pile - important or not.  There's at least one important
            # change in the list, or we wouldn't have gotten here.
            ss = SourceStamp(changes=all_changes)
            ssid = db.get_sourcestampid(ss, t)
            self.create_buildset(ssid, "scheduler", t)

        # and finally retire all of the changes from scheduler_changes, regardless
        # of importance level
        changeids = [c.number for c in all_changes]
        db.scheduler_retire_changes(self.schedulerid, changeids, t)

    # the waterfall needs to know the next time we plan to trigger a build
    def getPendingBuildTimes(self):
        if self.stableAt and self.stableAt > util.now():
            return [ self.stableAt ]
        return []

class AnyBranchScheduler(Scheduler):
    compare_attrs = ('name', 'treeStableTimer', 'builderNames',
                     'fileIsImportant', 'properties', 'change_filter')
    def __init__(self, name, treeStableTimer, builderNames,
                 fileIsImportant=None, properties={}, categories=None,
                 branches=NotABranch, change_filter=None):
        """
        Same parameters as the scheduler, but without 'branch', and adding:

        @param branches: (deprecated)
        """

        Scheduler.__init__(self, name, builderNames=builderNames, properties=properties,
                categories=categories, treeStableTimer=treeStableTimer,
                fileIsImportant=fileIsImportant, change_filter=change_filter,
                # this is the interesting part:
                branch=branches)

    def _process_changes(self, t):
        db = self.parent.db
        res = db.scheduler_get_classified_changes(self.schedulerid, t)
        (important, unimportant) = res
        def _twolists(): return [], [] # important, unimportant
        branch_changes = collections.defaultdict(_twolists)
        for c in important:
            branch_changes[c.branch][0].append(c)
        for c in unimportant:
            branch_changes[c.branch][1].append(c)
        delays = []
        for branch in branch_changes:
            (b_important, b_unimportant) = branch_changes[branch]
            delay = self.decide_and_remove_changes(t, b_important,
                                                   b_unimportant)
            if delay is not None:
                delays.append(delay)
        if delays:
            return min(delays)
        return None

class Dependent(base.BaseScheduler):
    # register with our upstream, so they'll tell us when they submit a
    # buildset
    compare_attrs = ('name', 'upstream_name', 'builderNames', 'properties')

    def __init__(self, name, upstream, builderNames, properties={}):
        assert interfaces.IScheduler.providedBy(upstream)
        base.BaseScheduler.__init__(self, name, builderNames, properties)
        # by setting self.upstream_name, our buildSetSubmitted() method will
        # be called whenever that upstream Scheduler adds a buildset to the
        # DB.
        self.upstream_name = upstream.name

    def buildSetSubmitted(self, bsid, t):
        db = self.parent.db
        db.scheduler_subscribe_to_buildset(self.schedulerid, bsid, t)

    def run(self):
        d = self.parent.db.runInteraction(self._run)
        return d
    def _run(self, t):
        db = self.parent.db
        res = db.scheduler_get_subscribed_buildsets(self.schedulerid, t)
        # this returns bsid,ssid,results for all of our active subscriptions.
        # We ignore the ones that aren't complete yet. This leaves the
        # subscription in place until the buildset is complete.
        for (bsid,ssid,complete,results) in res:
            if complete:
                if results in (SUCCESS, WARNINGS):
                    self.create_buildset(ssid, "downstream", t)
                db.scheduler_unsubscribe_buildset(self.schedulerid, bsid, t)
        return None

# Dependent/Triggerable schedulers will make a BuildSet with linked
# BuildRequests. The rest (which don't generally care when the set
# finishes) will just make the BuildRequests.

# runInteraction() should give us the all-or-nothing transaction
# semantics we want, with synchronous operation during the
# interaction function, transactions fail instead of retrying. So if
# a concurrent actor touches the database in a way that blocks the
# transaction, we'll get an errback. That will cause the overall
# Scheduler to errback, and not commit its "all Changes before X have
# been handled" update. The next time that Scheduler is processed, it
# should try everything again.
