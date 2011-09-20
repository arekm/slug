#!/usr/bin/python3

import copy
import glob
import sys
import os
import shutil
import subprocess
import queue
import threading

import argparse

import signal
import configparser

from git_slug.gitconst import GITSERVER, GIT_REPO, GIT_REPO_PUSH, REMOTEREFS
from git_slug.gitrepo import GitRepo, GitRepoError
from git_slug.refsdata import GitRemoteRefsData, RemoteRefsError

class DelAppend(argparse._AppendAction):
    def __call__(self, parser, namespace, values, option_string=None):
        item = copy.copy(getattr(namespace, self.dest, None)) if getattr(namespace, self.dest, None) is not None else []
        try:
            self._firstrun
        except AttributeError:
            self._firstrun = True
            del item[:]
        item.append(values)
        setattr(namespace, self.dest, item)

class ThreadFetch(threading.Thread):
    def __init__(self, queue, dir, depth=0):
        threading.Thread.__init__(self)
        self.queue = queue
        self.packagesdir = dir
        self.depth = depth

    def run(self):
        while True:
            (package, ref2fetch) = self.queue.get()
            gitrepo = GitRepo(os.path.join(self.packagesdir, package))
            (stdout, stderr) = gitrepo.fetch(ref2fetch, self.depth)
            print('------', package, '------\n' + stderr.decode('utf-8'))
            self.queue.task_done()

def readconfig(path):
    config = configparser.ConfigParser(delimiters='=', interpolation=None, strict=False)
    config.read(path)
    optionslist = {}
    if config.has_option('user','email'):
        optionslist['user'] = config['user']['email'].partition('@')[0]
    for option in ('newpkgs', 'prune'):
        if config.has_option('PLD',option):
            optionslist[option] = config.getboolean('PLD', option)
    for option in ('depth', 'repopattern', 'packagesdir', 'remoterefs'):
        if config.has_option('PLD',option):
            optionslist[option] = config.get('PLD', option)
    if config.has_option('PLD','branch'):
        optionslist['branch'] = config.get('PLD', 'branch').split()
    for option in ('j'):
        if config.has_option('PLD',option):
            optionslist[option] = config.getint('PLD', option)
    return optionslist

def initpackage(name, options):
    repo = GitRepo(os.path.join(options.packagesdir, name))
    if options.user:
        remotepush = 'ssh://' + os.path.join(options.user+GIT_REPO_PUSH ,name)
    else:
        remotepush = None
    repo.init(os.path.join(GIT_REPO,name), remotepush)
    return repo

def createpackage(name, options):
    if not options.user:
        print('user not defined', file=sys.stderr)
        sys.exit(1)
    if subprocess.Popen(['ssh', options.user+'@'+GITSERVER, 'create', name]).wait():
        sys.exit(1)
    initpackage(name, options)

def create_packages(options):
    for package in options.packages:
        createpackage(package, options)

def fetch_packages(options):
    fetch_queue = queue.Queue()
    for i in range(options.j):
        t = ThreadFetch(fetch_queue, options.packagesdir, options.depth)
        t.setDaemon(True)
        t.start()

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    try:
        refs = GitRemoteRefsData(options.remoterefs, options.branch, options.repopattern)
    except GitRepoError as e:
        print('Problem with repository {}: {}'.format(options.remoterefs,e), file=sys.stderr)
        sys.exit(1)
    except RemoteRefsError as e:
        print('Problem with file {} in repository {}'.format(*e), file=sys.stderr)
        sys.exit(1)


    print('Read remotes data')
    for dir in sorted(refs.heads):
        gitdir = os.path.join(options.packagesdir, dir, '.git')
        if not os.path.isdir(gitdir):
            if options.newpkgs:
                gitrepo = initpackage(dir, options)
            else:
                continue
        else:
            gitrepo = GitRepo(os.path.join(options.packagesdir, dir))
        ref2fetch = []
        for ref in refs.heads[dir]:
            if gitrepo.check_remote(ref) != refs.heads[dir][ref]:
                ref2fetch.append('+{}:{}/{}'.format(ref, REMOTEREFS, ref[len('refs/heads/'):]))
        if ref2fetch:
            fetch_queue.put((dir, ref2fetch))

    fetch_queue.join()

    if options.prune:
        try:
            refs = GitRemoteRefsData(options.remoterefs, '*')
        except GitRepoError as e:
            print('Problem with repository {}: {}'.format(options.remoterefs,e), file=sys.stderr)
            sys.exit(1)
        except RemoteRefsError as e:
            print('Problem with file {} in repository {}'.format(*e), file=sys.stderr)
            sys.exit(1)
        for fulldir in glob.iglob(os.path.join(options.packagesdir,options.repopattern)):
            dir = os.path.basename(fulldir)
            if len(refs.heads[dir]) == 0 and os.path.isdir(os.path.join(fulldir, '.git')):
                print('Removing', fulldir)
                shutil.rmtree(fulldir)


common_options = argparse.ArgumentParser(add_help=False)
common_options.add_argument('-d', '--packagesdir', help='local directory with git repositories',
    default=os.path.expanduser('~/PLD_clone/packages'))
common_options.add_argument('-u', '--user',
        help='the user name to register for pushes for new repositories')

parser = argparse.ArgumentParser(description='PLD tool for interaction with git repos',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

subparsers = parser.add_subparsers(help='[-h] [options]')
clone = subparsers.add_parser('update', help='fetch repositories', parents=[common_options],
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
clone.add_argument('-b', '--branch', help='branch to fetch', action=DelAppend, default=['master'])
clone.add_argument('-P', '--prune', help='prune git repositories that do no exist upstream',
        action='store_true')
clone.add_argument('-j', help='number of threads to use', default=4, type=int)
clone.add_argument('--depth', help='depth of fetch', default=0)
clone.add_argument('-n', '--newpkgs', help='download packages that do not exist on local side',
        action='store_true')
clone.add_argument('-r', '--remoterefs', help='repository with list of all refs',
    default=os.path.expanduser('~/PLD_clone/Refs.git'))
clone.add_argument('repopattern', nargs='*', default = ['*'])
clone.set_defaults(func=fetch_packages)

create = subparsers.add_parser('init', help='init new repository', parents=[common_options],
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
create.add_argument('packages', nargs='+', help='list of packages to create')
create.set_defaults(func=create_packages)

parser.set_defaults(**readconfig(os.path.expanduser('~/.gitconfig')))
options = parser.parse_args()
options.func(options)
