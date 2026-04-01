# Copyright (c) 2026 SiOrigin Co. Ltd.
# SPDX-License-Identifier: Apache-2.0

import subprocess
from pathlib import Path

from loguru import logger

from core.git_executable import GIT

from .models import FileChange, GitRef


class GitManager:
    """Git repository operations manager.

    Provides Git operations for version switching with incremental graph updates.
    Handles non-Git repositories gracefully (is_git_repo will be False).

    Example:
        git_mgr = GitManager(repo_path=Path("/path/to/repo"))

        if git_mgr.is_git_repo:
            branches = git_mgr.list_branches()
            tags = git_mgr.list_tags()
            changes = git_mgr.checkout("main")
    """

    def __init__(self, repo_path: Path):
        """Initialize the Git manager.

        Args:
            repo_path: Repository root path
        """
        self.repo_path = Path(repo_path)
        self._is_git_repo = self._check_is_git_repo()

    @property
    def is_git_repo(self) -> bool:
        """Whether this is a Git repository."""
        return self._is_git_repo

    def _check_is_git_repo(self) -> bool:
        """Check if the path is a Git repository."""
        git_dir = self.repo_path / ".git"
        return git_dir.exists() and git_dir.is_dir()

    def _git(
        self, *args: str, check: bool = True, capture: bool = True
    ) -> subprocess.CompletedProcess | None:
        """Run a Git command.

        Args:
            *args: Git command arguments
            check: Whether to raise on non-zero exit code
            capture: Whether to capture stdout/stderr

        Returns:
            CompletedProcess or None if command failed and check=False
        """
        cmd = [GIT] + list(args)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=capture,
                text=True,
                check=check,
            )
            return result
        except subprocess.CalledProcessError as e:
            # Include stderr in error message for better debugging
            stderr = e.stderr.strip() if e.stderr else ""
            error_msg = f"Git command failed: {' '.join(cmd)}"
            if stderr:
                error_msg += f" - {stderr}"
            logger.debug(error_msg)
            if check:
                # Raise a more informative exception
                raise RuntimeError(error_msg) from e
            return None
        except FileNotFoundError:
            logger.debug("Git is not installed")
            if check:
                raise RuntimeError("Git is not installed")
            return None

    def list_branches(self, include_remote: bool = False) -> list[GitRef]:
        """List all branches.

        Args:
            include_remote: Whether to include remote branches

        Returns:
            List of GitRef objects for each branch
        """
        if not self._is_git_repo:
            return []

        # Get current branch/commit
        current = self.get_current_ref()

        refs: list[GitRef] = []

        # Local branches
        result = self._git("branch", "--format=%(refname:short)%00%(objectname)")
        if result and result.stdout:
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\x00")
                if len(parts) == 2:
                    name, sha = parts
                    refs.append(
                        GitRef(
                            name=name,
                            ref_type="branch",
                            commit_sha=sha,
                            is_current=current is not None and current.name == name,
                        )
                    )

        # Remote branches
        if include_remote:
            result = self._git(
                "branch", "--remote", "--format=%(refname:short)%00%(objectname)"
            )
            if result and result.stdout:
                for line in result.stdout.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split("\x00")
                    if len(parts) == 2:
                        name, sha = parts
                        refs.append(
                            GitRef(
                                name=name,
                                ref_type="branch",
                                commit_sha=sha,
                                is_current=False,  # Remote branches are never directly checked out
                            )
                        )

        return refs

    def list_tags(self) -> list[GitRef]:
        """List all tags.

        Returns:
            List of GitRef objects for each tag
        """
        if not self._is_git_repo:
            return []

        refs: list[GitRef] = []

        result = self._git("tag", "--format=%(refname:short)%00%(objectname)")
        if result and result.stdout:
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\x00")
                if len(parts) == 2:
                    name, sha = parts
                    refs.append(
                        GitRef(
                            name=name,
                            ref_type="tag",
                            commit_sha=sha,
                            is_current=False,
                        )
                    )

        return refs

    def get_current_ref(self) -> GitRef | None:
        """Get the currently checked-out reference.

        Returns:
            GitRef for current branch/commit, or None if not a Git repo
        """
        if not self._is_git_repo:
            return None

        # Try to get branch name first
        result = self._git("rev-parse", "--abbrev-ref", "HEAD", check=False)
        if result and result.stdout:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                # We're on a branch
                sha_result = self._git("rev-parse", "HEAD")
                sha = sha_result.stdout.strip() if sha_result else ""
                return GitRef(
                    name=branch,
                    ref_type="branch",
                    commit_sha=sha,
                    is_current=True,
                )

        # We're in detached HEAD state, return commit
        sha_result = self._git("rev-parse", "HEAD")
        if sha_result and sha_result.stdout:
            sha = sha_result.stdout.strip()
            return GitRef(
                name=sha,
                ref_type="commit",
                commit_sha=sha,
                is_current=True,
            )

        return None

    def fetch(self, remote: str = "origin") -> None:
        """Fetch updates from a remote.

        Args:
            remote: Remote name to fetch from
        """
        if not self._is_git_repo:
            logger.warning("Cannot fetch: not a Git repository")
            return

        logger.info(f"Fetching from remote '{remote}'...")
        self._git("fetch", remote)

    def _has_local_changes(self) -> bool:
        """Check if there are any uncommitted local changes (staged or unstaged)."""
        result = self._git("status", "--porcelain", check=False)
        return bool(result and result.stdout and result.stdout.strip())

    def _stash_local_changes(self) -> bool:
        """Stash local changes if any exist.

        Returns:
            True if changes were stashed, False if working tree was clean.
        """
        if not self._has_local_changes():
            return False

        logger.info("Stashing local changes before pull...")
        result = self._git(
            "stash", "push", "-m", "atcode-auto-stash", "--include-untracked",
            check=False,
        )
        if result and result.returncode == 0:
            logger.info("Local changes stashed successfully")
            return True

        logger.warning("Failed to stash local changes, attempting reset")
        return False

    def _pop_stash(self) -> None:
        """Pop the most recent stash if it was created by us."""
        # Check if the top stash is ours
        result = self._git("stash", "list", "-1", check=False)
        if result and result.stdout and "atcode-auto-stash" in result.stdout:
            pop_result = self._git("stash", "pop", check=False)
            if pop_result and pop_result.returncode == 0:
                logger.info("Restored stashed local changes")
            else:
                logger.warning(
                    "Failed to restore stashed changes. "
                    "They remain in the stash and can be recovered with 'git stash pop'."
                )

    def pull(
        self, remote: str = "origin", branch: str | None = None, progress_callback=None,
        auto_stash: bool = True,
    ) -> list[FileChange]:
        """Pull updates from remote (fetch + merge) and return changed files.

        Fetches from the remote, then fast-forward merges.
        Uses diff between old HEAD and new HEAD to compute changes.

        Args:
            remote: Remote name to pull from
            branch: Branch to pull (defaults to current branch)
            progress_callback: Optional callback (progress, step, message)
            auto_stash: If True, automatically stash and restore local changes

        Returns:
            List of FileChange objects representing the changes

        Raises:
            RuntimeError: If pull fails (e.g. conflicts)
        """
        if not self._is_git_repo:
            logger.warning("Cannot pull: not a Git repository")
            return []

        def _update_progress(progress: int, step: str, message: str):
            if progress_callback:
                try:
                    progress_callback(progress, step, message)
                except Exception:
                    pass

        # Get current HEAD before pull
        current = self.get_current_ref()
        if current is None:
            logger.warning("Cannot pull: no current ref")
            return []

        old_sha = current.commit_sha

        # Determine branch to pull
        if branch is None:
            if current.ref_type == "branch":
                branch = current.name
            else:
                raise RuntimeError(
                    "Cannot pull: HEAD is detached. Specify a branch explicitly."
                )

        _update_progress(5, "fetching", f"Fetching from {remote}/{branch}...")

        # Fetch
        self._git("fetch", remote)

        _update_progress(20, "computing_diff", "Computing changes...")

        # Check if there are new commits
        remote_ref = f"{remote}/{branch}"
        result = self._git("rev-parse", remote_ref, check=False)
        if not result or not result.stdout:
            logger.info(f"Remote ref {remote_ref} not found")
            return []

        new_sha = result.stdout.strip()

        if old_sha == new_sha:
            logger.info("Already up to date")
            _update_progress(100, "up_to_date", "Already up to date")
            return []

        # Compute diff before merge
        changes = self._compute_diff(old_sha, new_sha)

        _update_progress(
            40,
            "merging",
            f"Merging {len(changes)} file changes from {remote}/{branch}...",
        )

        # Auto-stash local changes to prevent merge conflicts
        stashed = False
        if auto_stash:
            stashed = self._stash_local_changes()
            if stashed:
                _update_progress(45, "stashed", "Stashed local changes")

        # Fast-forward merge (safe: fails if not fast-forwardable)
        merge_result = self._git(
            "merge", "--ff-only", remote_ref, check=False
        )
        if merge_result is None or merge_result.returncode != 0:
            # Try regular merge
            merge_result = self._git("merge", remote_ref, check=False)
            if merge_result is None or merge_result.returncode != 0:
                stderr = merge_result.stderr.strip() if merge_result and merge_result.stderr else ""
                # Restore stashed changes before raising
                if stashed:
                    self._pop_stash()
                raise RuntimeError(
                    f"Merge failed for {remote_ref}: {stderr}. "
                    "Resolve conflicts manually and run sync."
                )

        # Restore stashed changes after successful merge
        if stashed:
            self._pop_stash()
            _update_progress(55, "unstashed", "Restored local changes")

        _update_progress(60, "merged", f"Merged {len(changes)} file changes")

        return changes

    def _compute_diff(
        self, from_ref: str, to_ref: str | None = None
    ) -> list[FileChange]:
        """Get file changes between two refs.

        Args:
            from_ref: Starting reference
            to_ref: Ending reference (defaults to HEAD)

        Returns:
            List of FileChange objects
        """
        if to_ref is None:
            to_ref = "HEAD"

        changes: list[FileChange] = []

        # Get list of changed files
        # Use .. (two dots) for direct diff between commits, not ... (merge base)
        result = self._git(
            "diff",
            "--name-status",
            f"{from_ref}..{to_ref}",
            check=False,
        )

        if result and result.stdout:
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                # Split by tab - note that rename/copy have 3 parts: status, old_path, new_path
                parts = line.split("\t")
                if len(parts) >= 2:
                    status = parts[0]
                    action_map = {
                        "A": "add",
                        "M": "modify",
                        "D": "delete",
                        "R": "rename",  # Rename: delete old + add new
                        "C": "add",  # Copy: just add the new file
                        "T": "modify",  # Type change is treated as modify
                    }
                    action = action_map.get(status[0], "modify")

                    if action == "rename" and len(parts) >= 3:
                        # For rename: parts[1] is old_path, parts[2] is new_path
                        old_path = parts[1]
                        new_path = parts[2]
                        # Treat rename as delete of old path + add of new path
                        old_file_path = self.repo_path / old_path
                        new_file_path = self.repo_path / new_path
                        changes.append(FileChange(path=old_file_path, action="delete"))
                        changes.append(FileChange(path=new_file_path, action="add"))
                    else:
                        # For other actions: parts[1] is the file path
                        # (for copy with len>=3, parts[2] is the new path, which we add)
                        path = (
                            parts[-1]
                            if action == "add" and len(parts) >= 3
                            else parts[1]
                        )
                        file_path = self.repo_path / path
                        changes.append(
                            FileChange(
                                path=file_path,
                                action=action if action != "rename" else "modify",
                            )
                        )

        return changes

    def checkout(
        self, ref: str, force: bool = False, progress_callback=None
    ) -> list[FileChange]:
        """Switch to a different Git reference.

        Calculates diff BEFORE executing checkout to get accurate change list.
        Can force checkout to discard local changes.

        Args:
            ref: Branch name, tag name, or commit SHA
            force: If True, use -f flag to discard local changes
            progress_callback: Optional callback for progress updates (progress, step, message)

        Returns:
            List of FileChange objects representing the changes
        """
        if not self._is_git_repo:
            logger.warning("Cannot checkout: not a Git repository")
            return []

        def _update_progress(progress: int, step: str, message: str):
            if progress_callback:
                try:
                    progress_callback(progress, step, message)
                except Exception:
                    pass

        current = self.get_current_ref()
        if current is None:
            # First checkout, all files are "added"
            _update_progress(
                10, "git_checkout", f"Checking out '{ref}' (first time)..."
            )
            if force:
                self._git("checkout", "-f", ref)
            else:
                self._git("checkout", ref)
            _update_progress(50, "git_checkout", f"Checked out '{ref}'")
            return []

        # Get current HEAD SHA
        current_sha = current.commit_sha

        _update_progress(
            5,
            "computing_diff",
            f"Computing diff between {current_sha[:7]} and {ref}...",
        )

        # Calculate diff before checkout
        changes = self._compute_diff(current_sha, ref)

        _update_progress(
            20,
            "git_checkout",
            f"Checking out '{ref}' ({len(changes)} files will change)...",
        )

        # Execute checkout
        logger.info(f"Checking out '{ref}' (force={force})...")

        # Use force flag if requested to discard local changes
        if force:
            # First, try to reset any local changes
            _update_progress(25, "git_reset", "Resetting local changes...")
            try:
                # Reset hard to discard all local changes
                self._git("reset", "--hard", "HEAD", check=False)
            except Exception as e:
                logger.debug(f"Reset failed (might be ok): {e}")

            # Then checkout with force flag
            _update_progress(30, "git_checkout", f"Force checking out '{ref}'...")
            self._git("checkout", "-f", ref)
        else:
            # Try normal checkout first
            try:
                self._git("checkout", ref)
            except RuntimeError as e:
                # If checkout fails due to local changes, log the error
                # The caller can decide what to do
                error_msg = str(e)
                if "would be overwritten" in error_msg or "local changes" in error_msg:
                    logger.warning(
                        "Checkout failed due to local changes. Use force=True to discard."
                    )
                    # Provide a helpful error message
                    raise RuntimeError(
                        f"Cannot checkout '{ref}': Local changes would be overwritten. "
                        f"Use force=True to discard local changes, or commit/stash them first."
                    ) from e
                raise

        _update_progress(60, "git_checkout", f"Checked out '{ref}' successfully")

        return changes

    def diff(self, from_ref: str, to_ref: str) -> list[FileChange]:
        """Get file changes between two refs without switching.

        Args:
            from_ref: Starting reference
            to_ref: Ending reference

        Returns:
            List of FileChange objects
        """
        if not self._is_git_repo:
            return []

        return self._compute_diff(from_ref, to_ref)
