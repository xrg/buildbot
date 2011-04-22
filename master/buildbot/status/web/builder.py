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


from twisted.web import html
from twisted.web.util import Redirect

import re, urllib, time
from twisted.python import log
from buildbot import interfaces
from buildbot.status.web.base import HtmlResource, BuildLineMixin, \
    path_to_build, path_to_slave, path_to_builder, path_to_change, \
    path_to_root, getAndCheckProperties, ICurrentBox, build_get_class, \
    map_branches, path_to_authfail

from buildbot.status.web.build import BuildsResource, StatusResourceBuild
from buildbot import util

# /builders/$builder
class StatusResourceBuilder(HtmlResource, BuildLineMixin):
    addSlash = True

    def __init__(self, builder_status):
        HtmlResource.__init__(self)
        self.builder_status = builder_status

    def getTitle(self, request):
        return "Buildbot: %s" % self.builder_status.getName()

    def builder(self, build, req):
        b = {}

        b['num'] = build.getNumber()
        b['link'] = path_to_build(req, build)

        when = build.getETA()
        if when is not None:
            b['when'] = util.formatInterval(when)
            b['when_time'] = time.strftime("%H:%M:%S",
                                      time.localtime(time.time() + when))

        step = build.getCurrentStep()
        # TODO: is this necessarily the case?
        if not step:
            b['current_step'] = "[waiting for Lock]"
        else:
            if step.isWaitingForLocks():
                b['current_step'] = "%s [waiting for Lock]" % step.getName()
            else:
                b['current_step'] = step.getName()

        b['stop_url'] = path_to_build(req, build) + '/stop'

        return b

    def content(self, req, cxt):
        b = self.builder_status

        cxt['name'] = b.getName()
        req.setHeader('Cache-Control', 'no-cache')
        slaves = b.getSlaves()
        connected_slaves = [s for s in slaves if s.isConnected()]

        cxt['current'] = [self.builder(x, req) for x in b.getCurrentBuilds()]

        cxt['pending'] = []
        for pb in b.getPendingBuildRequestStatuses():
            source = pb.getSourceStamp()
            changes = []

            if source.changes:
                for c in source.changes:
                    changes.append({ 'url' : path_to_change(req, c),
                                     'who' : c.who,
                                     'revision' : c.revision,
                                     'repo' : c.repository })

            cxt['pending'].append({
                'when': time.strftime("%b %d %H:%M:%S", time.localtime(pb.getSubmitTime())),
                'delay': util.formatInterval(util.now() - pb.getSubmitTime()),
                'id': pb.brid,
                'changes' : changes,
                'num_changes' : len(changes),
                })

        numbuilds = int(req.args.get('numbuilds', ['5'])[0])
        recent = cxt['recent'] = []
        for build in b.generateFinishedBuilds(num_builds=int(numbuilds)):
            recent.append(self.get_line_values(req, build, False))

        sl = cxt['slaves'] = []
        connected_slaves = 0
        for slave in slaves:
            s = {}
            sl.append(s)
            s['link'] = path_to_slave(req, slave)
            s['name'] = slave.getName()
            c = s['connected'] = slave.isConnected()
            if c:
                s['admin'] = unicode(slave.getAdmin() or '', 'utf-8')
                connected_slaves += 1
        cxt['connected_slaves'] = connected_slaves

        cxt['authz'] = self.getAuthz(req)
        cxt['builder_url'] = path_to_builder(req, b)

        template = req.site.buildbot_service.templates.get_template("builder.html")
        return template.render(**cxt)

    def force(self, req, auth_ok=False):
        name = req.args.get("username", ["<unknown>"])[0]
        reason = req.args.get("comments", ["<no reason specified>"])[0]
        branch = req.args.get("branch", [""])[0]
        revision = req.args.get("revision", [""])[0]
        repository = req.args.get("repository", [""])[0]
        project = req.args.get("project", [""])[0]

        log.msg("web forcebuild of builder '%s', branch='%s', revision='%s',"
                " repository='%s', project='%s' by user '%s'" % (
                self.builder_status.getName(), branch, revision, repository,
                project, name))

        # check if this is allowed
        if not auth_ok:
            if not self.getAuthz(req).actionAllowed('forceBuild', req, self.builder_status):
                log.msg("..but not authorized")
                return Redirect(path_to_authfail(req))

        # keep weird stuff out of the branch revision, and property strings.
        # TODO: centralize this somewhere.
        if not re.match(r'^[\w.+/~-]*$', branch):
            log.msg("bad branch '%s'" % branch)
            return Redirect(path_to_builder(req, self.builder_status))
        if not re.match(r'^[ \w\.\-\/]*$', revision):
            log.msg("bad revision '%s'" % revision)
            return Redirect(path_to_builder(req, self.builder_status))
        properties = getAndCheckProperties(req)
        if properties is None:
            return Redirect(path_to_builder(req, self.builder_status))
        if not branch:
            branch = None
        if not revision:
            revision = None

        master = self.getBuildmaster(req)
        d = master.db.sourcestamps.createSourceStamp(branch=branch,
                revision=revision, project=project, repository=repository)
        def make_buildset(ssid):
            r = ("The web-page 'force build' button was pressed by '%s': %s\n"
                 % (html.escape(name), html.escape(reason)))
            return master.addBuildset(
                    builderNames=[self.builder_status.getName()],
                    ssid=ssid, reason=r, properties=properties.asDict())
        d.addCallback(make_buildset)
        d.addErrback(log.err, "(ignored) while trying to force build")
        # send the user back to the builder page
        return Redirect(path_to_builder(req, self.builder_status))

    def ping(self, req):
        log.msg("web ping of builder '%s'" % self.builder_status.getName())
        if not self.getAuthz(req).actionAllowed('pingBuilder', req, self.builder_status):
            log.msg("..but not authorized")
            return Redirect(path_to_authfail(req))
        c = interfaces.IControl(self.getBuildmaster(req))
        bc = c.getBuilder(self.builder_status.getName())
        bc.ping()
        # send the user back to the builder page
        return Redirect(path_to_builder(req, self.builder_status))

    def cancelbuild(self, req):
        try:
            request_id = req.args.get("id", [None])[0]
            if request_id == "all":
                cancel_all = True
            else:
                cancel_all = False
                request_id = int(request_id)
        except:
            request_id = None

        authz = self.getAuthz(req)
        if request_id:
            c = interfaces.IControl(self.getBuildmaster(req))
            bc = c.getBuilder(self.builder_status.getName())
            for build_req in bc.getPendingBuildRequestControls():
                if cancel_all or (build_req.brid == request_id):
                    log.msg("Cancelling %s" % build_req)
                    if authz.actionAllowed('cancelPendingBuild', req, build_req):
                        build_req.cancel()
                    else:
                        return Redirect(path_to_authfail(req))
                    if not cancel_all:
                        break
        return Redirect(path_to_builder(req, self.builder_status))

    def stopchange(self, req, auth_ok=False):
        """Cancel all pending builds that include a given numbered change."""
        try:
            request_change = req.args.get("change", [None])[0]
            request_change = int(request_change)
        except:
            request_change = None

        authz = self.getAuthz(req)
        if request_change:
            c = interfaces.IControl(self.getBuildmaster(req))
            builder_control = c.getBuilder(self.builder_status.getName())
            brcontrols = builder_control.getPendingBuildRequestControls()
            build_controls = dict((x.brid, x) for x in brcontrols)
            for build_req in self.builder_status.getPendingBuildRequestStatuses():
                ss = build_req.getSourceStamp()
                if not ss.changes:
                    continue
                for change in ss.changes:
                    if change.number == request_change:
                        control = build_controls[build_req.brid]
                        log.msg("Cancelling %s" % control)
                        if auth_ok or authz.actionAllowed('stopChange', req, control):
                            control.cancel()
                        else:
                            return Redirect(path_to_authfail(req))
        return Redirect(path_to_builder(req, self.builder_status))

    def getChild(self, path, req):
        if path == "force":
            return self.force(req)
        if path == "ping":
            return self.ping(req)
        if path == "cancelbuild":
            return self.cancelbuild(req)
        if path == "stopchange":
            return self.stopchange(req)
        if path == "builds":
            return BuildsResource(self.builder_status)

        return HtmlResource.getChild(self, path, req)


