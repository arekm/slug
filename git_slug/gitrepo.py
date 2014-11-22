from .gitconst import EMPTYSHA1, REMOTE_NAME, REFFILE

import os
from subprocess import PIPE
import subprocess
import sys

class GitRepoError(Exception):
    pass

class GitRepo:
    def __init__(self, working_tree = None, git_dir = None):
        self.wtree = working_tree
        self.command_prefix = ['git']
        if git_dir is None and working_tree is not None:
            self.gdir = os.path.join(working_tree, '.git')
        else:
            self.gdir = git_dir
        if self.gdir is not None:
            self.command_prefix.append('--git-dir='+self.gdir)
        if self.wtree is not None:
            self.command_prefix.append('--work-tree='+self.wtree)

    def command(self, clist):
        return subprocess.Popen(self.command_prefix + clist, stdout=PIPE, stderr=PIPE, bufsize=-1)

    def commandio(self, clist):
        return self.command(clist).communicate()

    def commandexc(self, clist):
        proc = self.command(clist)
        (out, err) = proc.communicate()
        if proc.returncode:
            raise GitRepoError((out + err).decode('utf-8'))
        return (out, err)

    def checkout(self, branch):
        clist = ['checkout', '-m', branch]
        return self.commandexc(clist)

    def commitfile(self, path, message):
        clist = ['add', path]
        self.commandexc(clist)
        clist = ['diff', '--cached', '--exit-code']
        try:
            self.commandexc(clist)
        except GitRepoError:
            clist = ['commit', '-m', message]
            self.commandexc(clist)

    def configvalue(self, option):
        clist = ['config', '-z', option]
        try:
            return self.commandexc(clist)[0].decode("utf-8")
        except GitRepoError:
            return None

    def fetch(self, fetchlist=[], depth = 0, remotename=REMOTE_NAME):
        clist = ['fetch']
        if depth:
            clist.append('--depth={}'.format(depth))
        clist += [ remotename ] + fetchlist
        return self.commandexc(clist)

    def init_gitdir(self):
        clist = ['git', 'init']
        if os.path.dirname(self.gdir) == self.wtree:
            clist.append(self.wtree)
        else:
            clist.extend(['--bare', self.gdir])
        if subprocess.call(clist):
            raise GitRepoError(self.gdir)

    def init(self, remotepull, remotepush = None, remotename=REMOTE_NAME):
        if os.path.isdir(self.gdir):
            print("WARNING: Directory {} already existed".format(self.gdir), file=sys.stderr)
        self.init_gitdir()
        self.commandio(['remote', 'add', remotename, remotepull])
        if remotepush is not None:
            self.commandio(['remote', 'set-url', '--push', remotename, remotepush])
        self.commandio(['config', '--local', '--add', 'remote.{}.fetch'.format(remotename),
            'refs/notes/*:refs/notes/*'])

    def check_remote(self, ref, remote=REMOTE_NAME):
        localref = EMPTYSHA1
        ref = ref.replace(REFFILE, os.path.join('remotes', remote))
        try:
            with open(os.path.join(self.gdir, ref), 'r') as f:
                localref = f.readline().strip()
        except IOError:
            try:
                with open(os.path.join(self.gdir, 'packed-refs')) as f:
                    for line in f:
                        line_data = line.split()
                        if len(line_data) == 2 and line_data[1] == ref:
                            localref = line_data[0].strip()
                            break
            except IOError:
                pass
        return localref

    def showfile(self, filename, ref="/".join([REMOTE_NAME, "master"])):
        clist = ['show', ref + ':' + filename]
        return self.command(clist)
