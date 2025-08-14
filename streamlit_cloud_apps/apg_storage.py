# streamlit_cloud_apps/apg_storage.py
from __future__ import annotations
import os, json, base64, requests
from pathlib import Path
from typing import List, Dict, Any, Optional

# .../apg_git_repo/streamlit_cloud_apps/apg_storage.py -> parents[2] == apg_git_repo/
REPO_ROOT = Path(__file__).resolve().parents[2]

def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if (v is not None and str(v).strip() != "") else default

class Storage:
    """
    Backends:
      - 'gh'   GitHub repo via REST API (free, works across two Streamlit Cloud apps)
      - 'local' Local folder (for your on-prem APG Hub server)
    Select with APG_STORAGE_BACKEND=gh|local  (defaults to 'gh').
    """
    def __init__(self):
        self.backend = (_env("APG_STORAGE_BACKEND", "gh") or "gh").lower()

        if self.backend == "gh":
            self.gh_owner  = _env("APG_GH_OWNER")
            self.gh_repo   = _env("APG_GH_REPO")
            self.gh_branch = _env("APG_GH_BRANCH", "main")
            self.gh_prefix = (_env("APG_GH_PREFIX", "") or "").strip("/")
            self.gh_token  = _env("APG_GH_TOKEN")
            for k in ("APG_GH_OWNER", "APG_GH_REPO", "APG_GH_TOKEN"):
                if not _env(k):
                    raise RuntimeError(f"{k} must be set for GitHub storage backend.")
        else:
            self.backend = "local"
            base = _env("APG_STORAGE_DIR", str(REPO_ROOT / "storage"))
            self.base_dir = Path(base)
            (self.base_dir / "submissions").mkdir(parents=True, exist_ok=True)
            (self.base_dir / "screenshots").mkdir(parents=True, exist_ok=True)

    # -------- Public API --------
    def write_json(self, key: str, obj: Dict[str, Any]) -> str:
        data = json.dumps(obj, indent=2).encode("utf-8")
        return self._put_bytes(key, data, "application/json", commit_msg=f"write {key}")

    def read_json(self, key: str) -> Dict[str, Any]:
        b = self._get_bytes(key)
        return json.loads(b.decode("utf-8"))

    def list_json(self, prefix: str) -> List[Dict[str, Any]]:
        files = self._list(prefix)
        out: List[Dict[str, Any]] = []
        for key in files:
            if not key.endswith(".json"):
                continue
            try:
                d = self.read_json(key)
                d["_key"] = key  # for round-trip updates
                out.append(d)
            except Exception:
                pass
        return out

    def save_bytes(self, key: str, data: bytes, content_type: Optional[str] = None) -> str:
        return self._put_bytes(key, data, content_type or "application/octet-stream",
                               commit_msg=f"upload {key}")

    def url(self, key: str) -> Optional[str]:
        key = self._norm(key)
        if self.backend == "gh":
            # Private repo link (users need GH access)
            return f"https://github.com/{self.gh_owner}/{self.gh_repo}/blob/{self.gh_branch}/{key}"
        return str((self.base_dir / key).absolute())

    # -------- Internals --------
    def _norm(self, key: str) -> str:
        key = key.lstrip("/")
        if self.backend == "gh" and self.gh_prefix:
            return f"{self.gh_prefix}/{key}"
        return key

    def _put_bytes(self, key: str, data: bytes, content_type: str, commit_msg: str) -> str:
        key = self._norm(key)
        if self.backend == "local":
            p = self.base_dir / key
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            return str(p)
        return self._gh_put(key, data, commit_msg)

    def _get_bytes(self, key: str) -> bytes:
        key = self._norm(key)
        if self.backend == "local":
            return (self.base_dir / key).read_bytes()
        return self._gh_get(key)

    def _list(self, prefix: str) -> List[str]:
        prefix = self._norm(prefix.rstrip("/") + "/")
        if self.backend == "local":
            base = (self.base_dir / prefix)
            if not base.exists():
                return []
            return [f"{prefix}{n}" for n in os.listdir(base)]
        r = self._gh_api("GET", f"/repos/{self.gh_owner}/{self.gh_repo}/contents/{prefix}",
                         params={"ref": self.gh_branch})
        if r.status_code == 404:
            return []
        r.raise_for_status()
        entries = r.json()
        return [e["path"] for e in entries if e.get("type") == "file"]

    # -------- GitHub helpers --------
    def _gh_api(self, method: str, path: str, **kw):
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.gh_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        return requests.request(method, f"https://api.github.com{path}", headers=headers, **kw)

    def _gh_contents_path(self, key: str) -> str:
        return f"/repos/{self.gh_owner}/{self.gh_repo}/contents/{key}"

    def _gh_get_sha(self, key: str) -> Optional[str]:
        r = self._gh_api("GET", self._gh_contents_path(key), params={"ref": self.gh_branch})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json().get("sha")

    def _gh_put(self, key: str, data: bytes, message: str) -> str:
        b64 = base64.b64encode(data).decode("ascii")
        sha = self._gh_get_sha(key)
        payload = {
            "message": message,
            "content": b64,
            "branch": self.gh_branch,
            "committer": {
                "name": _env("APG_GH_COMMIT_NAME", "APG Bot"),
                "email": _env("APG_GH_COMMIT_EMAIL", "bot@apg.local"),
            }
        }
        if sha:
            payload["sha"] = sha
        r = self._gh_api("PUT", self._gh_contents_path(key), json=payload)
        r.raise_for_status()
        return key

    def _gh_get(self, key: str) -> bytes:
        r = self._gh_api("GET", self._gh_contents_path(key), params={"ref": self.gh_branch})
        r.raise_for_status()
        j = r.json()
        if j.get("encoding") == "base64":
            return base64.b64decode(j.get("content", ""))
        dl = j.get("download_url")
        if dl:
            r2 = requests.get(dl)
            r2.raise_for_status()
            return r2.content
        raise RuntimeError(f"Unable to fetch {key} from GitHub")

storage = Storage()
