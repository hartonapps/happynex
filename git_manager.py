import os
import subprocess
from datetime import datetime
from git import Repo, GitCommandError


class GitManager:
    def __init__(self, repo_path='.'):
        self.repo_path = repo_path
        self.repo = Repo(repo_path)
    
    def get_current_commit(self):
        """Get current commit hash."""
        return self.repo.head.commit.hexsha[:8]
    
    def check_remote_updates(self, remote='origin', branch='main'):
        """Check if remote branch has new commits compared to local."""
        try:
            self.repo.remotes[remote].fetch()
            local_head = self.repo.heads[branch].commit
            remote_head = self.repo.remotes[remote].heads[branch].commit
            return local_head != remote_head
        except GitCommandError:
            return False
    
    def pull_updates(self, remote='origin', branch='main'):
        """Pull latest updates from remote branch."""
        try:
            self.repo.remotes[remote].pull(branch)
            return True, "Updates pulled successfully"
        except GitCommandError as e:
            return False, str(e)
    
    def get_commit_diff(self, remote='origin', branch='main'):
        """Get files changed between local and remote."""
        try:
            self.repo.remotes[remote].fetch()
            local_head = self.repo.heads[branch].commit
            remote_head = self.repo.remotes[remote].heads[branch].commit
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


def get_git_manager():
    """Factory to get GitManager if repo exists."""
    if os.path.isdir('.git'):
        return GitManager()
    return None
