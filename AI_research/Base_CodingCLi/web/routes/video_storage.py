"""Admin folder picker and video storage migration."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from web.deps import require_admin
from web.models import ApiError
from web.services import video_storage

router = APIRouter(prefix="/api/config/video-storage", tags=["config"])


class StorageRequest(BaseModel):
    directory: str


@router.get("", summary="查看视频输出位置和迁移进度")
def read_storage(user: dict = Depends(require_admin)):
    return video_storage.status()


@router.get("/folders", summary="浏览服务器文件夹")
def folders(directory: str = "/Volumes", user: dict = Depends(require_admin)):
    try:
        return video_storage.browse(directory)
    except ValueError as exc:
        raise HTTPException(422, detail=ApiError.make("storage_directory_invalid", str(exc))) from exc


@router.put("", summary="设置视频输出目录并迁移旧文件")
def save_storage(body: StorageRequest, user: dict = Depends(require_admin)):
    try:
        result = video_storage.request_migration(body.directory)
        from web import audit
        audit.log("video_storage.migrate", user=user.get("username"), target=body.directory)
        return result
    except (ValueError, OSError) as exc:
        raise HTTPException(422, detail=ApiError.make("storage_directory_invalid", str(exc))) from exc
