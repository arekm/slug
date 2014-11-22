#!/usr/bin/python3

import copy
import glob
import sys
import os
import shutil
import subprocess
import queue
import multiprocessing
import argparse

import signal
import configparser

from multiprocessing import Pool as WorkerPool

from git_slug.gitconst import GITLOGIN, GITSERVER, GIT_REPO, GIT_REPO_PUSH, REMOTE_NAME, REMOTEREFS
from git_slug.gitrepo import GitRepo, GitRepoError
from git_slug.refsdata import GitArchiveRefsData, NoMatchedRepos, RemoteRefsError

class UnquoteConfig(configparser.ConfigParser):
    def get(self, section, option, **kwargs):
        value = super().get(section, option, **kwargs)
        return value.strip('"')

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

def cpu_count():
    try:
        return multiprocessing.cpu_count()
    except NotImplementedError:
        pass
    return 4

def pool_worker_init():
    signal.signal(signal.SIGINT, signal.SIG_IGN)

def readconfig(path):
    config = UnquoteConfig(delimiters='=', interpolation=None, strict=False)
    config.read(path)
    try:
        config.read(path)
    except UnicodeDecodeError:
        raise SystemExit("I have problems parsing {} file.\n\
Check if it is consistent with your locale settings.".format(path))
    optionslist = {}
    for option in ('newpkgs', 'prune'):
        if config.has_option('PLD', option):
            optionslist[option] = config.getboolean('PLD', option)
    for option in ('depth', 'repopattern', 'packagesdir'):
        if config.has_option('PLD', option):
            optionslist[option] = config.get('PLD', option)
    if config.has_option('PLD','branch'):
        optionslist['branch'] = config.get('PLD', 'branch').split()
    for option in ('jobs'):
        if config.has_option('PLD', option):
            optionslist[option] = config.getint('PLD', option)

    for pathopt in ('packagesdir'):
        if pathopt in optionslist:
            optionslist[pathopt] = os.path.expanduser(optionslist[pathopt])
    return optionslist

def initpackage(name, options):
    repo = GitRepo(os.path.join(options.packagesdir, name))
    remotepush = os.path.join(GIT_REPO_PUSH, name)
    repo.init(os.path.join(GIT_REPO, name), remotepush)
    return repo

def createpackage(name, options):
    subprocess.Popen(['ssh', GITLOGIN + GITSERVER, 'create', name]).wait()
    initpackage(name, options)

def create_packages(options):
    for package in options.packages:
        createpackage(package, options)

def getrefs(*args):
    try:
        refs = GitArchiveRefsData(*args)
    except RemoteRefsError as e:
        print('Problem with file {} in repository {}'.format(*e.args), file=sys.stderr)
        sys.exit(1)
    except NoMatchedRepos:
        print('No matching package has been found', file=sys.stderr)
        sys.exit(2)
    return refs

def fetch_package(gitrepo, ref2fetch, options):
    try:
        (stdout, stderr) = gitrepo.fetch(ref2fetch, options.depth)
        if stderr != b'':
            print('------', gitrepo.gdir[:-len('.git')], '------\n' + stderr.decode('utf-8'))
            return gitrepo
    except GitRepoError as e:
        print('------', gitrepo.gdir[:-len('.git')], '------\n', e)
         
