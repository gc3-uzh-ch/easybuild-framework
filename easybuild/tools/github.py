##
# Copyright 2012-2014 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://vscentrum.be/nl/en),
# the Hercules foundation (http://www.herculesstichting.be/in_English)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# http://github.com/hpcugent/easybuild
#
# EasyBuild is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# EasyBuild is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EasyBuild.  If not, see <http://www.gnu.org/licenses/>.
##
"""
Utility module for working with github

@author: Jens Timmerman (Ghent University)
@author: Kenneth Hoste (Ghent University)
"""
import base64
import os
import re
import tempfile
import urllib
from vsc import fancylogger

try:
    import keyring
except ImportError:
    pass

from easybuild.tools.agithub import Github
from easybuild.tools.filetools import mkdir


GITHUB_DIR_TYPE = u'dir'
GITHUB_EB_MAIN = 'hpcugent'
GITHUB_EASYCONFIGS_REPO = 'easybuild-easyconfigs'
GITHUB_FILE_TYPE = u'file'
GITHUB_MERGEABLE_STATE_CLEAN = 'clean'
GITHUB_RAW = 'https://raw.githubusercontent.com'
GITHUB_STATE_CLOSED = 'closed'
HTTP_STATUS_OK = 200
HTTP_STATUS_CREATED = 201
KEYRING_GITHUB_TOKEN = 'github_token'


_log = fancylogger.getLogger('github', fname=False)


class Githubfs(object):
    """This class implements some higher level functionality on top of the Github api"""

    def __init__(self, githubuser, reponame, branchname="master", username=None, password=None, token=None):
        """Construct a new githubfs object
        @param githubuser: the github user's repo we want to use.
        @param reponame: The name of the repository we want to use.
        @param branchname: Then name of the branch to use (defaults to master)
        @param username: (optional) your github username.
        @param password: (optional) your github password.
        @param token:    (optional) a github api token.
        """
        self.log = fancylogger.getLogger(self.__class__.__name__, fname=False)
        self.gh = Github(username, password, token)
        self.githubuser = githubuser
        self.reponame = reponame
        self.branchname = branchname

    @staticmethod
    def join(*args):
        """This method joins 'paths' inside a github repository"""
        args = [x for x in args if x]
        return os.path.sep.join(args)

    def get_repo(self):
        """Returns the repo as a Github object (from agithub)"""
        return self.gh.repos[self.githubuser][self.reponame]

    def get_path(self, path):
        """returns the path as a Github object (from agithub)"""
        endpoint = self.get_repo()['contents']
        if path:
            for subpath in path.split(os.path.sep):
                endpoint = endpoint[subpath]
        return endpoint

    @staticmethod
    def isdir(githubobj):
        """Check if this path points to a directory"""
        if isinstance(githubobj,(list, tuple)):
            return True
        else:
            try:
                return githubobj['type'] == GITHUB_DIR_TYPE
            except:
                return False

    @staticmethod
    def isfile(githubobj):
        """Check if this path points to a file"""
        try:
            return githubobj['type'] == GITHUB_FILE_TYPE
        except:
            return False

    def listdir(self, path):
        """List the contents of a directory"""
        path = self.get_path(path)
        listing = path.get(ref=self.branchname)
        self.log.debug("listdir response: %s" % str(listing))
        if listing[0] == 200:
            return listing[1]
        else:
            self.log.warning("error: %s" % str(listing))
            self.log.exception("Invalid response from github (I/O error)")

    def walk(self, top=None, topdown=True):
        """
        Walk the github repo in an os.walk like fashion.
        """
        isdir, listdir =  self.isdir, self.listdir

        # If this fails we blow up, since permissions on a github repo are recursive anyway.j
        githubobjs = listdir(top)
        # listdir works with None, but we want to show a decent 'root dir' name
        dirs, nondirs = [], []
        for githubobj in githubobjs:
            if isdir(githubobj):
                dirs.append(str(githubobj['name']))
            else:
                nondirs.append(str(githubobj['name']))

        if topdown:
            yield top, dirs, nondirs

        for name in dirs:
            new_path = self.join(top, name)
            for x in self.walk(new_path, topdown):
                yield x
        if not topdown:
            yield top, dirs, nondirs

    def read(self, path, api=True):
        """Read the contents of a file and return it
        Or, if api=False it will download the file and return the location of the downloaded file"""
        # we don't need use the api for this, but can also use raw.github.com
        # https://raw.github.com/hpcugent/easybuild/master/README.rst
        if not api:
            outfile = tempfile.mkstemp()[1]
            url = ("http://raw.github.com/%s/%s/%s/%s" % (self.githubuser, self.reponame, self.branchname, path))
            urllib.urlretrieve(url, outfile)
            return outfile
        else:
            obj = self.get_path(path).get(ref=self.branchname)[1]
            if not self.isfile(obj):
                raise GithubError("Error: not a valid file: %s" % str(obj))
            return  base64.b64decode(obj['content'])


class GithubError(Exception):
    """Error raised by the Githubfs"""
    pass