# /builders/_all
class StatusResourceAllBuilders(HtmlResource, BuildLineMixin):

    def __init__(self, status):
        HtmlResource.__init__(self)
        self.status = status

    def getChild(self, path, req):
        if path == "forceall":
            return self.forceall(req)
        if path == "stopall":
            return self.stopall(req)
        if path == "stopchangeall":
            return self.stopchangeall(req)

        return HtmlResource.getChild(self, path, req)

    def forceall(self, req):
        authz = self.getAuthz(req)
        if not authz.actionAllowed('forceAllBuilds', req):
            return Redirect(path_to_authfail(req))

        for bname in self.status.getBuilderNames():
            builder_status = self.status.getBuilder(bname)
            build = StatusResourceBuilder(builder_status)
            build.force(req, auth_ok=True) # auth_ok because we already checked
        # back to the welcome page
        return Redirect(path_to_root(req))

    def stopall(self, req):
        authz = self.getAuthz(req)
        if not authz.actionAllowed('stopAllBuilds', req):
            return Redirect(path_to_authfail(req))

        for bname in self.status.getBuilderNames():
            builder_status = self.status.getBuilder(bname)
            (state, current_builds) = builder_status.getState()
            if state != "building":
                continue
            for b in current_builds:
                build_status = builder_status.getBuild(b.number)
                if not build_status:
                    continue
                build = StatusResourceBuild(build_status)
                build.stop(req, auth_ok=True)
        # go back to the welcome page
        return Redirect(path_to_root(req))

    def stopchangeall(self, req):
        authz = self.getAuthz(req)
        if not authz.actionAllowed('stopChange', req):
            return Redirect(path_to_authfail(req))

        for bname in self.status.getBuilderNames():
            builder_status = self.status.getBuilder(bname)
            build = StatusResourceBuilder(builder_status)
            build.stopchange(req, auth_ok=True)

        return Redirect(path_to_root(req))