def fetch_packages(options, return_all=False):
    refs = getrefs(options.branch, options.repopattern)
    print('Read remotes data')
    args = []
    for pkgdir in sorted(refs.heads):
        gitdir = os.path.join(options.packagesdir, pkgdir, '.git')
        if not os.path.isdir(gitdir):
            if options.newpkgs:
                gitrepo = initpackage(pkgdir, options)
            else:
                continue
        elif options.omitexisting:
            continue
        else:
            gitrepo = GitRepo(os.path.join(options.packagesdir, pkgdir))
        ref2fetch = []
        for ref in refs.heads[pkgdir]:
            if gitrepo.check_remote(ref) != refs.heads[pkgdir][ref]:
                ref2fetch.append('+{}:{}/{}'.format(ref, REMOTEREFS, ref[len('refs/heads/'):]))
        if ref2fetch:
            ref2fetch.append('refs/notes/*:refs/notes/*')
            args.append((gitrepo, ref2fetch, options))

    pool = WorkerPool(options.jobs, pool_worker_init)
    try:
        updated_repos = pool.starmap(fetch_package, args)
    except KeyboardInterrupt:
        pool.terminate()
    else:
        pool.close()
    pool.join()

    updated_repos = list(filter(None, updated_repos))

    if options.prune:
        refs = getrefs('*')
        for pattern in options.repopattern:
            for fulldir in glob.iglob(os.path.join(options.packagesdir, pattern)):
                pkgdir = os.path.basename(fulldir)
                if len(refs.heads[pkgdir]) == 0 and os.path.isdir(os.path.join(fulldir, '.git')):
                    print('Removing', fulldir)
                    shutil.rmtree(fulldir)
    if return_all:
        return refs.heads
    else:
        return updated_repos

def checkout_package(repo, options):
    try:
        repo.checkout(options.checkout)
    except GitRepoError as e:
        print('Problem with checking branch {} in repo {}: {}'.format(options.checkout, repo.gdir, e), file=sys.stderr)

def checkout_packages(options):
    if options.checkout is None:
        options.checkout = "/".join([REMOTE_NAME, options.branch[0]])
    fetch_packages(options)
    refs = getrefs(options.branch, options.repopattern)
    repos = []
    for pkgdir in sorted(refs.heads):
        repos.append(GitRepo(os.path.join(options.packagesdir, pkgdir)))
    pool = WorkerPool(options.jobs)
    try:
        pool.starmap(checkout_package, zip(repos, [options] * len(repos)))
    except KeyboardInterrupt:
        pool.terminate()
    else:
        pool.close()
    pool.join()

def clone_package(repo, options):
    try:
        repo.checkout('master')
    except GitRepoError as e:
        print('Problem with checking branch master in repo {}: {}'.format(repo.gdir, e), file=sys.stderr)

def clone_packages(options):
    repos = fetch_packages(options)
    pool = WorkerPool(options.jobs)
    try:
        pool.starmap(clone_package, zip(repos, [options] * len(repos)))
    except KeyboardInterrupt:
        pool.terminate()
    else:
        pool.close()
    pool.join()

def pull_package(gitrepo, options):
    directory = os.path.basename(gitrepo.wtree)
    try:
        (out, err) = gitrepo.commandexc(['rev-parse', '-q', '--verify', '@{u}'])
        sha1 = out.decode().strip()
        (out, err) = gitrepo.commandexc(['rebase', sha1])
        for line in out.decode().splitlines():
            print(directory,":",line)
    except GitRepoError as e:
        for line in e.args[0].splitlines():
            print("{}: {}".format(directory,line))
        pass

def pull_packages(options):
    repolist = []
    if options.updateall:
        pkgs = fetch_packages(options, True)
        for directory in sorted(os.listdir(options.packagesdir)):
            if directory in pkgs:
                repolist.append(GitRepo(os.path.join(options.packagesdir, directory)))
    else:
        repolist = fetch_packages(options, False)
    print('--------Pulling------------')
    pool = WorkerPool(options.jobs, pool_worker_init)
    try:
        pool.starmap(pull_package, zip(repolist, [options] * len(repolist)))
    except KeyboardInterrupt:
        pool.terminate()
    else:
        pool.close()
    pool.join()

def list_packages(options):
    refs = getrefs(options.branch, options.repopattern)
    for package in sorted(refs.heads):
        print(package)

common_options = argparse.ArgumentParser(add_help=False)
common_options.add_argument('-d', '--packagesdir', help='local directory with git repositories',
    default=os.path.expanduser('~/rpm/packages'))

common_fetchoptions = argparse.ArgumentParser(add_help=False, parents=[common_options])
common_fetchoptions.add_argument('-j', '--jobs', help='number of threads to use', default=cpu_count(), type=int)
common_fetchoptions.add_argument('repopattern', nargs='*', default = ['*'])
common_fetchoptions.add_argument('--depth', help='depth of fetch', default=0)

