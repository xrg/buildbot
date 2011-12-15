%define git_repo buildbot

#define distsuffix xrg

%{?!py_requires: %global py_requires(d) BuildRequires: python}
%{?!py_sitedir: %global py_sitedir %(python -c 'import distutils.sysconfig; print distutils.sysconfig.get_python_lib()' 2>/dev/null || echo PYTHON-LIBDIR-NOT-FOUND)}

Name:		python-buildbot
Summary:	Continuous integration system for building/testing of software
Version:	%git_get_ver
Release:	%mkrel %git_get_rel
URL:		http://trac.buildbot.net/
Source0:	%git_bs_source %{name}-%{version}.tar.gz
License:	GPLv2
BuildArch:	noarch
Group:		Libraries
BuildRequires:	python
%py_requires -d

%description
Buildbot is a continuous integration system designed to automate the
build/test cycle. By automatically rebuilding and testing the tree each
time something has changed, build problems are pinpointed quickly, before
other developers are inconvenienced by the failure.

%package master
Summary:	Continuous integration system, Master
Group:		Libraries
BuildRequires:	python
%py_requires -d

%description master
The BuildBot is a system to automate the compile/test cycle required by
most software projects to validate code changes. By automatically
rebuilding and testing the tree each time something has changed, build
problems are pinpointed quickly, before other developers are
inconvenienced by the failure. The guilty developer can be identified
and harassed without human intervention. By running the builds on a
variety of platforms, developers who do not have the facilities to test
their changes everywhere before checkin will at least know shortly
afterwards whether they have broken the build or not. Warning counts,
lint checks, image size, compile time, and other build parameters can
be tracked over time, are more visible, and are therefore easier to
improve.

%package slave
Summary:	Continuous integration system, Build Slave Daemon
Group:		Libraries
BuildRequires:	python
%py_requires -d

%description slave
The build Slave Daemon will connect to a Buildbot Master and carry out the
builds instructed.

%prep
%git_get_source
%setup -q

%build
pushd master
python setup.py build
popd
pushd slave
python setup.py build
popd

%install
pushd master
	python setup.py install --root=%{buildroot} --compile
popd

pushd slave
	python setup.py install --root=%{buildroot} --compile
popd

%files master
%defattr(-,root,root)
%{_bindir}/buildbot
%doc master/COPYING master/CREDITS master/README master/NEWS master/UPGRADING
%{py_sitedir}/buildbot/*
%{py_sitedir}/buildbot-*-py*.egg-info/*

%files slave
%defattr(-,root,root)
%{_bindir}/buildslave
%doc slave/COPYING slave/README slave/NEWS slave/UPGRADING
%{py_sitedir}/buildslave/*
%{py_sitedir}/buildbot_slave-*-py*.egg-info/*

