from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class MRFile:
    old_path: str = ""
    new_path: str = ""
    new_file: bool = False
    deleted_file: bool = False
    diff: str = ""


@dataclass
class MRContext:
    mr_iid: str = ""
    title: str = ""
    description: str = ""
    source_branch: str = ""
    target_branch: str = ""
    author: str = ""
    state: str = ""
    web_url: str = ""
    files: List[MRFile] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    file_contents: dict = field(default_factory=dict)  # path -> content

    def combined_diff(self, limit: int = 12000) -> str:
        parts = []
        for f in self.files:
            parts.append(f"--- {f.old_path}\n+++ {f.new_path}\n{f.diff}")
        text = "\n\n".join(parts)
        return text[:limit]


class CodeHubError(RuntimeError):
    pass


class CodeHubClient:
    """华为 CodeHub 客户端 (GitLab v4 兼容协议)。无 token/开启 mock 时用内置样例。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def use_mock(self) -> bool:
        return self.settings.codehub_mock or not self.settings.codehub_token

    def _headers(self) -> dict:
        return {"PRIVATE-TOKEN": self.settings.codehub_token} if self.settings.codehub_token else {}

    def _url(self, path: str) -> str:
        prefix = self.settings.codehub_api_prefix.rstrip("/")
        base = self.settings.codehub_base_url.rstrip("/")
        return f"{base}{prefix}{path}"

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        try:
            r = httpx.get(self._url(path), params=params, headers=self._headers(), timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise CodeHubError(f"CodeHub 请求失败 {path}: {e}") from e

    @staticmethod
    def parse_mr_url(url: str) -> tuple:
        """从 MR 链接解析 (project, mr_iid)。
        支持: https://host/namespace/project/-/merge_requests/123
             https://host/namespace/project/merge_requests/123
        """
        m = re.search(r"/(?!-)([^/]+/[^/]+)/?(?:-/)?merge_requests/(\d+)", url)
        if m:
            return m.group(1), m.group(2)
        m = re.search(r"merge_requests/(\d+)", url)
        if m:
            return "", m.group(1)
        return "", ""

    @staticmethod
    def _proj(project: str) -> str:
        return quote(project, safe="") if project else ""

    # ---- 真实 API (GitLab v4) ----
    def get_mr(self, project: str, mr_iid: str) -> dict:
        return self._get(f"/projects/{self._proj(project)}/merge_requests/{mr_iid}")

    def get_mr_changes(self, project: str, mr_iid: str) -> dict:
        return self._get(f"/projects/{self._proj(project)}/merge_requests/{mr_iid}/changes")

    def get_mr_notes(self, project: str, mr_iid: str) -> list:
        return self._get(f"/projects/{self._proj(project)}/merge_requests/{mr_iid}/notes")

    def get_file(self, project: str, branch: str, path: str) -> str:
        data = self._get(
            f"/projects/{self._proj(project)}/repository/files/{quote(path, safe='')}",
            params={"ref": branch},
        )
        content = data.get("content", "")
        enc = data.get("encoding", "base64")
        if enc == "base64":
            try:
                return base64.b64decode(content).decode("utf-8", errors="replace")
            except Exception:
                return content
        return content

    # ---- 聚合: 取 MR + diff + 评论 + 变更文件全文 ----
    def fetch_mr_context(
        self,
        mr_url: Optional[str] = None,
        repo: Optional[str] = None,
        branch: Optional[str] = None,
        pasted_content: Optional[str] = None,
    ) -> MRContext:
        if pasted_content:
            return MRContext(
                title="(粘贴内容)",
                description=pasted_content[:4000],
                files=[MRFile(new_path="pasted.diff", diff=pasted_content[:8000])],
            )
        if self.use_mock:
            return _mock_mr_context()
        if not mr_url:
            raise CodeHubError("未提供 MR 链接, 且未开启 mock")
        project, mr_iid = self.parse_mr_url(mr_url)
        project = repo or project
        if not project or not mr_iid:
            raise CodeHubError(f"无法解析 MR 链接: {mr_url}")
        mr = self.get_mr(project, mr_iid)
        changes = self.get_mr_changes(project, mr_iid)
        notes = self.get_mr_notes(project, mr_iid)
        ctx = MRContext(
            mr_iid=str(mr_iid),
            title=mr.get("title", ""),
            description=mr.get("description", ""),
            source_branch=mr.get("source_branch", ""),
            target_branch=mr.get("target_branch", "") or branch or "",
            author=(mr.get("author") or {}).get("username", ""),
            state=mr.get("state", ""),
            web_url=mr.get("web_url", mr_url),
            notes=[n.get("body", "") for n in notes if n.get("body")],
        )
        for c in changes.get("changes", []):
            f = MRFile(
                old_path=c.get("old_path", ""),
                new_path=c.get("new_path", ""),
                new_file=c.get("new_file", False),
                deleted_file=c.get("deleted_file", False),
                diff=c.get("diff", ""),
            )
            ctx.files.append(f)
        # 取变更文件全文(目标分支), 供根因分析更完整上下文
        for f in ctx.files[:8]:
            try:
                ctx.file_contents[f.new_path] = self.get_file(project, ctx.target_branch, f.new_path)
            except Exception as e:
                logger.warning("取文件 %s 失败: %s", f.new_path, e)
        return ctx

    def health(self) -> tuple:
        if self.use_mock:
            return True, "CodeHub mock 模式(内置样例)"
        try:
            self._get("/version")
            return True, "CodeHub 连接正常"
        except Exception as e:
            return False, f"CodeHub 连接失败: {e}"


# ============ 内置样例 MR (离线/未配置 token 时使用) ============
def _mock_mr_context() -> MRContext:
    diff_py = (
        "@@ -18,8 +18,12 @@ def get_profile(user_id: int):\n"
        "     user = db.query(User).filter_by(id=user_id).first()\n"
        "-    return {\n"
        "-        'name': user.profile.name,\n"
        "-        'phone': user.profile.phone,\n"
        "-    }\n"
        "+    if user is None or user.profile is None:\n"
        "+        raise NotFoundError(f'user or profile not found: {user_id}')\n"
        "+    profile = user.profile\n"
        "+    return {\n"
        "+        'name': profile.name,\n"
        "+        'phone': profile.phone or '',\n"
        "+    }\n"
    )
    file_py = (
        "from db import db\n"
        "from models import User\n"
        "from errors import NotFoundError\n"
        "\n"
        "\n"
        "def get_profile(user_id: int):\n"
        "    user = db.query(User).filter_by(id=user_id).first()\n"
        "    if user is None or user.profile is None:\n"
        "        raise NotFoundError(f'user or profile not found: {user_id}')\n"
        "    profile = user.profile\n"
        "    return {'name': profile.name, 'phone': profile.phone or ''}\n"
    )
    diff_test = (
        "@@ -1,5 +1,12 @@\n"
        "+def test_get_profile_none_user():\n"
        "+    with pytest.raises(NotFoundError):\n"
        "+        get_profile(999999)\n"
        "+\n"
        "+def test_get_profile_none_profile():\n"
        "+    u = User(id=1, profile=None)\n"
        "+    # stub db ...\n"
        "+    with pytest.raises(NotFoundError):\n"
        "+        get_profile(1)\n"
    )
    return MRContext(
        mr_iid="142",
        title="fix(order): 修复获取用户资料时的空指针异常(500 AttributeError)",
        description=(
            "问题单 #BUG-2026-0815: 调用 GET /api/orders/{id}/profile 时, "
            "当用户未填写 profile 或用户不存在, 后端抛出 500 AttributeError: "
            "'NoneType' object has no attribute 'name'。本 MR 在 get_profile 中增加空值判断, "
            "缺失时返回明确的 NotFoundError。"
        ),
        source_branch="fix/null-profile",
        target_branch="master",
        author="zhangsan",
        state="opened",
        web_url="https://codehub.example.com/order-team/order-service/-/merge_requests/142",
        files=[
            MRFile(old_path="services/profile_service.py", new_path="services/profile_service.py", diff=diff_py),
            MRFile(old_path="tests/test_profile_service.py", new_path="tests/test_profile_service.py", diff=diff_test, new_file=True),
        ],
        file_contents={"services/profile_service.py": file_py},
    )
