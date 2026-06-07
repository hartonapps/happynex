import os
import subprocess
from datetime import datetime
from git import Repo, GitCommandError


class GitManager:
    def __init__(self, repo_path='.', remote_name='origin', remote_url=None):
        self.repo_path = repo_path
        self.remote_name = remote_name
        self.remote_url = remote_url

        if os.path.isdir(os.path.join(repo_path, '.git')):
            self.repo = Repo(repo_path)
        elif remote_url:
            self.repo = Repo.init(repo_path)
            self.ensure_remote(remote_name, remote_url)
        else:
            raise RuntimeError('Not a git repository and no remote URL configured')
    
    def get_current_commit(self):
        """Get current commit hash."""
        return self.repo.head.commit.hexsha[:8]
    
    def ensure_remote(self, remote_name, remote_url):
        if remote_name in [remote.name for remote in self.repo.remotes]:
            remote = self.repo.remotes[remote_name]
            current_url = next(iter(remote.urls), None)
            if remote_url and current_url != remote_url:
                self.repo.git.remote('set-url', remote_name, remote_url)
        else:
            remote = self.repo.create_remote(remote_name, remote_url)
        return remote

    def get_remote(self, remote_name=None):
        remote_name = remote_name or self.remote_name
        if remote_name in [remote.name for remote in self.repo.remotes]:
            return self.repo.remotes[remote_name]
        if self.remote_url:
            return self.ensure_remote(remote_name, self.remote_url)
        raise RuntimeError(f'Remote {remote_name} is not configured')

    def check_remote_updates(self, remote='origin', branch='main'):
        """Check if remote branch has new commits compared to local."""
        try:
            git_remote = self.get_remote(remote)
            git_remote.fetch(branch)
            remote_ref = next((ref for ref in git_remote.refs if ref.remote_head == branch), None)
            if not remote_ref:
                return False
            remote_head = remote_ref.commit
            local_head = None
            try:
                local_head = self.repo.heads[branch].commit
            except Exception:
                pass
            if local_head is None:
                return True
            return local_head != remote_head
        except GitCommandError:
            return False
    
    def pull_updates(self, remote='origin', branch='main'):
        """Fetch the remote branch and reset tracked files to match it."""
        try:
            git_remote = self.get_remote(remote)
            git_remote.fetch(branch)
            self.repo.git.reset('--hard', 'FETCH_HEAD')
            return True, "Updates pulled successfully"
        except GitCommandError as e:
            return False, str(e)
    
    def get_commit_diff(self, remote='origin', branch='main'):
        """Get files changed between local and remote."""
        try:
            git_remote = self.get_remote(remote)
            git_remote.fetch(branch)
            remote_ref = next((ref for ref in git_remote.refs if ref.remote_head == branch), None)
            if not remote_ref:
                return [f'Remote {git_remote.name}/{branch} is unavailable']
            remote_head = remote_ref.commit
            local_head = None
            try:
                local_head = self.repo.heads[branch].commit
            except Exception:
                pass
            if local_head is None:
                return [f'Remote branch {git_remote.name}/{branch} has commits and local branch does not exist']
            diff_index = local_head.diff(remote_head)
            changed_files = [d.a_path or d.b_path for d in diff_index]
            return changed_files
        except Exception:
            return []
    
    def get_last_commit_message(self):
        """Get the last commit message."""
        try:
            return self.repo.head.commit.message.strip()
        except Exception:
            return "No commits found"


def get_git_manager(remote_name='origin', remote_url=None):
    """Factory to get GitManager if repo exists or remote URL is configured."""
    if os.path.isdir('.git') or remote_url:
        try:
            return GitManager(remote_name=remote_name, remote_url=remote_url)
        except Exception as e:
            print(f'GitManager initialization failed: {e}')
            return None
    return None