def fetch_easyconfigs_from_pr(pr, path=None, github_user=None, github_token=None):
    """Fetch patched easyconfig files for a particular PR."""

    def download(url, path):
        """Download file from specified URL to specified path."""
        _, httpmsg = urllib.urlretrieve(url, path)

        if not httpmsg.type == 'text/plain':
            _log.error("Unexpected file type for %s: %s" % (path, httpmsg.type))

    if not isinstance(pr, int):
        try:
            pr = int(pr)
        except ValueError, err:
            _log.error("Failed to parse specified pull request number '%s' as an int: %s; " % (pr, err))

    if path is None:
        path = tempfile.mkdtemp()
    else:
        # make sure path exists, create it if necessary
        mkdir(path, parents=True)

    _log.debug("Fetching easyconfigs from PR #%s into %s" % (pr, path))

    # fetch data for specified PR
    g = Github(username=github_user, token=github_token)
    pr_url = g.repos[GITHUB_EB_MAIN][GITHUB_EASYCONFIGS_REPO].pulls[pr]
    status, pr_data = pr_url.get()
    _log.debug("status: %d, data: %s" % (status, pr_data))
    if not status == HTTP_STATUS_OK:
        tup = (pr, GITHUB_EB_MAIN, GITHUB_EASYCONFIGS_REPO, status, pr_data)
        _log.error("Failed to get data for PR #%d from %s/%s (status: %d %s)" % tup)

    # 'clean' on successful (or missing) test, 'unstable' on failed tests
    stable = pr_data['mergeable_state'] == GITHUB_MERGEABLE_STATE_CLEAN
    if not stable:
        tup = (pr, GITHUB_MERGEABLE_STATE_CLEAN, pr_data['mergeable_state'])
        _log.error("Mergeable state for PR #%d is not '%s': %s." % tup)

    for key in sorted(pr_data.keys()):
        _log.debug("\n%s:\n\n%s\n" % (key, pr_data[key]))

    # determine list of changed files via diff
    diff_path = os.path.join(path, os.path.basename(pr_data['diff_url']))
    download(pr_data['diff_url'], diff_path)

    diff_lines = open(diff_path).readlines()
    patched_regex = re.compile('^\+\+\+ [a-z]/(.*)$')
    patched_files = []
    for line in diff_lines:
        res = patched_regex.search(line)
        if res:
            patched_files.append(res.group(1))
    os.remove(diff_path)

    # obtain last commit
    status, commits_data = pr_url.commits.get()
    last_commit = commits_data[-1]
    _log.debug("Commits: %s" % commits_data)

    # obtain most recent version of patched files
    for patched_file in patched_files:
        fn = os.path.basename(patched_file)
        full_url = os.path.join(GITHUB_RAW, GITHUB_EB_MAIN, GITHUB_EASYCONFIGS_REPO, last_commit['sha'], patched_file)
        _log.info("Downloading %s from %s" % (fn, full_url))
        download(full_url, os.path.join(path, fn))

    all_files = [os.path.basename(x) for x in patched_files]
    tmp_files = os.listdir(path)
    if not sorted(tmp_files) == sorted(all_files):
        _log.error("Not all patched files were downloaded to %s: %s vs %s" % (path, tmp_files, all_files))

    ec_files = [os.path.join(path, fn) for fn in tmp_files]

    return ec_files

def create_gist(txt, fn=None, descr=None, github_user=None, github_token=None):
    """Create a gist with the provided text."""
    if descr is None:
        descr = "(none)"
    if fn is None:
        fn = 'file1.txt'

    body = {
        "description": descr,
        "public": True,
        "files": {
            fn: {
                "content": txt,
            }
        }
    }
    g = Github(username=github_user, token=github_token)
    status, data = g.gists.post(body=body)

    if not status == HTTP_STATUS_CREATED:
        _log.error("Failed to create gist; status %s, data: %s" % (status, data))

    return data['html_url']

def post_comment_in_issue(issue, txt, repo=GITHUB_EASYCONFIGS_REPO, github_user=None, github_token=None):
    """Post a comment in the specified PR."""
    if not isinstance(issue, int):
        try:
            issue = int(issue)
        except ValueError, err:
            _log.error("Failed to parse specified pull request number '%s' as an int: %s; " % (issue, err))

    g = Github(username=github_user, token=github_token)
    pr_url = g.repos[GITHUB_EB_MAIN][repo].issues[issue]

    status, data = pr_url.comments.post(body={'body': txt})
    if not status == HTTP_STATUS_CREATED:
        _log.error("Failed to create comment in PR %s#%d; status %s, data: %s" % (repo, issue, status, data))

def fetch_github_token(user):
    """Fetch GitHub token for specified user from keyring."""

    token_fail_msg = "Failed to obtain GitHub token from keyring, "
    if not 'keyring' in globals():
        _log.error(token_fail_msg + "required Python module https://pypi.python.org/pypi/keyring is not available.")
    github_token = keyring.get_password(KEYRING_GITHUB_TOKEN, user)

    if github_token is None:
        msg = '\n'.join([
            "Failed to obtain GitHub token for %s, required when testing easyconfig PRs." % user,
            "Use the following procedure to install a GitHub token in your keyring:",
            "$ python",
            ">>> import getpass, keyring",
            ">>> keyring.set_password('%s', '%s', getpass.getpass())" % (KEYRING_GITHUB_TOKEN, user),
        ])
    else:
        msg = "Successfully obtained GitHub token for user %s from keyring." % user

    return github_token, msg