# /builders
class BuildersResource(HtmlResource):
    title = "Builders"
    addSlash = True

    def content(self, req, cxt):
        status = self.getStatus(req)

        builders = req.args.get("builder", status.getBuilderNames())
        branches = [b for b in req.args.get("branch", []) if b]

        cxt['branches'] = branches
        bs = cxt['builders'] = []

        building = 0
        online = 0
        base_builders_url = path_to_root(req) + "builders/"
        for bn in builders:
            bld = { 'link': base_builders_url + urllib.quote(bn, safe=''),
                    'name': bn }
            bs.append(bld)

            builder = status.getBuilder(bn)
            builds = list(builder.generateFinishedBuilds(map_branches(branches),
                                                         num_builds=1))
            if builds:
                b = builds[0]
                bld['build_url'] = (bld['link'] + "/builds/%d" % b.getNumber())
                try:
                    label = b.getProperty("got_revision")
                except KeyError:
                    label = None
                if not label or len(str(label)) > 20:
                    label = "#%d" % b.getNumber()

                bld['build_label'] = label
                bld['build_text'] = " ".join(b.getText())
                bld['build_css_class'] = build_get_class(b)

            current_box = ICurrentBox(builder).getBox(status)
            bld['current_box'] = current_box.td()

            builder_status = builder.getState()[0]
            if builder_status == "building":
                building += 1
                online += 1
            elif builder_status != "offline":
                online += 1

        cxt['authz'] = self.getAuthz(req)
        cxt['num_building'] = building
        cxt['num_online'] = online

        template = req.site.buildbot_service.templates.get_template("builders.html")
        return template.render(**cxt)

    def getChild(self, path, req):
        s = self.getStatus(req)
        if path in s.getBuilderNames():
            builder_status = s.getBuilder(path)
            return StatusResourceBuilder(builder_status)
        if path == "_all":
            return StatusResourceAllBuilders(self.getStatus(req))

        return HtmlResource.getChild(self, path, req)