default_options = {}
parser = argparse.ArgumentParser(description='PLD tool for interaction with git repos',
        formatter_class=argparse.RawDescriptionHelpFormatter)
parser.set_defaults(**readconfig(os.path.expanduser('~/.gitconfig')))

subparsers = parser.add_subparsers(help='[-h] [options]', dest='command')
update = subparsers.add_parser('update', help='fetch repositories', parents=[common_fetchoptions],
        formatter_class=argparse.RawDescriptionHelpFormatter)
update.add_argument('-b', '--branch', help='branch to fetch', action=DelAppend, default=['master'])
newpkgsopt = update.add_mutually_exclusive_group()
newpkgsopt.add_argument('-n', '--newpkgs', help='download packages that do not exist on local side',
        action='store_true')
newpkgsopt.add_argument('-nn', '--nonewpkgs', help='do not download new packages', dest='newpkgs', action='store_false')
update.add_argument('-P', '--prune', help='prune git repositories that do no exist upstream',
        action='store_true')
update.set_defaults(func=fetch_packages, omitexisting=False)
default_options['update'] = {'omitexisting': False}

init = subparsers.add_parser('init', help='init new repository', parents=[common_options],
        formatter_class=argparse.RawDescriptionHelpFormatter)
init.add_argument('packages', nargs='+', help='list of packages to create')
init.set_defaults(func=create_packages)
default_options['init'] = {}

clone = subparsers.add_parser('clone', help='clone repositories', parents=[common_fetchoptions],
        formatter_class=argparse.RawDescriptionHelpFormatter)
clone.set_defaults(func=clone_packages, branch='[*]', prune=False, newpkgs=True, omitexisting=True)
default_options['clone'] = {'branch': '[*]', 'prune': False, 'newpkgs': True, 'omitexisting': True}

fetch = subparsers.add_parser('fetch', help='fetch repositories', parents=[common_fetchoptions],
        formatter_class=argparse.RawDescriptionHelpFormatter)
fetch.set_defaults(func=fetch_packages, branch='[*]', prune=False, newpkgs=False, omitexisting=False)
default_options['fetch'] = {'branch': '[*]', 'prune': False, 'newpkgs': False, 'omitexisting': False}

pull = subparsers.add_parser('pull', help='git-pull in all existing repositories', parents=[common_fetchoptions],
        formatter_class=argparse.RawDescriptionHelpFormatter)
pull.add_argument('--all', help='update local branches in all repositories', dest='updateall', action='store_true', default=True)
pull.add_argument('--noall', help='update local branches only when something has been fetched', dest='updateall', action='store_false', default=True)
pull.set_defaults(func=pull_packages, branch='[*]', prune=False, newpkgs=False, omitexisting=False)
default_options['pull'] = {'branch': ['*'], 'prune': False, 'newpkgs': False, 'omitexisting': False}

checkout =subparsers.add_parser('checkout', help='checkout repositories', parents=[common_fetchoptions],
        formatter_class=argparse.RawDescriptionHelpFormatter)
checkout.add_argument('-b', '--branch', help='branch to fetch', action=DelAppend, default=['master'])
checkout.add_argument('-c', '--checkout', help='branch to fetch', default=None)
checkout.add_argument('-P', '--prune', help='prune git repositories that do no exist upstream',
        action='store_true')
checkout.set_defaults(func=checkout_packages, newpkgs=True, omitexisting=False)
default_options['checkout'] = {'newpkgs': True, 'omitexisting': False}

listpkgs = subparsers.add_parser('list', help='list repositories',
        formatter_class=argparse.RawDescriptionHelpFormatter)
listpkgs.add_argument('-b', '--branch', help='show packages with given branch', action=DelAppend, default=['*'])
listpkgs.add_argument('repopattern', nargs='*', default = ['*'])
listpkgs.set_defaults(func=list_packages)
default_options['list'] = {}

options = parser.parse_args()
if hasattr(options, "func"):
    for key in default_options[options.command]:
        setattr(options, key, default_options[options.command][key])
    options.func(options)
else:
    parser.print_help()
